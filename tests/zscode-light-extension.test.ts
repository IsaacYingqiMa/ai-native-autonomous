import { test, expect, mock } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";

const requests: Array<{ action: string; payload: any }> = [];
let state: any = { stage: "discovery", requirements: { value: { intent: { problem: "用户目标" }, prd: "完整PRD" }, sha256: "version-1" }, revision: 1 };
let choice = "保留待批准";
let selectedHook: (() => void) | undefined;
let toolFailure = false;
let stopped = false;
let failures = 0;
let baselineRoute: any;
mock.module("../integrations/zscode/client", () => ({ Bridge: class {
  constructor(_root: string, runtime: string) { expect(runtime).toBe("zscode"); }
  request(action: string, payload?: any) {
    requests.push({ action, payload });
    if (action === "operation-failure") return Promise.resolve({ stopped: ++failures >= 3 });
    if (action === "tool" && toolFailure) return Promise.reject(new Error("Invalid artifact"));
    if (action === "activity") return Promise.resolve({ stopped, stage: state.stage, reason: state.attention_reason });
    if (action === "questions") return Promise.resolve({ input: { questions: [{ id: "Q1", question: "Canonical business question", options: [] }] } });
    if (action === "verifier-assignment") return Promise.resolve({ task_call: { agent: "aidevflow-independent-verifier", task: "Authoritative source and hash", solutionSpace: "read-only", outputSchema: { type: "object" }, schemaMode: "strict" } });
    if (action === "baseline-assignment") return Promise.resolve(baselineRoute || { task_call: { agent: "aidevflow-baseline-reviewer", task: "Authoritative source and hash", solutionSpace: "read-only", outputSchema: { type: "object" }, schemaMode: "strict" } });
    if (action === "tool" && ["requirements_context", "verifier_context", "baseline_context"].includes(payload.action)) {
      if (payload.action === "baseline_context" && baselineRoute) return Promise.resolve(baselineRoute);
      return Promise.resolve({ task_call: { agent: payload.action === "requirements_context" ? "aidevflow-requirements-analyst" : payload.action === "baseline_context" ? "aidevflow-baseline-reviewer" : "aidevflow-independent-verifier", task: "Authoritative source and hash", solutionSpace: "read-only" } });
    }
    return Promise.resolve(action === "status" ? state : action === "doctor" ? { ok: true, initial_commit: true } :
      action === "guard" ? { block: true, reason: "Gate policy" } : {});
  }
  close() {}
} }));
const { default: install } = await import("../integrations/zscode/index");
const { entryIntent } = await import("../integrations/zscode/router");

function fixture() {
  requests.length = 0;
  state = { stage: "discovery", requirements: { value: { intent: { problem: "用户目标" }, prd: "完整PRD" }, sha256: "version-1" }, revision: 1 };
  choice = "保留待批准"; selectedHook = undefined;
  toolFailure = false;
  stopped = false; failures = 0;
  baselineRoute = undefined;
  const root = mkdtempSync(join(process.cwd(), ".test-tmp/light-ts-"));
  mkdirSync(join(root, ".zscode/extensions/aidevflow"), { recursive: true });
  mkdirSync(join(root, ".aidevflow"));
  writeFileSync(join(root, ".aidevflow/light-active.json"), "{}");
  writeFileSync(join(root, ".zscode/extensions/aidevflow/flow.md"), "安装版 light 操作规则");
  const hooks = new Map<string, Function>(), commands = new Map<string, any>(), tools: any[] = [], prompts: string[] = [], messages: any[] = [], timers: Function[] = [];
  const T = { Object: (x: unknown) => x, Union: (x: unknown) => x, Literal: (x: unknown) => x,
    Optional: (x: unknown) => x, Record: (x: unknown, y: unknown) => [x, y], String: () => "string", Unknown: () => "unknown" };
  install({ typebox: { Type: T }, on: (n: string, f: Function) => hooks.set(n, f), registerCommand: (n: string, v: any) => commands.set(n, v),
    registerTool: (v: any) => tools.push(v), sendMessage: (v: any) => messages.push(v), sendUserMessage: (v: string) => prompts.push(v) } as any);
  const ctx: any = { cwd: root, hasUI: true, agent: { kind: "main" }, sessionManager: { getSessionId: () => "s1", getBranch: () => [] },
    setTimeout: (fn: Function) => { timers.push(fn); return timers.length; },
    ui: { select: async () => { selectedHook?.(); return choice; }, input: async () => "请修正目标" } };
  return { root, hooks, commands, tools, prompts, messages, ctx,
    command: (name: string, text = "") => commands.get(name).handler(text, ctx),
    flushTimers: async () => { while (timers.length) await timers.shift()!(); },
    pendingTimers: () => timers.length,
    cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

test("installed extension exposes one model tool and five-skill workflow entry", () => {
  const f = fixture(); try {
    expect(f.tools.map(t => t.name)).toEqual(["aidevflow_light"]);
    expect(f.commands.has("aidevflow-flow")).toBe(true);
    expect([...f.commands.keys()].sort()).toEqual(["aidevflow-discover", "aidevflow-flow"]);
  } finally { f.cleanup(); }
});
test("declining approval never writes approval or starts execution", async () => {
  const f = fixture(); try {
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "approve")).toBe(false);
    expect(f.prompts).toHaveLength(0);
  } finally { f.cleanup(); }
});
test("actual user choice binds the exact reviewed Intent and PRD", async () => {
  const f = fixture(); try {
    choice = "确认需求并开始自主开发";
    await f.command("aidevflow-flow");
    const r = requests.find(r => r.action === "approve")!;
    expect(r.payload.source).toBe("native_user_interaction");
    expect(r.payload.data.sha256).toBe("version-1");
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});
test("session switch while review is open invalidates the approval", async () => {
  const f = fixture(); try {
    choice = "确认需求并开始自主开发";
    selectedHook = () => { f.hooks.get("session_switch")!({}, f.ctx); };
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "approve")).toBe(false);
  } finally { f.cleanup(); }
});
test("headless host cannot approve", async () => {
  const f = fixture(); try {
    f.ctx.hasUI = false; choice = "确认需求并开始自主开发";
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "approve")).toBe(false);
  } finally { f.cleanup(); }
});
test("delivery review automatically returns stale evidence to AI verification", async () => {
  const f = fixture(); try {
    state = { stage: "delivery_ready", regression_fresh: false, verdict: { value: {} } };
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "confirm")).toBe(false);
  } finally { f.cleanup(); }
});
test("mutations are policy checked and method aliases resolve to the light package", async () => {
  const f = fixture(); try {
    const result = await f.hooks.get("tool_call")!({ toolName: "bash", input: { command: "anything" } }, f.ctx);
    expect(result.block).toBe(true);
    const read = await f.hooks.get("tool_call")!({ toolName: "read", input: { path: "skill://claude/zsspec-plan" } }, f.ctx);
    expect(read.input.path).toBe(join(f.root, ".zscode/skills/zsspec-plan/SKILL.md"));
  } finally { f.cleanup(); }
});
test("exception and the two review checkpoints never trigger continuation", async () => {
  const f = fixture(); try {
    for (const stage of ["needs_attention", "paused", "delivery_ready", "discovery"]) {
      state = { stage, revision: 1 };
      expect(await f.hooks.get("session_stop")!({}, f.ctx)).toBeUndefined();
    }
  } finally { f.cleanup(); }
});
test("goal owns continuation when enabled, avoiding a second loop", async () => {
  const f = fixture(); try {
    state = { stage: "applying", revision: 1 };
    f.ctx.sessionModes = { getState: () => ({ goal: { status: "active" }, loop: { status: "inactive" } }) };
    expect(await f.hooks.get("session_stop")!({}, f.ctx)).toBeUndefined();
  } finally { f.cleanup(); }
});

test("every autonomous stage continues without an extra human review", async () => {
  const f = fixture(); try {
    for (const [revision, stage] of ["spec", "plan", "applying", "verify", "done", "finalizing"].entries()) {
      state = { stage, revision, next_action: "continue automatically" };
      expect((await f.hooks.get("session_stop")!({}, f.ctx)).continue).toBe(true);
    }
    expect(f.commands.has("aidevflow-knowledge-reviewed")).toBe(false);
    expect(f.commands.has("aidevflow-discover")).toBe(true);
    expect(f.commands.has("aidevflow-accept")).toBe(false);
  } finally { f.cleanup(); }
});

test("final acceptance approves the independently verified delivery without a commit gate", async () => {
  const f = fixture(); try {
    state = { stage: "delivery_ready", regression_fresh: true, final_candidate: "candidate-2",
      verdict: { value: { decision: "pass" } }, verifier_bundle: { sha256: "bundle" } };
    choice = "验收通过";
    await f.command("aidevflow-flow");
    const r = requests.find(r => r.action === "confirm")!;
    expect(r.payload.data.candidate).toBe("candidate-2");
    expect(r.payload.source).toBe("native_user_interaction");
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("test success without independent verification cannot open final acceptance", async () => {
  const f = fixture(); try {
    state = { stage: "verify", regression_fresh: true };
    choice = "验收通过";
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "confirm")).toBe(false);
  } finally { f.cleanup(); }
});

test("independent role context drops writer memory and confines reads", async () => {
  const f = fixture(); try {
    writeFileSync(join(f.root, ".zscode/extensions/aidevflow/verifier.md"), "sealed verifier policy");
    const ctx = { ...f.ctx, agent: { kind: "subagent", name: "aidevflow-independent-verifier" } };
    const start = await f.hooks.get("before_agent_start")!({ prompt: "Read only bundle" }, ctx);
    expect(start.systemPrompt).toEqual(["sealed verifier policy"]);
    const context = await f.hooks.get("context")!({ messages: [
      { role: "user", content: "writer history", timestamp: 1 }, { role: "user", content: "injected peer note", timestamp: 2 },
      { role: "custom", content: "memory" }, { role: "assistant", content: [] },
    ] }, ctx);
    expect(context.messages.filter((m: any) => m.role === "user")).toEqual([{ role: "user", content: "Read only bundle", timestamp: 1 }]);
    expect(context.messages.some((m: any) => m.role === "custom")).toBe(false);
    await f.hooks.get("tool_call")!({ toolName: "read", input: { path: "src/private.py" } }, ctx);
    expect(requests.at(-1)!.action).toBe("guard");
    expect(requests.at(-1)!.payload.actor.name).toBe("aidevflow-independent-verifier");
  } finally { f.cleanup(); }
});

test("control intents do not confuse feature descriptions with workflow actions", () => {
  for (const text of ["实现暂停按钮", "增加查看状态功能", "不要放弃", "修改确认按钮文案", "验收通过后增加一个通知"]) {
    expect(entryIntent(text)).toBe("feedback");
  }
  expect(entryIntent("请暂停一下。")).toBe("pause");
  expect(entryIntent("现在到哪了")).toBe("status");
  expect(entryIntent("好的")).toBe("review");
  expect(entryIntent("放弃当前任务")).toBe("abandon");
});

test("flow status checks do not change requirements or start a model turn", async () => {
  const f = fixture(); try {
    await f.command("aidevflow-flow", "查看状态");
    expect(requests.some(r => r.action === "requirement_feedback")).toBe(false);
    expect(requests.some(r => r.action === "approve")).toBe(false);
    expect(f.prompts).toHaveLength(0);
  } finally { f.cleanup(); }
});

test("affirmative text still opens trusted review instead of auto-approving", async () => {
  const f = fixture(); try {
    choice = "暂不批准";
    await f.command("aidevflow-flow", "好的");
    expect(requests.some(r => r.action === "approve")).toBe(false);
    expect(f.prompts).toHaveLength(0);
  } finally { f.cleanup(); }
});

test("flow routes pause and explicit continuation without adding business gates", async () => {
  const f = fixture(); try {
    state = { stage: "applying", revision: 3, tasks: [], cursor: 0 };
    await f.command("aidevflow-flow", "暂停");
    expect(requests.some(r => r.action === "pause")).toBe(true);
    state = { stage: "paused", revision: 4, tasks: [], cursor: 0 };
    await f.command("aidevflow-flow", "继续");
    expect(requests.some(r => r.action === "resume")).toBe(true);
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("idle flow never invents a new task from a continuation intent", async () => {
  const f = fixture(); try {
    state = { active_run: null };
    await f.command("aidevflow-flow");
    expect(f.prompts).toHaveLength(0);
    await f.command("aidevflow-flow", "修复整数加法");
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("requirements review is deferred until stop returns and uses the lifecycle UI", async () => {
  const f = fixture(); try {
    choice = "确认需求并开始自主开发";
    await f.hooks.get("session_start")!({}, f.ctx);
    requests.length = 0;
    const stopCtx = { ...f.ctx, ui: { select: () => { throw new Error("stop-bound UI must not be used"); } } };
    expect(await f.hooks.get("session_stop")!({}, stopCtx)).toBeUndefined();
    expect(f.pendingTimers()).toBe(1);
    expect(requests.some(r => r.action === "approve")).toBe(false);
    await f.flushTimers();
    expect(requests.some(r => r.action === "approve")).toBe(true);
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("new input cancels a queued review before it can approve stale material", async () => {
  const f = fixture(); try {
    choice = "确认需求并开始自主开发";
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("session_stop")!({}, f.ctx);
    await f.hooks.get("input")!({}, f.ctx);
    await f.flushTimers();
    expect(requests.some(r => r.action === "approve")).toBe(false);
  } finally { f.cleanup(); }
});

test("delivery checkpoint automatically opens the second review and binds its bundle", async () => {
  const f = fixture(); try {
    state = { stage: "delivery_ready", regression_fresh: true, run_id: "run-1", final_candidate: "candidate",
      verdict: { id: "receipt-1", value: { decision: "pass" } }, verifier_bundle: { sha256: "reviewed-bundle" } };
    choice = "验收通过";
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("session_stop")!({}, f.ctx);
    await f.flushTimers();
    const confirmation = requests.find(r => r.action === "confirm")!;
    expect(confirmation.payload.data.bundle_sha256).toBe("reviewed-bundle");
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("closing a review does not reopen it repeatedly and flow can reopen it explicitly", async () => {
  const f = fixture(); try {
    choice = "暂不批准";
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("session_stop")!({}, f.ctx);
    await f.flushTimers();
    await f.hooks.get("session_stop")!({}, f.ctx);
    expect(f.pendingTimers()).toBe(0);
    choice = "确认需求并开始自主开发";
    await f.command("aidevflow-flow");
    expect(requests.some(r => r.action === "approve")).toBe(true);
  } finally { f.cleanup(); }
});

test("pending requirement feedback cannot reopen an old review", async () => {
  const f = fixture(); try {
    state.requirements_feedback_pending = true;
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("session_stop")!({}, f.ctx);
    expect(f.pendingTimers()).toBe(0);
  } finally { f.cleanup(); }
});

test("model transport declares only explicit string fields and forwards file paths", async () => {
  const f = fixture(); try {
    expect("data" in f.tools[0].parameters).toBe(false);
    await f.tools[0].execute("flat-1", { action: "submit", kind: "spec", path: "specs/drafts/run/spec.json" }, undefined, undefined, f.ctx);
    const call = requests.find(r => r.action === "tool")!;
    expect(call.payload).toEqual({ action: "submit", kind: "spec", path: "specs/drafts/run/spec.json" });
  } finally { f.cleanup(); }
});

test("requirements tasks use authoritative native assignment and cannot override policy", async () => {
  const f = fixture(); try {
    const result = await f.hooks.get("tool_call")!({ toolName: "task", toolCallId: "r1", input: {
      tasks: [{ agent: "aidevflow-requirements-analyst", task: "invent a different request" }],
    } }, f.ctx);
    expect(result.input.tasks[0].task).toBe("Authoritative source and hash");
    expect(requests.some(r => r.action === "observe-requirements")).toBe(true);
    const rejected = await f.hooks.get("tool_call")!({ toolName: "task", input: {
      agent: "aidevflow-requirements-analyst", task: "x", tools: ["write"],
    } }, f.ctx);
    expect(rejected.block).toBe(true);
  } finally { f.cleanup(); }
});

test("Gate alone decides when artifact errors stop the current turn", async () => {
  const f = fixture(); try {
    toolFailure = true;
    let aborts = 0;
    f.ctx.abort = async () => { aborts++; };
    for (let i = 0; i < 3; i++) {
      await expect(f.tools[0].execute("bad-" + i, { action: "submit", kind: "spec", path: "bad" }, undefined, undefined, f.ctx)).rejects.toThrow("Invalid artifact");
    }
    expect(aborts).toBe(0);
    state.stage = "needs_attention";
    await expect(f.tools[0].execute("gate-stopped", { action: "submit", kind: "spec", path: "bad" }, undefined, undefined, f.ctx)).rejects.toThrow("Invalid artifact");
    expect(aborts).toBe(1);
  } finally { f.cleanup(); }
});

test("formal submission queues review and waits for native abort to settle", async () => {
  const f = fixture(); try {
    let settle!: () => void;
    f.ctx.abort = () => new Promise<void>(resolve => { settle = resolve; });
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("tool_result")!({ toolName: "aidevflow_light", isError: false,
      input: { action: "submit", kind: "requirements", path: "immutable-result.json" } }, f.ctx);
    expect(f.pendingTimers()).toBe(1);
    const flush = f.flushTimers();
    expect(f.messages.some(m => String(m.content).startsWith("Intent："))).toBe(false);
    settle();
    await flush;
    expect(f.messages.some(m => String(m.content).startsWith("Intent："))).toBe(true);
    expect(requests.some(r => r.action === "approve")).toBe(false);
  } finally { f.cleanup(); }
});

test("flow pins the original user request in the host before starting the model", async () => {
  const f = fixture(); try {
    state = { active_run: null };
    rmSync(join(f.root, ".aidevflow/light-active.json"));
    const text = "实现任务看板，删光后刷新保持为空，统计不受搜索影响";
    await f.command("aidevflow-flow", text);
    const start = requests.find(r => r.action === "start-request")!;
    expect(start.payload.text).toBe(text);
    expect(start.payload.source).toBe("native_user_interaction");
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("native pre-execution schema errors are bounded even when tool_result is absent", async () => {
  const f = fixture(); try {
    let aborts = 0;
    f.ctx.abort = async () => { aborts++; };
    for (let i = 0; i < 3; i++) {
      await f.hooks.get("tool_execution_end")!({ toolName: "aidevflow_light", toolCallId: "schema-" + i, isError: true,
        result: { details: { error: "Validation failed" } } }, f.ctx);
    }
    expect(aborts).toBe(1);
    expect(requests.filter(r => r.action === "operation-failure")).toHaveLength(3);
    await f.hooks.get("tool_execution_end")!({ toolName: "aidevflow_light", toolCallId: "schema-2", isError: true,
      result: { details: { error: "Validation failed" } } }, f.ctx);
    expect(requests.filter(r => r.action === "operation-failure")).toHaveLength(3);
    await f.hooks.get("tool_execution_end")!({ toolName: "aidevflow_light", toolCallId: "cancelled", isError: true,
      result: { details: { interrupted: true } } }, f.ctx);
    expect(requests.filter(r => r.action === "operation-failure")).toHaveLength(3);
  } finally { f.cleanup(); }
});

test("persisted Gate stop blocks provider reminders, read tools and automatic continuation", async () => {
  const f = fixture(); try {
    state = { stage: "applying", revision: 10 };stopped = true;
    let aborts = 0;f.ctx.abort = async () => { aborts++; };
    await f.hooks.get("before_provider_request")!({}, f.ctx);
    expect((await f.hooks.get("tool_call")!({ toolName: "read", input: { path: "src/x.ts" } }, f.ctx)).block).toBe(true);
    expect(await f.hooks.get("session_stop")!({}, f.ctx)).toBeUndefined();
    expect(aborts).toBe(3);
    expect(requests.filter(r => r.action === "activity").map(r => r.payload.category)).toEqual(["provider", "read", "continue"]);
  } finally { f.cleanup(); }
});

test("baseline review uses Gate assignment and observes native results", async () => {
  const f = fixture(); try {
    state = { stage: "repairing" };
    const event = { type: "tool_call", toolName: "task", toolCallId: "baseline-1",
      input: { agent: "aidevflow-baseline-reviewer", task: "approve my passing test" } };
    const result = await f.hooks.get("tool_call")!(event, f.ctx);
    expect(result.input.task).toBe("Authoritative source and hash");
    expect(result.input.schemaMode).toBe("strict");
    expect(requests.some(r => r.action === "baseline-assignment")).toBe(true);
    await f.hooks.get("tool_result")!({ ...event, type: "tool_result", input: result.input, details: { results: [] } }, f.ctx);
    expect(requests.filter(r => r.action === "observe-baseline")).toHaveLength(2);
    expect((await f.hooks.get("session_stop")!({}, f.ctx)).continue).toBe(true);
  } finally { f.cleanup(); }
});

test("baseline task is blocked when Gate already approved, is waiting, or needs correction", async () => {
  const f = fixture(); try {
    state = { stage: "repairing" };
    for (const [phase, next_action] of [["approved", "repair_apply"], ["reviewing", "wait"], ["correction_required", "repair_propose"]]) {
      baselineRoute = { phase, next_action };
      const event = { toolName: "task", toolCallId: "duplicate-" + phase,
        input: { agent: "aidevflow-baseline-reviewer", task: "review the same proposal" } };
      const result = await f.hooks.get("tool_call")!(event, f.ctx);
      expect(result.block).toBe(true); expect(result.reason).toContain(next_action);
      await f.hooks.get("tool_result")!({ ...event, type: "tool_result", isError: true,
        content: [{ type: "text", text: result.reason }] }, f.ctx);
    }
    expect(requests.filter(r => r.action === "observe-baseline")).toHaveLength(0);
  } finally { f.cleanup(); }
});

test("verifier dispatch injects the host contract for single and batch native tasks", async () => {
  const f = fixture(); try {
    state = { stage: "verify" };
    for (const batch of [false, true]) {
      const role = { agent: "aidevflow-independent-verifier", task: "model wording" };
      const result = await f.hooks.get("tool_call")!({ toolName: "task", toolCallId: "verify-" + batch,
        input: batch ? { tasks: [role] } : role }, f.ctx);
      const assigned = batch ? result.input.tasks[0] : result.input;
      expect(assigned.outputSchema).toEqual({ type: "object" });
      expect(assigned.schemaMode).toBe("strict");
      expect(assigned.task).toBe("Authoritative source and hash");
      const observed = requests.filter(r => r.action === "observe-verifier").at(-1)!;
      expect(observed.payload.input).toEqual(result.input);
    }
    expect(requests.filter(r => r.action === "verifier-assignment")).toHaveLength(2);
    expect(requests.some(r => r.action === "tool" && r.payload?.action === "verifier_context")).toBe(false);
    for (const override of [{ outputSchema: { type: "object" } }, { schemaMode: "permissive" }]) {
      const rejected = await f.hooks.get("tool_call")!({ toolName: "task", input: {
        agent: "aidevflow-independent-verifier", task: "x", ...override,
      } }, f.ctx);
      expect(rejected.block).toBe(true);
    }
  } finally { f.cleanup(); }
});

test("native business asks use canonical questions and persist real tool results", async () => {
  const f = fixture(); try {
    const event = { type: "tool_call", toolName: "ask", toolCallId: "business-1",
      input: { questions: [{ id: "Q1", question: "invented question", options: [] }] } };
    const result = await f.hooks.get("tool_call")!(event, f.ctx);
    expect(result.input.questions[0].question).toBe("Canonical business question");
    await f.hooks.get("tool_result")!({ ...event, type: "tool_result", input: result.input, content: [{ type: "text", text: "Actual answer" }] }, f.ctx);
    expect(requests.filter(r => r.action === "questions")).toHaveLength(2);
    await f.hooks.get("input")!({ source: "interactive", text: "Use local storage" }, f.ctx);
    expect(requests.find(r => r.action === "requirement_feedback")!.payload.data.text).toBe("Use local storage");
    const count = requests.length;
    await f.hooks.get("input")!({ source: "extension", text: "Auto continuation" }, f.ctx);
    expect(requests.length).toBe(count);
  } finally { f.cleanup(); }
});

test("stop notice always includes a reason and is emitted once", async () => {
  const f = fixture(); try {
    state = { stage: "needs_attention", attention_reason: "达到 providers=24/24，停止自动续跑。" };stopped = true;
    await f.hooks.get("before_provider_request")!({}, f.ctx);
    await f.hooks.get("before_provider_request")!({}, f.ctx);
    const notices = f.messages.filter(m => String(m.content).includes("停止自动推进"));
    expect(notices).toHaveLength(1);
    expect(notices[0].content).not.toContain("undefined");
    expect(notices[0].content).not.toContain("。。");
    expect(notices[0].content).toContain("providers=24/24");
  } finally { f.cleanup(); }
});

test("continue previous task resumes a stopped checkpoint through the host", async () => {
  const f = fixture(); try {
    state = { stage: "needs_attention", tasks: [], cursor: 0 };
    expect(entryIntent("继续之前的任务")).toBe("continue");
    expect(entryIntent("增加一个继续之前的任务按钮")).toBe("feedback");
    await f.command("aidevflow-flow", "继续之前的任务");
    expect(requests.some(r => r.action === "resume")).toBe(true);
    expect(f.prompts[0]).toContain("recovery");
  } finally { f.cleanup(); }
});

test("business conflict resumes only after an explicit original-scope choice", async () => {
  const f = fixture(); try {
    state = { stage: "needs_attention", attention_kind: "business_decision", attention_reason: "Data contradicts PRD", tasks: [], cursor: 0 };
    choice = "保持停止";
    await f.command("aidevflow-flow", "继续");
    expect(requests.some(r => r.action === "resume")).toBe(false);
    expect(f.prompts).toHaveLength(0);
    choice = "按原批准需求恢复";
    await f.command("aidevflow-flow", "继续");
    expect(requests.filter(r => r.action === "resume")).toHaveLength(1);
    expect(f.prompts).toHaveLength(1);
  } finally { f.cleanup(); }
});

test("real business feedback crosses the native boundary without granting approval", async () => {
  const f = fixture(); try {
    state = { stage: "needs_attention", attention_kind: "business_decision" };
    await f.hooks.get("input")!({ source: "interactive", text: "请重新讨论结束时间的口径" }, f.ctx);
    const feedback = requests.find(r => r.action === "requirement_feedback")!;
    expect(feedback.payload.source).toBe("native_user_interaction");
    expect(feedback.payload.data.text).toBe("请重新讨论结束时间的口径");
    expect(requests.some(r => ["approve", "confirm"].includes(r.action))).toBe(false);
  } finally { f.cleanup(); }
});
