from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aidevflow.agents import capture_event
from aidevflow.diagnostics import doctor
from aidevflow.gate import Gate, GateError, SCHEMA, read_json, write_json_atomic
from aidevflow.installer import merge_settings
from aidevflow.receipts import read_invocations

ROLE = "aidevflow-intent-analyst"
VERIFIER = "aidevflow-independent-verifier"
FRAMEWORK = Path(__file__).resolve().parents[1]


class ObserverTests(unittest.TestCase):
    def setUp(self):
        temp_root = FRAMEWORK / ".test-tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root, ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.run = self.root / ".aidevflow/runs/OBS-1"
        self.state = {"schema": SCHEMA, "task_id": "OBS-1", "current_stage": "intent",
                      "status": "intake_started", "contract_path": None,
                      "request_sha256": "request-1", "intent_sha256": None,
                      "contract_sha256": None, "agent_receipts_required": True,
                      "verifier_bundle": None, "evidence_epoch": 0}
        write_json_atomic(self.root / ".aidevflow/active-run.json", {"task_id": "OBS-1"})
        self.save_state()
        self.runtime = patch("aidevflow.diagnostics.runtime_identity", return_value={"installed_version": "fixture", "running_version": None})
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.addCleanup(self.temp.cleanup)

    def save_state(self):
        write_json_atomic(self.run / "run-state.json", self.state)

    def dispatch(self, event="PreToolUse", call="call-1", agent="agent-1", role=ROLE, status="async_launched", result=None, **inputs):
        inp = {"subagent_type": role, "prompt": "Read the fixed input and return JSON", **inputs}
        payload = {"hook_event_name": event, "tool_name": "Agent", "session_id": "parent",
                   "tool_use_id": call, "tool_input": inp, "unknown_additive_field": 42}
        if event != "PreToolUse":
            payload["tool_response"] = {"status": status, "agentId": agent,
                                       "content": [{"type": "text", "text": json.dumps(result)}] if result is not None else []}
        capture_event(self.root, payload)

    def lifecycle(self, event, result=None, agent="agent-1", role=ROLE):
        capture_event(self.root, {"hook_event_name": event, "session_id": "parent",
                                 "agent_id": agent, "agent_type": role,
                                 "last_assistant_message": json.dumps(result) if result is not None else ""})

    def handback(self, result, agent="agent-1", role=ROLE):
        capture_event(self.root, {"hook_event_name": "PostToolUse", "tool_name": "SubagentHandback",
                                 "session_id": "parent", "agent_id": agent, "agent_type": role,
                                 "tool_input": {"message": result if isinstance(result, str) else json.dumps(result)}})

    def receipts(self):
        return [e for e in read_invocations(self.run) if e.get("status") != "superseded"]

    def require(self, result, role="intent_analyst", bundle=None):
        Gate(self.root)._require_agent_receipt(self.state, self.run, role, result, bundle)

    def test_missing_start_background_passes_with_real_dispatch_result_and_stop(self):
        self.dispatch()
        self.dispatch("PostToolUse")
        self.handback({"answer": 1})
        self.lifecycle("SubagentStop", {"closing": "done"})
        self.require({"answer": 1})
        receipt = self.receipts()[0]
        self.assertFalse(receipt["start_observed"])
        self.assertTrue(receipt["dispatch_observed"])
        self.assertEqual(receipt["output_source"], "SubagentHandback")
        self.assertNotIn("Read the fixed input", json.dumps(receipt))

    def test_foreground_completion_needs_neither_start_nor_stop(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed", result={"answer": 1})
        self.require({"answer": 1})

    def test_early_stop_and_handback_wait_for_exact_parent_bridge(self):
        self.dispatch()
        self.lifecycle("SubagentStop", {"closing": "done"})
        self.handback({"answer": 1})
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.dispatch("PostToolUse")
        self.require({"answer": 1})
        self.assertEqual(read_json(self.run / "agent-observer-status.json")["diagnostics"], [])

    def test_legacy_lifecycle_still_works_and_start_is_idempotent(self):
        self.lifecycle("SubagentStart")
        self.lifecycle("SubagentStart")
        self.lifecycle("SubagentStop", {"answer": 1})
        self.require({"answer": 1})
        self.assertEqual(len(self.receipts()), 1)

    def test_orphan_result_never_manufactures_start_or_completion(self):
        self.handback({"answer": 1})
        self.lifecycle("SubagentStop", {"answer": 1})
        self.assertEqual(self.receipts(), [])
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_post_without_pre_cannot_bind_current_inputs_retroactively(self):
        self.dispatch("PostToolUse", status="completed", result={"answer": 1})
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_async_launch_is_not_completion_even_with_report(self):
        self.dispatch()
        self.dispatch("PostToolUse")
        self.handback({"answer": 1})
        self.assertEqual(self.receipts()[0]["status"], "output_received")
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_completed_without_report_is_missing_output(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed")
        self.assertEqual(self.receipts()[0]["status"], "missing_output")

    def test_duplicate_events_do_not_duplicate_receipts_or_observations(self):
        for _ in range(3):
            self.dispatch()
            self.dispatch("PostToolUse", status="completed", result={"answer": 1})
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(len(list((self.run / "agent-observations").glob("*.json"))), 2)

    def test_same_role_parallel_dispatches_use_provider_identity(self):
        for call, agent in (("one", "a"), ("two", "b")):
            self.dispatch(call=call, agent=agent)
            self.dispatch("PostToolUse", call=call, agent=agent)
        self.handback({"answer": 2}, agent="b")
        self.lifecycle("SubagentStop", agent="b")
        self.require({"answer": 2})
        self.assertEqual(sum(e["status"] == "completed" for e in self.receipts()), 1)

    def test_display_alias_is_accepted_only_through_dispatch_mapping(self):
        self.dispatch(name="analyst-display")
        self.handback({"answer": 1}, role="analyst-display")
        self.lifecycle("SubagentStop", role="analyst-display")
        self.dispatch("PostToolUse", name="analyst-display")
        self.require({"answer": 1})

    def test_canonical_role_mismatch_is_rejected(self):
        self.dispatch()
        self.dispatch("PostToolUse")
        self.handback({"answer": 1}, role=VERIFIER)
        self.lifecycle("SubagentStop")
        self.assertEqual(self.receipts()[0]["status"], "identity_conflict")

    def test_provider_id_reuse_does_not_bind_old_result_to_new_attempt(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed", result={"answer": 1})
        self.dispatch(call="second")
        self.dispatch("PostToolUse", call="second", status="completed", result={"answer": 1})
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_changed_dispatch_input_is_rejected(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed", result={"answer": 1}, prompt="changed")
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_expanded_optional_defaults_and_unknown_fields_do_not_break_pairing(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed", result={"answer": 1},
                      name=None, resume=None, fork=False, run_in_background=False, new_field="ignored")
        self.require({"answer": 1})

    def test_missing_prompt_does_not_establish_dispatch_provenance(self):
        with self.assertRaisesRegex(ValueError, "nonempty prompt"):
            self.dispatch(prompt=None)
        self.assertEqual(self.receipts(), [])

    def test_teammate_result_is_diagnosable_and_not_accepted(self):
        self.dispatch(name="analyst-display")
        self.dispatch("PostToolUse", status="teammate_spawned", name="analyst-display")
        self.handback({"answer": 1}, role="analyst-display")
        self.lifecycle("SubagentStop", role="analyst-display")
        self.assertEqual(self.receipts()[0]["status"], "unsupported_mode")
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_fork_cannot_bypass_dispatch_checks_via_lifecycle_start(self):
        self.dispatch(fork=True)
        self.lifecycle("SubagentStart")
        self.lifecycle("SubagentStop", {"answer": 1})
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.dispatch("PostToolUse", status="completed", result={"answer": 1}, fork=True)
        self.assertEqual(self.receipts()[0]["status"], "unsupported_mode")

    def test_handback_cannot_be_replaced_by_valid_closing_json(self):
        self.dispatch()
        self.dispatch("PostToolUse")
        self.handback("{invalid")
        self.lifecycle("SubagentStop", {"answer": 1})
        self.assertEqual(self.receipts()[0]["status"], "invalid_output")

    def test_conflicting_reports_do_not_silently_choose_latest(self):
        self.dispatch()
        self.dispatch("PostToolUse")
        self.handback({"answer": 1})
        self.handback({"answer": 2})
        self.lifecycle("SubagentStop")
        self.assertEqual(self.receipts()[0]["status"], "identity_conflict")

    def test_failure_and_cancellation_cannot_be_passed_by_a_late_stop(self):
        self.dispatch()
        self.dispatch("PostToolUseFailure")
        self.handback({"answer": 1})
        self.lifecycle("SubagentStop")
        self.assertEqual(self.receipts()[0]["status"], "execution_failed")

    def test_verifier_bundle_is_fixed_before_dispatch_and_old_result_is_stale(self):
        # Intake fixture can test the receipt boundary without manufacturing a
        # full business acceptance. Existing full-flow tests cover Gate stages.
        self.state["verifier_bundle"] = {"sha256": "bundle-1", "candidate_sha256": "candidate-1"}
        self.save_state()
        self.dispatch(role=VERIFIER)
        self.dispatch("PostToolUse", role=VERIFIER, status="completed", result={"answer": 1})
        self.require({"answer": 1}, role="verifier", bundle="bundle-1")
        self.state["verifier_bundle"] = {"sha256": "bundle-2", "candidate_sha256": "candidate-2"}
        self.save_state()
        with self.assertRaises(GateError):
            self.require({"answer": 1}, role="verifier", bundle="bundle-2")

    def test_request_change_and_repair_epoch_invalidate_v2_receipt(self):
        self.dispatch()
        self.dispatch("PostToolUse", status="completed", result={"answer": 1})
        for key, changed in (("request_sha256", "changed"), ("evidence_epoch", 1)):
            original = self.state[key]
            self.state[key] = changed
            with self.assertRaises(GateError):
                self.require({"answer": 1})
            self.state[key] = original

    def test_old_v1_receipt_remains_readable_and_unmodified(self):
        path = self.run / "agent-invocations/legacy.json"
        output = self.run / "agent-results/legacy.json"
        write_json_atomic(output, {"answer": 1})
        from aidevflow.agents import _digest
        legacy = {"id": "legacy", "agent_type": ROLE, "status": "completed", "evidence_epoch": 0,
                  "result_path": output.relative_to(self.root).as_posix(), "result_sha256": _digest({"answer": 1})}
        write_json_atomic(path, legacy)
        before = path.read_bytes()
        self.dispatch()
        self.require({"answer": 1})
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_process_observations_are_serialized(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "parallel",
                   "tool_use_id": "one", "tool_input": {"subagent_type": ROLE, "prompt": "x"}}
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        command = [sys.executable, "-B", str(FRAMEWORK / "scripts/aidevflow_hook.py"), "trace-agent"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True, env=env), range(4)))
        self.assertTrue(all(r.returncode == 0 for r in results))
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(len(list((self.run / "agent-observations").glob("*.json"))), 1)

    def test_observation_bad_json_is_nonblocking_but_guard_stays_strict(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.root))
        command = [sys.executable, "-B", str(FRAMEWORK / "scripts/aidevflow_hook.py")]
        for action, code in (("trace-agent", 0), ("guard", 2)):
            result = subprocess.run([*command, action], input=b'{private-invalid', capture_output=True, env=env)
            self.assertEqual(result.returncode, code, result.stderr)
        self.assertNotIn("private-invalid", (self.root / ".aidevflow/events.jsonl").read_text(encoding="utf-8"))

    def test_doctor_does_not_modify_run_or_claim_live_compatibility(self):
        before = (self.run / "run-state.json").read_bytes()
        result = doctor(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(result["missing_hooks"])
        self.assertEqual(before, (self.run / "run-state.json").read_bytes())
        self.assertIn("No live role dispatch", " ".join(result["warnings"]))

    def test_cli_doctor_on_corrupt_state_does_not_append_an_event(self):
        (self.run / "run-state.json").write_text("{broken", encoding="utf-8")
        events = self.root / ".aidevflow/events.jsonl"
        result = subprocess.run([sys.executable, "-B", str(FRAMEWORK / "scripts/aidevflow.py"),
                                 "--root", str(self.root), "doctor"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(events.exists())


class HookMigrationTests(unittest.TestCase):
    def test_migrate_owned_groups_preserve_mixed_user_hooks_and_repeat_safely(self):
        source = read_json(FRAMEWORK / ".claude/settings.json")
        ours = {"type": "command", "command": 'python "${CLAUDE_PROJECT_DIR}/scripts/aidevflow_hook.py" trace-agent', "timeout": 15}
        user = {"type": "command", "command": "my-audit.exe"}
        target = {"env": {"USER_VALUE": "keep"}, "hooks": {"SubagentStart": [{"matcher": "^old$", "hooks": [ours, user]}]}}
        merge_settings(target, source)
        once = copy.deepcopy(target)
        merge_settings(target, source)
        self.assertEqual(target, once)
        self.assertEqual(target["env"], {"USER_VALUE": "keep"})
        self.assertIn({"matcher": "^old$", "hooks": [user]}, target["hooks"]["SubagentStart"])
        generated = [g for g in target["hooks"]["SubagentStart"] if g.get("matcher") == "*"]
        self.assertEqual(len(generated), 1)
