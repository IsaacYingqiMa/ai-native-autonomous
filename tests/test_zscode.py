"""Same product workflow and quality gates under the zscode runtime identity."""
from pathlib import Path

import test_discovery as discovery_tests
import test_zsspec_docs as docs_tests
from aidevflow.bridge import dispatch
from aidevflow.gate import GateError
from aidevflow.installer import install
from aidevflow.runtimes.registry import get_runtime
from aidevflow.receipts import completed_results
from aidevflow.services import design, revisions


class ZscodeDiscoveryTests(discovery_tests.DiscoveryTests):
    RUNTIME = "zscode"
    NATIVE = ".zscode"


class ZscodeDocsTests(docs_tests.CurrentDocsTests):
    RUNTIME = "zscode"
    NATIVE = ".zscode"

    def test_engineering_correction_has_real_native_review_receipts(self):
        self.bind()
        revisions.begin(self.gate, "Clarify technical wording without changing approved behavior")
        self.receipts.stop()
        for kind in ("spec", "plan"):
            target = design.prepare_review(self.gate, kind)
            from aidevflow.contexts import role_context
            assignment = role_context(self.root, "artifact_reviewer")["task_call"]
            event = {"type": "tool_call", "session_id": "native-engineering", "toolCallId": kind,
                     "input": assignment}
            adapter = get_runtime(self.RUNTIME)
            adapter.observe(self.root, event)
            value = {"schema": "aidevflow/artifact-review-v1", "task_id": self.gate.status()["task_id"],
                     "kind": kind, "input_sha256": target["sha256"], "outcome": "pass",
                     "coverage": {"ACCEPT-1": ["S1"]}, "findings": []}
            receipt = adapter.observe(self.root, {**event, "type": "tool_result", "details": {"results": [{
                "id": "review-" + kind, "agent": "aidevflow-artifact-reviewer", "exitCode": 0,
                "structuredOutput": {"status": "valid", "data": value}}]}})
            design.record_review(self.gate, receipt["result_path"])
        revisions.commit(self.gate)
        self.assertEqual(self.gate.status()["evidence_epoch"], 1)
        self.assertEqual(self.gate.status()["current_stage"], "build")

    def test_installed_host_is_separate_and_cannot_impersonate_omp(self):
        source = Path(__file__).resolve().parents[1]
        install(source, self.root, "zscode")
        self.assertFalse((self.root / ".omp/extensions/aidevflow/index.ts").exists())
        self.assertTrue((self.root / ".zscode/extensions/aidevflow/index.ts").is_file())
        with self.assertRaisesRegex(GateError, "host identity"):
            dispatch(self.root, "flow-enter", {"runtime": "omp", "text": "New task"})
        with self.assertRaisesRegex(GateError, "host identity"):
            dispatch(self.root, "flow-enter", {"text": "An old unbound client cannot enter this run"})
        state, _, _ = self.gate._load_active()
        # The actual bound runtime never changes during dispatch rejection.
        self.assertEqual(state["execution_binding"]["runtime"], "zscode")
        with self.assertRaisesRegex(GateError, "cannot migrate"):
            install(source, self.root, "omp")

    def test_zscode_receipt_cannot_downgrade_to_legacy_or_cross_runtime(self):
        adapter = get_runtime("zscode")
        event = {"type": "tool_call", "session_id": "schema-test", "toolCallId": "schema-call",
                 "input": {"agent": "aidevflow-artifact-reviewer", "task": "Review prepared spec"}}
        design.prepare_review(self.gate, "spec")
        adapter.observe(self.root, event)
        target = self.gate._load_active()[0]["artifact_review_target"]
        value = {"schema": "aidevflow/artifact-review-v1", "task_id": self.gate.status()["task_id"],
                 "kind": "spec", "input_sha256": target["sha256"], "outcome": "pass",
                 "coverage": {"ACCEPT-1": ["S1"]}, "findings": []}
        receipt = adapter.observe(self.root, {**event, "type": "tool_result", "details": {"results": [{
            "id": "schema-agent", "agent": "aidevflow-artifact-reviewer", "exitCode": 0,
            "structuredOutput": {"status": "valid", "data": value}}]}})
        for change in ({"receipt_schema": "aidevflow/invocation-v1"}, {"runtime": "omp"}):
            forged = {**receipt, **change}
            self.assertEqual(completed_results([forged], "artifact_reviewer", receipt["result_sha256"],
                             0, None, receipt["context"], "zscode"), [])
