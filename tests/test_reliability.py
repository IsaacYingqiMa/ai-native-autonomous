from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

from test_gate import GateFixture
from aidevflow.agents import capture_event
from aidevflow.gate import GateError, VERDICT_SCHEMA, validate_contract, validate_work_plan
from aidevflow.hooks import guard, stop_check
from aidevflow.installer import install


class ReliabilityTests(GateFixture):
    def build(self):
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")

    def green(self):
        return self.gate.run_tests("green", "tests/test_feature.py")

    def verdict(self):
        bundle = self.gate.prepare_verifier()
        value = {"schema": VERDICT_SCHEMA, "task_id": "TEST-1", "spec_version": bundle["spec_version"], "bundle_sha256": bundle["sha256"], "items": [{"id": "REQ-1", "verdict": "pass", "evidence": ["diff.patch"], "reason": "Test fixture verdict"}], "risks": []}
        self._write_json("verdict.json", value)
        return value

    def test_changed_tracked_code_invalidates_green(self):
        self.build()
        self.green()
        (self.root / "src/sample.py").write_text("# changed after green\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.advance("verify")

    def test_windows_target_path_uses_declared_test_identity(self):
        self.build()
        result = self.gate.run_tests("green", "tests\\test_feature.py")
        self.assertEqual(result["target"], "tests/test_feature.py")
        self.gate.advance("verify")

    def test_new_untracked_code_invalidates_green(self):
        self.build()
        self.green()
        (self.root / "src/new.py").write_text("# new implementation\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.advance("verify")

    def test_new_ignored_product_file_also_invalidates_green(self):
        with (self.root / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("src/ignored.py\n")
        self.build()
        self.green()
        (self.root / "src/ignored.py").write_text("# ignored implementation\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.advance("verify")

    def test_latest_failure_cannot_reuse_historical_green(self):
        self.build()
        self.green()
        original = subprocess.run
        def run(command, **kwargs):
            return original(command, **kwargs) if command[0] == "git" else subprocess.CompletedProcess(command, 1, "FAIL: behavior", "")
        with patch("aidevflow.gate.subprocess.run", side_effect=run):
            self.assertFalse(self.green()["passed"])
        with self.assertRaisesRegex(GateError, "failed"):
            self.gate.advance("verify")

    def test_test_that_mutates_candidate_is_not_a_pass(self):
        self.build()
        original = subprocess.run
        def run(command, **kwargs):
            if command[0] == "git":
                return original(command, **kwargs)
            (self.root / "src/sample.py").write_text("# mutation during test\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "passed", "")
        with patch("aidevflow.gate.subprocess.run", side_effect=run):
            result = self.green()
        self.assertTrue(result["candidate_changed"])
        self.assertFalse(result["passed"])

    def test_new_code_invalidates_bundle_and_accepted_verdict(self):
        self.build()
        self.green()
        self.gate.advance("verify")
        self.verdict()
        self.gate.record_verdict("verdict.json")
        (self.root / "src/sample.py").write_text("# changed after verdict\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.advance("delivery_ready")

    def test_recorded_verdict_tampering_blocks_delivery(self):
        self.build()
        self.green()
        self.gate.advance("verify")
        self.verdict()
        self.gate.record_verdict("verdict.json")
        path = self.root / ".aidevflow/runs/TEST-1/verifier-report.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["risks"] = ["modified"]
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(GateError, "verdict changed"):
            self.gate.advance("delivery_ready")

    def test_repair_limit_stops_gate_file_and_shell_writes(self):
        self.build()
        self.green()
        for _ in range(3):
            self.gate.route("review_finding", "fixture failure")
        self.assertEqual(self.gate.status()["status"], "needs_human")
        with self.assertRaisesRegex(GateError, "execution is stopped"):
            self.gate.advance("verify")
        with self.assertRaisesRegex(GateError, "execution is stopped"):
            self.green()
        with redirect_stderr(StringIO()):
            self.assertEqual(guard({"tool_name": "Write", "tool_input": {"file_path": str(self.root / "src/sample.py")}}, self.root), 2)
            self.assertEqual(guard({"tool_name": "Bash", "tool_input": {"command": "echo x > src/sample.py"}}, self.root), 2)

    def test_resume_requires_explicit_instruction_and_fresh_checks(self):
        self.build()
        self.green()
        for _ in range(3):
            self.gate.route("review_finding", "failure")
        with self.assertRaises(GateError):
            self.gate.resume_run("", "")
        self.gate.resume_run("user", "Continue with one additional repair attempt")
        with self.assertRaisesRegex(GateError, "stale"):
            self.gate.advance("verify")
        self.green()
        self.gate.advance("verify")

    def test_required_smoke_cannot_be_skipped(self):
        class Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"healthy")
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Establish a separate fixture before the run pins its command.
            for _ in range(3):
                self.gate.route("review_finding", "prepare")
            self.gate.archive()
            self.contract["task_id"] = "SMOKE"
            self.contract["test_commands"]["smoke"] = [sys.executable, "-c", "import urllib.request,sys; assert urllib.request.urlopen(sys.argv[1], timeout=5).read() == b'healthy'; print('HTTP smoke passed')", f"http://127.0.0.1:{server.server_port}/health"]
            self.contract["required_checks"] = [{"id": "C-http", "suite": "smoke", "acceptance_items": ["REQ-1"]}]
            self._write_json(".aidevflow/contract.json", self.contract)
            self.gate.init("SMOKE", ".aidevflow/contract.json")
            self.build()
            self.green()
            with self.assertRaisesRegex(GateError, "smoke"):
                self.gate.advance("verify")
            smoke = self.gate.run_tests("smoke")
            self.assertTrue(smoke["passed"], smoke["stderr"])
            self.assertIn("HTTP smoke passed", smoke["stdout"])
            self.gate.advance("verify")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_timeout_is_recorded_as_failure(self):
        self.build()
        original = subprocess.run
        def run(command, **kwargs):
            if command[0] == "git":
                return original(command, **kwargs)
            raise subprocess.TimeoutExpired(command, 1, output=b"partial")
        with patch("aidevflow.gate.subprocess.run", side_effect=run):
            record = self.green()
        self.assertEqual(record["exit_code"], 124)
        self.assertFalse(record["passed"])

    def test_stop_does_not_change_business_state(self):
        self.build()
        self.green()
        self.gate.advance("verify")
        with redirect_stderr(StringIO()):
            self.assertEqual(stop_check(self.root), 0)
            self.assertEqual(stop_check(self.root, {"stop_hook_active": True}), 0)
        self.assertEqual(self.gate.status()["status"], "active")


class ProjectionTests(GateFixture):
    contract_overrides = {
        "work_plan_required": True,
        "method": {"name": "zsspec", "stages": {"design": []}},
        "required_checks": [{"id": "C1", "suite": "green", "target": "tests/test_feature.py", "acceptance_items": ["REQ-1"]}],
    }

    def plan(self):
        self._reach_design()
        (self.root / "specs/REQ.md").write_bytes("| 编号 | 说明 | 状态 |\r\n|---|---|---|\r\n| TASK-1 | implement | 未开始 |\r\n| CHK-1 | verify | 待执行 |\r\n| REQ-1 | outcome | 未开始 |\r\n".encode("utf-8"))
        value = {"schema": "aidevflow/work-plan-v1", "task_id": "TEST-1", "items": [{"id": "W1", "title": "feature", "done_criteria": ["behavior works"], "acceptance_items": ["REQ-1"]}], "projections": [{"path": "specs/REQ.md", "row_id": row, "source_kind": kind, "source_id": source} for row, kind, source in [("TASK-1", "work_item", "W1"), ("CHK-1", "check", "C1"), ("REQ-1", "acceptance", "REQ-1")]]}
        path = "specs/aidevflow-intake/TEST-1/work-plan.json"
        self._write_json(path, value)
        self.gate.register_work_plan(path)
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")

    def test_task_and_check_projection_preserves_spec_and_line_endings(self):
        self.plan()
        self.gate.start_work_item("W1")
        self.assertIn("| TASK-1 | implement | 进行中 |", (self.root / "specs/REQ.md").read_text(encoding="utf-8"))
        self.gate.complete_work_item("W1", ["src/sample.py"])
        self.gate.run_tests("green", "tests/test_feature.py")
        text = (self.root / "specs/REQ.md").read_text(encoding="utf-8")
        self.assertIn("| TASK-1 | implement | 已完成 |", text)
        self.assertIn("| CHK-1 | verify | 通过 |", text)
        self.assertIn(b"\r\n", (self.root / "specs/REQ.md").read_bytes())
        self.gate.advance("verify")

    def test_source_change_resets_projected_check_to_pending(self):
        self.plan()
        self.gate.start_work_item("W1")
        self.gate.complete_work_item("W1", ["src/sample.py"])
        self.gate.run_tests("green", "tests/test_feature.py")
        (self.root / "src/sample.py").write_text("# changed\n", encoding="utf-8")
        self.gate.sync_projections()
        self.assertIn("| CHK-1 | verify | 待执行 |", (self.root / "specs/REQ.md").read_text(encoding="utf-8"))

    def test_specification_body_remains_frozen(self):
        self.plan()
        path = self.root / "specs/REQ.md"
        path.write_text(path.read_text(encoding="utf-8").replace("outcome", "changed outcome"), encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.integrity()


class IntakeReceiptTests(GateFixture):
    def setUp(self):
        super().setUp()
        for _ in range(3):
            self.gate.route("review_finding", "prepare autonomous fixture")
        self.gate.archive()
        (self.root / "request.md").write_text("实现函数并验证结果。", encoding="utf-8")
        self.state = self.gate.start_intake("request.md")
        self.seq = 0

    def dispatch(self, role, value):
        self.seq += 1
        common = {"session_id": "fixture-session", "agent_id": f"fixture-{self.seq}", "agent_type": role}
        capture_event(self.root, {**common, "hook_event_name": "SubagentStart"})
        capture_event(self.root, {**common, "hook_event_name": "SubagentStop", "last_assistant_message": json.dumps(value)})

    def intent(self):
        draft = self._draft()
        self._write_json("draft.json", draft)
        self.dispatch("aidevflow-intent-analyst", draft)
        self.gate.record_intent("draft.json")
        self.gate.answer("Q1", "A", "Return 1")
        self.gate.finalize_intent()

    def test_writer_cannot_submit_intent_without_observed_analyst_output(self):
        self._write_json("draft.json", self._draft())
        with self.assertRaisesRegex(GateError, "execution receipt"):
            self.gate.record_intent("draft.json")
        self.intent()
        self.assertEqual(self.gate.status()["status"], "intent_approved")

    def test_analyst_cannot_invent_a_user_answer(self):
        draft = self._draft()
        draft["decisions"] = [{"question_id": "Q1", "option_id": "A", "answer": "assumed choice", "recorded_at": "now"}]
        self.dispatch("aidevflow-intent-analyst", draft)
        self._write_json("draft.json", draft)
        with self.assertRaisesRegex(GateError, "unrecorded user decisions"):
            self.gate.record_intent("draft.json")

    def test_new_intake_requires_plan_and_check_coverage(self):
        self.intent()
        value = dict(self.contract, task_id=self.state["task_id"])
        self._write_json("contract.json", value)
        with self.assertRaisesRegex(GateError, "work_plan_required"):
            self.gate.register_contract("contract.json")

    def test_handback_result_is_preserved_over_closing_text(self):
        common = {"session_id": "s", "agent_id": "a", "agent_type": "aidevflow-intent-analyst"}
        capture_event(self.root, {**common, "hook_event_name": "SubagentStart"})
        capture_event(self.root, {**common, "hook_event_name": "PostToolUse", "tool_input": {"message": json.dumps(self._draft())}})
        capture_event(self.root, {**common, "hook_event_name": "SubagentStop", "last_assistant_message": '{"closing":"done"}'})
        self._write_json("draft.json", self._draft())
        self.gate.record_intent("draft.json")
        self.assertEqual(self.gate.status()["status"], "awaiting_user_input")

    def test_full_autonomous_fixture_requires_matching_verifier_receipt(self):
        self.intent()
        value = dict(self.contract, task_id=self.state["task_id"], work_plan_required=True, required_checks=[{"id": "C1", "suite": "green", "target": "tests/test_feature.py", "acceptance_items": ["REQ-1"]}])
        self.dispatch("aidevflow-contract-planner", value)
        self._write_json("contract.json", value)
        self.gate.register_contract("contract.json")
        self.gate.advance("design")
        plan = {"schema": "aidevflow/work-plan-v1", "task_id": self.state["task_id"], "items": [{"id": "W1", "title": "feature", "done_criteria": ["works"], "acceptance_items": ["REQ-1"]}]}
        plan_path = f"specs/aidevflow-intake/{self.state['task_id']}/work-plan.json"
        self._write_json(plan_path, plan)
        self.gate.register_work_plan(plan_path)
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.gate.start_work_item("W1")
        self.gate.complete_work_item("W1", ["src/sample.py"])
        self.gate.run_tests("green", "tests/test_feature.py")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        manifest = json.loads((self.root / bundle["path"] / "BUNDLE.json").read_text(encoding="utf-8"))
        self.assertTrue(any(item.get("source_path", "").endswith("source-request.md") for item in manifest["files"]))
        verdict = {"schema": VERDICT_SCHEMA, "task_id": self.state["task_id"], "spec_version": bundle["spec_version"], "bundle_sha256": bundle["sha256"], "items": [{"id": "REQ-1", "verdict": "pass", "evidence": ["diff.patch"], "reason": "fixture evidence"}], "risks": []}
        self._write_json("verdict.json", verdict)
        with self.assertRaisesRegex(GateError, "execution receipt"):
            self.gate.record_verdict("verdict.json")
        self.dispatch("aidevflow-independent-verifier", verdict)
        self.gate.record_verdict("verdict.json")
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready")


class CoverageTests(unittest.TestCase):
    def test_work_plan_cannot_omit_acceptance_coverage(self):
        errors = validate_work_plan({"schema": "aidevflow/work-plan-v1", "task_id": "T", "items": [{"id": "W", "title": "work", "done_criteria": ["works"]}]}, "T", {"acceptance_items": [{"id": "REQ-1", "criterion": "works"}]})
        self.assertTrue(any("cover" in error for error in errors))

    def test_unknown_required_suite_is_invalid(self):
        errors = validate_contract({"required_checks": [{"id": "C", "suite": "nonexistent", "acceptance_items": []}]})
        self.assertTrue(any("configured non-red" in error for error in errors))


class InstallationAndHookTests(GateFixture):
    def test_reinstall_preserves_active_run_and_method_assets(self):
        framework = Path(__file__).resolve().parents[1]
        run = self.root / ".aidevflow/runs/TEST-1/run-state.json"
        contract = self.root / ".aidevflow/contract.json"
        before = (run.read_bytes(), contract.read_bytes())
        skill = self.root / ".claude/skills/zsspec-apply/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("fixed asset", encoding="utf-8")
        install(framework, self.root)
        install(framework, self.root)
        self.assertEqual(before, (run.read_bytes(), contract.read_bytes()))
        self.assertEqual(skill.read_text(encoding="utf-8"), "fixed asset")
        settings = json.loads((self.root / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(settings["hooks"]["SubagentStart"]), 1)
        self.assertIn("SubagentStop", settings["hooks"])
        self.assertTrue((self.root / ".aidevflow/runtime/aidevflow/adapters.py").is_file())
        self.assertEqual(self.gate.status()["task_id"], "TEST-1")

    def test_large_utf8_write_input_reaches_scope_guard(self):
        framework = Path(__file__).resolve().parents[1]
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(self.root / "src/中文实现.py"), "content": "这是大段中文。" * 10000}}
        result = subprocess.run([sys.executable, str(framework / "scripts/aidevflow_hook.py"), "guard"], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"), capture_output=True, env=env)
        self.assertEqual(result.returncode, 2)
        events = (self.root / ".aidevflow/events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"rule": "code-offstage"', events)
        self.assertNotIn('"event": "hook_input_error"', events)

    def test_bad_input_has_safe_diagnostics_without_raw_payload(self):
        framework = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(framework / "scripts/aidevflow_hook.py"), "guard"], input=b'{private-invalid-payload', capture_output=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root)))
        self.assertEqual(result.returncode, 2)
        events = (self.root / ".aidevflow/events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "hook_input_error"', events)
        self.assertNotIn("private-invalid-payload", events)
