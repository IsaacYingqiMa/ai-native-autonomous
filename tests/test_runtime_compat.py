from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_agent_observer as observer_fixture
from test_managed import ManagedFixture
from aidevflow.gate import Gate, GateError, read_json, write_json_atomic, validate_contract
from aidevflow.receipts import execution_context
from aidevflow.runtime import inspect_session, recover, remember_session
from aidevflow.continuation import decide, set_owner, MAX_TOTAL
from aidevflow.methods import observe_skill, method_status


class RuntimeTests(unittest.TestCase):
    setUp = observer_fixture.ObserverTests.setUp
    save_state = observer_fixture.ObserverTests.save_state
    dispatch = observer_fixture.ObserverTests.dispatch
    require = observer_fixture.ObserverTests.require
    receipts = observer_fixture.ObserverTests.receipts
    lifecycle = observer_fixture.ObserverTests.lifecycle

    def native(self, *, terminal=True, success=True, omit_parent_result=False, extra=None):
        self.home = self.root / ".native-claude"
        self.env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        parent = self.home / "projects/fixture/parent.jsonl"
        parent.parent.mkdir(parents=True, exist_ok=True)
        child = parent.with_suffix("") / "subagents/agent-agent-1.jsonl"
        child.parent.mkdir(parents=True, exist_ok=True)
        def row(kind, content, **metadata):
            return {"type": kind, "sessionId": "parent", "cwd": str(self.root),
                    "timestamp": "2026-10-27T09:00:01.123Z", "uuid": "id-" + kind,
                    "version": "2.1.fixture", "message": {"content": content}, **metadata}
        request = row("assistant", [{"type": "tool_use", "id": "call-1", "name": "Agent", "input": {
            "subagent_type": observer_fixture.ROLE, "prompt": "Read the fixed input and return JSON", **(extra or {})}}])
        returned = row("user", [{"type": "tool_result", "tool_use_id": "call-1", "content": "native metadata"}],
                       toolUseResult={"status": "async_launched", "agentId": "agent-1", "additive": 42})
        delivered = row("assistant", [{"type": "tool_use", "id": "handback-1", "name": "SubagentHandback",
                                       "input": {"message": '{"answer":1}'}}], agentId="agent-1", isSidechain=True)
        result = row("user", [{"type": "tool_result", "tool_use_id": "handback-1", "content": "delivered"}],
                     toolUseResult={"success": success}, toolEndsTurn=terminal, agentId="agent-1", isSidechain=True)
        parent.write_text("\n".join(json.dumps(r) for r in ([request] if omit_parent_result else [request, returned])) + "\n", encoding="utf-8")
        child.write_text("\n".join(json.dumps(r) for r in [delivered, result]) + "\n", encoding="utf-8")
        return parent, child

    def test_cancelled_handback_is_recovered_from_native_success_without_fake_start(self):
        self.dispatch()
        self.native()
        self.require({"answer": 1})  # Automatic recovery at the Gate boundary.
        receipt = self.receipts()[0]
        self.assertEqual(receipt["status"], "completed")
        self.assertFalse(receipt["start_observed"])
        self.assertEqual(receipt["result_transport"], "claude-transcript-v1")
        observations = [read_json(p) for p in (self.run / "agent-observations").glob("*.json")]
        self.assertFalse(any(o["kind"] in ("start", "stop") for o in observations))
        self.assertEqual(recover(self.root)["recovered_events"], 0)

    def test_native_inspection_is_read_only(self):
        self.dispatch()
        parent, _ = self.native()
        before = {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()}
        report = inspect_session(self.root, "parent")
        after = {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(len(report["events"]), 3)
        self.assertEqual(report["running_versions"], ["2.1.fixture"])

    def test_launch_only_and_failed_handback_do_not_establish_completion(self):
        self.dispatch()
        self.native(success=False)
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.assertFalse(self.receipts()[0]["execution_finished"])

    def test_nonterminal_handback_needs_actual_stop(self):
        self.dispatch()
        self.native(terminal=False)
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.lifecycle("SubagentStop")
        self.require({"answer": 1})

    def test_missing_parent_bridge_is_never_guessed_from_child_prose(self):
        self.dispatch()
        self.native(omit_parent_result=True)
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_changed_dispatch_cannot_replace_captured_input(self):
        self.dispatch()
        self.native(extra={"prompt": "changed"})
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_old_epoch_cannot_be_relabelled_during_recovery(self):
        self.dispatch()
        self.native()
        self.state["evidence_epoch"] = 1
        self.save_state()
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_missing_pre_hook_recovers_using_historical_gate_snapshot(self):
        parent, _ = self.native()
        gate = Gate(self.root)
        gate._event("OBS-1", "intake_started", state_snapshot=self.state)
        # Set the real event timestamp earlier than the native call.
        event = read_json(gate.events_path)
        event["ts"] = "2026-10-27T09:00:00Z"
        gate.events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        remember_session(self.root, {"session_id": "parent", "transcript_path": str(parent)})
        self.require({"answer": 1})

    def test_wrong_project_and_wrong_session_never_imported(self):
        self.dispatch()
        parent, child = self.native()
        rows = [json.loads(s) for s in parent.read_text(encoding="utf-8").splitlines()]
        for row in rows:
            row["cwd"] = str(self.root.parent)
        parent.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        self.assertEqual(inspect_session(self.root, "parent")["events"], [])

    def test_partial_tail_tolerated_but_complete_corrupt_record_rejected(self):
        self.dispatch()
        parent, _ = self.native()
        with parent.open("a", encoding="utf-8") as out:
            out.write('{"partial')
        self.assertEqual(len(inspect_session(self.root, "parent")["events"]), 3)
        with parent.open("a", encoding="utf-8") as out:
            out.write("\n")
        with self.assertRaisesRegex(ValueError, "corrupt"):
            inspect_session(self.root, "parent")

    def test_external_transcript_locator_not_registered(self):
        self.native()
        outside = self.root / "parent.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        remember_session(self.root, {"session_id": "parent", "transcript_path": str(outside)})
        self.assertFalse((self.root / ".aidevflow/runtime-sessions").exists())

    def test_native_fork_remains_unsupported(self):
        self.dispatch(fork=True)
        self.native(extra={"fork": True})
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.assertEqual(self.receipts()[0]["status"], "unsupported_mode")

    def test_unknown_parent_metadata_does_not_certify_missing_agent_identity(self):
        self.dispatch()
        parent, _ = self.native()
        rows = [json.loads(s) for s in parent.read_text(encoding="utf-8").splitlines()]
        rows[1]["toolUseResult"] = {"future_status": "done", "text": "agentId: agent-1"}
        parent.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        with self.assertRaises(GateError):
            self.require({"answer": 1})

    def test_conflicting_native_handbacks_are_not_silently_overwritten(self):
        self.dispatch()
        _, child = self.native()
        rows = [json.loads(s) for s in child.read_text(encoding="utf-8").splitlines()]
        rows[0]["message"]["content"][0]["id"] = "handback-2"
        rows[0]["message"]["content"][0]["input"]["message"] = '{"answer":2}'
        rows[1]["message"]["content"][0]["tool_use_id"] = "handback-2"
        with child.open("a", encoding="utf-8") as out:
            out.write("\n".join(json.dumps(r) for r in rows) + "\n")
        with self.assertRaises(GateError):
            self.require({"answer": 1})
        self.assertEqual(self.receipts()[0]["status"], "identity_conflict")


class ContinuationTests(unittest.TestCase):
    setUp = observer_fixture.ObserverTests.setUp
    save_state = observer_fixture.ObserverTests.save_state

    def adaptive(self):
        self.state["execution_policy"] = {"continuation_owner": "bounded_stop", "method_evidence": "artifact_gated"}
        self.save_state()

    def test_legacy_stop_stays_observational(self):
        self.assertIsNone(decide(self.root, {}))

    def test_premature_stop_continues_twice_then_pauses_without_changing_business(self):
        self.adaptive()
        before = (self.run / "run-state.json").read_bytes()
        self.assertEqual(decide(self.root, {})["decision"], "block")
        self.assertEqual(decide(self.root, {"stop_hook_active": True})["decision"], "block")
        self.assertNotIn("decision", decide(self.root, {"stop_hook_active": True}))
        self.assertEqual(before, (self.run / "run-state.json").read_bytes())
        self.assertTrue(read_json(self.run / "continuation.json")["attention_required"])

    def test_progress_resets_stall_counter_but_total_is_bounded(self):
        self.adaptive()
        decide(self.root, {})
        decide(self.root, {})
        self.state["updated_at"] = "progress"
        self.save_state()
        self.assertEqual(decide(self.root, {})["decision"], "block")
        path = self.run / "continuation.json"
        value = read_json(path)
        value["total"] = MAX_TOTAL
        write_json_atomic(path, value)
        self.assertNotIn("decision", decide(self.root, {}))

    def test_native_owner_user_decision_and_subagent_never_auto_continue(self):
        self.adaptive()
        set_owner(self.root, "native")
        self.assertIsNone(decide(self.root, {}))
        self.state["execution_policy"]["continuation_owner"] = "bounded_stop"
        for status in ("needs_human", "awaiting_user_input", "delivery_ready", "archived"):
            self.state["status"] = status
            self.save_state()
            self.assertIsNone(decide(self.root, {}))
        self.state["status"] = "active"
        self.save_state()
        self.assertIsNone(decide(self.root, {"agent_id": "child"}))

    def test_protection_block_does_not_trigger_tool_retry_loop(self):
        self.adaptive()
        Gate(self.root)._event("OBS-1", "hook_block", rule="protected-shell-write")
        self.assertIsNone(decide(self.root, {}))


class AdaptiveMethodTests(ManagedFixture):
    def test_complete_artifact_gated_flow_without_skill_telemetry_still_requires_verifier(self):
        from aidevflow.agents import capture_event
        from aidevflow.gate import VERDICT_SCHEMA
        # Disposable fixture policy setup, never a supported old-run migration.
        state, run, _ = self.gate._load_active()
        state["execution_policy"] = {"method_evidence": "artifact_gated"}
        state["method_observations_required"] = False
        write_json_atomic(run / "run-state.json", state)
        self.observe = lambda phases: None
        self.build()
        self.assertFalse((run / "method-observations").exists())
        self.gate.run_tests("green", "tests/test_feature.py")
        self.attest()
        self.attest("manual")
        self.gate.advance("verify")
        bundle = self.gate.prepare_verifier()
        verdict = {"schema": VERDICT_SCHEMA, "task_id": "MANAGED", "spec_version": bundle["spec_version"],
                   "bundle_sha256": bundle["sha256"], "items": [{"id": "REQ-1", "verdict": "pass",
                   "evidence": ["diff.patch"], "reason": "Protocol fixture only"}], "risks": []}
        self._write_json("verdict.json", verdict)
        state, _, _ = self.gate._load_active()
        state["agent_receipts_required"] = True
        write_json_atomic(run / "run-state.json", state)
        with self.assertRaisesRegex(GateError, "Missing completed execution receipt"):
            self.gate.record_verdict("verdict.json")
        common = {"session_id": "fixture-verifier", "agent_id": "verifier-1", "agent_type": "aidevflow-independent-verifier"}
        capture_event(self.root, {**common, "hook_event_name": "SubagentStart"})
        capture_event(self.root, {**common, "hook_event_name": "PostToolUse", "tool_name": "SubagentHandback",
                                  "tool_input": {"message": json.dumps(verdict)}})
        capture_event(self.root, {**common, "hook_event_name": "SubagentStop"})
        self.gate.record_verdict("verdict.json")
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready")

    def test_new_intake_selects_artifact_gates_without_migrating_legacy_runs(self):
        self.assertTrue(self.gate._load_active()[0]["method_observations_required"])
        self.gate.active_path.unlink()
        (self.root / "request.md").write_text("Build feature", encoding="utf-8")
        state = self.gate.start_intake("request.md")
        self.gate._pin_method_policy(state, self.root / ".aidevflow/runs" / state["task_id"], self.contract)
        self.assertFalse(state["method_observations_required"])
        self.assertEqual(state["execution_policy"]["method_evidence"], "artifact_gated")

    def test_artifact_gated_review_works_without_skill_hooks_but_not_without_tests(self):
        self.design(review=False)
        state, run, _ = self.gate._load_active()
        state["execution_policy"] = {"method_evidence": "artifact_gated"}
        state["method_observations_required"] = False
        write_json_atomic(run / "run-state.json", state)
        for p in (run / "method-observations").glob("*.json"):
            p.unlink()
        self.review()
        (self.root / "tests/test_feature.py").unlink()
        with self.assertRaises(GateError):
            self.gate.freeze_tests([])

    def test_manual_probe_cannot_create_formal_observation(self):
        self._reach_design()
        with self.assertRaisesRegex(ValueError, "native session"):
            observe_skill(self.root, {"hook_event_name": "PostToolUse", "tool_name": "Skill",
                "session_id": "manual-probe", "tool_input": {"skill": "zsspec-req"}})
        self.assertFalse((self.gate._load_active()[1] / "method-observations").exists())

    def test_contract_rejects_globs_early_in_frozen_file_lists(self):
        for key in ("test_support_files", "test_files", "design_artifacts"):
            contract = copy.deepcopy(self.contract)
            contract[key] = ["tests/fixtures/**"]
            self.assertTrue(any("concrete file paths" in e for e in validate_contract(contract)))

    def test_pre_skill_is_requested_and_post_is_observed_not_completion(self):
        self._reach_design()
        native = self.root / ".native-claude/projects/fixture/managed-fixture.jsonl"
        native.parent.mkdir(parents=True, exist_ok=True)
        native.touch()
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Skill", "session_id": "managed-fixture",
                   "tool_use_id": "real-tool", "transcript_path": str(native), "tool_input": {"skill": "zsspec-req"}}
        native.write_text(json.dumps({"type": "assistant", "sessionId": "managed-fixture", "cwd": str(self.root),
            "message": {"content": [{"type": "tool_use", "name": "Skill", "id": "real-tool", "input": {"skill": "zsspec-req"}}]}}) + "\n", encoding="utf-8")
        state, run, _ = self.gate._load_active()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / ".native-claude")}):
            observe_skill(self.root, payload)
            step = method_status(self.root, run, state, self.contract)[0]
            self.assertEqual(step["status"], "requested")
            observe_skill(self.root, {**payload, "hook_event_name": "PostToolUse"})
            self.assertEqual(method_status(self.root, run, state, self.contract)[0]["status"], "observed")
            with self.assertRaisesRegex(ValueError, "no matching native"):
                observe_skill(self.root, {**payload, "tool_use_id": "made-up-probe"})

    def test_project_gitignore_drift_is_advisory_core_runtime_drift_is_not(self):
        from aidevflow.diagnostics import doctor
        ignore = self.root / ".gitignore"
        ignore.write_text(".aidevflow/runs/\n.aidevflow/events.jsonl\n.aidevflow/active-run.json\n", encoding="utf-8")
        core = self.root / ".aidevflow/runtime/aidevflow/hooks.py"
        core.parent.mkdir(parents=True)
        core.write_text("# original", encoding="utf-8")
        files = {str(p.relative_to(self.root)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ignore, core)}
        write_json_atomic(self.root / ".aidevflow/runtime-manifest.json", {"files": files})
        with ignore.open("a", encoding="utf-8") as out:
            out.write("node_modules/\n")
        with patch("aidevflow.diagnostics.runtime_identity", return_value={}):
            report = doctor(self.root)
            self.assertEqual(report["configuration_drift"], [".gitignore"])
            self.assertEqual(report["manifest_mismatches"], [])
            core.write_text("# changed", encoding="utf-8")
            self.assertIn(".aidevflow/runtime/aidevflow/hooks.py", doctor(self.root)["manifest_mismatches"])
            ignore.write_text("node_modules/\n", encoding="utf-8")
            self.assertIn(".gitignore", doctor(self.root)["manifest_mismatches"])
