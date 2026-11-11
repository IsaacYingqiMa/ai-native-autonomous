# Authorized engineering work

Bug fixes, test-command repairs, plan corrections and demonstrably incorrect frozen assertions are authorized by the approved requirements. Do not ask the user to reapprove them or invent an operator utterance. Material changes to requirements, acceptance goals or business semantics need a real business decision.

For an incorrect frozen test, use begin-engineering-revision, edit only returned drafts, independently review spec/plan and the technical Contract, then commit-engineering-revision. Preserve acceptance strength and explain the inconsistency against approved examples. Do not use the legacy operator-approved baseline path for routine current-method corrections. After correction, replay current frozen assertions with run-tests --suite red --target <test> --against-base. The pinned initial worktree includes uncommitted previous deliveries. Old Red for different assertions is insufficient.

Gate recognizes top-level Sx-n, Pn, Tn and Rn cards; other checkboxes are errors, never silently omitted. Keep testing/setup cards and the full regression replay. Never change product behavior merely to help a test script run.

For a legacy ordinary-repair budget stop use recover-routine; the original flow entry also recovers it. Real requirement decisions, user pauses and integrity incidents stay blocked. Ordinary repair attempts do not share one small global allowance. Repeating the same failing candidate/subjects without progress stops automation and reports a technical cause; it does not authorize weakening checks or imply delivery.

# Runtime checks

Use check-context --id <check> to obtain all approved scenarios and their expected outcomes. Execute the actual triggers, then verify every clause and interacting state. Record an observations JSON under evidence_paths:

    {"schema":"aidevflow/check-observations-v1","check_id":"<check>","candidate_sha256":"<current>","observations":[{"id":"<approved scenario>","result":"passed","observed":"<concrete measured result>","evidence":["<captured screenshot/log>"]}]}

Pass it to record-check --observations <path> with actual evidence paths. Unverified/failed scenarios cannot pass. Refresh/new store instances do not prove process close/reopen; a static screenshot does not prove a timer, network behavior or non-overlap in other states. Prefer replayable browser assertions, capture screenshots and retain scripts/logs with the report. AI observations remain AI attestations, not human approval or native test execution.

# Acceptance repair

After a failed verdict, route --type review_finding reopens affected cards and dependants. Inspect reopened_work_items/status before starting; do not reopen them again. Repair the whole failed criterion and specification, not merely the last symptom. Exercise interacting features and regression scenarios before a new independent evaluation.

Read candidate-binding.json: physical object hashes and normalized candidate hashes have different purposes. Its machine-checked mappings establish identity. Use verification-summary.json for real test counts. Cite short E IDs from BUNDLE.json instead of copying hashes. A rejected evidence citation can use the bounded correction assignment for the same frozen bundle; preserve the independent conclusions and correct citations rather than repeating the audit solely to retype paths.
