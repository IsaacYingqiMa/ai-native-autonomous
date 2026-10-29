from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from aidevflow.gate import Gate, GateError, INTENT_SCHEMA, VERDICT_SCHEMA, valid_red_result, validate_intent
from aidevflow.hooks import guard


class GateFixture(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = Path(__file__).resolve().parents[1] / ".test-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root, ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self._git("init", "-q")
        (self.root / ".gitignore").write_text(".aidevflow/runs/\n.aidevflow/active-run.json\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "baseline")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_feature.py").write_text("# frozen acceptance test\n", encoding="utf-8")
        (self.root / "specs").mkdir()
        (self.root / "specs" / "REQ.md").write_text("# REQ-1\nReturn the expected value.\n", encoding="utf-8")
        self.contract = {
            "schema": "aidevflow/contract-v1",
            "task_id": "TEST-1",
            "title": "Gate lifecycle test",
            "max_repair_attempts": 2,
            "code_paths": ["src/**"],
            "design_artifacts": ["specs/REQ.md"],
            "test_files": ["tests/test_feature.py"],
            "test_commands": {
                "red": [sys.executable, "-c", "print('FAIL: expected 1'); import sys; sys.exit(1)", "{target}"],
                "green": [sys.executable, "-c", "print('passed')", "{target}"],
            },
            "acceptance_items": [{"id": "REQ-1", "criterion": "Return the expected value."}],
            "routing": {"review_finding": {"to": "build"}},
            "verifier": {"required": True},
        }
        self.contract.update(getattr(self, "contract_overrides", {}))
        self._write_json(".aidevflow/contract.json", self.contract)
        self.gate = Gate(self.root)
        self.gate.init("TEST-1", ".aidevflow/contract.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *args: str) -> str:
        env = os.environ.copy()
        env.update({
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "user.email",
            "GIT_CONFIG_VALUE_1": "aidevflow@example.invalid",
            "GIT_CONFIG_KEY_2": "user.name",
            "GIT_CONFIG_VALUE_2": "AIDevFlow Test",
        })
        result = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=True, env=env)
        return result.stdout.strip()

    def _write_json(self, relative: str, value: object) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _draft(self) -> dict[str, object]:
        return {
            "schema": INTENT_SCHEMA,
            "problem": "The feature does not return a value.",
            "users": ["developer"],
            "desired_outcome": "Return the expected value.",
            "scope": ["implement the function"],
            "non_goals": [],
            "constraints": [],
            "success_criteria": ["The function returns 1."],
            "assumptions": [],
            "open_questions": [{
                "id": "Q1", "question": "Which behavior is required?", "blocking": True,
                "options": [{"id": "A", "label": "Return 1", "impact": "Matches the current requirement."}, {"id": "B", "label": "Return 2", "impact": "Changes the requirement."}],
                "recommended_option": "A", "recommendation_reason": "The requirement states 1."
            }],
            "decisions": [],
        }

    def _reach_design(self) -> None:
        self._write_json("draft.json", self._draft())
        result = self.gate.record_intent("draft.json")
        self.assertEqual(result["status"], "awaiting_user_input")
        self.gate.answer("Q1", "A", "Return 1")
        self.gate.finalize_intent()
        self.gate.approve_intent("user", "I approve intent TEST-1")
        self.gate.advance("design")


class GateTests(GateFixture):
    def test_blocking_intent_question_pauses_until_answered(self) -> None:
        self._write_json("draft.json", self._draft())
        self.assertEqual(self.gate.record_intent("draft.json")["status"], "awaiting_user_input")
        with self.assertRaisesRegex(GateError, "blocking questions"):
            self.gate.finalize_intent()
        result = self.gate.answer("Q1", "A", "Return 1")
        self.assertEqual(result["status"], "intent_review")
        finalized = self.gate.finalize_intent()
        self.assertTrue((self.root / finalized["path"]).is_file())

    def test_intent_validator_rejects_malformed_choice_contract(self) -> None:
        draft = self._draft()
        draft["unexpected"] = "must not be silently accepted"
        draft["open_questions"][0]["recommended_option"] = "unknown"
        draft["decisions"] = [{"question_id": "Q-UNKNOWN", "option_id": None, "answer": "answer", "recorded_at": "now"}]
        errors = validate_intent(draft)
        self.assertTrue(any("unexpected properties" in error for error in errors))
        self.assertTrue(any("must recommend one listed option" in error for error in errors))
        self.assertTrue(any("decision references an unknown question" in error for error in errors))

    def test_revised_intent_preserves_gate_recorded_user_choice(self) -> None:
        self._write_json("draft.json", self._draft())
        self.gate.record_intent("draft.json")
        self.gate.answer("Q1", "A", "Return 1")
        revised = self._draft()
        revised["desired_outcome"] = "Return 1 and retain the explicit user choice."
        self._write_json("revised-draft.json", revised)
        result = self.gate.record_intent("revised-draft.json")
        self.assertEqual(result["status"], "intent_review")
        stored = json.loads((self.root / result["draft_path"]).read_text(encoding="utf-8"))
        self.assertEqual(stored["decisions"][0]["option_id"], "A")

    def test_intent_draft_path_must_stay_inside_repository(self) -> None:
        with self.assertRaisesRegex(GateError, "Path escapes"):
            self.gate.record_intent("../draft.json")

    def test_infrastructure_error_is_not_valid_red_evidence(self) -> None:
        self.assertFalse(valid_red_result(1, "SyntaxError: missing dependency"))
        self.assertFalse(valid_red_result(127, "Could not start test command: executable missing"))
        self.assertTrue(valid_red_result(1, "FAIL: AssertionError expected 1, got 0"))

    def test_cannot_skip_approval_or_stages(self) -> None:
        with self.assertRaisesRegex(GateError, "approved, unchanged intent"):
            self.gate.advance("design")
        with self.assertRaisesRegex(GateError, "Invalid transition"):
            self.gate.advance("build")

    def test_contract_mutation_invalidates_the_active_run(self) -> None:
        self.contract["title"] = "Silently changed after run initialization"
        self._write_json(".aidevflow/contract.json", self.contract)
        with self.assertRaisesRegex(GateError, "contract changed"):
            self.gate.status()

    def test_powershell_hook_blocks_protected_state_writes(self) -> None:
        payload = {"tool_name": "PowerShell", "tool_input": {"command": "Set-Content .aidevflow/run-state.json x"}}
        with redirect_stderr(StringIO()):
            result = guard(payload, self.root)
        self.assertEqual(result, 2)

    def test_hash_freeze_detects_test_mutation_and_blocks_progress(self) -> None:
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        (self.root / "tests" / "test_feature.py").write_text("# tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "integrity mismatch"):
            self.gate.freeze_tests([])
        self.assertEqual(self.gate.status()["status"], "needs_human")

    def test_full_flow_requires_frozen_red_green_and_matching_verdict(self) -> None:
        self._reach_design()
        self.gate.freeze_tests([])
        red = self.gate.run_tests("red", "tests/test_feature.py")
        self.assertTrue(red["valid_red"])
        self.gate.advance("build")
        (self.root / "src" / "sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        self.assertTrue(self.gate.run_tests("green", "tests/test_feature.py")["passed"])
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        bundle_dir = self.root / bundle["path"]
        manifest = json.loads((bundle_dir / "BUNDLE.json").read_text(encoding="utf-8"))
        bundle_paths = {item["path"] for item in manifest["files"]}
        self.assertIn("diff.patch", bundle_paths)
        sources = {item.get("source_path") for item in manifest["files"]}
        self.assertIn(red["evidence_path"], sources)
        self.assertIn(".aidevflow/contract.json", sources)
        verdict = {
            "schema": VERDICT_SCHEMA,
            "task_id": "TEST-1",
            "spec_version": bundle["spec_version"],
            "bundle_sha256": bundle["sha256"],
            "items": [{"id": "REQ-1", "verdict": "pass", "evidence": ["diff.patch"], "reason": "The change is present."}],
            "risks": [],
        }
        self._write_json("verdict.json", verdict)
        self.gate.record_verdict("verdict.json")
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready")

    def test_verdict_cannot_claim_pass_without_bundle_evidence(self) -> None:
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.gate.run_tests("green", "tests/test_feature.py")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        self._write_json("verdict.json", {
            "schema": VERDICT_SCHEMA, "task_id": "TEST-1", "spec_version": bundle["spec_version"],
            "bundle_sha256": bundle["sha256"],
            "items": [{"id": "REQ-1", "verdict": "pass", "evidence": ["../../outside.txt"], "reason": "trust me"}],
            "risks": [],
        })
        with self.assertRaises(GateError):
            self.gate.record_verdict("verdict.json")

    def test_intent_hash_change_invalidates_approval(self) -> None:
        self._reach_design()
        state = self.gate.status()
        (self.root / state["intent_path"]).write_text("# changed after approval\n", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "unchanged intent"):
            self.gate.advance("design")

    def test_event_log_rebuilds_state_after_snapshot_loss(self) -> None:
        state_path = self.root / ".aidevflow" / "runs" / "TEST-1" / "run-state.json"
        state_path.unlink()
        rebuilt = self.gate.rebuild("TEST-1")
        self.assertEqual(rebuilt["current_stage"], "intent")
        self.assertEqual(self.gate.status()["task_id"], "TEST-1")

    def test_verifier_bundle_tampering_is_rejected(self) -> None:
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.gate.run_tests("green", "tests/test_feature.py")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        (self.root / bundle["path"] / "diff.patch").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "bundle content changed"):
            self.gate.record_verdict("verdict.json")

    def test_test_evidence_tampering_before_bundle_is_rejected(self) -> None:
        self._reach_design()
        self.gate.freeze_tests([])
        self.gate.run_tests("red", "tests/test_feature.py")
        self.gate.advance("build")
        self.gate.run_tests("green", "tests/test_feature.py")
        self.gate.advance("verify")
        evidence = self.root / self.gate.status()["test_runs"][0]["evidence_path"]
        evidence.write_text("tampered evidence", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "Execution evidence is damaged"):
            self.gate.prepare_verifier()


if __name__ == "__main__":
    unittest.main()
