---
name: aidevflow-flow
description: Discuss and approve Intent/PRD, then autonomously deliver through the current zsspec 2 method and AIDevFlow quality gates.
---

# Autonomous delivery coordinator

Read `python scripts/aidevflow.py status` before every start/resume. Gate checkpoints, immutable original role results and fresh evidence are authoritative. Do the next authorized action in the same turn. End only at delivery, a real decision/permission blocker, a running native role, user interruption or an explicit budget limit. A progress report is not a pause request.

## Requirements and authorization

Use the native `/aidevflow-flow` entry for discussion and human Intent/PRD review. In discovery, use role-context --role requirements_analyst and the immutable result_path with discovery-record. Ask only unresolved business questions. The native review interaction records actual approval and automatically starts delivery. Never call approval commands from AI shell or manufacture user decisions.

After approval the original Intent/PRD, scope, non-goals, constraints and acceptance IDs stay fixed. Do not repeat elicitation or create another intake. Ordinary engineering decisions are authorized; record their rationale. A genuinely new business decision produces a precise issue/proposal and preserves the current approval.

Read [autonomous repairs and evidence](references/autonomous-repairs.md) for technical corrections, frozen-test mistakes, runtime checks and acceptance retries. Ordinary fixes need no additional human authorization. A technical retry limit is a no-progress checkpoint, not a request to approve bug fixing; preserve and report its cause.

## Current method and Design

1. Run `prepare-method` once. It assigns a stable slug and pins the unmodified five-skill method. Read methods via their controlled physical source/skill URI. Initialize missing knowledge with pinned zsspec-init, preserving existing work; specs/ existence alone is not initialization. Run record-initialization when MAP and INDEX are ready.
2. Invoke zsspec-spec to consume the exact approved Intent/PRD and scenarios. Its demand/value questioning and scope pruning already happened upstream: skip them. Do not delete approved requirements or ask resolved questions again. Existing behavior intentionally changed by this request is not a conflict. Follow specification, design and validation quality rules. The current task/slug is Gate-selected; do not create or choose another change. Retain the required blind comparison for more than five specifications, using an independent read-only artifact reviewer assignment.
3. `prepare-artifact-review --kind spec`; `role-context --role artifact_reviewer`; dispatch one fresh read-only native role, return its unchanged `result_path` to `record-artifact-review --path ...`. Repair changes_required findings automatically within approved scope, prepare fresh inputs and review again. Never edit approved source material. Only a genuine requires_decision needs a new user decision; repeated unchanged attempts produce a technical no-progress checkpoint.
4. Invoke zsspec-plan; repeat the independent review using kind plan. Every S specification needs task cards, expected results and suitable validation. Preserve P/R tasks and the full replay requirement. Review failures are artifact repairs, not a new requirements approval.
5. After both passing reviews use `role-context --role contract_planner`, register-contract from its immutable result_path, `advance --to design`, `compile-work-plan`, `advance --to build`. The execution binding consumes the real reviewed artifacts. Do not invent old req/des/task/chk/test/done skills or run zsspec_check.py.

## Build and validation

Load zsspec-apply before implementation. Gate alone records progress and projects task checkboxes, group status, observation and completion slots. Do not manually edit frozen descriptions, expectations or specification rules. Start the next registered work item; respect dependencies. The current managed scheduler is serial; replace vendor parallel dispatch with the Gate-selected order and never spawn arbitrary workers.

For a testing card, generate its mapped tests and support files, inspect assertions against approved examples, write the actual review report under evidence_paths, `record-design-review`, then `freeze-tests <this task's tests and support files>`. Run genuine Red before implementation. Syntax, import, collection and environment errors are not valid Red. Implement until Green; never weaken assertions. For other cards use their build/runtime/metric checks without manufacturing test files or Red.

Run declared checks through `run-tests`. Run static/manual checks honestly with candidate-bound `record-check`, reports and actual screenshots where required. Browser work uses aidevflow_browser.py through Gate; read actual screenshots. Only reuse checks that Gate says are fresh. Complete work with `complete-work-item --id ... --observation <concrete actual observation> --evidence ...`. A short claim of success is insufficient. Replay all validation before finishing R1.

Implementation defects auto-repair with recorded hypotheses and bounded retries. A repairable specification/plan mistake uses begin-engineering-revision --reason <actual defect>, edits only the returned drafts, repeats independent spec/plan reviews, then commit-engineering-revision and invalidates affected work/evidence; never patch frozen files directly. Genuine business changes require a new proposal and actual human approval. Infrastructure failure is not product failure. After context changes, read status and recover-runtime before re-dispatching any execution whose state is unknown.

## Independent acceptance and closure

Advance to Verify only with completed work and fresh checks. `prepare-delivery-archive` creates a disposable specs workspace. Run pinned zsspec-archive there: update knowledge and index, move only this change; no code edits or Git commits. `seal-delivery-archive` validates the exact relocation and allowable knowledge changes. Prepare the verifier bundle; dispatch role-context --role verifier using its exact bundle identity and immutable native result. Record the verdict. Failure routes to repair and new evidence, without declaring success or requesting ordinary stage approval. Passing verdict permits `commit-delivery-archive`, then `advance --to delivery_ready`.

## Runtime and recovery

Critical roles use exact Gate role-context, one native call at a time and their installed read-only tools/output schema. Do not override their role settings. Native launch is not completion. Never copy rendered role output, synthesize hooks or rewrite returned JSON. Recover actual native results before starting another attempt. Verifier sees only its exact frozen bundle.

Choose one continuation owner: the existing native Goal when explicitly selected, otherwise bounded_stop or manual. Do not add another retry/stop loop. Budget exhaustion is a resumable checkpoint, not acceptance. User pause/interrupt stays paused. Unknown execution waits for native reconciliation, never duplicate dispatch.

Report technical delivery with actual evidence and residual risks. Git commit, merge and release remain separately authorized. Method archive does not substitute for independent acceptance. Preserve all history and frozen records.
