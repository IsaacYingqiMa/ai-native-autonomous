import { test, expect, mock } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, unlinkSync } from "node:fs";
import { join } from "node:path";

const requests: Array<{ action: string; payload: any }> = [];
let bridgeError: Error | undefined;
let bridgeErrorAction: string | undefined;
let preflightOk = true;
let preflightInitialCommit = true;
let workflowProfile = "legacy";
let workflowRoute: any = { action: "discuss" };
let gateStatus: any = {};
let reviewResponse: any = { action: "deliver", task_id: "ADF-test", replayed: false };
let reviewDiscussionOnly = false;
let onReviewPrepare: (() => void) | undefined;
let onReviewRespond: (() => void) | undefined;
mock.module("../integrations/omp/client", () => ({ Bridge: class {
  request(action: string, payload: any) { requests.push({ action, payload });
    if (bridgeError && (!bridgeErrorAction || bridgeErrorAction === action)) return Promise.reject(bridgeError);
    if (action === "review-prepare") {
      onReviewPrepare?.();
      return Promise.resolve({ action: "review", token: "token", discovery_id: "DISC-test", revision: 1,
        intent: "Intent approved for review", prd: "PRD", scenarios: [], paths: {}, discussion_only: reviewDiscussionOnly });
    }
    if (action === "flow-enter") return Promise.resolve(workflowRoute);
    if (action === "status") return Promise.resolve(gateStatus);
    if (action === "review-respond") { onReviewRespond?.(); return Promise.resolve(reviewResponse); }
    return Promise.resolve(action === "method-load" ? { input: { ...payload.input, path: "C:/pinned/zsspec-req/SKILL.md" },
      additionalContext: "Gate owns status; review serially", source: { sha256: "bound-hash" } } :
      action === "preflight" ? { ok: preflightOk, workflow_profile: workflowProfile, readiness: { initial_commit: preflightInitialCommit } } : action === "guard" ? { block: false } : action === "role-context" ?
    { task_call: { context: "Gate context", tasks: [{ agent: "aidevflow-independent-verifier", task: "Read only bundle-1", solutionSpace: "bounded" }] } } : {}); }
  close() {}
} }));
const { default: install } = await import("../integrations/omp/index");

function fixture() {
  const dir = mkdtempSync(join(process.cwd(), ".test-tmp/omp-ts-"));
  mkdirSync(join(dir, ".aidevflow"));
  writeFileSync(join(dir, ".aidevflow/active-run.json"), "{}");
  const hooks = new Map<string, Function>();
  const commands = new Map<string, unknown>();
  const messages: any[] = [];
  const prompts: string[] = [];
  const timers: Function[] = [];
  let choice: string | undefined = "确认并开始自主开发";
  let feedback = "请增加异常处理";
  let aborted = false;
  let abortCount = 0;
  install({ on: (name: string, fn: Function) => hooks.set(name, fn),
    getActiveTools: () => ["read", "bash", "task"],
    getCommands: () => [{ name: "skill:zsspec-req", source: "skill" }],
    sendMessage: (message: any) => messages.push(message), sendUserMessage: (prompt: string) => prompts.push(prompt),
    registerCommand: (name: string, value: unknown) => commands.set(name, value) } as any);
  const ctx = { cwd: dir, hasUI: true, ui: { select: async () => choice, input: async () => feedback },
    isIdle: () => true, setTimeout: (fn: Function) => { timers.push(fn); return timers.length; },
    abort: async () => { aborted = true; abortCount++; }, agent: { kind: "main", id: "Main", name: "main" },
    sessionManager: { getSessionId: () => "s1", getSessionFile: () => undefined, getBranch: () => [] } };
  return { hooks, commands, ctx, messages, prompts, setChoice: (value: string | undefined) => { choice = value; },
    flushTimers: async () => { while (timers.length) await timers.shift()!(); },
    aborted: () => aborted, abortCount: () => abortCount, cleanup: () => { bridgeError = undefined; bridgeErrorAction = undefined; preflightOk = true; preflightInitialCommit = true; workflowProfile = "legacy";
      workflowRoute = { action: "discuss" }; gateStatus = {}; reviewResponse = { action: "deliver", task_id: "ADF-test", replayed: false };
      onReviewPrepare = undefined; onReviewRespond = undefined; reviewDiscussionOnly = false; rmSync(dir, { recursive: true, force: true }); } };
}

async function finishStop(f: ReturnType<typeof fixture>, event: any = {}, ctx: any = f.ctx) {
  const previous = requests.length;
  await f.hooks.get("session_start")!({}, ctx);
  requests.splice(previous); // Session recovery is outside the stop behavior under test.
  const result = await f.hooks.get("session_stop")!(event, ctx);
  await f.flushTimers();
  return result;
}

function reviewedFixture() {
  const f = fixture();
  workflowProfile = "reviewed-discovery-v1";
  unlinkSync(join(f.ctx.cwd, ".aidevflow/active-run.json"));
  writeFileSync(join(f.ctx.cwd, ".aidevflow/active-discovery.json"), "{}");
  mkdirSync(join(f.ctx.cwd, ".omp/skills/aidevflow-flow"), { recursive: true });
  writeFileSync(join(f.ctx.cwd, ".omp/skills/aidevflow-flow/SKILL.md"), "Gate delivery playbook");
  return f;
}

test("single entry routes natural-language requirements without asking for commands", async () => {
  const f = reviewedFixture(); requests.length = 0;
  try {
    await (f.commands.get("aidevflow-flow") as any).handler("采购审批系统", f.ctx);
    expect(requests.find(r => r.action === "flow-enter")?.payload.text).toBe("采购审批系统");
    expect(f.prompts[0]).toContain("requirements_analyst");
    expect(f.prompts[0]).not.toContain("/aidevflow-approve <");
  } finally { f.cleanup(); }
});

test("approved scope routing preserves input and uses an actual native user choice", async () => {
  const f = reviewedFixture(); requests.length = 0;
  workflowRoute = { action: "choose_approved_action" };
  f.setChoice("按已批准需求继续开发");
  try {
    await (f.commands.get("aidevflow-flow") as any).handler("按我的需求完成开发", f.ctx);
    const entries = requests.filter(r => r.action === "flow-enter");
    expect(entries.length).toBe(2);
    expect(entries[1].payload.approved_action).toBe("continue");
    expect(entries[1].payload.text).toBe("按我的需求完成开发");
  } finally { f.cleanup(); }
});

test("closing the approved scope choice preserves approval without resuming", async () => {
  const f = reviewedFixture(); requests.length = 0;
  workflowRoute = { action: "choose_approved_action" }; f.setChoice(undefined);
  try {
    await (f.commands.get("aidevflow-flow") as any).handler("new text", f.ctx);
    expect(requests.filter(r => r.action === "flow-enter").length).toBe(1);
    expect(f.prompts.length).toBe(0);
  } finally { f.cleanup(); }
});

test("only explicit business acceptance command invokes method close", async () => {
  const f = fixture(); requests.length = 0;
  try {
    await (f.commands.get("aidevflow-accept") as any).handler("feature", f.ctx);
    expect(requests.find(r => r.action === "method-accept")?.payload.utterance).toBe("/aidevflow-accept feature");
    expect(f.prompts.length).toBe(0);
  } finally { f.cleanup(); }
});

test("review checkpoint accepts a human choice and continues the same workflow", async () => {
  const f = reviewedFixture(); requests.length = 0; gateStatus = { status: "review_ready" };
  try {
    const result = await finishStop(f, {}, f.ctx);
    expect(result).toBeUndefined();
    expect(f.prompts[0]).toContain("approved");
    expect(requests.find(r => r.action === "review-respond")?.payload.choice).toBe("approve_start");
    expect(f.prompts).toHaveLength(1); // Only the actual human choice wakes a model turn.
  } finally { f.cleanup(); }
});

test("closing review pauses without approving or resuming", async () => {
  const f = reviewedFixture(); f.setChoice(undefined); gateStatus = { status: "review_ready" };
  reviewResponse = { action: "paused" }; requests.length = 0;
  try {
    const result = await finishStop(f, {}, f.ctx);
    expect(result).toBeUndefined();
    expect(requests.find(r => r.action === "review-respond")?.payload.choice).toBe("pause");
    expect(f.aborted()).toBe(true);
  } finally { f.cleanup(); }
});

test("headless review cannot approve or start another model turn", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  try {
    await finishStop(f, {}, { ...f.ctx, hasUI: false });
    expect(requests.some(r => r.action === "review-respond")).toBe(false);
    expect(f.prompts).toEqual([]);
    expect(f.aborted()).toBe(true);
  } finally { f.cleanup(); }
});

test("saving an approved discussion stops instead of letting native autonomy start delivery", async () => {
  const f = reviewedFixture(); f.setChoice("确认，仅保存需求"); gateStatus = { status: "review_ready" };
  reviewDiscussionOnly = true; reviewResponse = { action: "approved_saved" }; requests.length = 0;
  try {
    const result = await finishStop(f, {}, f.ctx);
    expect(requests.find(r => r.action === "review-respond")?.payload.choice).toBe("approve_save");
    expect(result).toBeUndefined();
    expect(f.prompts).toEqual([]);
    expect(f.aborted()).toBe(true);
  } finally { f.cleanup(); }
});

test("feedback is persisted then continues requirements rather than implementation", async () => {
  const f = reviewedFixture(); f.setChoice("提出修改意见"); gateStatus = { status: "review_ready" };
  reviewResponse = { action: "discuss" }; requests.length = 0;
  try {
    const result = await finishStop(f, {}, f.ctx);
    const response = requests.find(r => r.action === "review-respond")!;
    expect(response.payload.feedback).toContain("异常处理");
    expect(response.payload.choice).toBe("revise");
    expect(result).toBeUndefined();
    expect(f.prompts[0]).toContain("requirements_analyst");
  } finally { f.cleanup(); }
});

test("session switch while review is open cannot apply the old choice", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  onReviewPrepare = () => { void f.hooks.get("session_shutdown")!({}, f.ctx); };
  try {
    await finishStop(f, {}, f.ctx);
    expect(requests.some(r => r.action === "review-respond")).toBe(false);
    expect(f.aborted()).toBe(true);
  } finally { f.cleanup(); }
});

test("pause does not deadlock by awaiting the native abort idle barrier", async () => {
  const f = reviewedFixture(); gateStatus = { status: "paused" };
  try {
    await finishStop(f, {}, { ...f.ctx, abort: () => new Promise<void>(() => {}) });
  } finally { f.cleanup(); }
});

test("a paused native Goal cannot reopen discovery review from the stop hook", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  try {
    await finishStop(f, {}, { ...f.ctx, sessionModes: {
      getState: () => ({ goal: { status: "paused" }, loop: { status: "inactive" } }),
    } });
    expect(requests).toEqual([]);
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("replayed review outcome never queues a duplicate continuation", async () => {
  const f = reviewedFixture(); workflowRoute = { action: "review" };
  reviewResponse = { action: "deliver", replayed: true };
  try {
    await (f.commands.get("aidevflow-flow") as any).handler("", f.ctx);
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("discussion-only entry does not queue implementation for an existing delivery", async () => {
  const f = reviewedFixture(); workflowRoute = { action: "delivery_active", task_id: "ADF-current" };
  try {
    await (f.commands.get("aidevflow-discover") as any).handler("", f.ctx);
    expect(f.prompts).toEqual([]);
    expect(f.messages.at(-1).content).toContain("已有交付任务");
  } finally { f.cleanup(); }
});

for (const action of ["status", "review-prepare", "review-respond"]) {
  test(`a ${action} failure stops native continuation and leaves a visible recovery message`, async () => {
    const f = reviewedFixture(); gateStatus = { status: "review_ready" };
    bridgeError = new Error("Review transport interrupted"); bridgeErrorAction = action;
    try {
      const result = await finishStop(f, {}, f.ctx);
      expect(result).toBeUndefined();
      expect(f.aborted()).toBe(true);
      expect(f.prompts).toEqual([]);
      expect(f.messages.at(-1).content).toContain("已停止续跑");
    } finally { f.cleanup(); }
  });
}

test("a failed review dialog stops native continuation", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" };
  try {
    await finishStop(f, {}, { ...f.ctx, ui: { ...f.ctx.ui,
      select: async () => { throw new Error("UI disconnected"); } } });
    expect(f.aborted()).toBe(true);
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("switching sessions after selection cannot open a feedback dialog in the new session", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; let asked = false;
  try {
    await finishStop(f, {}, { ...f.ctx, ui: {
      select: async () => { await f.hooks.get("session_shutdown")!({}, f.ctx); return "提出修改意见"; },
      input: async () => { asked = true; return "Old session feedback"; },
    } });
    expect(asked).toBe(false);
    expect(f.abortCount()).toBe(1); // Only the original run was stopped, before opening review.
  } finally { f.cleanup(); }
});

test("new user input while review is open cannot apply the previous approval", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  const signal = { aborted: false };
  try {
    const result = await finishStop(f, { signal }, { ...f.ctx, ui: { ...f.ctx.ui,
      select: async () => { await f.hooks.get("input")!({}, f.ctx); return "确认并开始自主开发"; } } });
    expect(result).toBeUndefined();
    expect(requests.some(r => r.action === "review-respond")).toBe(false);
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("switching sessions while approval is being saved cannot continue the new session", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" };
  onReviewRespond = () => { void f.hooks.get("session_shutdown")!({}, f.ctx); };
  try {
    expect(await finishStop(f, {}, f.ctx)).toBeUndefined();
    expect(f.prompts).toEqual([]);
    expect(f.abortCount()).toBe(1);
  } finally { f.cleanup(); }
});

test("stop returns before human review and never uses its abort-bound dialog", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  let answer: ((value: string) => void) | undefined;
  let title = "";
  const ui = { ...f.ctx.ui, select: async (value: string) => {
    title = value;
    return await new Promise<string>(resolve => { answer = resolve; });
  } };
  try {
    await f.hooks.get("session_start")!({}, { ...f.ctx, ui });
    const result = await f.hooks.get("session_stop")!({}, { ...f.ctx, ui: {
      ...f.ctx.ui, select: async () => { throw new Error("Stop-bound UI must never be used"); },
    } });
    expect(result).toBeUndefined();
    expect(title).toBe(""); // No human wait took place inside the timed hook.
    const pending = f.flushTimers();
    for (let i = 0; i < 10 && !answer; i++) await Promise.resolve();
    expect(title.length).toBeLessThan(160);
    expect(f.messages.some(m => m.content.includes("验收场景"))).toBe(true);
    expect(requests.some(r => r.action === "review-respond")).toBe(false);
    expect(answer).toBeDefined();
    answer!("保存并暂停");
    reviewResponse = { action: "paused" };
    await pending;
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("new input before deferred review prevents an obsolete dialog from opening", async () => {
  const f = reviewedFixture(); gateStatus = { status: "review_ready" }; requests.length = 0;
  try {
    await f.hooks.get("session_start")!({}, f.ctx);
    await f.hooks.get("session_stop")!({}, f.ctx);
    await f.hooks.get("input")!({}, f.ctx);
    await f.flushTimers();
    expect(requests.some(r => r.action === "review-prepare")).toBe(false);
    expect(f.prompts).toEqual([]);
  } finally { f.cleanup(); }
});

test("method reads bind physical sources and attach managed constraints on every load", async () => {
  const f = fixture(); requests.length = 0;
  try {
    for (const path of ["skill://zsspec-req", ".omp/skills/zsspec-req/SKILL.md"]) {
      const result = await f.hooks.get("tool_call")!({ toolName: "read", input: { path } }, f.ctx);
      expect(result.input.path).toBe("C:/pinned/zsspec-req/SKILL.md");
      expect(result.additionalContext).toContain("bound-hash");
    }
    expect(requests.every(request => request.action === "method-load")).toBe(true);
  } finally { f.cleanup(); }
});

test("phase and transport errors produce an actionable block", async () => {
  const f = fixture(); bridgeError = new Error("Planner requires finalized Intent");
  try {
    const result = await f.hooks.get("tool_call")!({ type: "tool_call", toolName: "task", toolCallId: "x", input: {
      tasks: [{ agent: "aidevflow-contract-planner", task: "plan" }] } }, f.ctx);
    expect(result.block).toBe(true);
    expect(result.reason).toContain("Planner requires finalized Intent");
    expect(result.reason).toContain("role-context");
  } finally { f.cleanup(); }
});

test("native preflight reports tools and prevents starting a misconfigured flow", async () => {
  const f = fixture(); requests.length = 0; preflightOk = false;
  try {
    await (f.commands.get("aidevflow-flow") as any).handler("build", f.ctx);
    expect(f.prompts.length).toBe(0);
    expect(f.messages[0].customType).toBe("aidevflow.preflight");
    expect(requests[0].payload.tools).toEqual(["read", "bash", "task"]);
    expect(requests[0].payload.skill_commands).toEqual(["skill:zsspec-req"]);
  } finally { f.cleanup(); }
});

test("ordinary read has no bridge requests", async () => {
  const f = fixture(); requests.length = 0;
  try { await f.hooks.get("tool_call")!({ toolName: "read", input: { path: "src/a.py" } }, f.ctx);
    expect(requests.length).toBe(0);
  } finally { f.cleanup(); }
});

test("discovery without an initial commit explains why it did not start", async () => {
  const f = reviewedFixture(); requests.length = 0; preflightOk = false; preflightInitialCommit = false;
  try {
    await (f.commands.get("aidevflow-discover") as any).handler("企业雷达需求头脑风暴", f.ctx);
    expect(f.messages[0].content).toContain("项目还没有初始 Git 提交");
    expect(f.messages[0].content).toContain("没有启动需求讨论或开发");
    expect(f.prompts).toEqual([]);
    expect(requests.some(r => r.action === "flow-enter")).toBe(false);
  } finally { f.cleanup(); }
});

test("human requirements approval binds revision without starting a model turn", async () => {
  const f = fixture(); requests.length = 0;
  try {
    const sha = "a".repeat(64);
    await (f.commands.get("aidevflow-approve") as any).handler(`DISC-example 2 ${sha}`, f.ctx);
    expect(requests[0]).toEqual({ action: "discovery-approve", payload: {
      id: "DISC-example", revision: 2, sha256: sha, utterance: `DISC-example 2 ${sha}` } });
    expect(f.prompts).toEqual([]);
    expect(f.messages[0].customType).toBe("aidevflow.approval");
  } finally { f.cleanup(); }
});

test("incomplete human approval never reaches the bridge", async () => {
  const f = fixture(); requests.length = 0;
  try {
    await expect((f.commands.get("aidevflow-approve") as any).handler("yes continue", f.ctx)).rejects.toThrow("Usage:");
    expect(requests).toEqual([]);
  } finally { f.cleanup(); }
});

test("discovery alone activates requirements task observation", async () => {
  const f = fixture(); requests.length = 0;
  try {
    unlinkSync(join(f.ctx.cwd, ".aidevflow/active-run.json"));
    writeFileSync(join(f.ctx.cwd, ".aidevflow/active-discovery.json"), "{}");
    await f.hooks.get("tool_call")!({ toolName: "task", toolCallId: "req-1", input: {
      tasks: [{ agent: "aidevflow-requirements-analyst", task: "prepare requirements" }] } }, f.ctx);
    expect(requests[0].action).toBe("observe");
    expect(requests[0].payload.session_id).toBe("s1");
  } finally { f.cleanup(); }
});

test("the native one-item batch shape is observed unchanged", async () => {
  const f = fixture(); requests.length = 0;
  const input = { context: "fixed context", tasks: [{ agent: "aidevflow-intent-analyst", task: "analyze", solutionSpace: "bounded" }] };
  try { await f.hooks.get("tool_call")!({ type: "tool_call", toolCallId: "c1", toolName: "task", input }, f.ctx);
    expect(requests[0].action).toBe("observe");
    expect(requests[0].payload.input).toEqual(input);
    expect(requests[0].payload.session_id).toBe("s1");
  } finally { f.cleanup(); }
});

test("critical multi-item batch fails before native dispatch", async () => {
  const f = fixture(); requests.length = 0;
  try { const result = await f.hooks.get("tool_call")!({ toolName: "task", input: { tasks: [
    { agent: "aidevflow-intent-analyst" }, { agent: "aidevflow-contract-planner" }] } }, f.ctx);
    expect(result.block).toBe(true); expect(requests.length).toBe(0);
  } finally { f.cleanup(); }
});

test("task results use native details without interpreting content", async () => {
  const f = fixture(); requests.length = 0;
  const details = { results: [{ id: "a1", exitCode: 0, structuredOutput: { status: "valid", data: { value: 1 } } }] };
  try { await f.hooks.get("tool_result")!({ type: "tool_result", toolName: "task", input: { agent: "aidevflow-intent-analyst" }, details }, f.ctx);
    expect(requests[0].payload.details).toEqual(details);
  } finally { f.cleanup(); }
});

test("critical role reads are policy checked", async () => {
  const f = fixture(); requests.length = 0;
  try { await f.hooks.get("tool_call")!({ toolName: "read", input: { path: "outside" } },
    { ...f.ctx, agent: { kind: "sub", name: "aidevflow-independent-verifier" } });
    expect(requests[0].action).toBe("guard");
  } finally { f.cleanup(); }
});

test("native Goal suppresses a second stop-hook continuation", async () => {
  const f = fixture(); requests.length = 0;
  try { await f.hooks.get("session_stop")!({}, { ...f.ctx, sessionModes: {
    getState: () => ({ goal: { status: "active" }, loop: { status: "inactive" } }),
  } }); expect(requests.length).toBe(0);
  } finally { f.cleanup(); }
});

test("paused Goal and operator interruption are never restarted by Stop", async () => {
  const f = fixture(); requests.length = 0;
  try {
    await f.hooks.get("session_stop")!({}, { ...f.ctx, sessionModes: {
      getState: () => ({ goal: { status: "paused" }, loop: { status: "inactive" } }),
    } });
    await f.hooks.get("session_stop")!({ signal: { aborted: true } }, f.ctx);
    expect(requests.length).toBe(0);
  } finally { f.cleanup(); }
});

test("Verifier assignment is replaced with Gate prepared bundle context", async () => {
  const f = fixture(); requests.length = 0;
  try { const result = await f.hooks.get("tool_call")!({ type: "tool_call", toolCallId: "c2", toolName: "task", input: {
    context: "writer self-review", tasks: [{ agent: "aidevflow-independent-verifier", task: "believe the writer" }] } }, f.ctx);
    expect(result.input.context).toBe("Gate context");
    expect(result.input.tasks[0].task).toBe("Read only bundle-1");
    expect(result.input.tasks[0].schemaMode).toBeUndefined();
    expect(requests[1].payload.input).toEqual(result.input);
  } finally { f.cleanup(); }
});

test("Verifier has a sealed base prompt and drops injected memory and peer notes", async () => {
  const f = fixture();
  mkdirSync(join(f.ctx.cwd, ".omp/agents"), { recursive: true });
  writeFileSync(join(f.ctx.cwd, ".omp/agents/aidevflow-independent-verifier.md"), "---\nname: verifier\n---\nOnly assess the frozen bundle.");
  const ctx = { ...f.ctx, agent: { kind: "sub", name: "aidevflow-independent-verifier" } };
  try { const prepared = await f.hooks.get("before_agent_start")!({ prompt: "Gate bundle assignment" }, ctx);
    expect(prepared.systemPrompt).toEqual(["Only assess the frozen bundle."]);
    const result = f.hooks.get("context")!({ messages: [
      { role: "custom", content: "writer memory" }, { role: "developer", content: "advisor note" },
      { role: "user", content: "injected user note", timestamp: 1 },
      { role: "user", content: "other history", timestamp: 2 },
      { role: "assistant", content: [], timestamp: 3 },
      { role: "toolResult", content: [], timestamp: 4 },
    ] }, ctx);
    expect(result.messages.length).toBe(3);
    expect(result.messages[0].content).toBe("Gate bundle assignment");
  } finally { f.cleanup(); }
});
