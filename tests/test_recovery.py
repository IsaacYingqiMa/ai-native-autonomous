from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from unittest import TestCase

from test_gate import GateFixture
from test_managed import ManagedFixture
from aidevflow.gate import Gate, GateError, VERDICT_SCHEMA, read_json, write_json_new
from aidevflow.hooks import guard
from aidevflow import baselines
from aidevflow.test_execution import summary


TARGET = "tests/test_feature.py"


class RecoveryTests(GateFixture):
    def build(self):
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", TARGET)
        self.gate.advance("build")

    def draft(self, contract=None):
        base = "specs/aidevflow-intake/TEST-1/baseline-drafts/"
        path = self.root / (base + "test.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# revised acceptance baseline\n", encoding="utf-8")
        (self.root / (base + "review.md")).write_text("Reviewed revised assertions against REQ-1; implementation scope unchanged.", encoding="utf-8")
        value = {"schema": "aidevflow/baseline-revision-v1", "reason": "Correct an approved acceptance baseline defect",
                 "changes": [{"path": TARGET, "draft": base + "test.py"}]}
        if contract:
            self._write_json(base + "contract.json", contract)
            value["contract_draft"] = base + "contract.json"
        self._write_json(base + "revision.json", value)
        return base, self.gate.plan_baseline_revision(base + "revision.json")

    def apply(self, base, proposal):
        return self.gate.apply_baseline_revision(proposal["path"], proposal["sha256"], "fixture user", "Approve this exact reviewed revision", "fixture reviewer", base + "review.md")

    def test_freeze_is_idempotent_and_preserves_baseline(self):
        self.build()
        before = self.gate._load_active()[0]
        self.assertEqual(self.gate.freeze_tests([]), before["frozen_tests"])
        after = self.gate._load_active()[0]
        self.assertEqual(after["baseline_ref"], before["baseline_ref"])
        self.assertEqual(after["revision"], before["revision"])

    def test_direct_gate_threads_and_cli_processes_do_not_lose_executions(self):
        self.build()
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            records = list(pool.map(lambda _: Gate(self.root).run_tests("green", TARGET), range(3)))
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        processes = [subprocess.Popen([sys.executable, "-B", "-m", "aidevflow.cli", "--root", str(self.root), "run-tests", "--suite", "green", "--target", TARGET],
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
            records.append(json.loads(stdout))
        state = self.gate._load_active()[0]
        self.assertEqual(len(state["test_runs"]), 7)
        self.assertEqual(len({record["evidence_path"] for record in records}), 6)
        self.assertTrue(self.gate.diagnose_evidence()["ok"])
        for line in self.gate.events_path.read_text(encoding="utf-8").splitlines():
            json.loads(line)

    def test_five_damaged_records_need_audited_quarantine_and_real_replacement(self):
        self.build()
        records = [self.gate.run_tests("green", TARGET) for _ in range(6)]
        self.gate.advance("verify")
        for item in records[-5:]:
            (self.root / item["evidence_path"]).write_text("overwritten legacy result", encoding="utf-8")
        state_before = (self.root / ".aidevflow/runs/TEST-1/run-state.json").read_bytes()
        events_before = self.gate.events_path.read_bytes()
        self.assertEqual(len(self.gate.diagnose_evidence()["anomalies"]), 5)
        self.assertEqual(state_before, (self.root / ".aidevflow/runs/TEST-1/run-state.json").read_bytes())
        self.assertEqual(events_before, self.gate.events_path.read_bytes())
        with self.assertRaisesRegex(GateError, "audited evidence repair"):
            self.gate.prepare_verifier()
        plan = self.gate.plan_evidence_repair("Legacy concurrent writer overwrote five records")
        with self.assertRaisesRegex(GateError, "actual authorization"):
            self.gate.apply_evidence_repair(plan["path"], plan["sha256"], "", "")
        self.gate.apply_evidence_repair(plan["path"], plan["sha256"], "user", "Approve quarantine and real re-execution")
        self.assertEqual(self.gate.status()["checks"][0]["status"], "invalid")
        with self.assertRaisesRegex(GateError, "fresh executions"):
            self.gate.prepare_verifier()
        replacement = self.gate.run_tests("green", TARGET)
        self.assertEqual(len(replacement["replaces_incidents"]), 5)
        self.assertEqual(len(self.gate._load_active()[0]["test_runs"]), 8)
        bundle = self.gate.prepare_verifier()
        manifest = read_json(self.root / bundle["path"] / "BUNDLE.json")
        sources = [entry.get("source_path", "") for entry in manifest["files"]]
        self.assertTrue(any("/evidence-repairs/" in path for path in sources))
        self.assertTrue(any(path.endswith("execution-ledger.json") for path in sources))
        self.assertTrue(self.gate.diagnose_evidence()["ok"])

    def test_intact_failures_cannot_be_quarantined_and_stale_plan_is_rejected(self):
        self.build()
        item = self.gate.run_tests("green", TARGET)
        with self.assertRaisesRegex(GateError, "ordinary test failures"):
            self.gate.plan_evidence_repair("Hide failure")
        (self.root / item["evidence_path"]).unlink()
        plan = self.gate.plan_evidence_repair("Missing result")
        self.gate.run_tests("green", TARGET)
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.apply_evidence_repair(plan["path"], plan["sha256"], "user", "Approved")

    def test_immutable_publish_does_not_replace_existing_result(self):
        path = self.root / "immutable.json"
        write_json_new(path, {"original": True})
        with self.assertRaises(FileExistsError):
            write_json_new(path, {"replaced": True})
        self.assertEqual(read_json(path), {"original": True})

    def test_baseline_revision_preserves_history_and_requires_new_red(self):
        self.build()
        old = self.gate._load_active()[0]
        base, proposal = self.draft()
        self.apply(base, proposal)
        current = self.gate._load_active()[0]
        self.assertNotEqual(old["baseline_ref"], current["baseline_ref"])
        self.assertEqual(current["current_stage"], "design")
        self.assertEqual(current["test_runs"], old["test_runs"])
        with self.assertRaisesRegex(GateError, "Red"):
            self.gate.advance("build")
        self.gate.run_tests("red", TARGET)
        self.gate.advance("build")
        self.gate.run_tests("green", TARGET)
        self.gate.advance("verify")
        self.gate.prepare_verifier()
        self.assertEqual(self.apply(base, proposal)["baseline"]["id"], current["baseline_ref"]["id"])

    def test_revision_stale_scope_and_blank_authorization_rejected(self):
        self.build()
        base, proposal = self.draft()
        with self.assertRaisesRegex(GateError, "actual operator authorization"):
            self.gate.apply_baseline_revision(proposal["path"], proposal["sha256"], "", "", "AI", base + "review.md")
        self.gate.run_tests("green", TARGET)
        with self.assertRaisesRegex(GateError, "stale"):
            self.apply(base, proposal)
        revised = copy.deepcopy(self.contract)
        revised["code_paths"] = ["src/**", "other/**"]
        with self.assertRaisesRegex(GateError, "product scope"):
            self.draft(revised)

    def test_frozen_test_change_has_a_usable_revision_route(self):
        self.build()
        (self.root / TARGET).write_text("# accidental edit\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.integrity()
        self.assertEqual(guard({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "specs/aidevflow-intake/TEST-1/baseline-drafts/corrected.py")}}, self.root), 0)
        base, proposal = self.draft()
        self.apply(base, proposal)
        self.assertEqual(self.gate._load_active()[0]["status"], "active")
        self.assertFalse(self.gate.integrity())

    def test_interrupted_revision_can_finish_and_rollback_without_rebuild(self):
        self.build()
        original = self.gate._load_active()[0]["baseline_ref"]
        for mode in ("rollback", "finish"):
            base, proposal = self.draft()
            real = baselines._bytes_atomic
            count = 0
            def interrupt(path, content):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError("simulated power loss")
                real(path, content)
            with patch("aidevflow.baselines._bytes_atomic", side_effect=interrupt):
                with self.assertRaisesRegex(OSError, "power loss"):
                    self.apply(base, proposal)
            with self.assertRaisesRegex(GateError, "recover-baseline-transaction"):
                self.gate.status()
            with self.assertRaisesRegex(GateError, "recover-baseline-transaction"):
                self.gate.rebuild("TEST-1")
            self.gate.recover_baseline_transaction(mode, "user", "Recover reviewed transaction")
            self.assertFalse(self.gate.integrity())
            state = self.gate._load_active()[0]
            self.assertEqual(state["baseline_ref"] == original, mode == "rollback")

    def test_conflicting_revision_recovery_preserves_bytes_before_overwrite(self):
        self.build()
        base, proposal = self.draft()
        with patch("aidevflow.baselines._bytes_atomic", side_effect=OSError("interrupt")):
            with self.assertRaises(OSError):
                self.apply(base, proposal)
        (self.root / TARGET).write_text("conflicting local edit", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "Revision conflict"):
            self.gate.recover_baseline_transaction("finish", "user", "Finish")
        self.gate.recover_baseline_transaction("finish", "user", "Preserve conflict and finish", True)
        objects = (self.root / ".aidevflow/runs/TEST-1/baseline-objects").iterdir()
        self.assertTrue(any(path.read_bytes() == b"conflicting local edit" for path in objects))
        self.assertFalse(self.gate.integrity())

    def test_unfrozen_conftest_requires_contract_revision(self):
        self._reach_design()
        (self.root / "tests/conftest.py").write_text("# shared control\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "Undeclared test execution controls"):
            self.gate.freeze_tests([])
        revised = copy.deepcopy(self.contract)
        revised["test_support_files"] = ["tests/conftest.py"]
        base, proposal = self.draft(revised)
        self.apply(base, proposal)
        self.assertIn("tests/conftest.py", self.gate.freeze_tests([]))

    def test_declared_project_runner_in_scripts_can_be_revised_without_touching_framework(self):
        self._reach_design()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/test_runner.py").write_text("# project test runner\n", encoding="utf-8")
        revised = copy.deepcopy(self.contract)
        revised["test_support_files"] = ["scripts/test_runner.py"]
        base, proposal = self.draft(revised)
        self.apply(base, proposal)
        self.assertIn("scripts/test_runner.py", self.gate.freeze_tests([]))
        self.assertFalse(self.gate.integrity())


class IsolatedRedTests(GateFixture):
    contract_overrides = {"test_commands": {
        "red": [sys.executable, "-c", "import runpy; assert runpy.run_path('src/sample.py')['value']() == 2, 'AssertionError: expected 2'", "{target}"],
        "green": [sys.executable, "-c", "import runpy; assert runpy.run_path('src/sample.py')['value']() == 2", "{target}"]}}

    def test_replay_uses_original_implementation_not_current_passing_code(self):
        self._reach_design()
        self.gate.freeze_tests([])
        self.assertTrue(self.gate.run_tests("red", TARGET)["valid_red"])
        self.gate.advance("build")
        (self.root / "src/sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        base, proposal = RecoveryTests.draft(self)
        RecoveryTests.apply(self, base, proposal)
        self.assertFalse(self.gate.run_tests("red", TARGET)["valid_red"])
        replay = self.gate.run_tests("red", TARGET, against_base=True)
        self.assertTrue(replay["valid_red"], replay)
        self.assertEqual(replay["replay_base_commit"], self.gate._load_active()[0]["base_commit"])
        self.gate.advance("build")
        self.assertTrue(self.gate.run_tests("green", TARGET)["passed"])


class SkipTests(GateFixture):
    contract_overrides = {"test_commands": {
        "red": [sys.executable, "-c", "print('FAIL: assertion expected behavior'); raise SystemExit(1)", "{target}"],
        "green": [sys.executable, "-m", "pytest", "-q", "{target}"]}}

    def setUp(self):
        super().setUp()
        (self.root / TARGET).write_text("import pytest\ndef test_pass():\n    assert True\ndef test_exception():\n    pytest.skip('pending approved upstream correction')\n", encoding="utf-8")

    def build(self):
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", TARGET)
        self.gate.advance("build")

    def test_real_pytest_skip_is_not_a_green_pass(self):
        self.build()
        record = self.gate.run_tests("green", TARGET)
        self.assertEqual(record["exit_code"], 0, record)
        self.assertFalse(record["passed"])
        self.assertFalse(record["coverage"]["ok"])
        case = next(case["id"] for case in record["coverage"]["cases"] if case["status"] == "skipped")
        self.gate.record_test_waiver("green", TARGET, [case], ["REQ-1"], "Known acceptance exception", "user", "Approve specific exception")
        self.assertFalse(self.gate.run_tests("green", TARGET)["accepted_with_exceptions"])
        with self.assertRaisesRegex(GateError, "Fresh passing"):
            self.gate.advance("verify")


class AllowedSkipTests(SkipTests):
    contract_overrides = copy.deepcopy(SkipTests.contract_overrides)
    contract_overrides["test_policy"] = {"allow_approved_waivers": True}

    # Override inherited test since this fixture explicitly permits waivers.
    def test_real_pytest_skip_is_not_a_green_pass(self):
        self.build()
        record = self.gate.run_tests("green", TARGET)
        case = next(case["id"] for case in record["coverage"]["cases"] if case["status"] == "skipped")
        self.gate.record_test_waiver("green", TARGET, [case], ["REQ-1"], "Approved limitation", "user", "Approve this exact exception")
        record = self.gate.run_tests("green", TARGET)
        self.assertFalse(record["passed"])
        self.assertTrue(record["accepted_with_exceptions"])
        self.assertEqual(self.gate.status()["checks"][0]["status"], "passed_with_waivers")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        manifest = read_json(self.root / bundle["path"] / "BUNDLE.json")
        waiver = next(entry["path"] for entry in manifest["files"] if "/test-waivers/" in entry.get("source_path", ""))
        verdict = {"schema": VERDICT_SCHEMA, "task_id": "TEST-1", "spec_version": bundle["spec_version"], "bundle_sha256": bundle["sha256"],
                   "items": [{"id": "REQ-1", "verdict": "pass", "evidence": [waiver], "reason": "Approved exception"}], "risks": ["One required case remains unexecuted"]}
        self._write_json("verdict.json", verdict)
        with self.assertRaisesRegex(GateError, "report waived"):
            self.gate.record_verdict("verdict.json")
        verdict["items"][0]["verdict"] = "waived"
        self._write_json("verdict.json", verdict)
        self.gate.record_verdict("verdict.json")
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready_with_exceptions")


class ManagedRevisionTests(ManagedFixture):
    def test_revision_keeps_managed_review_work_plan_and_projections_consistent(self):
        self.build()
        task = self.gate._load_active()[0]["task_id"]
        base = f"specs/aidevflow-intake/{task}/baseline-drafts/"
        (self.root / base).mkdir(parents=True)
        (self.root / (base + "test.py")).write_text("# reviewed revised test\n", encoding="utf-8")
        (self.root / (base + "review.md")).write_text("Inspected criteria, TASK and frozen support controls", encoding="utf-8")
        self._write_json(base + "revision.json", {"schema": "aidevflow/baseline-revision-v1", "reason": "Approved baseline correction", "changes": [{"path": TARGET, "draft": base + "test.py"}]})
        proposal = self.gate.plan_baseline_revision(base + "revision.json")
        self.gate.apply_baseline_revision(proposal["path"], proposal["sha256"], "user", "Approve proposal", "AI reviewer", base + "review.md")
        self.gate.freeze_tests([])
        self.gate.run_tests("red", TARGET)
        self.gate.advance("build")
        self.assertEqual(self.gate._load_active()[0]["work_items"][0]["status"], "not_started")
        self.gate.start_work_item("W1")
        self.gate.complete_work_item("W1", ["src/sample.py"])
        self.assertFalse(self.gate.integrity())


class RequiredCaseTests(GateFixture):
    contract_overrides = {"test_policy": {"report_format": "summary", "required_cases": {"green": ["present", "missing"]}, "allow_approved_waivers": True},
        "test_commands": {
            "red": [sys.executable, "-c", "print('FAIL: assertion expected behavior'); raise SystemExit(1)", "{target}"],
            "green": [sys.executable, "-c", "import json,os; from pathlib import Path; Path(os.environ['AIDEVFLOW_TEST_REPORT']).write_text(json.dumps({'schema':'aidevflow/test-summary-v1','cases':[{'id':'present','status':'passed'}]}))", "{target}"]}}

    def test_omitted_required_case_cannot_be_waived_into_a_pass(self):
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", TARGET)
        self.gate.advance("build")
        self.gate.record_test_waiver("green", TARGET, ["missing"], ["REQ-1"], "Requested exception", "user", "Approve")
        result = self.gate.run_tests("green", TARGET)
        self.assertEqual(result["coverage"]["missing_cases"], ["missing"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["accepted_with_exceptions"])
        with self.assertRaisesRegex(GateError, "Fresh passing"):
            self.gate.advance("verify")


class ReportValidationTests(TestCase):
    def test_invalid_empty_and_unidentified_deselection_reports_fail_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test-tmp") as directory:
            path = Path(directory) / "report.json"
            report = {"path": str(path), "format": "summary"}
            for raw in (b"[]", b"\xff", b'{"schema":"aidevflow/test-summary-v1","cases":[]}'):
                path.write_bytes(raw)
                self.assertFalse(summary(report, "")["ok"])
            path.write_text(json.dumps({"schema": "aidevflow/test-summary-v1", "cases": [{"id": "case", "status": "passed"}]}), encoding="utf-8")
            self.assertFalse(summary(report, "1 passed, 3 deselected")["ok"])
