import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Bridge } from "./client";

const hostRuntime = "omp";

const roles = new Set(["aidevflow-intent-analyst", "aidevflow-contract-planner", "aidevflow-independent-verifier", "aidevflow-requirements-analyst"]);
const reads = new Set(["read", "grep", "glob", "web_search", "ask", "todo", "wait", "recall", "reflect"]);
const criticalInput = (input: any): boolean => roles.has(String(input?.agent)) ||
  (Array.isArray(input?.tasks) && input.tasks.some((item: any) => roles.has(item?.agent)));
const methodRead = (input: any): boolean => typeof input?.path === "string" &&
  (/(?:^|\/)zsspec-[a-z0-9-]+(?:\/|:\d|$)/.test(input.path.replaceAll("\\", "/")) ||
   /(?:^|\/)README\.md(?::\d+(?:-\d+)?)?$/.test(input.path.replaceAll("\\", "/")));

export default function aidevflow(pi: ExtensionAPI): void {
  // Factories are rebound for children; keep all mutable state in this closure.
  let bridge: Bridge | undefined;
  let root = "";
  let lastContext = "";
  let verifierPrompt = "";
  let reviewBusy = false;
  let entryBusy = false;
  let sessionEpoch = 0;
  let sessionUI: ExtensionContext["ui"] | undefined;
  let reviewController: AbortController | undefined;
  let reviewQueued = false;
  let reviewGeneration = 0;
  const cancelReview = () => {
    reviewGeneration++;
    reviewQueued = false;
    reviewController?.abort();
  };
  const client = (ctx: ExtensionContext): Bridge => {
    if (!bridge || root !== ctx.cwd) {
      bridge?.close();
      root = ctx.cwd;
      bridge = new Bridge(root, hostRuntime);
    }
    return bridge;
  };
  const observation = (event: any, ctx: ExtensionContext) => ({
    ...event, session_id: ctx.sessionManager.getSessionId(),
    session_file: ctx.sessionManager.getSessionFile(), actor: ctx.agent,
  });
  const active = (ctx: ExtensionContext) => existsSync(join(ctx.cwd, ".aidevflow/active-run.json")) || existsSync(join(ctx.cwd, ".aidevflow/active-discovery.json"));
  const nativeCapabilities = () => ({
    tools: pi.getActiveTools(),
    skill_commands: pi.getCommands().filter(command => command.source === "skill").map(command => command.name),
  });
  const showPreflight = (report: any) => {
    const explanation = report?.ok === false
      ? "AIDevFlow 启动检查未通过，本次命令没有启动需求讨论或开发。\n" +
        (report.readiness?.initial_commit === false
          ? "项目还没有初始 Git 提交。请先审查项目文件并完成初始提交，再重新发送 /aidevflow-discover 或 /aidevflow-flow。\n"
          : "请先根据下面的检查结果修复配置，再重新发送流程命令。\n")
      : "AIDevFlow 启动配置检查通过。\n";
    return pi.sendMessage({ customType: "aidevflow.preflight",
      content: explanation + "\n详细检查结果：\n" + JSON.stringify(report, null, 2), display: true,
    }, { triggerTurn: false });
  };
  const recover = async (ctx: ExtensionContext) => {
    const entries = ctx.sessionManager.getBranch().filter((entry: any) => {
      const message = entry.message;
      return message?.toolName === "task" || (message?.role === "assistant" &&
        Array.isArray(message.content) && message.content.some((block: any) => block.type === "toolCall" && block.name === "task"));
    });
    return await client(ctx).request("recover", { session_id: ctx.sessionManager.getSessionId(), entries });
  };

  const discussionPrompt = "Continue the existing AIDevFlow requirements discussion. Use role-context --role requirements_analyst, one native task, and pass its immutable result_path to discovery-record. Read recorded sources and feedback; do not invent decisions, approve, or implement. Present the Intent, PRD and normal/exception/permission scenarios. End the turn when review_ready so the extension can request human review. If awaiting_answers, ask only the remaining business questions and record actual answers through discovery-answer. Never tell the user to copy internal IDs or hashes.";
  const deliveryPrompt = "Human review authorized delivery of the exact approved Intent/PRD. Read status and the approved Contract Planner role-context. Continue Design, Build and Verify through Gate; preserve the approved acceptance IDs and requirements_ref. Do not recreate intake or reinterpret product scope. Respect needs_human, user interruption and repair budgets.";
  const announce = (content: string) => pi.sendMessage({ customType: "aidevflow.workflow", content, display: true }, { triggerTurn: false });
  const stopForInput = (ctx: ExtensionContext) => {
    // Native abort waits for idle, including this stop hook. Signal now, never await our own unwind.
    return Promise.resolve(ctx.abort()).catch(() => { /* Durable Gate checkpoint remains stopped. */ });
  };
  const reviewCheckpoint = async (ctx: ExtensionContext, signal?: { aborted: boolean }): Promise<any> => {
    if (reviewBusy) return { action: "wait" };
    if (!ctx.hasUI || typeof ctx.ui?.select !== "function") {
      announce("需求已保存，等待人工确认。当前界面不能显示确认选项；请在支持交互的 omp 会话中再次运行 /aidevflow-flow。不会自动批准。");
      return { action: "wait" };
    }
    reviewBusy = true;
    const controller = new AbortController();
    reviewController = controller;
    const epoch = sessionEpoch;
    const session = ctx.sessionManager.getSessionId();
    const interruption = () => epoch !== sessionEpoch || session !== ctx.sessionManager.getSessionId()
      ? { action: "superseded" } : signal?.aborted || controller.signal.aborted ? { action: "interrupted" } : undefined;
    try {
      const prepared = await client(ctx).request("review-prepare", { session_id: session, actor: ctx.agent });
      let stopped = interruption();
      if (stopped) return stopped;
      if (prepared.action !== "review") return prepared;
      const approve = prepared.discussion_only ? "确认，仅保存需求" : "确认并开始自主开发";
      const summary = `需求审查 · 版本 ${prepared.revision}\n\n${prepared.intent}\n\n${prepared.prd}\n\n验收场景：\n${JSON.stringify(prepared.scenarios, null, 2)}\n\n完整文件：${JSON.stringify(prepared.paths)}\n请确认业务范围与验收；关闭窗口将保存并暂停。`;
      announce(summary);
      // Pause is first: a headless/default response must never imply approval.
      const selected = await ctx.ui.select(`需求审查 · 版本 ${prepared.revision}\n请审阅上方材料及完整文件后选择`,
        ["保存并暂停", "提出修改意见", approve], { signal: controller.signal });
      stopped = interruption();
      if (stopped) return stopped;
      let choice = selected === approve ? (prepared.discussion_only ? "approve_save" : "approve_start") : "pause";
      let feedback = "";
      if (selected === "提出修改意见") {
        feedback = (await ctx.ui.input("请说明需要修改的业务规则、范围或场景", undefined, { signal: controller.signal }))?.trim() || "";
        choice = feedback ? "revise" : "pause";
      }
      stopped = interruption();
      if (stopped) return stopped;
      const result = await client(ctx).request("review-respond", { discovery_id: prepared.discovery_id,
        token: prepared.token, session_id: session, choice, feedback, actor: ctx.agent });
      return interruption() || result;
    } finally {
      if (reviewController === controller) reviewController = undefined;
      reviewBusy = false;
    }
  };
  const queueReview = (ctx: ExtensionContext, idle: Promise<void>) => {
    if (reviewQueued || reviewBusy) return;
    if (!sessionUI || !ctx.hasUI || typeof ctx.setTimeout !== "function") {
      announce("需求已保存，等待人工审查。请运行 /aidevflow-flow 打开确认选项；不会自动批准。");
      return;
    }
    reviewQueued = true;
    const epoch = sessionEpoch;
    const generation = reviewGeneration;
    // Use the completed lifecycle/command UI, never session_stop's abort-bound UI.
    const reviewContext = Object.create(ctx) as ExtensionContext;
    Object.defineProperty(reviewContext, "ui", { value: sessionUI });
    ctx.setTimeout(async () => {
      try {
        await idle; // The stop handler has returned; native abort can now finish settling.
        if (epoch !== sessionEpoch || generation !== reviewGeneration) return;
        if (!ctx.isIdle()) {
          announce("需求已保存；当前会话正在执行其他操作，请稍后运行 /aidevflow-flow 审查。");
          return;
        }
        const result = await reviewCheckpoint(reviewContext);
        if (epoch !== sessionEpoch || generation !== reviewGeneration || result.replayed) return;
        if (result.action === "deliver") pi.sendUserMessage(deliveryPrompt, { attribution: "agent" });
        else if (result.action === "discuss") pi.sendUserMessage(discussionPrompt, { attribution: "agent" });
        else if (result.action === "paused") announce("已保存并暂停。运行 /aidevflow-flow 可重新打开审查。");
        else if (result.action === "approved_saved") announce("需求已确认并保存。准备开发时，运行 /aidevflow-flow。");
      } catch (error) {
        if (epoch === sessionEpoch && generation === reviewGeneration)
          announce(`需求审查未能完成，已停止续跑。请运行 /aidevflow-flow 恢复。原因：${error instanceof Error ? error.message : String(error)}`);
      } finally {
        if (generation === reviewGeneration) reviewQueued = false;
      }
    }, 0);
  };
  const enterFlow = async (args: string, ctx: ExtensionContext, discussionOnly = false) => {
    if (entryBusy || reviewBusy) return;
    entryBusy = true;
    cancelReview();
    sessionUI = ctx.ui;
    const epoch = sessionEpoch;
    try {
      const preflight = await client(ctx).request("preflight", nativeCapabilities());
      if (epoch !== sessionEpoch) return;
      if (!preflight.ok) { showPreflight(preflight); return; }
      const skill = readFileSync(join(ctx.cwd, ".omp/skills/aidevflow-flow/SKILL.md"), "utf8");
      if (preflight.workflow_profile !== "reviewed-discovery-v1") {
        if (discussionOnly) { announce("独立需求讨论需要启用 reviewed-discovery-v1。"); return; }
        pi.sendUserMessage(`${skill}\n\nUser request: ${args || "Read doctor/status and resume the current task."}`);
        return;
      }
      let route = await client(ctx).request("flow-enter", { text: args || "", discussion_only: discussionOnly, actor: ctx.agent });
      if (epoch !== sessionEpoch) return;
      if (route.action === "choose_approved_action") {
        if (!ctx.hasUI) { announce("需求已批准。直接运行 /aidevflow-flow 继续开发；修改需求请使用 /aidevflow-discover 加上修改意见。"); return; }
        const choice = await ctx.ui.select("需求已批准，这段文字用于哪一步？", ["按已批准需求继续开发", "修改需求并重新审阅"]);
        if (epoch !== sessionEpoch || !choice) return;
        route = await client(ctx).request("flow-enter", { text: args || "", approved_action: choice === "按已批准需求继续开发" ? "continue" : "revise", actor: ctx.agent });
      }
      if (route.action === "review") route = await reviewCheckpoint(ctx);
      if (epoch !== sessionEpoch) return;
      if (route.replayed) { announce("这次确认已经处理；当前任务已保留。"); return; }
      if (route.action === "discuss") pi.sendUserMessage(discussionPrompt, { attribution: "agent" });
      else if (route.action === "deliver") pi.sendUserMessage(`${skill}\n\n${deliveryPrompt}\n\nUser input: ${route.user_text || ""}`, { attribution: "agent" });
      else if (route.action === "request_needed") announce("请用 /aidevflow-flow 描述想做的功能，或提供项目内的需求文档路径。");
      else if (route.action === "approved_saved") announce("需求已确认并保存。准备开发时，运行 /aidevflow-flow 即可。");
      else if (route.action === "paused") announce("已保存并暂停。稍后运行 /aidevflow-flow 从当前检查点继续。");
      else if (route.action === "delivery_active") announce("当前已有交付任务或待恢复的交接。需求讨论入口不会恢复开发；如需继续当前任务，请使用 /aidevflow-flow。");
    } finally { entryBusy = false; }
  };

  pi.registerCommand("aidevflow-flow", {
    description: "Start or resume the AIDevFlow delivery workflow",
    handler: async (args, ctx) => enterFlow(args, ctx),
  });
  pi.registerCommand("aidevflow-discover", {
    description: "Discuss Intent/PRD before human approval and autonomous delivery",
    handler: async (args, ctx) => enterFlow(args, ctx, true),
  });
  pi.registerCommand("aidevflow-approve", {
    description: "Explicitly approve an exact Intent/PRD revision (does not itself start delivery)",
    handler: async (args, ctx) => {
      const [id, revision, sha256, ...extra] = args.trim().split(/\s+/);
      if (extra.length || !id || !/^[1-9]\d*$/.test(revision || "") || !/^[a-f0-9]{64}$/.test(sha256 || "")) {
        throw new Error("Usage: /aidevflow-approve <discovery-id> <revision> <manifest-sha256>");
      }
      const result = await client(ctx).request("discovery-approve", { id, revision: Number(revision), sha256, utterance: args });
      pi.sendMessage({ customType: "aidevflow.approval", content: JSON.stringify(result), display: true }, { triggerTurn: false });
    },
  });
  pi.registerCommand("aidevflow-doctor", {
    description: "Inspect Gate setup and this OMP session's actual tools without starting a task",
    handler: async (_args, ctx) => showPreflight(await client(ctx).request("preflight", nativeCapabilities())),
  });
  pi.registerCommand("aidevflow-accept", {
    description: "Record actual business acceptance for one verified zsspec module",
    handler: async (args, ctx) => {
      const module = args.trim();
      if (!module || /\s/.test(module)) throw new Error("Usage: /aidevflow-accept <module>");
      const result = await client(ctx).request("method-accept", { module, utterance: `/aidevflow-accept ${module}`, actor: ctx.agent });
      pi.sendMessage({ customType: "aidevflow.acceptance", content: JSON.stringify(result), display: true }, { triggerTurn: false });
    },
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
    cancelReview();
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
      return { additionalContext: `AIDevFlow native receipt: ${JSON.stringify({ id: receipt.id, status: receipt.status, result_path: receipt.result_path })}. Pass the exact immutable result_path to discovery-record --draft, record-intent --draft, register-contract --draft or record-verdict --path as appropriate. Do not transcribe rendered JSON or read private observation logs. Invalid output must be corrected by a fresh role attempt after the previous execution has ended.` };
    }
  });

  pi.on("session_stop", async (_event, ctx) => {
    if (!active(ctx) || ctx.agent.kind !== "main") return;
    if (_event.signal?.aborted) return;
    const modes = ctx.sessionModes?.getState();
    if (modes?.goal.status === "paused" || modes?.loop.status === "paused") return;
    if (existsSync(join(ctx.cwd, ".aidevflow/active-discovery.json"))) {
      const epoch = sessionEpoch;
      try {
        const status = await client(ctx).request("status");
        if (epoch !== sessionEpoch) return;
        if (status.status === "review_ready") {
          queueReview(ctx, stopForInput(ctx));
        } else if (["paused", "awaiting_answers"].includes(status.status)) {
          stopForInput(ctx); // End this run while the durable checkpoint awaits human input.
        }
      } catch (error) {
        if (epoch !== sessionEpoch) return;
        stopForInput(ctx);
        announce(`需求审查未能完成，已停止续跑。请检查状态后使用 /aidevflow-flow 恢复；系统不会自动重试批准。原因：${error instanceof Error ? error.message : String(error)}`);
      }
      return;
    }
    // Native mode continuation takes precedence; never add a second loop.
    if ((modes && modes.goal.status !== "inactive") || (modes && modes.loop.status !== "inactive")) return;
    return await client(ctx).request("stop");
  });
  pi.on("session_switch", async (_event, ctx) => {
    cancelReview();
    sessionEpoch++;
    bridge?.close(); bridge = undefined; lastContext = "";
    if (active(ctx) && ctx.agent.kind === "main") await recover(ctx);
    if (ctx.agent.kind === "main") sessionUI = ctx.ui;
  });
  pi.on("session_start", async (_event, ctx) => {
    if (active(ctx) && ctx.agent.kind === "main") await recover(ctx);
    if (ctx.agent.kind === "main") sessionUI = ctx.ui;
  });
  pi.on("input", async () => { cancelReview(); });
  pi.on("session_shutdown", async () => { cancelReview(); sessionEpoch++; sessionUI = undefined; bridge?.close(); bridge = undefined; });
}
