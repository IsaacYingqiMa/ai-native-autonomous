import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { Bridge } from "./client";
import { entryIntent } from "./router";

/** Installed extension only: no changes to the zscode binary or global config. */
export default function light(pi: ExtensionAPI): void {
  let bridge: Bridge | undefined;
  let root = "";
  let generation = 0;
  let busy = false;
  let verifierPrompt = "";
  let sessionUI: ExtensionContext["ui"] | undefined;
  let reviewBusy = false;
  let reviewQueued = false;
  let reviewController: AbortController | undefined;
  let lastReviewKey = "";
  const recordedFailures = new Set<string>();
  const blockedReviewCalls = new Map<string, string>();
  let lastStopReason = "";
  const managedRoles = new Set(["aidevflow-independent-verifier", "aidevflow-requirements-analyst", "aidevflow-baseline-reviewer"]);
  const stop = (ctx: ExtensionContext) => { void Promise.resolve(ctx.abort?.()).catch(() => {}); };
  const cancelReview = () => { generation++; reviewController?.abort(); reviewQueued = false; };
  const client = (ctx: ExtensionContext): Bridge => {
    if (!bridge || root !== ctx.cwd) {
      bridge?.close(); root = ctx.cwd; bridge = new Bridge(root, "zscode");
    }
    return bridge;
  };
  const active = (ctx: ExtensionContext) => existsSync(join(ctx.cwd, ".aidevflow/light-active.json"));
  const announce = (content: string) => pi.sendMessage({ customType: "aidevflow.light", content, display: true }, { triggerTurn: false });
  const user = (ctx: ExtensionContext) => `zscode user:${ctx.sessionManager.getSessionId()}`;
  const status = (ctx: ExtensionContext) => client(ctx).request("status");
  const activity = async (ctx: ExtensionContext, category: string) => {
    if (!active(ctx) || ctx.agent.kind !== "main") return false;
    const result = await client(ctx).request("activity", { category });
    if (!result.stopped) return false;
    const reason = result.reason || "当前工作项没有进展，自动推进已停止";
    if (result.stage === "needs_attention" && reason !== lastStopReason) {
      lastStopReason = reason;
      announce(`已保存检查点并停止自动推进：${reason.replace(/[。.!！？]+$/u, "")}。使用 /aidevflow-flow 查看状态或明确恢复。`);
    }
    stop(ctx);
    return true;
  };
  const human = (ctx: ExtensionContext, action: string, data: unknown) =>
    client(ctx).request(action, { source: "native_user_interaction", data });
  const kickoff = (ctx: ExtensionContext, text = "继续当前 light 检查点") => {
    recordedFailures.clear();
    lastStopReason = "";
    pi.sendUserMessage(`按 .zscode/extensions/aidevflow/flow.md 执行，先查询 aidevflow_light status。正常人工审查只有 Intent/PRD 和最终产物；其他阶段自主推进。已有失败任务时直接使用 status 的 failure_summary、current_task.group 和 recovery 模板处理。修订格式已完整提供，不读取运行时代码或历史 events，不沿用旧诊断中“必须放弃重启”的建议。\n\n用户需求或反馈：${text}`, { attribution: "agent" });
  };
  const command = (name: string, description: string, run: (args: string, ctx: ExtensionContext) => Promise<void>) =>
    pi.registerCommand(name, { description, handler: async (args, ctx) => {
      if (busy) { announce("已有 light 命令正在处理，请稍后再试。"); return; }
      busy = true;
      try { await run(args, ctx); }
      catch (error) { announce(`AIDevFlow light：${error instanceof Error ? error.message : String(error)}`); }
      finally { busy = false; }
    } });

  const T = pi.typebox.Type;
  pi.registerTool({
    name: "aidevflow_light", label: "AIDevFlow light", loadMode: "essential", approval: "exec",
    description: "查询和执行 light 工作流。status 返回当前 group、真实失败摘要，以及冻结测试的 recovery 修订模板、路径和 schema；按此写草稿并 repair_propose，无需查运行时源码。复杂产物用 kind/path 提交。禁止 data 或内联对象。协议见 .zscode/extensions/aidevflow/flow.md。",
    parameters: T.Object({
      action: T.Union(["report_conflict", "status", "environment", "requirements_context", "validate", "submit", "run", "retry", "prepare_baseline", "baseline_context", "repair_propose", "repair_apply", "prepare_verifier", "verifier_context", "archive"].map(value => T.Literal(value))),
      kind: T.Optional(T.Union(["requirements", "spec", "plan", "knowledge", "verification"].map(value => T.Literal(value)))),
      path: T.Optional(T.String()), reason: T.Optional(T.String()),
    }, { additionalProperties: false }),
    execute: async (_id, params, signal, _update, ctx) => {
      const worker = client(ctx);
      const cancel = () => { worker.close(); };
      if (signal?.aborted) throw new Error("已中断；查看状态，确认旧验证结束后再恢复。");
      signal?.addEventListener("abort", cancel, { once: true });
      try {
        const result = await worker.request("tool", params);
        if (result.stopped) { announce("连续操作失败，已保留检查点并停止重复提交。"); stop(ctx); }
        return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], details: result };
      } catch (error) {
        recordedFailures.add(_id);
        if (!signal?.aborted) {
          try { if ((await worker.request("status")).stage === "needs_attention") stop(ctx); }
          catch { stop(ctx); announce("无法读取 Gate 状态，已停止并保留检查点。"); }
        }
        throw error;
      } finally { signal?.removeEventListener("abort", cancel); }
    },
  });

  const showStatus = (s: any) => announce(s.active_run === null
    ? "当前没有活动任务。直接在 /aidevflow-flow 后描述需求即可。"
    : `当前阶段：${s.stage}\n下一步：${s.next_action}${s.current_task ? `\n当前任务：${s.current_task.id} ${s.current_task.title}` : ""}`);
  const readyForReview = (s: any) => s.stage === "delivery_ready" && s.regression_fresh && s.verdict
    || s.stage === "discovery" && s.requirements && !s.requirements_feedback_pending && (Array.isArray(s.blocking_questions) ? s.blocking_questions.length === 0 : !(s.requirements.value.intent.open_questions || []).some((q: any) => q.blocking));
  const enter = async (text: string, ctx: ExtensionContext) => {
    const epoch = generation;
    const session = ctx.sessionManager.getSessionId();
    sessionUI = ctx.ui;
    await recover(ctx);
    const intent = entryIntent(text);
    const report = await client(ctx).request("doctor");
    if (epoch !== generation || session !== ctx.sessionManager.getSessionId()) return;
    if (!report.ok || !report.initial_commit) {
      announce(`启动检查未通过：\n${(report.errors || []).join("\n")}${!report.initial_commit ? "\n项目还没有初始 Git 提交，请先完成项目准备。" : ""}`); return;
    }
    if (intent === "diagnose") { announce("安装与环境检查通过。\n" + JSON.stringify(report, null, 2)); return; }
    const s = await status(ctx);
    if (epoch !== generation || session !== ctx.sessionManager.getSessionId()) return;
    if (intent === "status") { showStatus(s); return; }
    if (s.active_run === null) {
      if (intent !== "feedback") { showStatus(s); return; }
      await client(ctx).request("start-request", { source: "native_user_interaction", text });
      if (epoch !== generation || session !== ctx.sessionManager.getSessionId()) return;
      kickoff(ctx, "读取 Gate 固定的原始用户需求，开始需求讨论与 Intent/PRD 分析。"); return;
    }
    if (intent === "pause") { await human(ctx, "pause", {}); announce("已保存检查点并暂停后续推进。"); return; }
    if (intent === "abandon") { await abandon(ctx); return; }
    if (intent === "resume" && ["applying", "verify", "repairing"].includes(s.stage)) { await resume(ctx, s); return; }
    if (s.stage === "needs_attention" && s.attention_kind === "business_decision" && intent === "feedback") {
      await human(ctx, "requirement_feedback", { text, by: user(ctx) }); kickoff(ctx); return;
    }
    if (["paused", "needs_attention"].includes(s.stage)) {
      if (["continue", "resume"].includes(intent)) { await resume(ctx, s); return; }
      showStatus(s); announce("输入 /aidevflow-flow 继续 可恢复，或附上需要处理的问题。"); return;
    }
    if (readyForReview(s) && ["continue", "resume", "review"].includes(intent)) {
      await review(s.stage === "discovery" ? "requirements" : "delivery", ctx); return;
    }
    if (s.stage === "delivery_ready") {
      if (!s.regression_fresh) { announce("产物已变化，自动重新检查与独立验收。"); kickoff(ctx); return; }
      await human(ctx, "delivery_feedback", { text, by: user(ctx) }); kickoff(ctx); return;
    }
    if (["discovery", "spec", "plan"].includes(s.stage) && intent === "feedback") {
      await human(ctx, "requirement_feedback", { text, by: user(ctx) });
    }
    if (intent === "review") { announce("尚未到达可审查的检查点，继续由 AI 推进。"); }
    kickoff(ctx, intent === "feedback" ? text : "继续当前检查点；缺少业务答案时讨论，批准后自主推进");
  };
  command("aidevflow-flow", "提出需求、继续、查看进度、审查和验收的统一入口", enter);
  command("aidevflow-discover", "讨论需求、查看 Intent/PRD，统一处理入口审查", enter);
  const review = async (kind: "requirements" | "delivery", ctx: ExtensionContext) => {
    if (reviewBusy) return;
    reviewBusy = true;
    const controller = new AbortController();
    reviewController = controller;
    const epoch = generation;
    const session = ctx.sessionManager.getSessionId();
    const valid = () => !controller.signal.aborted && epoch === generation && session === ctx.sessionManager.getSessionId();
    try {
      const s = await status(ctx);
      if (!valid() || !readyForReview(s) || (kind === "requirements") !== (s.stage === "discovery")) return;
      lastReviewKey = `${s.run_id}:${kind}:${kind === "requirements" ? `${s.requirements.sha256}:${s.requirements.revision}` : s.verdict.id}`;
      if (!ctx.hasUI) { announce("审查材料已保存，等待原生交互确认；请在支持交互的会话运行 /aidevflow-flow。"); return; }
      if (kind === "requirements") {
        const value = s.requirements.value;
        announce(`Intent：\n${JSON.stringify(value.intent, null, 2)}\n\nPRD：\n${value.prd}\n\n需求与验收场景：\n${JSON.stringify({ requirements: value.requirements, scenarios: value.scenarios }, null, 2)}`);
        const choice = await ctx.ui.select("人工审查①：Intent/PRD；确认后 AI 自主研发及独立验收", ["暂不批准", "提出修改意见", "确认需求并开始自主开发"], { signal: controller.signal });
        if (!valid()) return;
        if (choice === "提出修改意见") {
          const text = (await ctx.ui.input("请说明需要调整的目标、范围或验收要求", undefined, { signal: controller.signal }))?.trim();
          if (!valid() || !text) return;
          await human(ctx, "requirement_feedback", { text, by: user(ctx) }); if (valid()) kickoff(ctx); return;
        }
        if (choice !== "确认需求并开始自主开发") return;
        await human(ctx, "approve", { sha256: s.requirements.sha256, by: user(ctx) });
      } else {
        announce(`交付报告：\n${JSON.stringify({ candidate: s.final_candidate, verdict: s.verdict.value, bundle: s.verifier_bundle, directory: s.directory }, null, 2)}`);
        const choice = await ctx.ui.select("人工审查②：审查最终产物；无需 Git 提交", ["暂不验收", "提出修改意见", "验收通过"], { signal: controller.signal });
        if (!valid()) return;
        if (choice === "提出修改意见") {
          const text = (await ctx.ui.input("请说明批准范围内尚未满足的行为或产物问题", undefined, { signal: controller.signal }))?.trim();
          if (!valid() || !text) return;
          await human(ctx, "delivery_feedback", { text, by: user(ctx) }); if (valid()) kickoff(ctx); return;
        }
        if (choice !== "验收通过") return;
        await human(ctx, "confirm", { candidate: s.final_candidate, bundle_sha256: s.verifier_bundle.sha256, by: user(ctx) });
      }
      if (valid()) kickoff(ctx);
    } finally {
      if (reviewController === controller) reviewController = undefined;
      reviewBusy = false;
    }
  };
  const queueReview = (s: any, ctx: ExtensionContext, idle: Promise<unknown> = Promise.resolve()) => {
    const kind = s.stage === "discovery" ? "requirements" : "delivery";
    const key = `${s.run_id}:${kind}:${kind === "requirements" ? `${s.requirements.sha256}:${s.requirements.revision}` : s.verdict.id}`;
    if (reviewQueued || reviewBusy || key === lastReviewKey) return;
    if (!sessionUI || !ctx.hasUI || typeof ctx.setTimeout !== "function") {
      announce("材料已就绪，运行 /aidevflow-flow 即可打开审查。"); return;
    }
    reviewQueued = true;
    const epoch = generation;
    const session = ctx.sessionManager.getSessionId();
    // Capture a lifecycle UI, not session_stop's abort-bound UI; show only after this callback returns.
    const context = Object.create(ctx) as ExtensionContext;
    Object.defineProperty(context, "ui", { value: sessionUI });
    ctx.setTimeout(async () => {
      await idle;
      if (epoch !== generation || session !== ctx.sessionManager.getSessionId()) return;
      reviewQueued = false;
      try { await review(kind, context); }
      catch (error) { announce(`审查已保留，稍后用 /aidevflow-flow 继续：${String(error)}`); }
    }, 0);
  };
  const resume = async (ctx: ExtensionContext, s: any) => {
    if (s.attention_kind === "business_decision") {
      const epoch = generation;
      if (!ctx.hasUI) { announce("存在业务冲突，等待真实用户决定；保留原批准与检查点。"); return; }
      const decision = await ctx.ui.select(`业务冲突：${s.attention_reason}。恢复表示继续按原批准需求修复；修改需求请在 flow 输入具体意见。`, ["保持停止", "按原批准需求恢复"]);
      if (epoch !== generation || decision !== "按原批准需求恢复") return;
    }
    const task = s.tasks?.[s.cursor];
    const receipts = [s.analyst_invocation, s.verifier_invocation, s.baseline_invocation].filter(Boolean).map(p => JSON.parse(readFileSync(join(ctx.cwd, p), "utf8")));
    if (task?.inflight || receipts.some(receipt => !receipt.execution_finished)) {
      const epoch = generation;
      if (!ctx.hasUI) { announce("存在未知在途执行，请在原生交互中核实后恢复。"); return; }
      const choice = await ctx.ui.select("请确认旧验证进程已结束，再恢复当前检查点", ["保持停止", "确认并恢复"]);
      if (epoch !== generation || choice !== "确认并恢复") return;
    }
    await human(ctx, "resume", { by: user(ctx) }); kickoff(ctx);
  };
  const abandon = async (ctx: ExtensionContext) => {
    const epoch = generation;
    if (!ctx.hasUI) return;
    const choice = await ctx.ui.select("放弃当前变更并留档；保留代码，不更新知识", ["保留变更", "确认放弃"]);
    if (epoch !== generation || choice !== "确认放弃") return;
    announce(JSON.stringify(await human(ctx, "abandon", { by: user(ctx) }), null, 2));
  };
  const observation = (event: any, ctx: ExtensionContext) => ({ ...event,
    session_id: ctx.sessionManager.getSessionId(), actor: ctx.agent });
  const argumentFailure = async (id: string, ctx: ExtensionContext) => {
    if (recordedFailures.has(id)) return;
    recordedFailures.add(id);
    const failure = await client(ctx).request("operation-failure", {
      action: "arguments", reason: "工具参数校验失败，请使用声明的字符串字段和文件路径",
    });
    if (failure.stopped) stop(ctx);
  };
  pi.on("before_agent_start", async (event, ctx) => {
    if (!managedRoles.has(ctx.agent.name)) return;
    verifierPrompt = event.prompt;
    const policy = ctx.agent.name === "aidevflow-independent-verifier" ? "verifier.md"
      : ctx.agent.name === "aidevflow-baseline-reviewer" ? "baseline.md" : "requirements.md";
    return { systemPrompt: [readFileSync(join(ctx.cwd, ".zscode/extensions/aidevflow", policy), "utf8")] };
  });
  pi.on("context", (event, ctx) => {
    if (!managedRoles.has(ctx.agent.name)) return;
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
  pi.on("tool_call", async (event, ctx) => {
    try {
      if (managedRoles.has(ctx.agent.name)) {
        return await client(ctx).request("guard", observation(event, ctx));
      }
      const category = event.toolName === "aidevflow_light" ? (event.input as any).action : event.toolName;
      if (await activity(ctx, category)) return { block: true, reason: "Gate 已停止自动推进；查看 flow 检查点后恢复" };
      if (active(ctx) && event.toolName === "ask") {
        const recorded = await client(ctx).request("questions", observation(event, ctx));
        return { input: recorded.input };
      }
      if (event.toolName === "task") {
        const original: any = event.input;
        const roles = Array.isArray(original.tasks) ? original.tasks : [original];
        if (roles.length !== 1 || !managedRoles.has(roles[0]?.agent)) {
          return { block: true, reason: "只允许单个受管需求分析、基线审查或独立验收角色" };
        }
        // Validate the original input before replacing it with Gate's authoritative assignment.
        const forbidden = [original, ...roles].some(item => item.isolated || ["tools", "outputSchema", "schemaMode", "sourceSession", "model"].some(k => k in item));
        if (forbidden) return { block: true, reason: "不得覆盖独立验收策略" };
        const analyst = roles[0].agent === "aidevflow-requirements-analyst";
        const baseline = roles[0].agent === "aidevflow-baseline-reviewer";
        const prepared = analyst
          ? await client(ctx).request("tool", { action: "requirements_context" })
          : await client(ctx).request(baseline ? "baseline-assignment" : "verifier-assignment");
        if (!prepared.task_call) {
          const reason = `Gate 当前为 ${prepared.phase || "不可派发"}；下一步 ${prepared.next_action || "status"}。不重复启动角色。`;
          blockedReviewCalls.set(ctx.sessionManager.getSessionId() + ":" + event.toolCallId, reason);
          return { block: true, reason };
        }
        const input = Array.isArray(original.tasks)
          ? { context: analyst ? "Managed requirements analysis" : "Independent acceptance of the frozen evidence bundle", tasks: [prepared.task_call] }
          : prepared.task_call;
        await client(ctx).request(analyst ? "observe-requirements" : baseline ? "observe-baseline" : "observe-verifier", observation({ ...event, input }, ctx));
        return { input };
      }
      if (event.toolName === "read") {
        const input = event.input as { path?: string };
        const match = input.path?.replaceAll("\\", "/").match(/(?:^|\/)(zsspec-(?:init|spec|plan|apply|archive))(?:\/SKILL\.md)?$/);
        if (match) return { input: { ...event.input, path: join(ctx.cwd, ".zscode/skills", match[1], "SKILL.md") } };
      }
      if (["ask", "web_search", "todo", "wait", "aidevflow_light"].includes(event.toolName)) return;
      return await client(ctx).request("guard", observation(event, ctx));
    } catch (error) { return { block: true, reason: `light Gate 拒绝操作：${String(error)}` }; }
  });
  pi.on("tool_result", async (event, ctx) => {
    if (managedRoles.has(ctx.agent.name)) return;
    if (event.toolName === "task") {
      const key = ctx.sessionManager.getSessionId() + ":" + event.toolCallId;
      const reason = blockedReviewCalls.get(key);
      if (reason) {
        blockedReviewCalls.delete(key);
        return { additionalContext: reason };
      }
    }
    if (active(ctx) && event.toolName === "ask") {
      if (event.isError) return;
      const recorded = await client(ctx).request("questions", observation(event, ctx));
      return { additionalContext: recorded.source_updated || recorded.replayed
        ? "真实业务回答已自动记入需求来源。重新 requirements_context 分析并提交当前结果；无需用户重复输入 flow。"
        : "本次未取得真实业务回答，超时默认选择不能代替用户决定。保留入口检查点。" };
    }
    if (event.toolName === "aidevflow_light" && !event.isError && (event.input as any)?.action === "submit" && (event.input as any)?.kind === "requirements") {
      const s = await status(ctx);
      if (readyForReview(s)) {
        const idle = Promise.resolve(ctx.abort?.()).catch(() => {});
        queueReview(s, ctx, idle);
      }
      return;
    }
    if (event.toolName === "aidevflow_light" && event.isError && !recordedFailures.has(event.toolCallId)) {
      await argumentFailure(event.toolCallId, ctx);
      return;
    }
    if (event.toolName !== "task") return;
    const inp: any = event.input;
    const role = Array.isArray(inp.tasks) ? inp.tasks[0]?.agent : inp.agent;
    if (!managedRoles.has(role)) return;
    const receipt = await client(ctx).request(role === "aidevflow-requirements-analyst" ? "observe-requirements"
      : role === "aidevflow-baseline-reviewer" ? "observe-baseline" : "observe-verifier", observation(event, ctx));
    const s = await status(ctx);
    if (s.stage === "needs_attention") stop(ctx);
    if (readyForReview(s)) {
      const idle = Promise.resolve(ctx.abort?.()).catch(() => {});
      queueReview(s, ctx, idle);
    }
    const next = role === "aidevflow-requirements-analyst"
      ? "需求完成后用 submit(kind=requirements,path=result_path) 登记；不能重述或伪造结果。"
      : role === "aidevflow-baseline-reviewer"
      ? "先查询 status.recovery 与 baseline_review；initial approve 后 run；repair/derived approve 后 repair_apply。initial revise 的测试错误直接修改 unfrozen_files 再 prepare_baseline；规格/计划错误使用含 spec_path/plan_path 的 repair_propose。冻结文件始终通过提案修订。"
      : "先查询状态；revise 则自主修复并重新回归、独立验收，pass 才能交付用户审查。不能自行提交结论。";
    return { additionalContext: `Gate 已保存原生角色结果：${JSON.stringify({ status: receipt.status, result_path: receipt.result_path, error: receipt.error })}。${next}` };
  });
  // Native schema rejection occurs before the tool wrapper, so tool_result alone misses it.
  pi.on("tool_execution_end", async (event, ctx) => {
    if (!managedRoles.has(ctx.agent.name) && event.toolName === "aidevflow_light" && event.isError &&
        typeof (event.result?.details as any)?.error === "string") {
      await argumentFailure(event.toolCallId, ctx);
    }
  });
  pi.on("session_stop", async (event, ctx) => {
    if (!active(ctx) || ctx.agent.kind !== "main" || event.signal?.aborted) return;
    const modes = ctx.sessionModes?.getState();
    if (modes && (modes.goal.status !== "inactive" || modes.loop.status !== "inactive")) return;
    try {
      const epoch = generation;
      const session = ctx.sessionManager.getSessionId();
      const s = await status(ctx);
      if (epoch !== generation || session !== ctx.sessionManager.getSessionId()) return;
      if (readyForReview(s)) { queueReview(s, ctx); return; }
      if (!["spec", "plan", "applying", "repairing", "verify", "done", "finalizing"].includes(s.stage)) return;
      if (await activity(ctx, "continue")) return;
      return { continue: true, additionalContext: `入口已批准。继续自主研发、修复及独立验收，不要求人工确认测试或提交 Git。${s.next_action}` };
    } catch (error) { announce(`恢复状态失败，停止续跑并保留检查点：${String(error)}`); }
  });
  pi.on("before_provider_request", async (_event, ctx) => {
    try { await activity(ctx, "provider"); }
    catch (error) { if (active(ctx)) { stop(ctx); announce(`无法核实检查点，已停止自动推进：${String(error)}`); } }
  });
  const recover = async (ctx: ExtensionContext) => {
    if (ctx.agent.kind !== "main" || !active(ctx)) return;
    await client(ctx).request("recover-verifier", { session_id: ctx.sessionManager.getSessionId(),
      entries: ctx.sessionManager.getBranch(), actor: ctx.agent });
  };
  pi.on("session_start", async (_event, ctx) => { if (ctx.agent.kind === "main") sessionUI = ctx.ui; await recover(ctx); });
  pi.on("session_switch", async (_event, ctx) => { cancelReview(); lastReviewKey = ""; bridge?.close(); bridge = undefined; if (ctx.agent.kind === "main") sessionUI = ctx.ui; await recover(ctx); });
  pi.on("input", async (event, ctx) => {
    cancelReview();
    if (!["interactive", "rpc"].includes(event.source) || !event.text.trim() || event.text.trim().startsWith("/") || !active(ctx) || ctx.agent.kind !== "main") return;
    const s = await status(ctx);
    if (s.stage === "discovery" || s.stage === "needs_attention" && s.attention_kind === "business_decision") {
      try { await human(ctx, "requirement_feedback", { text: event.text, by: user(ctx) }); }
      catch (error) { announce(`需求反馈已保持停止：${String(error)}`); }
    }
  });
  pi.on("session_shutdown", async () => { cancelReview(); sessionUI = undefined; bridge?.close(); bridge = undefined; });
}
