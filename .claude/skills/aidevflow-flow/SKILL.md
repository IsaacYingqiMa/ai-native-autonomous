---
name: aidevflow-flow
description: Start or resume AIDevFlow autonomous delivery from a natural-language request or requirement document. AIDevFlow owns Intent, Contract generation, state, gates, and evidence; zsspec is an optional delivery-method adapter.
---

# AIDevFlow flow entrypoint

You are the interactive coordinator. The Gate is the source of workflow state. For every new task or resume, call `python scripts/aidevflow.py status` first. Do not infer stage or approval from conversation memory.

## Claude execution compatibility (observer B)

- After installing/updating, restart Claude Code. Run `python scripts/aidevflow.py doctor` before a new rehearsal; it checks configuration without dispatching roles. A successful doctor does not prove live dispatch or business acceptance.
- Dispatch the three critical roles using the native Agent tool with the exact `subagent_type`, a complete `prompt`, and a description. Use a fresh ordinary subagent: omit `name`, `resume`, `fork` and `isolation`. Put task/bundle identity in the prompt, not in a display name. Do not change the user's global Teams setting.
- Ordinary foreground and background subagents are supported. The harness observes parent Pre/PostToolUse:Agent, actual Start/Stop, and successful Handback. A missing Start alone need not block registration when the parent dispatch, actual result and execution completion are correlated. A background launch or report alone never establishes completion.
- For a Verifier, include the exact prepare-verifier path and SHA-256 in its prompt before dispatch; give it only the bundle. Do not reuse an earlier Verifier or infer its input from the bundle current at finish time.
- Read `status.agent_invocations` and `status.agent_observer` when a role result cannot be registered. `dispatched`/`started` means wait or inspect execution; `output_received` still needs completion; `missing_output`/`invalid_output` needs the original role's valid result; `identity_conflict` needs harness diagnosis. `unsupported_mode` means teammate/resume/fork/isolation was detected. Confirm that execution has ended or been cancelled, then create a fresh ordinary role attempt. Never launch duplicates while the previous execution is uncertain.
- `agent_observer.diagnostics` can contain buffered unmatched observations while a parent Agent call is still running. Wait for its PostToolUse bridge rather than fabricating a receipt. Persistent dispatch_pre_missing requires checking/reinstalling Hooks and restarting, then a fresh dispatch; old orphan results are not retroactively certified.
- Output and evidence hashes remain strict. Do not edit role JSON, write synthetic Hooks, read provider transcripts to fabricate receipts, disable receipt requirements, or delete historical probe records to claim recovery. The observer is a local provenance guard, not OS isolation.
- An existing intake_started run continues Intake after the observer is repaired; resume-run is only for needs_human. Reconcile earlier user answers with the current draft before recording them through answer. Do not turn a toolchain update into product repair.

## Keep the run proportionate

- For a small, cohesive change, prefer one work item covering its implementation and applicable acceptance checks. Split only for genuine dependencies, independently deliverable changes, or risk. Do not create a work item for every Skill or command.
- Preserve the selected method's required artifacts. Avoid extra progress reports and duplicate checklists merely for AIDevFlow. Generate all structured documents yourself; the user only supplies the request and blocking decisions.
- Before implementation, explicitly invoke every declared `method.stages.build` Skill (including `zsspec-apply` when configured). New v3 runs observe native Skill loading automatically and expose `status.method_steps`; loading is a prerequisite, not proof that the method was followed. Old runs have no retroactive observation requirement: do not invent historical receipts or rerun an entire implementation just to create one.
- Use `status.pending_checks` to run each missing/failed/stale suite+target once. Several acceptance IDs may reference the same execution. Do not rerun an unchanged successful check just because it has multiple mappings.
- Gate is the only authoritative execution state. Registered table status cells are generated views. Other prose/DoD checkboxes describe requirements; do not manually synchronize them as a second task ledger or change frozen content after Design.

## New request intake

When there is no active run:

1. Identify the user's original request. Prefer an existing requirements Markdown file in the repository when the user points to one. If the user gave the request only in chat, save a faithful, concise Markdown snapshot to `specs/aidevflow-intake/request-<timestamp>.md`. Do not ask the user to write a structured Intent or Contract.
2. Start durable intake with `python scripts/aidevflow.py start-intake --request <repo-relative-request-path>`. Keep the returned task ID; it is Gate-assigned and unique.
3. Delegate intent understanding to `aidevflow-intent-analyst`, giving it the original request snapshot and only relevant repository context. Require exactly one JSON `IntentDraft` matching `.aidevflow/schemas/intent-draft.schema.json`. The analyst is read-only and must not write state.
4. Save the returned JSON unchanged to `specs/aidevflow-intake/<TASK_ID>/intent-draft.json`, then call `record-intent --draft <path>`. New autonomous runs require a matching completed Analyst receipt captured by correlated dispatch/lifecycle observations. Do not impersonate an Analyst or synthesize its receipt. If receipts are missing, inspect Hook diagnostics and repair the harness before proceeding.
5. If Gate reports unresolved blockers, show only those questions in the main user interaction, with bounded options, impacts, and the recommended choice. Ask the user only for decisions that affect scope, architecture, risk, or acceptance. Record every answer through `answer --question-id ... --option-id ... --text ...`. Re-run the Analyst only when an answer materially changes interpretation or reveals another ambiguity; otherwise use the Gate-recorded decision and continue.
6. When all blocking questions are resolved, call `finalize-intent`. For runs started with `start-intake`, Gate records the user's selected `clarify_then_auto` policy against the finalized Intent and returns `intent_approved`. This is permission to proceed with the clarified request; it does not override later risk, scope-change, or Gate blockers. Summarize the resolved goal and assumptions to the user, then continue without asking for a separate approval.
7. Delegate to `aidevflow-contract-planner`, providing the immutable source request snapshot, finalized Intent, assigned task ID, repository context, `.aidevflow/contract.example.json`, and actual available method Skills. Require one JSON `aidevflow/contract-v1`. The planner is read-only; it must not implement or write workflow state. If it finds a decision-driving gap, return to Intent clarification rather than inventing a choice.
8. Save the Planner's JSON unchanged to `specs/aidevflow-intake/<TASK_ID>/contract-draft.json`; call `register-contract --draft <path>`. New autonomous contracts require `work_plan_required: true`, complete `required_checks` acceptance coverage, and a matching Planner receipt. If validation fails, return errors to the Planner and submit its revised output. Gate pins the accepted copy and hash. Do not edit the pinned copy.
9. Only after successful registration call `advance --to design`, then follow the delivery stages below.

## Resume from Gate state

Read `status` and continue based on the exact persisted stage and status:

- `intake_started`, `intent_review`, or `awaiting_user_input`: continue Analyst/clarification and record drafts or answers through Gate.
- `intent_approved` with no `contract_path`: run Contract Planner, register its draft, then advance to Design.
- `intent_approved` with a registered contract: advance to Design.
- `design`, `build`, or `verify`: continue the configured method and Gate checks for that stage.
- `needs_human`, invalid hashes, corrupted state, or a scope-changing ambiguity: stop and explain the specific blocker. Do not silently repair Gate state. After an actual user instruction, a recorded infrastructure/legacy Stop blocker supports `resume-run --mode retry`; product acceptance failures use the default `--mode repair`.

Never create a second run while an active run exists. The source request snapshot, Gate event log, and run-state are the recovery source.

## Method-driven delivery

Read the pinned Contract and dispatch its configured method Skills. The default `zsspec` method invokes installed `zsspec-*` Skills for specification, tests, implementation, and review; zsspec does not own Intent, AIDevFlow state, transitions, or independent acceptance.

- In Design, run the configured method's Design Skills and create all declared artifacts and test files. Use the installed method's established document paths/templates. Establish a runnable test environment before collecting behavioral Red; do not hide import/setup failures by converting every exception to AssertionError. Freeze tests through Gate; collect valid Red for `red_test_files` (defaults to all test files in legacy contracts).
- For a completely new module, a test can first assert that the explicitly required module/entrypoint file exists, then import it normally and run behavioral assertions. Its absence is an explicit feature assertion; exceptions from a present module must remain visible. Do not catch arbitrary import/runtime failures or suppress tracebacks to manufacture a valid Red.
- Create `specs/aidevflow-intake/<TASK_ID>/work-plan.json` matching `.aidevflow/schemas/work-plan.schema.json`. Map stable work-item IDs to criteria, dependencies, and acceptance IDs; the union must cover every acceptance item. For zsspec, include `projections` mapping the generated TASK/CHK/REQ table row IDs to work items, required check IDs, and acceptance IDs. Gate validates those rows and owns their status cells. Register the plan, then `advance --to build`; Gate pins specification content while allowing its own status projections.
- In Build, use the configured Coding Agent/Skills. For each registered work item, call `start-work-item --id <ID>` before implementation and `complete-work-item --id <ID> --evidence <PATH...>` only after its completion criteria are met. Reopen a completed item through `reopen-work-item --id <ID> --reason <REASON>` when a repair changes that work. Do not change frozen tests. Run required suites only through `aidevflow run-tests` so Gate captures output. Capture Green for every required test and regression evidence when configured, then call `advance --to verify`; Gate refuses that transition while required work items remain incomplete.
- Give each method Skill this run context: "AIDevFlow owns execution state. Use Gate work-item/check commands for status updates; its adapter projects the result into generated documents. Preserve the Skill's engineering requirements and do not directly Edit projected status cells." This wrapper applies only to this invocation; never modify the zsspec Skill asset. Use `sync-projections` if the generated view needs refreshing. Specification-content changes require a controlled new baseline and human handling in this POC.
- Before Verify, run every configured `method.stages.review` Skill that applies, and every `required_checks` entry, including HTTP smoke/build/lint when declared. A real HTTP smoke exercises the actual application/Workflow route; module imports and AST checks alone do not establish HTTP integration. Extra generated test/report paths must be declared before the Contract is pinned.
- After any implementation/dependency change, rerun affected required checks; Gate currently conservatively requires a fresh passing set for the entire candidate. `status.checks` reports missing/failed/stale results. A successful old run cannot override a later failure. Repair routing starts a new evidence epoch.
- Continue through routine method-stage confirmations autonomously when the proposed action stays within the approved Intent and pinned Contract. If a zsspec Skill pauses for approval of an ordinary generated plan, review that plan against those bounds and proceed; return to the user only when it exposes a decision-driving ambiguity, material scope/risk change, or Gate blocker.
- In Verify, first run any `status.pending_checks`, then call `prepare-verifier` and delegate only the returned bundle to `aidevflow-independent-verifier`. Read `BUNDLE.json`: v2 bundles map original `source_path` values to short `objects/<hash>` paths. Cite the physical `path` in verdict evidence. The bundle contains the request, Intent, specification, tests, checks, candidate and execution results. The Verifier must not see writer conversation or self-review. Save its exact JSON at `specs/aidevflow-intake/<TASK_ID>/verdict.json` and call `record-verdict`. A new autonomous run requires a completed Verifier receipt matching both its output and the current bundle. After a passing verdict call `advance --to delivery_ready`; changed code or evidence prevents delivery.
- If the verdict fails, route the failure through Gate and return to its specified stage. Changed implementation requires fresh test evidence and a new verifier bundle.
- Advance only through Gate. Do not call a task delivery-ready until Gate reaches `delivery_ready`.

## Recover without restarting the product task

- A packaging I/O error is infrastructure failure. Follow the reported retry action; `prepare-verifier` counts up to three failed packaging attempts independently of code-repair attempts. Never route it as `review_finding`, rewrite product code, extract transcript files to bypass protection, or request Windows registry changes as the default workaround.
- `resume-run --mode retry` preserves stage, checks and repair budget. It requires a recorded infrastructure/legacy Stop cause and an explicit user continuation instruction. A runtime reinstall changes candidate identity: rerun stale checks in Verify; do not falsify or rehash old results.
- A malformed Agent JSON result is `invalid_output`; absent output is `missing_output`. Ask the original role to return valid JSON, retaining its meaning. Do not edit the result yourself or create its receipt. Bound repeated attempts and report a persistent protocol problem.
- A Stop hook is observational. It never accepts a task, changes its phase, or grants continuation. Continue the requested workflow yourself; if native `/goal` is used, let it own turn continuation and use Gate for business acceptance. Do not layer a second Stop-driven retry loop over it.
- Acceptance of the change and the existence of a final acceptance verdict must not depend on each other circularly. Product criteria are checked by relevant evidence; `record-verdict` and `delivery_ready` enforce completion of the delivery procedure.

## User interruption policy

Proceed autonomously after the Intent blockers are resolved and the Contract passes Gate validation. Ask the user only for decision-driving ambiguity, a material scope change, a risk exception, or an action that Gate explicitly requires. Put non-blocking uncertainty in Intent assumptions with a revisit condition.

## Boundaries

- Do not write `.aidevflow` run state, approvals, manifests, or event logs directly. Gate is their only writer.
- Do not let the Intent Analyst author a Contract or let the Contract Planner modify code.
- Do not let the Coding Agent verify its own work. The independent Verifier receives only Gate's frozen evidence bundle.
- Do not modify AIDevFlow scripts, hooks, contracts, or schemas during a normal product task.
- `needs_human` stops execution. Only after the user explicitly instructs continuation may the coordinator call `resume-run --by ... --utterance <actual instruction> --additional-attempts N`. Do not invent this approval. State/history reading and archive remain available while stopped.
