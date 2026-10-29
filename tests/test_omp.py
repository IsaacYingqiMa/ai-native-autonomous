from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_gate import GateFixture
from test_managed import ManagedFixture
from aidevflow.gate import GateError, read_json, write_json_atomic
from aidevflow.omp import observe, recover, recover_branch
from aidevflow.runtimes import selected, skills_root
from aidevflow.bridge import guard
from aidevflow.installer import install
from aidevflow.diagnostics import doctor


class OmpTests(GateFixture):
    def setUp(self):
        super().setUp()
        self.gate.active_path.unlink()  # Disposable legacy fixture, not a product run.
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "omp", "skills_root": ".claude/skills", "python": sys.executable})
        (self.root / "request.md").write_text("Return the expected value", encoding="utf-8")
        self.state = self.gate.start_intake("request.md")
        self.role = "aidevflow-intent-analyst"
        self.request = {"type": "tool_call", "session_id": "session-1", "toolCallId": "call-1",
                        "input": {"agent": self.role, "task": "Read request.md and return IntentDraft", "solutionSpace": "bounded"}}

    def response(self, **changes):
        result = {"id": "agent-1", "agent": self.role, "exitCode": 0,
                  "structuredOutput": {"status": "valid", "data": self._draft()}}
        result.update(changes)
        return {**self.request, "type": "tool_result", "details": {"results": [result]}, "isError": False}

    def test_native_result_registers_intent_with_a_real_bound_receipt(self):
        observe(self.root, self.request)
        receipt = observe(self.root, self.response())
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["runtime"], "omp")
        result = self.gate.record_intent(receipt["result_path"])
        self.assertEqual(result["status"], "awaiting_user_input")

    def test_unknown_and_legacy_schemas_cannot_downgrade_an_omp_receipt(self):
        from aidevflow.receipts import completed_results
        observe(self.root, self.request)
        receipt = observe(self.root, self.response())
        for schema in (None, "aidevflow/invocation-v1", "aidevflow/invocation-v999"):
            changed = {**receipt, "receipt_schema": schema}
            self.assertEqual(completed_results([changed], "intent_analyst", receipt["result_sha256"], 0, None,
                                               receipt["context"], runtime="omp"), [])

    def test_launch_content_is_never_completion(self):
        observe(self.root, self.request)
        launch = {**self.request, "type": "tool_result", "content": [{"text": json.dumps(self._draft())}],
                  "details": {"results": [], "async": {"state": "running"}}}
        self.assertEqual(observe(self.root, launch)["status"], "dispatched")
        self._write_json("draft.json", self._draft())
        with self.assertRaises(GateError):
            self.gate.record_intent("draft.json")

    def test_orphan_result_cannot_create_dispatch(self):
        with self.assertRaises(GateError):
            observe(self.root, self.response())

    def test_unknown_native_identity_shape_is_not_guessed(self):
        observe(self.root, self.request)
        with self.assertRaises(GateError):
            observe(self.root, self.response(id=["agent-1"]))

    def test_cancelled_or_invalid_schema_cannot_pass(self):
        observe(self.root, self.request)
        self.assertEqual(observe(self.root, self.response(aborted=True))["status"], "execution_failed")
        self.assertNotEqual(observe(self.root, self.response())["status"], "completed")

    def test_invalid_native_data_is_not_rescued_by_prose(self):
        observe(self.root, self.request)
        reply = self.response(structuredOutput={"status": "invalid", "data": self._draft()}, output=json.dumps(self._draft()))
        self.assertEqual(observe(self.root, reply)["status"], "invalid_output")

    def test_duplicate_delivery_is_idempotent(self):
        observe(self.root, self.request)
        result = observe(self.root, self.response())
        with patch("aidevflow.gate.utc_now", return_value="2099-01-01T00:00:00Z"):
            self.assertEqual(observe(self.root, self.response()), result)
        self.assertEqual(recover(self.root)["recovered_events"], 0)

    def test_changed_input_and_conflicting_output_are_rejected(self):
        observe(self.root, self.request)
        changed = self.response()
        changed["input"] = {**self.request["input"], "task": "different task"}
        self.assertEqual(observe(self.root, changed)["status"], "identity_conflict")
        self.assertEqual(observe(self.root, self.response())["status"], "identity_conflict")

    def test_stale_epoch_is_not_relabeled(self):
        observe(self.root, self.request)
        state, run, _ = self.gate._load_active()
        state["evidence_epoch"] = 1
        write_json_atomic(run / "run-state.json", state)
        self.assertEqual(observe(self.root, self.response())["status"], "stale")

    def test_runtime_binding_survives_default_change_and_rejects_tampering(self):
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "claude-code"})
        self.assertEqual(selected(self.root), "omp")
        state, run, _ = self.gate._load_active()
        state["execution_binding"]["runtime"] = "claude-code"
        write_json_atomic(run / "run-state.json", state)
        with self.assertRaises(GateError):
            self.gate._load_active()

    def test_missing_modern_binding_reference_is_not_treated_as_legacy(self):
        state, run, _ = self.gate._load_active()
        del state["execution_binding_ref"]
        write_json_atomic(run / "run-state.json", state)
        with self.assertRaises(GateError):
            self.gate._load_active()

    def test_native_branch_recovers_missed_result_callback(self):
        observe(self.root, self.request)
        entries = [{"message": {"role": "assistant", "content": [
            {"type": "toolCall", "name": "task", "id": "call-1", "arguments": self.request["input"]}]}},
            {"message": {"role": "toolResult", "toolName": "task", "toolCallId": "call-1", "details": self.response()["details"]}}]
        self.assertEqual(recover_branch(self.root, "wrong-session", entries), 0)
        self.assertEqual(recover_branch(self.root, "session-1", entries), 1)
        self.assertEqual(recover_branch(self.root, "session-1", entries), 0)

    def test_verifier_requires_pinned_bundle_in_dispatch(self):
        request = copy.deepcopy(self.request)
        request["input"]["agent"] = "aidevflow-independent-verifier"
        with self.assertRaises(GateError):
            observe(self.root, request)

    def test_control_paths_and_unclassified_mutations_are_blocked(self):
        for tool, inp in (("write", {"path": ".omp/extensions/aidevflow/index.ts"}),
                          ("edit", {"path": "../outside.py"}), ("eval", {"code": "pass"}),
                          ("write", {"path": "xd://eval"}), ("lsp", {"action": "rename"})):
            self.assertTrue(guard(self.root, {"toolName": tool, "input": inp})["block"])
        self.assertFalse(guard(self.root, {"toolName": "read", "input": {"path": "request.md"}})["block"])
        command = 'python scripts/aidevflow.py status\npython -c "from pathlib import Path; Path(\'.omp/extensions/aidevflow/index.ts\').write_text(\'changed\')"'
        self.assertTrue(guard(self.root, {"toolName": "bash", "input": {"command": command}})["block"])

    def test_verifier_reads_only_bundle(self):
        payload = {"toolName": "read", "input": {"path": "request.md"}, "actor": {"name": "aidevflow-independent-verifier"}}
        self.assertTrue(guard(self.root, payload)["block"])

    def test_native_lsp_previews_are_read_only_but_apply_is_not(self):
        for action in ("status", "capabilities", "implementation", "code_actions", "rename", "rename_file"):
            self.assertFalse(guard(self.root, {"toolName": "lsp", "input": {"action": action, "apply": False}})["block"])
        for action in ("rename", "rename_file", "code_actions"):
            self.assertTrue(guard(self.root, {"toolName": "lsp", "input": {"action": action, "apply": True}})["block"])

    def test_one_item_batch_uses_native_result_and_shared_context(self):
        request = {**self.request, "input": {"context": "shared", "tasks": [self.request["input"]]}}
        observe(self.root, request)
        result = {**self.response(), "input": request["input"]}
        self.assertEqual(observe(self.root, result)["status"], "completed")

    def test_hashline_checks_all_files_and_move_destinations(self):
        state, run, _ = self.gate._load_active()
        state["status"] = "active"
        write_json_atomic(run / "run-state.json", state)
        for text in ("[specs/aidevflow-intake/draft.md#ABCD]\nPUT >1:\n+ok\n[.aidevflow/run-state.json#1234]\nREM",
                     "[specs/aidevflow-intake/draft.md#ABCD]\nMV .omp/extensions/aidevflow/index.ts"):
            self.assertTrue(guard(self.root, {"toolName": "edit", "input": {"input": text}})["block"])

    def test_same_native_agent_identity_cannot_complete_another_call(self):
        observe(self.root, self.request)
        observe(self.root, self.response())
        next_request = {**self.request, "toolCallId": "call-2"}
        observe(self.root, next_request)
        self.assertEqual(observe(self.root, {**self.response(), "toolCallId": "call-2"})["status"], "identity_conflict")

    def test_recovery_preserves_conflict_after_receipt_loss(self):
        receipt = observe(self.root, self.request)
        observe(self.root, self.response())
        value = self._draft()
        value["problem"] = "Changed result from the same native execution"
        conflict = observe(self.root, self.response(structuredOutput={"status": "valid", "data": value}))
        self.assertEqual(conflict["status"], "identity_conflict")
        _, run, _ = self.gate._load_active()
        (run / "agent-invocations" / f"{receipt['id']}.json").unlink()
        self.assertEqual(recover(self.root)["recovered_events"], 1)
        self.assertEqual(read_json(run / "agent-invocations" / f"{receipt['id']}.json")["status"], "identity_conflict")

    def test_role_tool_and_schema_overrides_are_rejected(self):
        for key, value in (("tools", ["bash"]), ("outputSchema", {"type": "object"})):
            request = copy.deepcopy(self.request)
            request["input"][key] = value
            with self.assertRaises(GateError):
                observe(self.root, request)

    def test_summary_never_hashes_candidate(self):
        with patch.object(self.gate, "_candidate", side_effect=AssertionError("expensive hash called")):
            self.assertEqual(self.gate.status(summary=True)["candidate_freshness"], "not_checked")

    def test_public_role_context_is_read_only_and_binds_exact_inputs(self):
        from aidevflow.contexts import role_context
        before = {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()}
        context = role_context(self.root, "intent_analyst")
        self.assertIn(self.state["request_snapshot_path"], context["task_call"]["tasks"][0]["task"])
        self.assertIn(self.state["task_id"], context["task_call"]["context"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()})
        with self.assertRaises(GateError):
            role_context(self.root, "contract_planner")
        process = subprocess.run([sys.executable, "-B", "scripts/aidevflow.py", "--root", str(self.root),
                                  "role-context", "--role", "contract_planner"],
                                 capture_output=True, cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()})

    def test_another_coordinator_is_refused_and_os_releases_the_lease(self):
        workers = []
        try:
            def start():
                worker = subprocess.Popen([sys.executable, "-B", "-m", "aidevflow.bridge", str(self.root)],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding="utf-8", cwd=Path(__file__).resolve().parents[1])
                workers.append(worker)
                return worker
            def call(worker, ident):
                worker.stdin.write(json.dumps({"protocol": 1, "id": ident, "action": "guard", "payload": {
                    "toolName": "write", "input": {"path": "specs/aidevflow-intake/draft.md"},
                    "actor": {"kind": "main", "name": "main"}}}) + "\n")
                worker.stdin.flush()
                return json.loads(worker.stdout.readline())
            first, second = start(), start()
            self.assertTrue(call(first, "1")["ok"])
            self.assertFalse(call(second, "2")["ok"])
            first.stdin.close()
            first.wait(timeout=5)
            self.assertTrue(call(second, "3")["ok"])
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.terminate()
                worker.wait(timeout=5)
                for stream in (worker.stdin, worker.stdout, worker.stderr):
                    if stream and not stream.closed:
                        stream.close()

    def test_project_method_root_is_pinned(self):
        self.assertEqual(skills_root(self.root), self.root / ".claude/skills")
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "omp", "skills_root": ".omp/skills"})
        self.assertEqual(skills_root(self.root), self.root / ".claude/skills")

    def test_install_is_repeatable_and_preserves_user_and_vendor_assets(self):
        framework = Path(__file__).resolve().parents[1]
        settings = self.root / ".omp/config.yml"
        settings.parent.mkdir(exist_ok=True)
        settings.write_text("tools:\n  approvalMode: always-ask\n", encoding="utf-8")
        before = settings.read_bytes()
        install(framework, self.root, runtime="omp")
        install(framework, self.root, runtime="omp")
        self.assertEqual(settings.read_bytes(), before)
        self.assertFalse((self.root / ".claude/settings.json").exists())
        self.assertTrue((self.root / ".omp/agents/aidevflow-independent-verifier.md").is_file())
        analyst_text = (self.root / ".omp/agents/aidevflow-intent-analyst.md").read_text(encoding="utf-8")
        schema = json.loads(next(line.removeprefix("output: ") for line in analyst_text.splitlines() if line.startswith("output: ")))
        self.assertEqual(schema["properties"]["users"]["type"], "array")
        config = read_json(self.root / ".aidevflow/project.json")
        self.assertEqual(config["runtime"], "omp")
        with patch("aidevflow.runtimes.identity", return_value={"executable": "omp"}):
            report = doctor(self.root)
        self.assertEqual(report["manifest_mismatches"], [])

    def test_runtime_source_drift_is_rejected_at_gate_boundary(self):
        install(Path(__file__).resolve().parents[1], self.root, runtime="omp")
        path = self.root / ".omp/extensions/aidevflow/index.ts"
        path.write_text("changed adapter", encoding="utf-8")
        with self.assertRaises(GateError):
            self.gate.integrity()
        state, _, _ = self.gate._load_active()
        self.assertEqual(state["status"], "needs_human")

    def test_bridge_handles_multiple_frames_and_bad_input_without_stdout_noise(self):
        messages = [{"protocol": 1, "id": "1", "action": "ping"},
                    {"protocol": 1, "id": "2", "action": "status"}]
        process = subprocess.run([sys.executable, "-B", "-m", "aidevflow.bridge", str(self.root)],
                                 input="\n".join(json.dumps(x) for x in messages) + "\n", capture_output=True,
                                 encoding="utf-8", cwd=Path(__file__).resolve().parents[1], timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        frames = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([frame["id"] for frame in frames], ["1", "2"])
        self.assertTrue(all(frame["ok"] for frame in frames))


class PinnedMethodProtectionTests(ManagedFixture):
    def test_config_absence_is_protected_before_a_design_write(self):
        from aidevflow.hooks import guard as file_guard, guard_bash
        from contextlib import redirect_stderr
        from io import StringIO
        self._reach_design()
        with redirect_stderr(StringIO()):
            self.assertEqual(file_guard({"tool_name": "Write", "tool_input": {"file_path": "specs/zsspec.yml"}}, self.root), 2)
            self.assertEqual(guard_bash({}, self.root, "python -c \"from pathlib import Path; Path('specs/zsspec.yml').write_text('changed')\""), 2)
        self.assertFalse((self.root / "specs/zsspec.yml").exists())
        self.assertEqual(self.gate.status()["status"], "active")

    def test_config_cannot_be_declared_as_generated_design_content(self):
        from aidevflow.gate import validate_contract
        self.contract["design_artifacts"].append("specs/zsspec.yml")
        self.assertTrue(any("project setup input" in error for error in validate_contract(self.contract)))
