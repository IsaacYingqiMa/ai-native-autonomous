import { test, expect, mock } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";

const requests: Array<{ action: string; payload: any }> = [];
let bridgeError: Error | undefined;
let preflightOk = true;
mock.module("../integrations/omp/client", () => ({ Bridge: class {
  request(action: string, payload: any) { requests.push({ action, payload });
    if (bridgeError) return Promise.reject(bridgeError);
    return Promise.resolve(action === "method-load" ? { input: { ...payload.input, path: "C:/pinned/zsspec-req/SKILL.md" },
      additionalContext: "Gate owns status; review serially", source: { sha256: "bound-hash" } } :
      action === "preflight" ? { ok: preflightOk } : action === "guard" ? { block: false } : action === "role-context" ?
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
  install({ on: (name: string, fn: Function) => hooks.set(name, fn),
    getActiveTools: () => ["read", "bash", "task"],
    getCommands: () => [{ name: "skill:zsspec-req", source: "skill" }],
    sendMessage: (message: any) => messages.push(message), sendUserMessage: (prompt: string) => prompts.push(prompt),
    registerCommand: (name: string, value: unknown) => commands.set(name, value) } as any);
  const ctx = { cwd: dir, agent: { kind: "main", id: "Main", name: "main" },
    sessionManager: { getSessionId: () => "s1", getSessionFile: () => undefined, getBranch: () => [] } };
  return { hooks, commands, ctx, messages, prompts, cleanup: () => { bridgeError = undefined; preflightOk = true; rmSync(dir, { recursive: true, force: true }); } };
}

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
