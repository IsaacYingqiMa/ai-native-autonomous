# Evidence and frozen baseline recovery

Applies to both runtimes. Gate is the only state writer. Keep one writer active: run-tests and other mutations serialize across CLI processes and direct Gate API calls. A busy writer returns a bounded retry error; wait for the real running command rather than deleting its lock file. OS releases locks when the process exits.

## Diagnose execution evidence

Run `python scripts/aidevflow.py diagnose-evidence`. It is read-only and scans every execution, not only the latest passing check. It reports missing/changed evidence, quarantined identities, pending replacements and executions found only in historical event snapshots. Event-only records never automatically become passing evidence.

For actual missing/overwritten evidence, prepare `plan-evidence-repair --reason <actual cause>`. Review the returned plan path, SHA-256, exact affected executions and forensic snapshots. Ask for a decision only if existing user authorization does not cover that concrete quarantine and real re-execution. Apply with:

```text
python scripts/aidevflow.py apply-evidence-repair --plan <returned-path> --sha256 <returned-sha> --by <actual-operator> --utterance <actual-authorization>
```

The original ledger remains intact. Quarantined records are excluded from acceptance and their observed damaged bytes are preserved. Rerun affected checks through Gate; an older successful result cannot satisfy a pending replacement. Intact ordinary failed tests cannot be quarantined. Changed plans, changed observed bytes or changed execution history require a new plan. A damaged historical Red discovered after Design requires a reviewed baseline revision and real Red replay as below. Do not edit hashes, delete history, reset the task or fabricate Red.

## Revise a frozen baseline

Repeated freeze-tests with identical files is idempotent; it cannot accept changed bytes. For incorrect specifications/assertions, prepare a versioned revision under `specs/aidevflow-intake/<TASK_ID>/baseline-drafts/`. This draft directory is writable through normal write/edit tools during Design/Build/Verify, including needs_human; frozen live files remain protected. Do not write Gate internals. A pending transaction must be recovered before normal edits.

Read `.aidevflow/schemas/baseline-revision.schema.json`. A minimal revision.json is:

```json
{
  "schema": "aidevflow/baseline-revision-v1",
  "reason": "Specific defect and intended correction",
  "changes": [
    {
      "path": "tests/test_feature.py",
      "draft": "specs/aidevflow-intake/TASK-ID/baseline-drafts/test_feature.py"
    }
  ]
}
```

Use the actual task ID and declared paths. Optional contract_draft/work_plan_draft point to complete revised JSON files in the same draft directory. Contract revisions may adjust declared tests/support/commands/checks/specifications/acceptance/evidence directories/test policy; they cannot change runtime, method, implementation scope, task identity or repair budgets. Broadening the business request needs a separate task. Declare common execution controls such as conftest.py, pytest configuration and project test runners in test_support_files. Declare any additional helper/import dependencies yourself; automatic discovery is not a complete import graph.

1. Run `plan-baseline-revision --draft <revision.json>`. This snapshots the proposed files and anchors the proposal to current state and actual bytes. Review the old/new Contract, acceptance coverage, tests/support/specification objects and work plan. For legacy tasks, old hashes alone cannot reconstruct lost historical bytes; do not claim those were recovered.
2. Save a substantive review report in baseline-drafts. Explain why the tests express the intended behavior and disclose any weakened criteria or exceptions. Obtain actual authorization for the concrete revision when it is not already authorized. Never invent a user's words.
3. Run `apply-baseline-revision --proposal <returned-path> --sha256 <returned-sha> --by <operator> --utterance <actual-authorization> --reviewer <reviewer> --review <report-path>`.
4. Gate keeps prior baselines, Contract, proposal and execution history; creates a new baseline and epoch; reopens Design and work items; invalidates current checks/verdicts/waivers. Exhausted repair budgets remain exhausted and require explicit resume-run after the baseline is consistent. Recheck the method/design review and freeze state through status; collect new Red before Build.
5. If implementation already exists, run `run-tests --suite red --target <declared-test> --against-base`. Gate archives the original Git base_commit into a separate workspace, overlays the revised frozen tests/support/specifications, and runs the real Contract argv there. It never changes the live implementation or manufactures a failing result. Archive links/submodules and embedded absolute live-project paths are rejected. Dependencies must already be available through the declared interpreter/runner; if the baseline commit cannot be replayed, correct the portable setup through another reviewed revision or explain the concrete blocker.
6. Complete reopened work, run fresh Green/required checks, prepare a new bundle and obtain an independent verdict. Old Red cannot authorize a new baseline. Routine implementation defects still use normal failure routing.

## Interrupted baseline transaction

Gate journals before/after objects before replacing any live file. During an interrupted revision it blocks normal commands and tells you to use:

```text
python scripts/aidevflow.py recover-baseline-transaction --mode finish --by <operator> --utterance <actual-authorization>
python scripts/aidevflow.py recover-baseline-transaction --mode rollback --by <operator> --utterance <actual-authorization>
```

Choose one operation. Both may safely resume after interruption. A target differing from both journal images is a conflict: preserve or review it before explicitly passing --accept-conflicts; Gate snapshots those bytes before replacing them. Recovery never recursively deletes or resets a checkout. Do not use rebuild to jump over a pending transaction. Rollback here only applies while the transaction is pending; after a completed revision use another reviewed revision.

## Skips and acceptance exceptions

Exit code zero is insufficient when required cases are skipped, xfailed or deselected. Gate automatically captures JUnit for direct pytest argv. Remove custom --junitxml flags and declare selection/plugins in frozen argv/config instead of PYTEST_ADDOPTS/PYTEST_PLUGINS. Other runners can declare test_policy.report_format as junit or summary and write their actual case report to AIDEVFLOW_TEST_REPORT; see `.aidevflow/schemas/test-summary.schema.json`. Reports are snapshotted into immutable execution artifacts. For custom JUnit the runner must write to that environment path itself.

Optional `test_policy.required_cases` maps `suite` or `suite:target` to stable exact case IDs and requires report_format. Missing required cases, failed cases, malformed reports and unidentified deselections block acceptance. Legacy/custom runners without reports retain exit-code behavior with visible skip-summary detection; hidden omissions cannot be inferred from arbitrary output. Require structured reports and expected case IDs when completeness matters.

Prefer correcting an incorrect test through baseline revision. For a deliberate limitation, record exact cases and affected acceptance IDs:

```text
python scripts/aidevflow.py record-test-waiver --suite green --target <declared-test> --case <exact-case-id> --acceptance <acceptance-id> --reason <specific-limitation> --by <operator> --utterance <actual-authorization>
```

This record alone cannot weaken acceptance. The pinned Contract must explicitly set `test_policy.allow_approved_waivers: true`; adding that policy after freeze requires a reviewed baseline revision. Rerun the suite. `passed` remains false; a completely covered approved omission yields `accepted_with_exceptions` and check status `passed_with_waivers`. An independent Verifier must cite the current bundled waiver and report affected items as `waived` with residual risks. Final status becomes `delivery_ready_with_exceptions`, never ordinary delivery_ready. Missing/failed cases cannot be waived into passes. Waivers expire at baseline revision.

Authorization fields record operator assertions; they are not authenticated human identities. These controls prevent routine workflow corruption, not hostile local filesystem modification. Preserve ignored runtime data and immutable objects in backups. Deleted/corrupted journals, pinned metadata or forensic snapshots require restoring trustworthy backups and diagnosis; do not silently rehash damaged objects to pass.
