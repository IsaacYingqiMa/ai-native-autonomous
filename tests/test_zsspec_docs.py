"""Current vendor documents + real behavioral checks; role outputs are explicit protocol fixtures."""
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

from test_gate import GateFixture
from aidevflow.gate import GateError, read_json, sha256_file, validate_contract
from aidevflow.services import design, delivery
from aidevflow.method_adapters.zsspec_docs import PROFILE, STAGES, parse_spec, parse_plan, plan_semantics, project_plan
from aidevflow.hooks import guard

SPEC = """# Return two
## 需求
Return the approved value two.
## 边界
- 做：return two
- 不做：network access, unnecessary for the approved function
## 规格
### S1 Return two
- 做什么：Return two from the existing function
- 规则：调用 → 返回 2
- 验证：运行 value() → 2
## 设计
### 方案
Modify the existing function.
### 模块与接口
- 修改：src/sample.py::value
### 数据与状态
无
### 取舍
Preserve the existing interface.
## 记录
### 知识维护
Update the sample module contract.
### 冲突声明
无
"""
PLAN = """# Plan
## 任务
### S1 Return two · 未开始
- [ ] S1-1 Implement return two
  - 描述：Modify the existing value function
  - 预期：value() returns exactly two
  - 验收：（回填）
### 回归
- [ ] R1 Replay validation
  - 预期：All approved observations remain correct
  - 验收：（回填）
## 阻塞
无
## 完成
<!-- completion is initially empty -->
"""


class CurrentDocsTests(GateFixture):
    RUNTIME = "omp"
    NATIVE = ".omp"

    def setUp(self):
        super().setUp()
        self.gate.active_path.unlink()
        source = Path(__file__).resolve().parents[1] / ".claude/skills"
        for name in ("zsspec-init", "zsspec-spec", "zsspec-plan", "zsspec-apply", "zsspec-archive"):
            shutil.copytree(source / name, self.root / f"{self.NATIVE}/skills" / name)
        shutil.copyfile(source / "README.md", self.root / f"{self.NATIVE}/skills/README.md")
        self._write_json(".aidevflow/project.json", {"runtime": self.RUNTIME, "skills_root": f"{self.NATIVE}/skills", "python": sys.executable})
        (self.root / "request.md").write_text("Return exactly two; no network access.", encoding="utf-8")
        self.gate.start_intake("request.md")
        self.receipts = patch.object(self.gate, "_require_agent_receipt")
        self.receipts.start()
        self.addCleanup(self.receipts.stop)
        draft = self._draft()
        draft.update(open_questions=[], success_criteria=["value() returns exactly two"], desired_outcome="Return two")
        self._write_json("intent.json", draft)
        self.gate.record_intent("intent.json")
        self.gate.finalize_intent()
        self.prep = design.prepare_method(self.gate, "return-two")
        (self.root / "specs/archive").mkdir(parents=True, exist_ok=True)
        (self.root / "specs/MAP.md").write_text("# Module map\n", encoding="utf-8")
        (self.root / "specs/archive/INDEX.md").write_text("# Change index\n", encoding="utf-8")
        design.record_initialization(self.gate)
        for key, text in (("spec", SPEC), ("plan", PLAN)):
            p = self.root / self.prep[key]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def review(self, kind, outcome="pass", findings=None):
        target = design.prepare_review(self.gate, kind)
        state, _, _ = self.gate._load_active()
        value = {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"], "kind": kind,
                 "input_sha256": target["sha256"], "outcome": outcome, "coverage": {"ACCEPT-1": ["S1"]},
                 "findings": findings or []}
        self._write_json("review.json", value)
        return design.record_review(self.gate, "review.json")

    def bind(self, testing=False, manual=False):
        self.review("spec")
        self.review("plan")
        state, _, _ = self.gate._load_active()
        self.contract.update(task_id=state["task_id"], work_plan_required=True,
            method={"name": "zsspec", "profile": PROFILE, "slug": "return-two", "stages": STAGES,
                    "acceptance_map": {"ACCEPT-1": ["S1"]}, "task_tests": {"S1-1": ["tests/test_two.py"]} if testing else {}},
            design_artifacts=[self.prep["spec"], self.prep["plan"]], evidence_paths=["test-results"],
            acceptance_items=[{"id": "ACCEPT-1", "criterion": "value() returns two"}],
            test_files=["tests/test_two.py"] if testing else [], test_support_files=[],
            red_test_files=["tests/test_two.py"] if testing else [],
            test_commands={"red": [sys.executable, "-B", "{target}"], "green": [sys.executable, "-B", "{target}"]} if testing else
            {"behavior": [sys.executable, "-B", "-c", "from src.sample import value; assert value()==2, value()"]},
            required_checks=[{"id": "C-behavior", "suite": "green" if testing else "behavior", "acceptance_items": ["ACCEPT-1"],
                              **({"target": "tests/test_two.py"} if testing else {})},
                             {"id": "C-static", "kind": "static", "acceptance_items": ["ACCEPT-1"]}])
        if manual:
            self.contract["required_checks"].append({"id": "C-ui", "kind": "manual", "acceptance_items": ["ACCEPT-1"]})
        self._write_json("contract.json", self.contract)
        self.gate.register_contract("contract.json")
        self.gate.advance("design")
        design.compile_plan(self.gate)
        self.gate.advance("build")

    def green(self):
        result = self.gate.run_tests("green" if self.contract["test_files"] else "behavior",
                                     "tests/test_two.py" if self.contract["test_files"] else None)
        self.assertTrue(result["passed"])
        report = self.root / "test-results/static.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("Protocol fixture: actual code reviewed against approved value two, no blocking findings.", encoding="utf-8")
        snapshots = []
        original = self.gate._candidate
        def inspect_candidate(*args, **kwargs):
            value = original(*args, **kwargs)
            snapshots.append(value)
            return value
        try:
            with patch.object(self.gate, "_candidate", side_effect=inspect_candidate):
                self.gate.record_check("C-static", "passed", self.gate.status()["candidate_sha256"], "test coordinator",
                                       "Concrete fixture review of the current function", ["test-results/static.md"])
        except GateError:
            if len(snapshots) >= 2:
                left, right = snapshots[-2:]
                changed = {k: (left["files"].get(k), right["files"].get(k))
                           for k in left["files"].keys() | right["files"].keys()
                           if left["files"].get(k) != right["files"].get(k)}
                raise AssertionError({"candidate_files_changed": changed,
                                      "before": {k: v for k, v in left.items() if k != "files"},
                                      "after": {k: v for k, v in right.items() if k != "files"}})
            raise
        return result

    def reach_verify(self):
        self.bind()
        self.gate.start_work_item("S1-1")
        self.assertFalse(self.gate.run_tests("behavior")["passed"])
        (self.root / "src/sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        result = self.green()
        self.gate.complete_work_item("S1-1", [result["evidence_path"]], "Called value() and observed integer 2")
        self.gate.start_work_item("R1")
        result = self.green()
        self.gate.complete_work_item("R1", [result["evidence_path"]], "Replayed approved behavior; value() returned integer 2")
        self.gate.advance("verify")

    def stage_archive(self):
        prepared = delivery.prepare(self.gate)
        stage = self.root / prepared["workspace"] / "specs"
        destination = stage / "archive/return-two_2026-11-09"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage / "active/return-two"), destination)
        (stage / "archive/INDEX.md").write_text("| 日期 | slug | 摘要 | 模块 | 状态 |\n|---|---|---|---|---|\n| 2026-11-09 | return-two | Return two | sample | completed |\n", encoding="utf-8")
        (stage / "MAP.md").write_text("| 模块 | 职责 |\n|---|---|\n| sample | Return approved value |\n", encoding="utf-8")
        delivery.seal(self.gate)

    def verdict(self):
        bundle = self.gate.prepare_verifier()
        state, _, _ = self.gate._load_active()
        value = {"schema": "aidevflow/verifier-verdict-v1", "task_id": state["task_id"], "spec_version": bundle["spec_version"],
                 "bundle_sha256": bundle["sha256"], "items": [{"id": "ACCEPT-1", "verdict": "pass", "evidence": ["diff.patch"],
                 "reason": "Explicit protocol fixture; actual behavioral execution is in the bundle"}], "risks": []}
        self._write_json("verdict.json", value)
        self.gate.record_verdict("verdict.json")

    def test_structure_and_semantic_progress(self):
        self.assertEqual(set(parse_spec(SPEC)), {"S1"})
        self.assertEqual(len(parse_plan(PLAN)), 2)
        value = project_plan(PLAN, [{"id": "S1-1", "status": "completed", "observation": "Observed integer 2"}, {"id": "R1", "status": "not_started"}])
        self.assertEqual(plan_semantics(PLAN), plan_semantics(value))
        self.assertNotEqual(plan_semantics(PLAN), plan_semantics(PLAN.replace("exactly two", "exactly three")))

    def test_approved_requirement_scope_and_idempotent_preparation(self):
        self.assertEqual(design.prepare_method(self.gate, "return-two"), self.prep)
        with self.assertRaises(GateError):
            design.prepare_method(self.gate, "another-change")
        self.assertEqual(guard({"tool_name": "Write", "tool_input": {"file_path": self.prep["spec"]}}, self.root), 0)
        self.assertNotEqual(guard({"tool_name": "Write", "tool_input": {"file_path": "src/sample.py"}}, self.root), 0)

    def test_independent_reviews_auto_repair_and_stale_rejection(self):
        self.review("spec", "changes_required", ["Clarify the existing function location"])
        self.assertNotEqual(self.gate.status()["status"], "needs_human")
        self.assertIn("Repair", self.gate.status()["next_actions"][0])
        self.review("spec")
        (self.root / self.prep["spec"]).write_text(SPEC.replace("Return two from", "Return exactly two from"), encoding="utf-8")
        with self.assertRaisesRegex(GateError, "stale"):
            design.require_review(self.gate, self.gate._load_active()[0], "spec")

    def test_no_test_task_can_reach_build_without_fake_red(self):
        self.bind()
        self.assertEqual(validate_contract(self.contract), [])
        self.assertEqual(self.gate.status()["current_stage"], "build")
        self.gate.start_work_item("S1-1")
        self.assertEqual(guard({"tool_name": "Write", "tool_input": {"file_path": "src/sample.py"}}, self.root), 0)

    def test_task_level_real_red_green_and_progress(self):
        self.bind(testing=True)
        self.gate.start_work_item("S1-1")
        self.assertNotEqual(guard({"tool_name": "Write", "tool_input": {"file_path": "src/sample.py"}}, self.root), 0)
        p = self.root / "tests/test_two.py"
        p.write_text("import sys,unittest\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]))\nfrom src.sample import value\nclass Test(unittest.TestCase):\n def test_two(self): self.assertEqual(value(),2)\nunittest.main()\n", encoding="utf-8")
        report = self.root / "test-results/assertions.md"
        report.parent.mkdir(exist_ok=True)
        report.write_text("Actual assertion checked: value()==2, same expected value as approved S1.", encoding="utf-8")
        self.gate.record_design_review("coordinator fixture", "Inspected actual assertion against S1", ["test-results/assertions.md"])
        self.gate.freeze_tests(["tests/test_two.py"])
        self.assertTrue(self.gate.run_tests("red", "tests/test_two.py")["valid_red"])
        self.assertEqual(guard({"tool_name": "Write", "tool_input": {"file_path": "src/sample.py"}}, self.root), 0)
        (self.root / "src/sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        self.green()

    def test_real_behavior_to_independent_acceptance_archive_and_delivery(self):
        self.reach_verify()
        self.stage_archive()
        before = self.gate.status()["candidate_sha256"]
        self.verdict()
        with self.assertRaisesRegex(GateError, "Commit"):
            self.gate.advance("delivery_ready")
        delivery.commit(self.gate)
        self.assertEqual(self.gate.status()["candidate_sha256"], before)
        self.assertTrue(self.gate.status()["verdict_current"])
        self.assertEqual(self.gate.advance("delivery_ready")["status"], "delivery_ready")
        self.assertFalse((self.root / self.prep["spec"]).exists())

    def test_archive_rejects_spec_change_and_concurrent_edits(self):
        self.reach_verify()
        self.stage_archive()
        self.verdict()
        (self.root / self.prep["spec"]).write_text(SPEC + "\nChanged after review", encoding="utf-8")
        with self.assertRaises(GateError):
            delivery.commit(self.gate)

    def test_archive_preparation_resumes_partial_copy_without_overwriting_conflicts(self):
        from aidevflow.gate import write_bytes_new
        self.reach_verify()
        copied = []
        def interrupt(path, content):
            write_bytes_new(path, content)
            copied.append(path)
            raise OSError("Interrupted before archive input marker publication")
        with patch("aidevflow.gate.write_bytes_new", side_effect=interrupt):
            with self.assertRaises(OSError):
                delivery.prepare(self.gate)
        self.assertEqual(len(copied), 1)
        prepared = delivery.prepare(self.gate)
        self.assertEqual(delivery.prepare(self.gate), prepared)
        delivery.reset(self.gate)
        with patch("aidevflow.gate.write_bytes_new", side_effect=interrupt):
            with self.assertRaises(OSError):
                delivery.prepare(self.gate)
        copied[-1].write_text("Concurrent staging edit", encoding="utf-8")
        with self.assertRaisesRegex(GateError, "staging conflicts"):
            delivery.prepare(self.gate)
        self.assertEqual(copied[-1].read_text(encoding="utf-8"), "Concurrent staging edit")
        delivery.reset(self.gate)
        self.assertEqual(delivery.prepare(self.gate)["slug"], "return-two")

    def test_native_receipt_remains_required(self):
        target = design.prepare_review(self.gate, "spec")
        state, _, _ = self.gate._load_active()
        self._write_json("review.json", {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"], "kind": "spec", "input_sha256": target["sha256"], "outcome": "pass", "coverage": {"ACCEPT-1": ["S1"]}, "findings": []})
        self.receipts.stop()
        with self.assertRaisesRegex(GateError, "receipt"):
            design.record_review(self.gate, "review.json")

    def test_bound_native_reviewer_result_and_duplicate_dispatch_fence(self):
        from aidevflow.runtimes.registry import get_runtime
        observe = get_runtime(self.RUNTIME).observe
        from aidevflow.contexts import role_context
        target = design.prepare_review(self.gate, "spec")
        state, _, _ = self.gate._load_active()
        value = {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"], "kind": "spec", "input_sha256": target["sha256"], "outcome": "pass", "coverage": {"ACCEPT-1": ["S1"]}, "findings": []}
        assignment = role_context(self.root, "artifact_reviewer")["task_call"]["tasks"][0]
        event = {"type": "tool_call", "session_id": "review-session", "toolCallId": "review-call", "input": assignment}
        observe(self.root, event)
        with self.assertRaisesRegex(GateError, "unresolved"):
            observe(self.root, {**event, "toolCallId": "duplicate-call"})
        self.assertIn("unresolved", self.gate.status()["next_actions"][0])
        receipt = observe(self.root, {**event, "type": "tool_result", "details": {"results": [{"id": "native-reviewer", "agent": assignment["agent"], "exitCode": 0,
                            "structuredOutput": {"status": "valid", "data": value}}]}})
        self.receipts.stop()
        self.assertEqual(design.record_review(self.gate, receipt["result_path"])["outcome"], "pass")

    def test_engineering_revision_is_reviewed_and_reopens_completed_work(self):
        from aidevflow.services import revisions
        self.bind()
        approved = self.gate._load_active()[0]["intent_sha256"]
        revision = revisions.begin(self.gate, "Clarify engineering wording without changing expected value")
        p = self.root / revision["files"][self.prep["spec"]]
        p.write_text(SPEC.replace("Modify the existing function.", "Modify the existing value function in place."), encoding="utf-8")
        proposal = read_json(self.root / revision["contract_draft"])
        proposal["code_paths"].append("config.py")
        self._write_json(revision["contract_draft"], proposal)
        self.review("spec")
        self.review("plan")
        revisions.commit(self.gate)
        status = self.gate.status()
        self.assertEqual(status["intent_sha256"], approved)
        self.assertEqual(status["current_stage"], "build")
        self.assertTrue(all(w["status"] == "not_started" for w in status["work_items"]))
        self.assertNotIn("engineering_revision", status)
        self.assertIn("config.py", read_json(self.root / status["contract_path"])["code_paths"])
        self.assertEqual(status["evidence_epoch"], 1)
        design.require_review(self.gate, self.gate._load_active()[0], "spec")
        design.require_review(self.gate, self.gate._load_active()[0], "plan")

    def test_archive_interruption_recovers_without_duplicate_closure(self):
        import aidevflow.gate as gate_module
        self.reach_verify()
        self.stage_archive()
        self.verdict()
        real_replace = gate_module.replace_file_atomic
        calls = 0
        def interrupt(source, target):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("Injected archive interruption")
            return real_replace(source, target)
        with patch("aidevflow.gate.replace_file_atomic", side_effect=interrupt):
            with self.assertRaises(OSError):
                delivery.commit(self.gate)
        with self.assertRaisesRegex(GateError, "transaction pending"):
            self.gate.status()
        delivery.commit(self.gate, recover=True)
        self.assertTrue(self.gate.status()["verdict_current"])
        self.assertTrue(delivery.commit(self.gate)["committed"])

    def test_large_specification_requires_blind_source_isolation(self):
        from aidevflow.bridge import guard as runtime_guard
        p = self.root / self.prep["spec"]
        extra = "".join(f"### S{i} Check boundary {i}\n- 做什么：Observe approved boundary {i}\n- 验证：运行 value() → 2\n" for i in range(2, 7))
        p.write_text(SPEC.replace("## 设计", extra + "## 设计"), encoding="utf-8")
        target = design.prepare_review(self.gate, "spec")
        self.assertEqual(target["kind"], "blind")
        denied = runtime_guard(self.root, {"toolName": "read", "input": {"path": self.prep["spec"]}, "actor": {"name": "aidevflow-artifact-reviewer"}})
        self.assertTrue(denied["block"])
        state, _, _ = self.gate._load_active()
        self._write_json("blind.json", {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"], "kind": "blind", "input_sha256": target["sha256"],
                                      "outcome": "pass", "coverage": {}, "findings": ["Call value and observe exactly 2", "Preserve existing callable signature", "Do not perform network access"]})
        design.record_review(self.gate, "blind.json")
        self.assertEqual(design.prepare_review(self.gate, "spec")["kind"], "spec")

    def test_installed_engine_and_runtime_ownership_are_separate(self):
        import subprocess
        from aidevflow.installer import install
        source = Path(__file__).resolve().parents[1]
        install(source, self.root, self.RUNTIME)
        self.assertTrue((self.root / ".aidevflow/engine/python/aidevflow/services/design.py").is_file())
        self.assertTrue((self.root / ".aidevflow/engine/host/shared/client.ts").is_file())
        self.assertFalse((self.root / ".claude/settings.json").exists())
        common = read_json(self.root / ".aidevflow/install/common.manifest.json")
        native = read_json(self.root / f".aidevflow/install/runtimes/{self.RUNTIME}.manifest.json")
        self.assertTrue(all(not p.startswith(self.NATIVE + "/") for p in common["files"]))
        self.assertTrue(all(p.startswith(self.NATIVE + "/") for p in native["files"]))
        command = subprocess.run([sys.executable, "-B", "scripts/aidevflow.py", "method-inspect"], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(json.loads(command.stdout)["profile"], PROFILE)

    def test_task_baseline_replay_after_reviewed_engineering_correction(self):
        from aidevflow.services import revisions
        self.test_task_level_real_red_green_and_progress()
        revision = revisions.begin(self.gate, "Refine frozen test comments while preserving the approved assertion")
        test_draft = self.root / revision["files"]["tests/test_two.py"]
        test_draft.write_text(test_draft.read_text(encoding="utf-8") + "\n# Approved expectation remains integer two\n", encoding="utf-8")
        self.review("spec")
        self.review("plan")
        revisions.commit(self.gate)
        self.gate.start_work_item("S1-1")
        self.gate.record_design_review("coordinator fixture", "Rechecked actual assertion after reviewed correction", ["test-results/assertions.md"])
        self.gate.freeze_tests(["tests/test_two.py"])
        self.assertFalse(self.gate.run_tests("red", "tests/test_two.py")["valid_red"])
        replay = self.gate.run_tests("red", "tests/test_two.py", against_base=True)
        self.assertTrue(replay["valid_red"], replay)

    def test_preparation_recovers_after_snapshot_publication_before_state_commit(self):
        state, run, _ = self.gate._load_active()
        state.pop("method_preparation")
        state.pop("knowledge_initialized")
        self.gate._save(state, run, "protocol_fixture_lost_preparation_state")
        for key in ("spec", "plan"):
            (self.root / self.prep[key]).unlink()
        (self.root / self.prep["spec"]).parent.rmdir()
        recovered = design.prepare_method(self.gate, "return-two")
        self.assertEqual(recovered, self.prep)
        self.assertEqual(self.gate._load_active()[0]["method_preparation"]["path"], (run / "method-preparation.json").relative_to(self.root).as_posix())

    def test_review_request_mutation_after_dispatch_cannot_be_accepted(self):
        target = design.prepare_review(self.gate, "spec")
        state, _, _ = self.gate._load_active()
        self._write_json(target["path"], {"kind": "spec", "subjects": {}, "acceptance_ids": []})
        self._write_json("review.json", {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"], "kind": "spec", "input_sha256": target["sha256"],
                                        "outcome": "pass", "coverage": {"ACCEPT-1": ["S1"]}, "findings": []})
        with self.assertRaisesRegex(GateError, "changed after dispatch"):
            design.record_review(self.gate, "review.json")

    def test_testing_cards_are_preserved_and_unknown_cards_rejected(self):
        text = PLAN.replace("- [ ] S1-1", "- [ ] T1 Freeze shared tests\n  - 预期：Assertions match approved examples\n  - 验收：（待执行）\n- [ ] S1-1")
        self.assertEqual([t["id"] for t in parse_plan(text)], ["T1", "S1-1", "R1"])
        projected = project_plan(text, [{"id": t["id"], "status": "completed", "observation": "Actually observed the approved result"} for t in parse_plan(text)])
        self.assertEqual(plan_semantics(text), plan_semantics(projected))
        with self.assertRaisesRegex(GateError, "Unrecognized task"):
            parse_plan(text.replace("T1 Freeze", "UNKNOWN Freeze"))

    def test_progress_projection_is_complete_and_stable_during_acceptance(self):
        self.reach_verify()
        plan = self.root / self.prep["plan"]
        self.assertTrue(all(t["completed"] for t in parse_plan(plan.read_text(encoding="utf-8"))))
        before = plan.read_bytes()
        self.stage_archive()
        self.verdict()
        self.assertEqual(plan.read_bytes(), before)
        delivery.commit(self.gate)

    def test_routine_repairs_do_not_share_engineering_budget_or_require_approval(self):
        from aidevflow.services.autonomy import attempt
        state = {"repair_attempts": {"engineering_correction": 30}}
        contract = {"method": {"name": "zsspec", "profile": PROFILE}, "max_repair_attempts": 2}
        for n in range(6):
            self.assertTrue(attempt(state, contract, "review_finding", {"files": {"app": str(n)}}))
        self.assertNotEqual(state.get("status"), "needs_human")
        self.assertTrue(attempt(state, contract, "review_finding", {"files": {"app": "unchanged"}}))
        self.assertTrue(attempt(state, contract, "review_finding", {"files": {"app": "unchanged"}}))
        self.assertFalse(attempt(state, contract, "review_finding", {"files": {"app": "unchanged"}}))
        self.assertEqual(state["status"], "automation_stalled")

    def test_legacy_ordinary_checkpoint_recovers_but_business_decision_does_not(self):
        from aidevflow.services.autonomy import recover_routine
        self.bind()
        state, run, _ = self.gate._load_active()
        approval = state["intent_sha256"]
        state.update(status="needs_human", blocker={"kind": "product", "failure_type": "review_finding", "reason": "Legacy budget exhausted"})
        self.gate._save(state, run, "fixture_legacy_routine_stop")
        self.assertEqual(recover_routine(self.gate)["authorization"], "approved_requirements")
        state, run, _ = self.gate._load_active()
        self.assertEqual(state["intent_sha256"], approval)
        state.update(status="needs_human", blocker={"kind": "requirements_decision", "reason": "New business requirement"})
        self.gate._save(state, run, "fixture_business_decision")
        with self.assertRaisesRegex(GateError, "Only a current-method"):
            recover_routine(self.gate)
        self.assertEqual(self.gate.status()["status"], "needs_human")

    def test_changed_frozen_assertions_require_current_red_replay(self):
        from aidevflow.services import revisions
        from aidevflow.services.execution import missing_red
        self.test_task_level_real_red_green_and_progress()
        revision = revisions.begin(self.gate, "Correct an engineering test annotation while retaining value two")
        test = self.root / revision["files"]["tests/test_two.py"]
        test.write_text(test.read_text(encoding="utf-8") + "\n# Corrected test baseline\n", encoding="utf-8")
        self.review("spec")
        self.review("plan")
        revisions.commit(self.gate)
        self.gate.start_work_item("S1-1")
        self.gate.record_design_review("coordinator fixture", "Rechecked corrected assertions against S1", ["test-results/assertions.md"])
        self.gate.freeze_tests(["tests/test_two.py"])
        state, _, contract = self.gate._load_active()
        self.assertEqual(missing_red(self.gate, state, contract), ["tests/test_two.py"])
        result = self.green()
        with self.assertRaisesRegex(GateError, "genuine Red"):
            self.gate.complete_work_item("S1-1", [result["evidence_path"]], "Observed correct value but have not replayed corrected assertions")
        self.assertTrue(self.gate.run_tests("red", "tests/test_two.py", against_base=True)["valid_red"])
        self.assertEqual(missing_red(self.gate, *self.gate._load_active()[::2]), [])

    def test_bundle_short_citations_and_normalized_identity(self):
        self.reach_verify()
        (self.root / "src/sample.py").write_bytes(b"def value():\r\n    return 2\r\n")
        self.stage_archive()
        bundle = self.gate.prepare_verifier()
        folder = self.root / bundle["path"]
        metadata = read_json(folder / "BUNDLE.json")
        binding = next(b for b in read_json(folder / "candidate-binding.json")["bindings"] if b["source_path"] == "src/sample.py")
        self.assertNotEqual(binding["raw_sha256"], binding["candidate_sha256"])
        state, _, _ = self.gate._load_active()
        ident = next(e["id"] for e in metadata["files"] if e["path"] == "diff.patch")
        value = {"schema": "aidevflow/verifier-verdict-v1", "task_id": state["task_id"], "spec_version": bundle["spec_version"],
                 "bundle_sha256": bundle["sha256"], "items": [{"id": "ACCEPT-1", "verdict": "pass", "evidence": [ident], "reason": "Protocol fixture with an exact short bundle citation"}], "risks": []}
        self._write_json("verdict.json", {**value, "items": [{**value["items"][0], "evidence": ["E9999"]}]})
        with self.assertRaisesRegex(GateError, "not in the frozen bundle"):
            self.gate.record_verdict("verdict.json")
        from aidevflow.contexts import role_context
        assignment = role_context(self.root, "verifier")["task_call"]["tasks"][0]["task"]
        self.assertIn("citation/format correction", assignment)
        self.assertIsNone(self.gate._load_active()[0].get("verdict_ref"))
        self._write_json("verdict.json", value)
        self.assertEqual(self.gate.record_verdict("verdict.json")["status"], "accepted")
        self.assertNotIn("verifier_output_correction", self.gate._load_active()[0])

    def test_runtime_check_cannot_pass_with_blanket_attestation(self):
        from aidevflow.services.observations import context
        self.bind(manual=True)
        self.gate.start_work_item("S1-1")
        (self.root / "src/sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        self.green()
        screenshot = self.root / "test-results/protocol-only.png"
        screenshot.write_bytes(b"protocol fixture, not a claimed real UI screenshot")
        template = context(self.gate, "C-ui")
        candidate = template["candidate_sha256"]
        with self.assertRaisesRegex(GateError, "observations"):
            self.gate.record_check("C-ui", "passed", candidate, "fixture", "A blanket claim is insufficient", ["test-results/protocol-only.png"])
        self._write_json("test-results/observations.json", template)
        with self.assertRaisesRegex(GateError, "Unverified"):
            self.gate.record_check("C-ui", "passed", candidate, "fixture", "Still unverified scenario", ["test-results/protocol-only.png"], "test-results/observations.json")
        template["observations"][0].update(result="passed", observed="Protocol fixture: value returned exactly integer two", evidence=["test-results/protocol-only.png"])
        self._write_json("test-results/observations.json", template)
        result = self.gate.record_check("C-ui", "passed", candidate, "fixture", "Observed the approved result", ["test-results/protocol-only.png"], "test-results/observations.json")
        self.assertEqual(result["observations"][0]["expected"], "value() returns two")

    def test_replay_pins_uncommitted_starting_implementation(self):
        from aidevflow.test_execution import snapshot_implementation, replay
        self.test_task_level_real_red_green_and_progress()
        state, run, contract = self.gate._load_active()
        (self.root / "src/sample.py").write_text("def value():\n    return 7\n", encoding="utf-8")
        state = {**state, "task_id": "UNCOMMITTED-BASELINE"}
        new_run = self.gate.runs / state["task_id"]
        ref = snapshot_implementation(self.gate, state, new_run)
        state["implementation_baseline"] = ref
        (new_run / "task-test-baselines").mkdir()
        for digest in state["frozen_tests"].values():
            shutil.copyfile(run / "task-test-baselines" / digest, new_run / "task-test-baselines" / digest)
        (self.root / "src/sample.py").write_text("def value():\n    return 2\n", encoding="utf-8")
        folder, _ = replay(self.gate, state, contract, [sys.executable, "-B", "tests/test_two.py"])
        self.assertIn("return 7", (folder / "src/sample.py").read_text(encoding="utf-8"))

    def test_route_ignores_prior_engineering_allowance_and_detects_no_progress(self):
        self.reach_verify()
        state, run, contract = self.gate._load_active()
        state["repair_attempts"] = {"engineering_correction": 30}
        self.gate._save(state, run, "fixture_previous_technical_corrections")
        result = self.gate.route("review_finding", "Repair the existing approved implementation")
        self.assertEqual(result["to"], "build")
        self.assertTrue(result["reopened_work_items"])
        self.assertEqual(self.gate.route("review_finding", "Same defect without any code change")["to"], "build")
        self.assertEqual(self.gate.route("review_finding", "Still no change")["status"], "automation_stalled")

    def test_empty_git_baseline_replay_does_not_decode_pax_only_tar(self):
        import subprocess
        from aidevflow.test_execution import replay
        self.test_task_level_real_red_green_and_progress()
        state, _, contract = self.gate._load_active()
        tree = subprocess.run(["git", "hash-object", "-t", "tree", "-w", "--stdin"], input=b"", cwd=self.root, capture_output=True, check=True).stdout.decode().strip()
        state = {**state, "base_commit": tree}
        state.pop("implementation_baseline", None)
        folder, _ = replay(self.gate, state, contract, [sys.executable, "-B", "tests/test_two.py"])
        self.assertTrue((folder / "tests/test_two.py").is_file())
        self.assertFalse((folder / "src/sample.py").exists())
