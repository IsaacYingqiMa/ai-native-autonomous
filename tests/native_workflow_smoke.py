"""Installed /aidevflow-flow -> native review -> real checks -> delivery.

Uses deterministic local model responses. Tests the actual product adapter and
Gate with no patched approval, receipt, review, test execution or archive service.
This does not establish the quality of an external model's engineering output.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "tests"))
from aidevflow.installer import install
from aidevflow.gate import Gate, read_json
from aidevflow.method_adapters.zsspec_docs import PROFILE, STAGES
from test_gate import GateFixture
from test_zsspec_docs import SPEC, PLAN
from zscode_native_smoke import NativeRPC


def main(runtime="zscode", output=None):
    native = ".zscode" if runtime == "zscode" else ".omp"
    base = SOURCE / ".test-tmp"
    base.mkdir(exist_ok=True)
    result = {"runtime": runtime, "scripted_model": True, "external_model_requests": 0}
    with tempfile.TemporaryDirectory(dir=base, prefix=runtime + "-product-", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        root.relative_to(base.resolve())
        (root / "src").mkdir()
        (root / "src/sample.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "-c", "core.fsmonitor=false", "init", "-q"], cwd=root, check=True, capture_output=True)
        for name in ("init", "spec", "plan", "apply", "archive"):
            shutil.copytree(SOURCE / ".claude/skills" / ("zsspec-" + name), root / native / "skills" / ("zsspec-" + name))
        shutil.copy2(SOURCE / ".claude/skills/README.md", root / native / "skills/README.md")
        provider = root / native / "extensions/zz-native-fixture"
        provider.mkdir(parents=True)
        shutil.copy2(SOURCE / "tests/fixtures/native-workflow.ts", provider / "index.ts")
        shutil.copy2(SOURCE / "tests/fixtures/native_workflow_plan.py", root / "native_workflow_plan.py")
        (root / native / "config.yml").write_text(
            "modelRoles:\n  default: aidevflow-probe/native\n  task: aidevflow-probe/native\n  smol: aidevflow-probe/native\n"
            "async:\n  enabled: false\nedit:\n  mode: patch\n", encoding="utf-8")
        draft = GateFixture()._draft()
        draft.update(open_questions=[], desired_outcome="Return exactly two", success_criteria=["value() returns exactly two"],
                     constraints=["No network access"])
        requirements = {"schema": "aidevflow/requirements-v1", "intent": draft, "prd": "# Product\nReturn two.",
                        "requirements": [{"id": "REQ-1", "statement": "Return two", "source": "request", "acceptance_ids": ["ACCEPT-1"]}],
                        "scenarios": [{"id": "SCN-1", "requirement_ids": ["REQ-1"], "acceptance_ids": ["ACCEPT-1"],
                                      "given": "The existing function", "when": "A caller invokes value()", "then": "It returns integer two"}]}
        contract = {"schema": "aidevflow/contract-v1", "task_id": "assigned-by-Gate", "title": "Return two",
                    "code_paths": ["src/**"], "design_artifacts": ["specs/active/return-two/spec.md", "specs/active/return-two/plan.md"],
                    "test_files": [], "test_support_files": [], "red_test_files": [], "evidence_paths": ["test-results"],
                    "test_commands": {"behavior": [sys.executable, "-B", "-c", "from src.sample import value; assert value()==2, value()"]},
                    "required_checks": [{"id": "C-behavior", "suite": "behavior", "acceptance_items": ["ACCEPT-1"]},
                                        {"id": "C-static", "kind": "static", "acceptance_items": ["ACCEPT-1"]}],
                    "acceptance_items": [{"id": "ACCEPT-1", "criterion": "value() returns exactly two"}],
                    "method": {"name": "zsspec", "profile": PROFILE, "slug": "return-two", "stages": STAGES,
                               "acceptance_map": {"ACCEPT-1": ["S1"]}, "task_tests": {}},
                    "work_plan_required": True, "routing": {"review_finding": {"to": "build"}}, "verifier": {"required": True}}
        (root / "native-fixture.json").write_text(json.dumps({"requirements": requirements, "contract": contract, "engineering_revision": True,
                                                           "spec": SPEC, "plan": PLAN}), encoding="utf-8")
        install(SOURCE, root, runtime, skills_root=native + "/skills", workflow_profile="reviewed-discovery-v1")
        maintenance = root / ".aidevflow/maintenance"
        maintenance.mkdir(exist_ok=True)
        (maintenance / "native-flow-trace.jsonl").touch()
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Native Test", "-c", "user.email=native@example.invalid", "commit", "-qm", "Fixture baseline"],
                       cwd=root, check=True, capture_output=True)
        rpc = None
        original_path = os.environ.get("PATH", "")
        try:
            with patch.dict(os.environ, {"AIDEVFLOW_FIXTURE_PYTHON": sys.executable,
                                         "PATH": str(Path(sys.executable).parent) + os.pathsep + original_path}):
                rpc = NativeRPC(shutil.which(runtime), root)
            selected = rpc.request("set_model", provider="aidevflow-probe", modelId="native")
            assert selected.get("success"), selected
            rpc.command("/aidevflow-flow Change the existing value() function to return exactly two. No network access.")
            dialog = rpc.wait(lambda f: f.get("type") == "extension_ui_request" and f.get("method") == "select", timeout=90)
            assert "确认并开始自主开发" in dialog.get("options", []), dialog
            result["native_review"] = True
            rpc.send({"type": "extension_ui_response", "id": dialog["id"], "value": "确认并开始自主开发"})
            active = root / ".aidevflow/active-run.json"
            def complete(_frame):
                if not active.exists():
                    return False
                state = read_json(root / read_json(active)["state_path"])
                return state.get("current_stage") == "delivery_ready"
            rpc.wait(complete, timeout=600)
            status = Gate(root).status()
            receipts = status["agent_invocations"]
            result.update(passed=status["verdict_current"] and status["delivery_archive"]["committed"]
                          and all(e["runtime"] == runtime for e in receipts),
                          status=status["status"], verdict_current=status["verdict_current"],
                          native_engineering_revision=bool(status.get("engineering_history")),
                          original_entry=True, real_failed_check=any(not r["passed"] for r in status["test_runs"]),
                          real_passing_check=any(r["passed"] for r in status["test_runs"]),
                          runtime_receipts=sorted({r["role"] for r in receipts}), archive_committed=status["delivery_archive"]["committed"])
            assert result["real_failed_check"] and result["real_passing_check"]
            prior_task = status["task_id"]
            rpc.complete(rpc.command("/aidevflow-flow"))
            assert read_json(active)["task_id"] == prior_task
            rpc.command("/aidevflow-flow A separate next requirement; discuss before development.")
            next_review = rpc.wait(lambda f: f.get("type") == "extension_ui_request" and f.get("method") == "select", timeout=90)
            rpc.send({"type": "extension_ui_response", "id": next_review["id"], "value": "保存并暂停"})
            from aidevflow.discovery import Discovery
            rpc.wait(lambda _f: Discovery(root).load()[0]["status"] == "paused")
            result["same_entry_next_requirement"] = not active.exists() and (
                root / ".aidevflow/archive" / (prior_task + ".json")).is_file()
            assert result["same_entry_next_requirement"]
        except Exception as exc:
            result.update(passed=False, error=str(exc))
            # Diagnose the real bridge independently; do not hide worker startup failures.
            frame = json.dumps({"protocol": 1, "id": "diagnostic", "action": "ping", "payload": {"runtime": runtime}}) + "\n"
            worker = subprocess.run([sys.executable, "-B", "scripts/aidevflow_bridge.py", str(root)],
                                    cwd=root, input=frame, capture_output=True, text=True, encoding="utf-8", timeout=15)
            result["bridge_diagnostic"] = {"exit_code": worker.returncode, "stdout": worker.stdout[-1800:], "stderr": worker.stderr[-2500:]}
            trace = maintenance / "native-flow-trace.jsonl"
            result["recent_decisions"] = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()[-4:]]
            event_log = root / ".aidevflow/events.jsonl"
            result["recent_events"] = event_log.read_text(encoding="utf-8").splitlines()[-3:] if event_log.exists() else []
            result["native_errors"] = [f for f in rpc.history if f.get("type") == "tool_execution_end" and f.get("isError")][-3:] if rpc else []
        finally:
            if rpc:
                rpc.close()
    if output:
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=("omp", "zscode"), default="zscode")
    parser.add_argument("--output")
    args = parser.parse_args()
    raise SystemExit(main(args.runtime, args.output))
