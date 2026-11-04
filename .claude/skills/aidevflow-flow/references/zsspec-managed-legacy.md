# zsspec skill package v1.4 under AIDevFlow

This is an opt-in execution profile, `zsspec-managed-v1`. The user selected AI-coordinator review of ordinary artifacts, serial execution, and Design-frozen tests. Keep Intent → Design → Build → Verify → delivery_ready. No vendor Skill is patched. Outside AIDevFlow, vendor confirmation rules still apply.

Observer B2: new start-intake runs pin execution_policy.method_evidence=artifact_gated. Skill invocation observations are advisory, not proof of completion. Continue req → des → task → chk → verify → test even when PostToolUse:Skill is unavailable, and record truthful AI reviews against actual artifacts and tests. Older runs without this policy keep their original strict observation rule; never change their state by hand. Do not test Hooks with piped mock events in a real run. Diagnostics use doctor; native role recovery uses recover-runtime.

All design_artifacts, test_files and test_support_files entries must be concrete planned filenames; no fixtures/** or directory entries. The Contract validator rejects globs before pinning. This avoids a delayed Design-freeze failure.

## Context to pass to every method Skill

"This invocation is managed by AIDevFlow under the user's clarify-then-auto authorization. Source request, approved Intent and pinned Contract define scope. The coordinator reviews ordinary REQ/DES/TASK/CHK and executable tests and records an AI review; do not claim human confirmation. Continue routine work inside those bounds. Bring material scope, risk, interface/acceptance changes and unresolved critical choices to the user. Gate alone writes task/check/acceptance status cells. Return findings and evidence to the coordinator instead of editing mapped status cells. Use serial execution even when zsspec.yml says parallel:auto. All acceptance tests are prepared/reviewed in Design and frozen; after freezing do not generate replacements or auto-repair frozen tests. All executable checks go through Gate. Feedback and usage reporting are not acceptance evidence."

## Project preparation (before task Contract)

- Project-local `.claude/skills` only in this first profile. Do not resolve a global Skill while Gate fingerprints a project copy. Read `doctor.method_errors`; confirm actual tools in the Claude session. The profile does not add support for CodeBuddy or another runtime.
- Check existing project knowledge and `specs/zsspec.yml`. Use zsspec-init only when requested/necessary and before an active Contract restricts writes. Preserve existing CLAUDE.md/AGENTS.md. No task automatically rewrites project governance.
- Gate supports the documented two-level scalar mapping form of zsspec.yml, with comments and simple quotes, not general YAML (anchors, lists, flow mappings or block strings). Unsupported forms fail explicitly. Missing fields use documented defaults. `gate.mode` must be strict; quick is not supported by this profile. Effective parallel is always off without rewriting the file.
- Test switches never remove approved acceptance obligations. If a disabled type is required, resolve that conflict before registering Contract. A required auto command must declare `mode: auto` so Gate can check the e2e switch. Manual checks use `kind: manual`.
- Confirm dependencies, Playwright browser, commands, test data and service startup. The coordinator prepares authorized test services; zsspec-test is not expected to start them. Missing browser MCP or real external access stays blocked; never fabricate checks.
- No automatic zsspec-feedback invocation, transcript attachment or identity collection. Usage scripts are not Gate prerequisites. Do not run a report script merely to fabricate method observations.

## Intent and Contract

- Analyst resolves goal and critical decisions. Req consumes those decisions; do not restart the interview. Reference documents are read only when explicitly named/authorized. Preserve original request and never add imagined user decisions.
- Planner reads actual new Skills and the current example. Declare Design req/des/task/chk/verify/test, Build apply, Review test/done; the same test Skill has different purposes in Design and Review. Skill loading proves availability, not mode execution or correctness.
- Required checks: command (default, `suite` and optional `mode:auto`), static (`kind:static`), manual (`kind:manual`). Every acceptance ID needs appropriate coverage. Always require a static review. UI acceptance generally needs real auto/manual evidence; an import check is insufficient.
- Declare executable test_files, test_support_files (POM/fixtures/config), commands and product paths before pinning. Both lists freeze together; only test_files create mandatory Green targets. Different test file types may need a dispatching test-runner command. `red_test_files` is the subset for new behavior, not every helper/config file.
- `evidence_paths` are dedicated output directories (for example specs/checklist/reports, test-results, playwright-report). They cannot overlap product, frozen tests or design documents. Outputs are excluded from candidate identity only for this profile and are separately captured. Never put code or dependencies in these directories.

## Design

1. Invoke req → des → task → chk → verify. Req materializes the accepted Intent, without introducing new scope. Preserve module paths and stable semantic REQ/TASK/CHK IDs. Critical new ambiguity returns to the coordinator/user; a pinned Contract cannot be silently expanded.
2. Derive Work Plan from TASK and its coverage/dependencies/DoD. Use internal safe work IDs (W1 etc.) and map semantic document row IDs via projections. Do not independently re-plan a conflicting task list.
3. Invoke `zsspec-test <module> --auto --gen-only` to prepare applicable E2E tests in Design. If none apply, load the Skill and state no auto tests; other executable acceptance tests are still mandatory. Never defer declared test generation to Build. Review test assertions and fixtures, requirement coverage and valid failure signals. Do not treat missing services/dependencies as behavioral Red.
4. Register Work Plan. Coordinator reviews REQ/DES/TASK/CHK consistency and test meaning; write a concrete review report under evidence_paths. Record it, explicitly as AI review:

   `python scripts/aidevflow.py record-design-review --reviewer "Claude coordinator" --reason "Reviewed scope coverage and executable test assertions" --evidence specs/checklist/reports/design/review.md`

5. Freeze tests, collect valid Red for declared red_test_files, advance to Build. Review is bound to current specs, plan and all declared tests. If those change before freezing, review again. Freezing is one-time. Frozen test defects require human baseline handling, never rehashing the modified file or weakening an assertion.

## Build, test and method closeout

- Invoke apply, use Gate work-item commands, stay within declared code paths. If repair changes completed work, reopen it. Static/manual/browser workers return findings; one coordinator writes Gate state. No autonomous parallel fan-out in this profile.
- Invoke Review test Skill separately after implementation. Use `--static`, `--auto --run-only`, `--manual` as applicable, serially. Skip a passed check only if Gate reports it fresh for current candidate, not merely because CHK is unchanged.
- Run commands through run-tests. To capture command-generated artifacts:

  `python scripts/aidevflow.py run-tests --suite e2e --evidence test-results/result.json test-results/screenshot.png`

  Use predictable per-run output paths. Evidence files must exist and be non-empty after execution. Reports do not replace the actual command result. Do not run the same E2E command a second time outside Gate to collect a receipt.
- Capture `status.candidate_sha256` **before** static/manual inspection. Save report/screenshots under evidence_paths; do not change product code during inspection. Then:

  `python scripts/aidevflow.py record-check --id CHECK-static --result passed --candidate <CAPTURED_SHA> --reviewer "Claude coordinator" --reason "No blocking findings; see report" --evidence specs/checklist/reports/static/review.md`

  Manual checks also require screenshots for a pass. Records explicitly say ai_coordinator_attestation; they are not authenticated human approval, execution receipts or proof that a model actually inspected a page. The independent Verifier must assess the evidence. Never record a pass just because a report exists. Failed/blocked records remain failures; later code changes make records stale.
- Gate projects TASK/CHK states. The CHK manual label is AI-assisted. Store screenshot links, dates, findings and other provenance in immutable reports; no direct edits to frozen CHK or change-history sections. Gate events are the status-change audit trail under this profile.
- Run done as method closeout, using Gate state and fresh evidence. It returns remaining findings; it cannot mark REQ completed or demand a final verdict that does not exist yet. REQ completion is projected after independent acceptance. This avoids circular acceptance.
- Pure implementation bugs stay in Gate repair routing, with reopened work and fresh evidence. Frozen spec/plan/test changes stop for baseline handling; zsspec-change cannot silently revise them. Review failures do not authorize bypassing scope.

## Independent acceptance and recovery

- All required commands and static/manual attestations must pass for current candidate before Verify. Resolve both pending_checks and pending_attestations. Package policy, AI design review, command records, non-command records and captured files into the bundle. Missing/failed/stale/invalid evidence cannot pass.
- Give only that bundle to a fresh ordinary independent Verifier; it distinguishes actual execution, AI review and AI-assisted screenshots and compares source request against acceptance. No external ledger or model-generated prose overrides Gate.
- Pinning snapshots zsspec.yml and method instructions/templates/test references. Changes invalidate method integrity, not just an individual CHK. No in-flight profile migration. Keep historical contracts and observations; a new example does not upgrade an old run.
- Installation updates framework assets only, not vendor zsspec or the active run. New projects need the complete v1.4 skill package (including .skillhub per vendor distribution rules, excluding maintenance-only zsspec-doctor). The vendor README document version v1.18 is independent of the package version; see ZSSPEC-MANAGED.md in the framework source. Do not execute feedback or telemetry to prove installation.
