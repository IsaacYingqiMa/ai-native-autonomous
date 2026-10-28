from __future__ import annotations

import copy
import json
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from test_gate import GateFixture
from aidevflow.gate import GateError, VERDICT_SCHEMA, read_json, validate_contract
from aidevflow.managed import DESIGN_SKILLS, PROFILE, read_policy
from aidevflow.methods import method_status, observe_skill
from aidevflow.hooks import guard
from aidevflow.diagnostics import doctor


class ManagedFixture(GateFixture):
    def setUp(self):
        super().setUp()
        # Replace only the disposable fixture's active pointer, not a real project.
        self.gate.active_path.unlink()
        self.contract.update({
            "task_id": "MANAGED", "work_plan_required": True,
            "method": {"name": "zsspec", "profile": PROFILE, "stages": {
                "design": DESIGN_SKILLS + ["zsspec-test"], "build": ["zsspec-apply"],
                "review": ["zsspec-test", "zsspec-done"]}},
            "evidence_paths": ["specs/checklist/reports", "test-results"],
            "design_artifacts": ["specs/REQ-feature.md", "specs/DES-feature.md", "specs/TASK-feature.md", "specs/CHK-feature.md"],
            "required_checks": [
                {"id": "C-green", "suite": "green", "target": "tests/test_feature.py", "acceptance_items": ["REQ-1"]},
                {"id": "C-static", "kind": "static", "acceptance_items": ["REQ-1"]},
                {"id": "C-manual", "kind": "manual", "acceptance_items": ["REQ-1"]}],
        })
        names = {name for values in self.contract["method"]["stages"].values() for name in values}
        for name in names | {"zsspec-standard", "zsspec-change"}:
            path = self.root / ".claude/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}\nfixture method instructions\n", encoding="utf-8")
        self.reference = self.root / ".claude/skills/zsspec-test/reference/auto.md"
        self.reference.parent.mkdir(parents=True)
        self.reference.write_text("Frozen acceptance tests only\n", encoding="utf-8")
        self.write_report("design.md", "AI review: requirements, design, TASK coverage and frozen assertions inspected.")
        for name, rows in {
            "REQ-feature": [("REQ-1", "outcome")], "DES-feature": [],
            "TASK-feature": [("TASK-feature-实现", "implementation")],
            "CHK-feature": [("CHK-feature-green", "behavior"), ("CHK-feature-static", "review"), ("CHK-feature-manual", "visual")],
        }.items():
            (self.root / f"specs/{name}.md").write_text(
                "| 编号 | 说明 | 状态 |\n|---|---|---|\n" + "".join(f"| {key} | {title} | 未开始 |\n" for key, title in rows), encoding="utf-8")
        self._write_json("managed-contract.json", self.contract)
        self.gate.init("MANAGED", "managed-contract.json")

    def write_report(self, name, content="AI fixture review evidence"):
        path = self.root / "specs/checklist/reports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path.relative_to(self.root).as_posix()

    def observe(self, phases):
        native = self.root / ".native-claude/projects/fixture/managed-fixture.jsonl"
        native.parent.mkdir(parents=True, exist_ok=True)
        native.touch()
        for phase in phases:
            for name in self.contract["method"]["stages"][phase]:
                with patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(self.root / ".native-claude")}):
                    with native.open("a", encoding="utf-8") as out:
                        out.write(json.dumps({"type": "assistant", "sessionId": "managed-fixture", "cwd": str(self.root),
                            "message": {"content": [{"type": "tool_use", "id": "fixture-" + name,
                                "name": "Skill", "input": {"skill": name, "args": "feature --gen-only"}}]}}) + "\n")
                    observe_skill(self.root, {"hook_event_name": "PostToolUse", "tool_name": "Skill",
                        "session_id": "managed-fixture", "tool_use_id": "fixture-" + name,
                        "transcript_path": str(native), "tool_input": {"skill": name, "args": "feature --gen-only"}, "tool_response": {}})

    def design(self, review=True, two_items=False):
        self._reach_design()
        plan = {"schema": "aidevflow/work-plan-v1", "task_id": "MANAGED",
                "items": [{"id": "W1", "title": "feature", "done_criteria": ["behavior works"], "acceptance_items": ["REQ-1"]}],
                "projections": [
                    {"path": "specs/REQ-feature.md", "row_id": "REQ-1", "source_kind": "acceptance", "source_id": "REQ-1"},
                    {"path": "specs/TASK-feature.md", "row_id": "TASK-feature-实现", "source_kind": "work_item", "source_id": "W1"},
                    *[{"path": "specs/CHK-feature.md", "row_id": f"CHK-feature-{mode}", "source_kind": "check", "source_id": f"C-{mode}"}
                      for mode in ("green", "static", "manual")]]}
        if two_items:
            plan["items"].append({"id": "W2", "title": "independent work", "done_criteria": ["other behavior"], "acceptance_items": ["REQ-1"]})
            path = self.root / "specs/TASK-feature.md"
            path.write_text(path.read_text(encoding="utf-8") + "| TASK-feature-第二项 | independent | 未开始 |\n", encoding="utf-8")
            plan["projections"].append({"path": "specs/TASK-feature.md", "row_id": "TASK-feature-第二项", "source_kind": "work_item", "source_id": "W2"})
        self._write_json("specs/aidevflow-intake/MANAGED/work-plan.json", plan)
        self.gate.register_work_plan("specs/aidevflow-intake/MANAGED/work-plan.json")
        self.observe(("design",))
        if review:
            self.review()

    def review(self):
        return self.gate.record_design_review("AI fixture coordinator", "Inspected coverage and executable assertions", ["specs/checklist/reports/design.md"])

    def build(self):
        self.design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.observe(("build", "review"))
        self.gate.start_work_item("W1")
        self.gate.complete_work_item("W1", ["src/sample.py"])

    def attest(self, mode="static", result="passed", candidate=None):
        report = self.write_report(f"{mode}.md")
        evidence = [report]
        if mode == "manual" and result == "passed":
            screenshot = self.root / "specs/checklist/reports/screenshot.png"
            # Test fixture bytes; no claim that an actual UI was inspected.
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            evidence.append(screenshot.relative_to(self.root).as_posix())
        return self.gate.record_check(f"C-{mode}", result, candidate or self.gate.status()["candidate_sha256"],
                                      "AI fixture coordinator", "See captured evidence", evidence)

    def check(self, check_id):
        return next(check for check in self.gate.status()["checks"] if check["id"] == check_id)


class ManagedTests(ManagedFixture):
    def test_autonomous_intake_registers_profile_after_real_protocol_receipts(self):
        from aidevflow.agents import capture_event
        # Synthetic native events test the protocol; no LLM or business claim.
        self.gate.active_path.unlink()
        request = self.root / "request.md"
        request.write_text("Implement the expected feature", encoding="utf-8")
        state = self.gate.start_intake("request.md")
        def receipt(role, value):
            common = {"session_id": "fixture", "agent_id": role, "agent_type": role}
            capture_event(self.root, {**common, "hook_event_name": "SubagentStart"})
            capture_event(self.root, {**common, "hook_event_name": "PostToolUse", "tool_name": "SubagentHandback", "tool_input": {"message": json.dumps(value)}})
            capture_event(self.root, {**common, "hook_event_name": "SubagentStop"})
        draft = self._draft()
        receipt("aidevflow-intent-analyst", draft)
        self._write_json("draft.json", draft)
        self.gate.record_intent("draft.json")
        self.gate.answer("Q1", "A", "Return 1")
        self.gate.finalize_intent()
        contract = dict(self.contract, task_id=state["task_id"])
        receipt("aidevflow-contract-planner", contract)
        self._write_json("profile-draft.json", contract)
        self.gate.register_contract("profile-draft.json")
        self.assertEqual(self.gate.advance("design")["current_stage"], "design")
        status = self.gate.status()
        self.assertTrue(status["agent_receipts_required"])
        self.assertEqual(status["method_policy_details"]["profile"], PROFILE)

    def test_red_requires_frozen_reviewed_baseline(self):
        self.design()
        with self.assertRaisesRegex(GateError, "frozen tests first"):
            self.gate.run_tests("red", "tests/test_feature.py")

    def test_design_requires_explicit_ai_review_before_freeze(self):
        self.design(review=False)
        with self.assertRaisesRegex(GateError, "record-design-review"):
            self.gate.freeze_tests([])
        report = self.review()
        self.assertEqual(report["source"], "ai_coordinator_attestation")
        self.gate.freeze_tests([])

    def test_review_is_invalidated_by_test_or_design_changes_before_freeze(self):
        self.design()
        (self.root / "tests/test_feature.py").write_text("# revised assertions\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "review is stale"):
            self.gate.freeze_tests([])
        self.review()
        self.gate.freeze_tests([])

    def test_changed_method_reference_stops_execution(self):
        self.design()
        self.reference.write_text("weakened method\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.integrity()
        self.assertEqual(self.gate.status()["status"], "needs_human")

    def test_yaml_change_cannot_silently_change_acceptance(self):
        self.build()
        (self.root / "specs/zsspec.yml").write_text("gate:\n  mode: quick\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.run_tests("green", "tests/test_feature.py")

    def test_method_load_is_not_check_completion(self):
        self.build()
        self.gate.run_tests("green", "tests/test_feature.py")
        state = self.gate.status()
        self.assertFalse(state["pending_checks"])
        self.assertEqual({item["id"] for item in state["pending_attestations"]}, {"C-static", "C-manual"})
        with self.assertRaisesRegex(GateError, "Fresh passing"):
            self.gate.advance("verify")

    def test_report_is_snapshotted_and_original_changes_do_not_rewrite_evidence(self):
        self.build()
        self.attest()
        self.write_report("static.md", "overwritten source report")
        self.assertEqual(self.check("C-static")["status"], "passed")
        state, _, _ = self.gate._load_active()
        snapshot = self.root / state["check_results"][-1]["evidence"][0]["path"]
        self.assertNotEqual(snapshot.read_text(encoding="utf-8"), "overwritten source report")

    def test_tampered_snapshot_blocks_check(self):
        self.build()
        self.attest()
        state, _, _ = self.gate._load_active()
        (self.root / state["check_results"][-1]["evidence"][0]["path"]).write_text("tampered", encoding="utf-8")
        self.assertEqual(self.check("C-static")["status"], "invalid")

    def test_unchanged_chk_does_not_preserve_pass_after_code_change(self):
        self.build()
        self.attest()
        before = (self.root / "specs/CHK-feature.md").read_bytes()
        (self.root / "src/sample.py").write_text("def value(): return 2\n", encoding="utf-8")
        self.assertEqual(self.check("C-static")["status"], "stale")
        self.assertEqual(before, (self.root / "specs/CHK-feature.md").read_bytes())

    def test_stale_candidate_cannot_be_rebound_to_new_code(self):
        self.build()
        candidate = self.gate.status()["candidate_sha256"]
        (self.root / "src/sample.py").write_text("# changed\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "candidate is stale"):
            self.attest(candidate=candidate)

    def test_later_failure_wins_over_prior_pass(self):
        self.build()
        self.attest()
        self.attest(result="failed")
        self.assertEqual(self.check("C-static")["status"], "failed")

    def test_manual_pass_requires_screenshot_and_keeps_ai_label(self):
        self.build()
        report = self.write_report("manual.md")
        with self.assertRaisesRegex(GateError, "screenshot"):
            self.gate.record_check("C-manual", "passed", self.gate.status()["candidate_sha256"], "AI", "review", [report])
        self.attest("manual")
        self.assertIn("通过 (AI辅助)", (self.root / "specs/CHK-feature.md").read_text(encoding="utf-8"))

    def test_command_checks_cannot_be_marked_passed_by_attestation(self):
        self.build()
        with self.assertRaisesRegex(GateError, "only declared static/manual"):
            self.gate.record_check("C-green", "passed", self.gate.status()["candidate_sha256"], "AI", "claim", [self.write_report("fake.md")])

    def test_non_artifact_files_cannot_be_registered_as_a_review_report(self):
        self.build()
        with self.assertRaisesRegex(GateError, "evidence_paths"):
            self.gate.record_check("C-static", "passed", self.gate.status()["candidate_sha256"], "AI", "claim", ["src/sample.py"])

    def test_disabled_manual_policy_conflicts_with_required_evidence(self):
        (self.root / "specs/zsspec.yml").write_text("test:\n  manual: disabled\n", encoding="utf-8")
        errors = self.gate._validate_available_method_skills(self.contract)
        self.assertTrue(any("requires manual evidence" in error for error in errors))

    def test_serial_profile_prevents_two_in_progress_work_items(self):
        self.design(two_items=True)
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.gate.start_work_item("W1")
        with self.assertRaisesRegex(GateError, "serial"):
            self.gate.start_work_item("W2")
        self.gate.complete_work_item("W1", ["src/sample.py"])
        self.assertEqual(self.gate.start_work_item("W2")["status"], "in_progress")

    def test_missing_or_unchanged_artifact_is_not_a_passing_check(self):
        self.build()
        report = self.write_report("old.md", "prior run output")
        for path in (report, "test-results/missing.json"):
            result = self.gate.run_tests("green", "tests/test_feature.py", [path])
            self.assertFalse(result["passed"])
            self.assertIn("artifact_error", result)
            self.assertIn("passed", result["stdout"])
        self.assertEqual(self.check("C-green")["status"], "failed")

    def test_support_files_freeze_without_creating_fake_green_targets(self):
        # Prepare a second managed fixture contract before entering Design.
        from aidevflow.gate import sha256_file, write_json_atomic
        state, _, contract = self.gate._load_active()
        support = "tests/fixtures.py"
        (self.root / support).write_text("DATA = 1\n", encoding="utf-8")
        contract["test_support_files"] = [support]
        write_json_atomic(self.root / state["contract_path"], contract)
        state["contract_sha256"] = sha256_file(self.root / state["contract_path"])
        write_json_atomic(self.root / ".aidevflow/runs/MANAGED/run-state.json", state)
        self.design()
        frozen = self.gate.freeze_tests([])
        self.assertIn(support, frozen)
        self.assertFalse(any(check["id"] == "green:" + support for check in self.gate.status()["checks"]))
        (self.root / support).write_text("DATA = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.integrity()

    def test_artifact_paths_are_allowed_but_frozen_chk_edits_stay_blocked(self):
        self.build()
        def write(path):
            with redirect_stderr(StringIO()):
                return guard({"tool_name": "Write", "tool_input": {"file_path": str(self.root / path)}}, self.root)
        self.assertEqual(write("specs/checklist/reports/new.md"), 0)
        self.assertEqual(write("specs/CHK-feature.md"), 2)
        self.assertEqual(write("tests/test_feature.py"), 2)
        self.assertEqual(write("other/report.md"), 2)

    def test_command_artifacts_are_captured_without_self_invalidating_the_candidate(self):
        self.build()
        # Execute a real subprocess that produces a runtime artifact.
        state, _, pinned = self.gate._load_active()
        command = [sys.executable, "-c", "from pathlib import Path; p=Path('test-results/result.json'); p.parent.mkdir(exist_ok=True); p.write_text('{\"passed\":true}'); print('actual subprocess ran')"]
        from aidevflow.gate import sha256_file, write_json_atomic
        # Disposable fixture contract setup, not a supported production migration.
        pinned["test_commands"]["green"] = command + ["{target}"]
        path = self.root / state["contract_path"]
        write_json_atomic(path, pinned)
        state["contract_sha256"] = sha256_file(path)
        write_json_atomic(self.root / ".aidevflow/runs/MANAGED/run-state.json", state)
        result = self.gate.run_tests("green", "tests/test_feature.py", ["test-results/result.json"])
        self.assertTrue(result["passed"])
        self.assertFalse(result["candidate_changed"])
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertIn("actual subprocess ran", result["stdout"])

    def test_full_managed_gate_flow_packages_review_and_screenshot(self):
        self.build()
        self.gate.run_tests("green", "tests/test_feature.py")
        self.attest()
        self.attest("manual")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        manifest = read_json(self.root / bundle["path"] / "BUNDLE.json")
        sources = [item.get("source_path", "") for item in manifest["files"]]
        self.assertTrue(any(path.endswith("method-policy.json") for path in sources))
        self.assertTrue(any(path.endswith("design-review.json") for path in sources))
        self.assertTrue(any("check-results/" in path for path in sources))
        self.assertTrue(any(path.endswith(".png") for path in sources))
        verdict = {"schema": VERDICT_SCHEMA, "task_id": "MANAGED", "spec_version": bundle["spec_version"], "bundle_sha256": bundle["sha256"],
                   "items": [{"id": "REQ-1", "verdict": "pass", "evidence": ["diff.patch"], "reason": "Gate fixture verdict, not a real business acceptance"}], "risks": []}
        self._write_json("verdict.json", verdict)
        self.gate.record_verdict("verdict.json")
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready")
        self.assertIn("已完成", (self.root / "specs/REQ-feature.md").read_text(encoding="utf-8"))

    def test_doctor_checks_new_method_but_never_dispatches_or_mutates(self):
        before = (self.root / ".aidevflow/runs/MANAGED/run-state.json").read_bytes()
        with patch("aidevflow.diagnostics.runtime_identity", return_value={}):
            report = doctor(self.root)
        self.assertFalse(report["method_errors"])
        self.assertEqual(report["method_policy"]["effective"]["parallel"], "off")
        self.assertEqual(before, (self.root / ".aidevflow/runs/MANAGED/run-state.json").read_bytes())


class ManagedValidationTests(unittest.TestCase):
    def setUp(self):
        self.example = read_json(Path(__file__).resolve().parents[1] / ".aidevflow/contract.example.json")

    def test_current_example_matches_current_skills_and_gate(self):
        from aidevflow.methods import available_skill_errors
        self.assertEqual(validate_contract(self.example), [])
        self.assertEqual(available_skill_errors(Path(__file__).resolve().parents[1], self.example), [])

    def test_evidence_cannot_hide_product_or_frozen_files(self):
        for prefix in ("src", "specs/requirement", ".aidevflow/x", "../reports", "reports/*"):
            contract = copy.deepcopy(self.example)
            contract["evidence_paths"] = [prefix]
            self.assertTrue(validate_contract(contract), prefix)

    def test_non_command_checks_do_not_accept_fabricated_command_suites(self):
        self.example["required_checks"][0]["suite"] = "green"
        self.assertTrue(any("do not declare" in error for error in validate_contract(self.example)))

    def test_policy_parser_rejects_unsupported_or_weak_configuration(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test-tmp") as directory:
            root = Path(directory)
            (root / "specs").mkdir()
            path = root / "specs/zsspec.yml"
            for content in ("gate:\n  mode: quick\n", "paths:\n  skills_root: /global/skills\n", "test: {e2e: disabled}\n", "test:\n  e2e: enabled\n  e2e: disabled\n"):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(GateError, msg=content):
                    read_policy(root)
            path.write_text("# config\ntest:\n  manual: 'disabled' # explicit\nparallel:\n  mode: auto\n  max: 4\n", encoding="utf-8")
            policy = read_policy(root)
            self.assertEqual(policy["effective"]["manual"], "disabled")
            self.assertEqual(policy["effective"]["parallel"], "off")


class AtomicWriteTests(unittest.TestCase):
    def test_transient_windows_sharing_error_is_retried(self):
        import os
        import tempfile
        from aidevflow.gate import write_json_atomic
        original = os.replace
        calls = []
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test-tmp") as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"old":true}', encoding="utf-8")
            def replace(source, destination):
                calls.append(1)
                if len(calls) == 1:
                    error = PermissionError("fixture Windows sharing violation")
                    error.winerror = 32
                    raise error
                return original(source, destination)
            with patch("aidevflow.gate.os.replace", side_effect=replace), patch("aidevflow.gate.time.sleep"):
                write_json_atomic(path, {"new": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(read_json(path), {"new": True})

    def test_permanent_windows_error_stops_after_bounded_retries(self):
        import tempfile
        from aidevflow.gate import write_json_atomic
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test-tmp") as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"old":true}', encoding="utf-8")
            error = PermissionError("fixture persistent Windows access error")
            error.winerror = 5
            with patch("aidevflow.gate.os.replace", side_effect=error) as replace, patch("aidevflow.gate.time.sleep"):
                with self.assertRaises(PermissionError):
                    write_json_atomic(path, {"new": True})
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(read_json(path), {"old": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


class VersionProbeTests(unittest.TestCase):
    def test_windows_shim_uses_native_executable_and_file_backed_output(self):
        from aidevflow.diagnostics import runtime_identity
        process = Mock(returncode=0)
        def start(command, **kwargs):
            self.assertTrue(command[0].replace("\\", "/").endswith("node_modules/@anthropic-ai/claude-code/bin/claude.exe"))
            self.assertEqual(command[1:], ["--version"])
            kwargs["stdout"].write(b"2.1.fixture (Claude Code)\n")
            return process
        with patch("aidevflow.diagnostics.shutil.which", return_value="C:/fixture/claude.cmd"), patch("pathlib.Path.is_file", return_value=True), patch("aidevflow.diagnostics.subprocess.Popen", side_effect=start):
            result = runtime_identity()
        self.assertEqual(result["installed_version"], "2.1.fixture (Claude Code)")
        self.assertIsNone(result["version_probe_error"])
        process.wait.assert_called_once_with(timeout=3)

    def test_version_probe_timeout_does_not_wait_on_descendant_pipes(self):
        import subprocess
        from aidevflow.diagnostics import runtime_identity
        process = Mock(returncode=-9)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 3), None]
        with patch("aidevflow.diagnostics.shutil.which", return_value="/fixture/claude"), patch("aidevflow.diagnostics.subprocess.Popen", return_value=process):
            result = runtime_identity()
        self.assertIsNone(result["installed_version"])
        self.assertEqual(result["version_probe_error"], "version_probe_timeout")
        process.kill.assert_called_once()
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list], [3, 1])


if __name__ == "__main__":
    unittest.main()
