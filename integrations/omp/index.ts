import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Bridge } from "./client";

const roles = new Set(["aidevflow-intent-analyst", "aidevflow-contract-planner", "aidevflow-independent-verifier"]);
const reads = new Set(["read", "grep", "glob", "web_search", "ask", "todo", "wait", "recall", "reflect"]);
const criticalInput = (input: any): boolean => roles.has(String(input?.agent)) ||
  (Array.isArray(input?.tasks) && input.tasks.some((item: any) => roles.has(item?.agent)));
const methodRead = (input: any): boolean => typeof input?.path === "string" &&
  /(?:^|\/)zsspec-[a-z0-9-]+(?:\/|:\d|$)/.test(input.path.replaceAll("\\", "/"));

export default function aidevflow(pi: ExtensionAPI): void {
  // Factories are rebound for children; keep all mutable state in this closure.
  let bridge: Bridge | undefined;
  let root = "";
  let lastContext = "";
  let verifierPrompt = "";
  const client = (ctx: ExtensionContext): Bridge => {
    if (!bridge || root !== ctx.cwd) {
      bridge?.close();
      root = ctx.cwd;
      bridge = new Bridge(root);
    }
    return bridge;
  };
  const observation = (event: any, ctx: ExtensionContext) => ({
    ...event, session_id: ctx.sessionManager.getSessionId(),
    session_file: ctx.sessionManager.getSessionFile(), actor: ctx.agent,
  });
  const active = (ctx: ExtensionContext) => existsSync(join(ctx.cwd, ".aidevflow/active-run.json"));
  const nativeCapabilities = () => ({
    tools: pi.getActiveTools(),
    skill_commands: pi.getCommands().filter(command => command.source === "skill").map(command => command.name),
  });
  const showPreflight = (report: unknown) => pi.sendMessage({
    customType: "aidevflow.preflight", content: JSON.stringify(report, null, 2), display: true,
  }, { triggerTurn: false });
  const recover = async (ctx: ExtensionContext) => {
    const entries = ctx.sessionManager.getBranch().filter((entry: any) => {
      const message = entry.message;
      return message?.toolName === "task" || (message?.role === "assistant" &&
        Array.isArray(message.content) && message.content.some((block: any) => block.type === "toolCall" && block.name === "task"));
    });
    return await client(ctx).request("recover", { session_id: ctx.sessionManager.getSessionId(), entries });
  };

  pi.registerCommand("aidevflow-flow", {
    description: "Start or resume the AIDevFlow delivery workflow",
    handler: async (args, ctx) => {
      const preflight = await client(ctx).request("preflight", nativeCapabilities());
      if (!preflight.ok) { showPreflight(preflight); return; }
      const skill = readFileSync(join(ctx.cwd, ".omp/skills/aidevflow-flow/SKILL.md"), "utf8");
      pi.sendUserMessage(`${skill}\n\nUser request: ${args || "Read doctor/status and resume the current task; ask for a requirement if none exists."}`);
    },
  });
  pi.registerCommand("aidevflow-doctor", {
    description: "Inspect Gate setup and this OMP session's actual tools without starting a task",
    handler: async (_args, ctx) => showPreflight(await client(ctx).request("preflight", nativeCapabilities())),
  });

  pi.on("before_agent_start", async (_event, ctx) => {
    if (!active(ctx)) return;
    if (ctx.agent.name === "aidevflow-independent-verifier") {
      if (_event.prompt) verifierPrompt = _event.prompt;
      const definition = readFileSync(join(ctx.cwd, ".omp/agents/aidevflow-independent-verifier.md"), "utf8");
      const body = definition.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, "");
      // Remove project context, auto-loaded skills and memory from the base prompt.
      return { systemPrompt: [body] };
    }
    if (ctx.agent.kind !== "main") return;
    const status = await client(ctx).request("status");
    const context = JSON.stringify(status);
    if (context === lastContext) return;
    lastContext = context;
    return { message: { customType: "aidevflow.state", content: `Gate checkpoint: ${context}. Use full status before accepting evidence.`, display: false } };
  });

  const onToolCall = async (event: any, ctx: ExtensionContext) => {
    const critical = roles.has(ctx.agent.name);
    if (event.toolName === "read" && ctx.agent.name !== "aidevflow-independent-verifier" &&
        active(ctx) && methodRead(event.input)) {
      const prepared = await client(ctx).request("method-load", { input: event.input });
      if (prepared) return { input: prepared.input, additionalContext:
        `${prepared.additionalContext} Loaded source: ${JSON.stringify(prepared.source)}` };
    }
    if (reads.has(event.toolName) && !critical) return; // zero subprocess overhead for ordinary reads
    if (event.toolName === "task" && active(ctx) && ctx.agent.kind === "main") {
      const inp = event.input as Record<string, unknown>;
      if (criticalInput(inp) && Array.isArray(inp.tasks) && inp.tasks.length !== 1) {
        return { block: true, reason: "Use one critical AIDevFlow role per task call (a one-item tasks array is supported)" };
      }
      if (criticalInput(inp)) {
        const item: any = Array.isArray(inp.tasks) ? inp.tasks[0] : inp;
        if (item.agent === "aidevflow-independent-verifier") {
          const prepared = await client(ctx).request("role-context", { role: "verifier" });
          const safe = prepared.task_call;
          const input = Array.isArray(inp.tasks) ? { ...inp, context: safe.context, tasks: safe.tasks } :
            { agent: item.agent, task: `${safe.context}\n${safe.tasks[0].task}`, solutionSpace: safe.tasks[0].solutionSpace };
          await client(ctx).request("observe", observation({ ...event, input }, ctx));
          return { input };
        }
        await client(ctx).request("observe", observation(event, ctx));
        return;
      }
    }
    return await client(ctx).request("guard", observation(event, ctx));
  };
  pi.on("tool_call", async (event, ctx) => {
    try { return await onToolCall(event, ctx); }
    catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      return { block: true, reason: `AIDevFlow rejected ${event.toolName}: ${reason}. Read status and role-context for the current checkpoint; use /aidevflow-doctor for setup errors. Do not bypass or manufacture a receipt.` };
    }
  });

  pi.on("context", (event, ctx) => {
    if (ctx.agent.name !== "aidevflow-independent-verifier") return;
    let firstUser = false;
    return { messages: event.messages.flatMap((message: any) => {
      if (message.role === "user") {
        if (firstUser || !verifierPrompt) return [];
        firstUser = true;
        return [{ role: "user", content: verifierPrompt, timestamp: message.timestamp }];
      }
      return message.role === "assistant" || message.role === "toolResult" ? [message] : [];
    }) };
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.toolName === "task" && criticalInput(event.input) && active(ctx)) {
      const receipt = await client(ctx).request("observe", observation(event, ctx));
      return { additionalContext: `AIDevFlow native receipt: ${JSON.stringify({ id: receipt.id, status: receipt.status, result_path: receipt.result_path })}. Pass the exact immutable result_path to record-intent --draft, register-contract --draft or record-verdict --path as appropriate. Do not transcribe rendered JSON or read private observation logs. Invalid output must be corrected by a fresh role attempt after the previous execution has ended.` };
    }
  });

  pi.on("session_stop", async (_event, ctx) => {
    if (!active(ctx) || ctx.agent.kind !== "main") return;
    if (_event.signal?.aborted) return;
    const modes = ctx.sessionModes?.getState();
    // Native mode continuation takes precedence; never add a second loop.
    if ((modes && modes.goal.status !== "inactive") || (modes && modes.loop.status !== "inactive")) return;
    return await client(ctx).request("stop");
  });
  pi.on("session_switch", async (_event, ctx) => {
    bridge?.close(); bridge = undefined; lastContext = "";
    if (active(ctx) && ctx.agent.kind === "main") await recover(ctx);
  });
  pi.on("session_start", async (_event, ctx) => {
    if (active(ctx) && ctx.agent.kind === "main") await recover(ctx);
  });
  pi.on("session_shutdown", async () => { bridge?.close(); bridge = undefined; });
}
