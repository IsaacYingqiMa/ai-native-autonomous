/** Local scripted model for testing the installed product entry, not AI quality. */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { createAssistantMessageEventStream, type AssistantMessage } from "@oh-my-pi/pi-ai";
import { spawnSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { join } from "node:path";

export default function workflowFixture(pi: ExtensionAPI): void {
  let actor = "main";
  let cwd = process.cwd();
  pi.on("before_agent_start", (_event, ctx) => { actor = ctx.agent.name; cwd = ctx.cwd; });
  pi.registerProvider("aidevflow-probe", {
    baseUrl: "http://127.0.0.1:9", apiKey: "scripted-local-key", api: "aidevflow-flow-fixture",
    models: [{ id: "native", name: "Scripted workflow fixture", reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 262144, maxTokens: 8192 }],
    streamSimple: (model, context) => {
      const stream = createAssistantMessageEventStream();
      // Provider registrations are shared by native child sessions. Derive the
      // test role from this request, not a closure last rebound by another child.
      const first = context.messages.find(message => message.role === "user");
      const assignment = first && Array.isArray(first.content) ?
        first.content.map(part => "text" in part ? part.text : "").join("") : String(first?.content ?? "");
      actor = context.tools?.some(tool => tool.name === "task") ? "main" :
        assignment.includes("schemas/requirements.schema.json") ? "aidevflow-requirements-analyst" :
        assignment.includes("artifact-review-v1") ? "aidevflow-artifact-reviewer" :
        assignment.includes("schemas/contract.schema.json") ? "aidevflow-contract-planner" :
        "aidevflow-independent-verifier";
      const planner = spawnSync(process.env.AIDEVFLOW_FIXTURE_PYTHON!, ["-B", join(cwd, "native_workflow_plan.py"), actor],
        { cwd, encoding: "utf8", windowsHide: true, timeout: 20000 });
      if (planner.status !== 0) throw new Error("Read-only fixture planner failed: " + planner.stderr);
      const next = JSON.parse(planner.stdout);
      appendFileSync(join(cwd, ".aidevflow/maintenance/native-flow-trace.jsonl"), JSON.stringify({ actor, next }) + "\n");
      const tool = next.result ? "yield" : next.tool;
      const args = next.result ? { data: next.result } : next.args;
      const message: AssistantMessage = {
        role: "assistant", api: model.api, provider: model.provider, model: model.id,
        content: tool ? [{ type: "toolCall", id: "flow-" + context.messages.length, name: tool, arguments: args }] :
          [{ type: "text", text: next.text }],
        stopReason: tool ? "toolUse" : "stop", timestamp: Date.now(),
        usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      };
      stream.push({ type: "start", partial: message });
      const call = message.content[0];
      if (call.type === "toolCall") {
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: message });
        stream.push({ type: "toolcall_delta", contentIndex: 0, delta: JSON.stringify(args), partial: message });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall: call, partial: message });
      }
      stream.push({ type: "done", reason: message.stopReason, message });
      stream.end();
      return stream;
    },
  });
}
