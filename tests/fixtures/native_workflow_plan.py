"""Read-only scripted decisions for the installed-entry integration test.

All product mutations happen through native tools or the installed Gate CLI.
No business state, approval, role receipt or verdict is written by this helper.
"""
import json
import shlex
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / ".aidevflow/engine/python"))
from aidevflow.gate import Gate, read_json, GateError
from aidevflow.contexts import role_context
from aidevflow.services import design
from aidevflow.receipts import execution_context


def bash(*args):
    return {"tool": "bash", "args": {"command": "python scripts/aidevflow.py " + shlex.join(args)}}


def write(path, content):
    return {"tool": "write", "args": {"path": path, "content": content}}


def decide(role):
    fixture = read_json(ROOT / "native-fixture.json")
    gate = Gate(ROOT)
    if (gate.control / "active-discovery.json").exists():
        from aidevflow.discovery import Discovery
        state, run, _ = Discovery(ROOT).load()
        if role == "aidevflow-requirements-analyst":
            value = fixture["requirements"]
            value["requirements"][0]["source"] = state["request_snapshot_path"]
            return {"result": value}
        if state["status"] == "review_ready":
            return {"text": "Scripted requirements are ready for native review."}
        return native_or_record(gate, state, run, "requirements_analyst", "discovery-record", "--draft")
    state, run, contract = gate._load_active()
    if role == "aidevflow-artifact-reviewer":
        target = state["artifact_review_target"]
        request = read_json(ROOT / target["path"])
        return {"result": {"schema": "aidevflow/artifact-review-v1", "task_id": state["task_id"],
                "kind": request["kind"], "input_sha256": target["sha256"], "outcome": "pass",
                "coverage": {"ACCEPT-1": ["S1"]}, "findings": []}}
    if role == "aidevflow-contract-planner":
        value = fixture["contract"]
        value.update(task_id=state["task_id"], requirements_ref=state["requirements_ref"])
        return {"result": value}
    if role == "aidevflow-independent-verifier":
        bundle = state["verifier_bundle"]
        return {"result": {"schema": "aidevflow/verifier-verdict-v1", "task_id": state["task_id"],
                "spec_version": bundle["spec_version"], "bundle_sha256": bundle["sha256"],
                "items": [{"id": "ACCEPT-1", "verdict": "pass", "evidence": ["diff.patch"],
                           "reason": "Protocol fixture; genuine behavioral command and source diff are bundled."}], "risks": []}}
    if state["current_stage"] == "delivery_ready":
        return {"text": "Scripted delivery reached the real delivery_ready checkpoint."}
    if not state.get("method_preparation"):
        return bash("prepare-method", "--slug", "return-two")
    if not state.get("knowledge_initialized"):
        for path, content in (("specs/MAP.md", "# Module map\n"), ("specs/archive/INDEX.md", "# Archive index\n")):
            if not (ROOT / path).exists():
                return write(path, content)
        return bash("record-initialization")
    if state.get("engineering_revision"):
        for kind in ("spec", "plan"):
            if kind not in state.get("artifact_reviews", {}):
                target = state.get("artifact_review_target")
                if not target or read_json(ROOT / target["path"])["kind"] != kind:
                    return bash("prepare-artifact-review", "--kind", kind)
                return native_or_record(gate, state, run, "artifact_reviewer", "record-artifact-review", "--path")
        return bash("commit-engineering-revision")
    if contract is None:
        prep = design.preparation(gate, state)
        for kind in ("spec", "plan"):
            if not (ROOT / prep[kind]).exists():
                return write(prep[kind], fixture[kind])
            if kind not in state.get("artifact_reviews", {}):
                target = state.get("artifact_review_target")
                if not target or read_json(ROOT / target["path"])["kind"] != kind:
                    return bash("prepare-artifact-review", "--kind", kind)
                return native_or_record(gate, state, run, "artifact_reviewer", "record-artifact-review", "--path")
        return native_or_record(gate, state, run, "contract_planner", "register-contract", "--draft")
    if state["current_stage"] == "intent":
        return bash("advance", "--to", "design")
    if state["current_stage"] == "design":
        if fixture.get("engineering_revision") and not state.get("engineering_history"):
            return bash("begin-engineering-revision", "--reason", "Exercise native independent engineering re-review")
        return bash("compile-work-plan") if not state.get("work_plan") else bash("advance", "--to", "build")
    if state["current_stage"] == "build":
        items = state.get("work_items", [])
        current = next((w for w in items if w["status"] != "completed"), None)
        if current is None:
            return bash("advance", "--to", "verify")
        if current["status"] == "not_started":
            return bash("start-work-item", "--id", current["id"])
        if "return 1" in (ROOT / "src/sample.py").read_text() and state.get("test_runs"):
            return write("src/sample.py", "def value():\n    return 2\n")
        status = gate.status()
        command = next(c for c in status["checks"] if c["id"] == "C-behavior")
        history = [json.loads(line) for line in gate.events_path.read_text(encoding="utf-8").splitlines()]
        started_at = max((i for i, e in enumerate(history) if e.get("event") == "work_item_started"
                          and e.get("work_item_id") == current["id"]), default=-1)
        replayed = any(e.get("event") == "test_executed" for e in history[started_at + 1:])
        runs = state.get("test_runs", [])
        if command["status"] != "passed" or (current["id"].startswith("R") and not replayed):
            return bash("run-tests", "--suite", "behavior")
        report = ROOT / "test-results/static.md"
        if not report.exists():
            return write("test-results/static.md", "Scripted static review: current function returns exactly two; no scope changes.\n")
        if next(c for c in status["checks"] if c["id"] == "C-static")["status"] != "passed":
            return bash("record-check", "--id", "C-static", "--result", "passed", "--candidate", status["candidate_sha256"],
                        "--reviewer", "protocol-fixture", "--reason", "Inspected exact current function against approved behavior",
                        "--evidence", "test-results/static.md")
        return bash("complete-work-item", "--id", current["id"], "--observation", "Executed value() and observed exactly integer two",
                    "--evidence", runs[-1]["evidence_path"])
    if state["status"] == "accepted":
        if not state.get("delivery_archive", {}).get("committed"):
            return bash("commit-delivery-archive")
        return bash("advance", "--to", "delivery_ready")
    if state["current_stage"] == "verify":
        if not (run / "archive-input.json").exists():
            return bash("prepare-delivery-archive")
        staging = run / "archive-staging"
        destination = "specs/archive/return-two_2026-11-10"
        for name in ("spec.md", "plan.md"):
            old = staging / "specs/active/return-two" / name
            new = staging / destination / name
            if old.exists():
                if not new.exists():
                    return write(new.relative_to(ROOT).as_posix(), old.read_text(encoding="utf-8"))
                return {"tool": "edit", "args": {"path": old.relative_to(ROOT).as_posix(), "edits": [{"op": "delete"}]}}
        index = staging / "specs/archive/INDEX.md"
        if "| return-two |" not in index.read_text(encoding="utf-8"):
            return write(index.relative_to(ROOT).as_posix(),
                         "| 日期 | slug | 摘要 | 模块 | 状态 |\n|---|---|---|---|---|\n| 2026-11-10 | return-two | Return two | sample | completed |\n")
        if not state.get("delivery_archive"):
            return bash("seal-delivery-archive")
        if not state.get("verifier_bundle"):
            return bash("prepare-verifier")
        return native_or_record(gate, state, run, "verifier", "record-verdict", "--path")
    raise RuntimeError("Unexpected fixture checkpoint: " + str(state.get("status")))


def native_or_record(gate, state, run, role, command, flag):
    context = execution_context(state)
    entries = [read_json(p) for p in (run / "agent-invocations").glob("*.json")]
    completed = next((e for e in entries if e.get("role") == role and e.get("status") == "completed"
                      and e.get("context") == context), None)
    if completed:
        return bash(command, flag, completed["result_path"])
    failed = [e for e in entries if e.get("role") == role and e.get("context") == context
              and e.get("status") not in ("completed", "dispatched", "started")]
    if failed:
        raise RuntimeError("Native fixture role failed: " + json.dumps(failed[-1]))
    return {"tool": "task", "args": role_context(ROOT, role)["task_call"]}


if __name__ == "__main__":
    # Native host decodes UTF-8; keep this fixture's stdout portable across
    # Windows Python locales without changing business command environments.
    print(json.dumps(decide(sys.argv[1]), ensure_ascii=True))
