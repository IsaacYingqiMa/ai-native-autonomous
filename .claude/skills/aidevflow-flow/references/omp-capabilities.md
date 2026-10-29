# OMP managed method capabilities

Read method instructions with `read skill://zsspec-<name>`, then the applicable
references. `/skill:<name>` is the user's interactive entry, not a shell command.
In an active OMP task the extension routes these reads to the pinned physical
source and adds managed constraints. The same applies to exact project method
paths. Use literal template paths; square brackets in vendor directory names are
not glob patterns. Never load maintenance-only zsspec-doctor for business delivery.

The mapping applies under AIDevFlow's approved task policy. Vendor files remain
unchanged. It is not a claim that the standalone vendor skill supports this host.

## Static review

Perform checklist compliance and correctness, contract, security, regression and
acceptance risk review serially in the coordinator. Cluster findings, verify high
risk findings against actual code/spec evidence, and produce the requested report.
Do not dispatch vendor-named orchestrator/reviewer/finding-verifier agents. These
are review responsibilities, not installed OMP agent identities. Register static
results as AI coordinator attestations, never as independent Gate acceptance.

## Browser execution

The managed equivalent of the vendor's chrome-devtools-mcp mechanics is an isolated
Python Playwright Chromium browser. It supports local HTTP(S), one declared origin,
goto/click/fill/press/visible/hidden/text steps, and captures a screenshot after each
step. The runner blocks cross-origin HTTP requests, service workers, WebSockets and
downloads. It uses no user browser profile and exposes no arbitrary evaluation.
This is an application test boundary, not an OS sandbox or proof of visual quality.

Prepare Python `playwright` and its Chromium installation before intake, using the
same interpreter configured in project.json. CLI doctor reports package presence;
it does not claim Chromium works. Run a real local-service smoke before pinning a
business contract. A missing driver/browser/service is an environment blocker.

Declare the JSON plan in test_support_files (or test_files when appropriate), the
runner argv in test_commands, and output under evidence_paths. Freeze the plan in
Design. The runner checks its frozen hash and Build/Verify phase before writing.
Do not use a missing service as behavioral Red; use an appropriate feature test.

Example tests/browser-plan.json:

```json
{
  "schema": "aidevflow/browser-plan-v1",
  "base_url": "http://127.0.0.1:8765",
  "checks": [{
    "id": "dashboard-refresh",
    "steps": [
      {"action": "goto", "url": "/"},
      {"action": "text", "selector": "h1", "text": "AIDevFlow"},
      {"action": "click", "selector": "button[data-action=refresh]"},
      {"action": "visible", "selector": "[data-testid=last-refresh]"}
    ]
  }]
}
```

Use selectors from the approved design and freeze them with the tests. The example
is illustrative, not a generated passing test for an unknown application.

test_commands.browser should invoke the configured interpreter with:

```text
scripts/aidevflow_browser.py --plan tests/browser-plan.json --output test-results/browser
```

Arrange actual test-service startup in the declared frozen test harness; stop only
the service it owns. All fixture creation and application actions use isolated test
data. Do not mutate a real Gate run to manufacture dashboard scenarios.

Execute via Gate, explicitly capturing report and screenshots:

```text
python scripts/aidevflow.py run-tests --suite browser --evidence test-results/browser/report.json test-results/browser/dashboard-refresh-01.png test-results/browser/dashboard-refresh-02.png test-results/browser/dashboard-refresh-03.png test-results/browser/dashboard-refresh-04.png
```

report.json marks source=playwright_execution. Nonzero exit or a failed check cannot
pass. Check screenshots with the image-capable read tool. If the current model/tool
cannot inspect images, keep visual checks blocked or request actual human review;
do not claim screenshots were inspected. For manual AI-assisted checks, capture the
current candidate before checking, write the actual findings and image references,
then record-check with reviewer, reason, fresh candidate and screenshot evidence.
The runner never records a manual pass or human approval. Subjective preferences
and inaccessible external systems remain blocked. Native computer/eval and unknown
MCP tools stay blocked; they are not a substitute for this declared test route.

## Other host differences

Vendor allowed-tools is metadata, not permission to execute or send telemetry.
Do not log identities, upload usage or send feedback without actual authorization.
Gate owns mapped status cells. Frozen test/spec defects require baseline handling.
Use Gate check freshness, not an unchanged CHK, to decide whether results can be reused.
Ordinary artifacts may be AI-reviewed within the authorized scope; major changes
still require the user. Never label an AI decision as human confirmation.

OMP may compress skill descriptions into a session snapshot. This is not a separate
method definition; read SKILL.md and references. Reload/restart after an authorized
method upgrade, and never edit the global description database as a setup step.
