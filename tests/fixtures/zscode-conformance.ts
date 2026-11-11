/**
 * Test-only native protocol fixture. The provider emits scripted tool calls;
 * it never connects to a model service or approves real business requirements.
 */
import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { createAssistantMessageEventStream, type AssistantMessage } from "@oh-my-pi/pi-ai";
import { appendFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export default function conformance(pi: ExtensionAPI): void {
  const record = (ctx: ExtensionContext, value: object) => appendFileSync(
    join(ctx.cwd, "probe-events.jsonl"),
    JSON.stringify({ ...value, actor: ctx.agent, session: ctx.sessionManager.getSessionId() }) + "\n",
  );
  pi.registerProvider("aidevflow-probe", {
    baseUrl: "http://127.0.0.1:9",
    apiKey: "local-script-fixture",
    api: "aidevflow-script",
    models: [{
      id: "native", name: "Local protocol fixture", reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 131072, maxTokens: 2048,
    }],
    streamSimple: (model, context, options) => {
      const stream = createAssistantMessageEventStream();
      const last = context.messages.findLast(entry => entry.role === "user" || entry.role === "toolResult");
      const user = context.messages.findLast(entry => entry.role === "user");
      const text = typeof user?.content === "string" ? user.content :
        Array.isArray(user?.content) ? user.content.map(part => "text" in part ? part.text : "").join("") : "";
      const child = text.includes("PROBE_ROLE:");
      const cancel = text.includes("PROBE_ROLE:cancel");
      const invalid = text.includes("PROBE_ROLE:invalid");
      const call = (name: string, args: object) => ({
        type: "toolCall" as const, id: "probe-" + name + "-" + context.messages.length,
        name, arguments: args,
      });
      let content: AssistantMessage["content"] = [{ type: "text", text: "Protocol fixture finished." }];
      let stopReason: AssistantMessage["stopReason"] = "stop";
      if (child) {
        if (last?.role === "user") {
          content = [call("read", { path: "fixture-input.txt" })];
          stopReason = "toolUse";
        } else if (invalid && last?.role === "toolResult" && last.toolName === "yield") {
          content = [{ type: "text", text: '{"schema":"wrong-schema","passed":true,"observations":[]}' }];
        } else {
          content = [call("yield", { data: {
            schema: invalid ? "wrong-schema" : "aidevflow/native-probe-v1", passed: true,
            observations: invalid ? [] : ["Read the physical fixture input through the native read tool."],
          } })];
          stopReason = "toolUse";
        }
      } else if (last?.role === "user" && text.includes("PROBE_GUARD")) {
        content = [call("write", { path: "forbidden.txt", content: "This write must be rejected." })];
        stopReason = "toolUse";
      } else if (last?.role === "user" && text.includes("PROBE_PARENT:")) {
        const scenario = text.includes("PROBE_PARENT:cancel") ? "cancel" :
          text.includes("PROBE_PARENT:invalid") ? "invalid" : "normal";
        content = [call("task", {
          context: "Local scripted native protocol test, no business approval.",
          tasks: [{
            agent: "aidevflow-probe-reviewer",
            task: "PROBE_ROLE:" + scenario + "\nRead fixture-input.txt and return the supplied probe schema.",
            solutionSpace: "test protocol only",
          }],
        })];
        stopReason = "toolUse";
      }
      const message: AssistantMessage = {
        role: "assistant" as const, api: model.api, provider: model.provider, model: model.id,
        usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        timestamp: Date.now(), content, stopReason,
      };
      let ended = false;
      let timer: Timer | undefined;
      const finish = (aborted: boolean) => {
        if (ended) return;
        ended = true;
        if (timer) clearTimeout(timer);
        options?.signal?.removeEventListener("abort", abort);
        if (aborted) {
          const failed: AssistantMessage = { ...message, content: [], stopReason: "aborted", errorMessage: "Scripted fixture cancelled" };
          stream.push({ type: "error", reason: "aborted", error: failed });
        } else {
          stream.push({ type: "start", partial: message });
          for (let i = 0; i < content.length; i++) {
            const item = content[i];
            if (item.type === "toolCall") {
              stream.push({ type: "toolcall_start", contentIndex: i, partial: message });
              stream.push({ type: "toolcall_end", contentIndex: i, toolCall: item, partial: message });
            }
          }
          stream.push({ type: "done", reason: stopReason, message });
        }
        stream.end();
      };
      const abort = () => finish(true);
      options?.signal?.addEventListener("abort", abort, { once: true });
      if (options?.signal?.aborted) finish(true);
      else if (cancel) timer = setTimeout(() => finish(false), 30000);
      else finish(false);
      return stream;
    },
  });
  pi.registerCommand("aidevflow-probe-state", {
    description: "Record native runtime capabilities without invoking a model",
    handler: async (_args, ctx) => {
      record(ctx, {
        type: "capabilities", tools: pi.getActiveTools(), commands: pi.getCommands(),
        session_file: ctx.sessionManager.getSessionFile(),
        branch_entries: ctx.sessionManager.getBranch().length,
        has_ui: ctx.hasUI,
      });
    },
  });
  pi.registerCommand("aidevflow-probe-review", {
    description: "Exercise a test-only review dialog",
    handler: async (_args, ctx) => {
      const choice = ctx.hasUI ? await ctx.ui.select("协议测试：选择保存并暂停", ["保存并暂停", "确认测试"]) : undefined;
      const checkpoint = { schema: "aidevflow/probe-checkpoint-v1", status: choice === "确认测试" ? "confirmed" : "paused" };
      writeFileSync(join(ctx.cwd, "probe-checkpoint.json"), JSON.stringify(checkpoint));
      record(ctx, { type: "review", choice: choice ?? null, checkpoint });
    },
  });
  pi.on("session_start", (_event, ctx) => {
    record(ctx, { type: "session_start", session_file: ctx.sessionManager.getSessionFile() });
  });
  pi.on("before_agent_start", (_event, ctx) => {
    record(ctx, { type: "before_agent_start", tools: pi.getActiveTools() });
  });
  pi.on("tool_call", (event, ctx) => {
    record(ctx, { type: "tool_call", tool: event.toolName, toolCallId: event.toolCallId, input: event.input });
    if (event.toolName === "write" && event.input.path === "forbidden.txt") {
      record(ctx, { type: "guard_blocked" });
      return { block: true, reason: "Native conformance fixture deliberately denies this write" };
    }
  });
  pi.on("tool_result", (event, ctx) => {
    record(ctx, {
      type: "tool_result", tool: event.toolName, toolCallId: event.toolCallId,
      input: event.input, isError: event.isError, details: event.details,
    });
  });
  pi.on("session_switch", (_event, ctx) => {
    record(ctx, { type: "session_switch", session_file: ctx.sessionManager.getSessionFile() });
  });
}
