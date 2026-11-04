# Current zsspec under AIDevFlow (zsspec-managed-v2)

Use this profile for the current package with zsspec-plan and zsspec_check.py. For an existing zsspec-managed-v1 Contract follow zsspec-managed-legacy.md instead. Never upgrade a pinned task. AIDevFlow owns Intent → Design → Build → Verify → delivery_ready. zsspec provides the engineering method; it does not own another autonomous loop.

## Authorization and preparation

The user approves the complete Intent/PRD before delivery. Ordinary design and test-code review is delegated to the AI coordinator. Escalate material scope, interface, acceptance, permission or risk changes. Final business acceptance and release belong to the actual user. Never write an approval on their behalf.

Use the configured project-local physical skills_root (.omp/skills or .claude/skills). Before a new Contract, run `python scripts/aidevflow.py method-inspect`: it discovers the current built-in adapter's documented capabilities and caches a content-addressed description. Prefix zsspec-* is discovery only, never authorization to execute every discovered Skill. Unknown protocols require adapter review. Installation does not copy vendor Skills; prepare the complete current package separately. Do not run feedback/usage/identity scripts or maintenance zsspec-doctor.

Project setup, dependencies, browser installation and specs/zsspec.yml belong before Contract registration. Simple two-level scalar YAML only; strict Gate, serial execution and Design-frozen tests remain effective. A disabled test type cannot erase an approved acceptance requirement. Missing services and inaccessible evidence remain blockers.

## Contract and pinned source

For new current-package tasks use method.profile=zsspec-managed-v2, method.name=zsspec, and exact stages:
- design: zsspec-req, zsspec-plan, zsspec-test
- build: zsspec-apply
- review: zsspec-test, zsspec-done

Declare method.modules as native module names. Preserve every approved canonical acceptance ID in acceptance_items; map each to one or more native AC IDs in method.acceptance_map. This map must exactly cover native ACs. Do not replace canonical IDs with zsspec IDs. Several ACs may belong to one REQ; they do not require duplicate REQ status rows.

Declare concrete filenames in design_artifacts/test_files/test_support_files, executable argv tests, required_checks covering all acceptance IDs (including static review), and dedicated evidence_paths. See the current contract/work-plan examples. The Contract pins the adapter API, resolved method, all used method instructions/references/scripts and configuration. OMP redirects managed method reads to their task-local source snapshot even if the installed package later changes. Claude native Skill loading retains a conservative installed-source drift block; do not assume OMP-style hot-update isolation on Claude. Runtime discovery never replaces a running plan.

## Context for every Skill load

"AIDevFlow manages this invocation. Consume the approved Intent/PRD and pinned Contract; do not restart clarification. Review ordinary artifacts as AI, never claim human confirmation. Execute applicable engineering work serially. Only the scripted adapter may synchronize mapped document statuses through native commands; do not call zsspec_check.py on live specs. All executable checks go through Gate. Tests are generated/reviewed in Design and frozen; never auto-repair frozen tests/specifications. Return findings; escalate material changes. Feedback/usage scripts are not acceptance evidence."

## Design

1. Read req and materialize the approved requirements. Read plan and its des/task/chk stage resources; generate DES, TASK and CHK in upstream order. Use current templates, exact module paths and semantic IDs. The Skill's normal human confirmation is fulfilled for ordinary design by the authorized AI review, not by inventing human responses.
2. Prepare all executable acceptance tests, fixtures and browser plans in Design. Use test --auto --gen-only where applicable. All declared Green tests must exist; red_test_files contains only the new behavior subset. Missing runner/service is not valid behavioral Red.
3. Derive the Work Plan from TASK. Register internal W1-style IDs, dependency/DoD and acceptance coverage; projections map every native TASK row to a work_item and every native CHK row to a required check. Canonical/native coverage must agree. Map each native REQ overview row once with source_kind=acceptance to one canonical ID whose native AC belongs to that row. This permits status-only close; full canonical coverage uses acceptance_map, so multiple canonical ACs may share the REQ row. Native close owns REQ completion after user acceptance.
4. Write actual AI review evidence under evidence_paths. Finalize layers with `method-run baseline --module <name> --scope req|des|task|chk --evidence <review.md>`, one layer at a time in that order. This is method document finalization, not changing a frozen AIDevFlow baseline. The adapter executes the real pinned native script on a staging copy, validates allowed edits, then commits with a recovery journal. Never call the native writer directly.
5. Run `method-run verify --module <name>` and `method-run gate --module <name>` for each module. Legacy/missing assertions, draft/stale headers and incomplete AC/TASK/CHK mapping cannot pass. Register the plan before native gate. Native verify/report are dry-run to preserve live state; their stdout is captured as evidence.
6. Record the final AI review with `record-design-review --reviewer "AI coordinator" --reason <actual findings> --evidence <review.md>`. Then freeze-tests, run valid Red, advance --to build. Any changed reviewed subject before freeze requires fresh review. Native gate must pass for the exact final specs before Build.

## Build and verification

Use apply and Gate work-item commands. After work-item, test or record-check changes call `sync-projections`. Gate facts are authoritative; the adapter uses native set, never direct table rewriting. Native TASK states are monotonic: reopened repair remains active in Gate and is reported as a deferred native transition; native 已完成 never overrides Gate's incomplete work. Repair still must complete and collect fresh evidence.

Load test for applicable static/auto/manual review, serially. Run executable checks once through run-tests with declared evidence output paths. Static/manual attestations use record-check with the candidate captured before inspection, actual reports and screenshots for visual acceptance. A script pass is not visual inspection. Missing/failed/stale checks cannot pass. Read status pending_checks/pending_attestations and complete all required work.

Read done for closeout findings. Its direct script close is intentionally deferred: do not mark REQ complete or claim final human acceptance before independent verification. Method closeout findings are evidence, not a second completion authority. Use prepare-verifier, a fresh bundle-only independent verifier, record-verdict and advance --to delivery_ready. At delivery_ready autonomy has delivered a reviewable result; the user then runs `/aidevflow-accept <module>` in OMP (or the operator CLI method-accept) after actual business acceptance. That invokes native close through the adapter. The generated native index is retained as operation evidence rather than overwriting a shared live specs/index.md. Release remains a separate human authorization.

## Recovery and boundaries

A single coordinator writes Gate state. Every native operation binds state revision, input hashes, script hash and review evidence; concurrent edits reject the result. Subprocess/model/network waits never hold the Gate writer lock. A pending method transaction blocks normal writers/rebuild. Run method-recover --mode finish for a validated interrupted commit; use --mode rollback for incomplete staging or an uncommitted result. Recovery refuses conflicting later edits and is idempotent. Do not edit journals/state or manufacture receipts.

Pure implementation bugs use Gate repair with reopened work and new evidence. Frozen specs/tests/plan changes require authorized baseline handling; zsspec-change cannot silently expand them. A baseline-revision proposal must contain already-finalized native specs with valid mappings; the pinned native gate checks the staged proposal before it can be approved/frozen. Revised Work Plans retain the method profile. Never auto-migrate legacy tasks, activate unknown packages, or weaken approved criteria just to keep running. Protocol checks and fixture tests do not promise success for every complex business project.
