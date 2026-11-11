"""Task-level test admission and Gate-owned zsspec progress."""
from __future__ import annotations

import re

from ..method_adapters.zsspec_docs import project_plan, plan_semantics


def active_task(gate, state):
    from ..gate import GateError
    active = [i for i in state.get("work_items", []) if i["status"] == "in_progress"]
    if len(active) != 1:
        raise GateError("Task test admission requires exactly one active work item")
    return active[0]


def record_test_review(gate, state, run, contract, reviewer, reason, evidence):
    from ..gate import GateError, write_json_atomic, sha256_file
    from ..checks import snapshot_files
    task = active_task(gate, state)
    files = contract["method"].get("task_tests", {}).get(task["id"], [])
    if (not files and not contract.get("test_support_files")) or not reviewer.strip() or not reason.strip():
        raise GateError("A task test review requires mapped tests, reviewer and an actual review reason")
    subjects = {rel: sha256_file(gate._safe_repo_path(rel)) for rel in files + contract.get("test_support_files", [])}
    snapshots = snapshot_files(gate, state, contract, evidence, "test-review-evidence")
    value = {"schema": "aidevflow/task-test-review-v1", "task_id": state["task_id"], "work_item": task["id"],
             "source": "ai_coordinator_attestation", "reviewer": reviewer, "reason": reason,
             "subjects": subjects, "evidence": snapshots}
    path = run / "task-test-reviews" / (task["id"] + ".json")
    write_json_atomic(path, value)
    state.setdefault("task_test_reviews", {})[task["id"]] = {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)}
    gate._save(state, run, "task_tests_reviewed", work_item_id=task["id"])
    return value


def freeze_task_tests(gate, state, run, contract, requested):
    from ..gate import GateError, read_json, sha256_file, write_bytes_new
    from ..checks import intact
    task = active_task(gate, state)
    mapped = contract["method"].get("task_tests", {}).get(task["id"], [])
    required = mapped + contract.get("test_support_files", [])
    if not required or set(requested) != set(required):
        raise GateError("Freeze exactly the active task's mapped tests and declared support files")
    ref = state.get("task_test_reviews", {}).get(task["id"])
    if not ref or sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
        raise GateError("record-design-review must review this task's real assertions before freezing")
    report = read_json(gate._safe_repo_path(ref["path"]))
    values = {rel: sha256_file(gate._safe_repo_path(rel)) for rel in required}
    if report["subjects"] != values or not intact(gate, report["evidence"], run):
        raise GateError("Task test review is stale or damaged")
    for rel, digest in values.items():
        if rel in state["frozen_tests"] and state["frozen_tests"][rel] != digest:
            raise GateError("Incorrect frozen assertions require begin-engineering-revision and independent spec/plan review; routine corrections need no new user approval")
        path = run / "task-test-baselines" / digest
        if not path.exists():
            write_bytes_new(path, gate._safe_repo_path(rel).read_bytes())
    state["frozen_tests"].update(values)
    gate._save(state, run, "task_tests_frozen", work_item_id=task["id"], files=values)
    return values


def require_task_red(gate, state, contract, target):
    from ..gate import GateError
    task = active_task(gate, state)
    if target not in contract["method"].get("task_tests", {}).get(task["id"], []) or target not in state["frozen_tests"]:
        raise GateError("Red requires reviewed frozen tests belonging to the active task")
    # Once implementation admission is recorded a fresh Red must use the replay path.
    if task.get("implementation_started"):
        raise GateError("Red must precede implementation; use reviewed baseline replay for later corrections")


def implementation_errors(gate, state, contract):
    from ..gate import GateError
    task = active_task(gate, state)
    required = contract["method"].get("task_tests", {}).get(task["id"], [])
    if set(required) - set(state["frozen_tests"]) or set(required) & set(missing_red(gate, state, contract)):
        return "The active task needs reviewed frozen assertions and genuine Red before implementation"
    return None


def check_completion(gate, state, contract, task, observation):
    from ..gate import GateError, check_acceptable, read_json
    if not observation or len(observation.strip()) < 8 or "\n" in observation or observation.strip().lower() in ("passed", "success", "all passed"):
        raise GateError("Task completion needs a concrete single-line observed result (at least eight characters)")
    if set(contract["method"].get("task_tests", {}).get(task["id"], [])) & set(missing_red(gate, state, contract)):
        raise GateError("Complete the current frozen tests' genuine Red replay before completing this task")
    declared = contract["method"].get("task_checks", {}).get(task["id"])
    relevant = [c for c in contract["required_checks"] if (c["id"] in declared if declared is not None and not task["id"].startswith("R") else bool(set(c["acceptance_items"]) & set(task["acceptance_items"])))]
    statuses = {c["id"]: c for c in gate._check_statuses(state, contract)}
    if any(not check_acceptable(statuses[c["id"]], contract) for c in relevant):
        raise GateError("Capture fresh passing acceptance checks before completing this task")
    if task["id"].startswith("R"):
        for check in relevant:
            if check.get("kind", "command") == "command":
                records = [r for r in state["test_runs"] if r["suite"] == check["suite"] and r.get("target") == check.get("target")]
                if not records or read_json(gate._safe_repo_path(records[-1]["evidence_path"]))["started_at"] < task["updated_at"]:
                    raise GateError("Replay every required command after starting the regression task")
    task["observation"] = observation.strip()


def red_subject(state, contract, target):
    return {"test_sha256": state.get("frozen_tests", {}).get(target),
            "support": {p: state.get("frozen_tests", {}).get(p) for p in contract.get("test_support_files", [])}}


def missing_red(gate, state, contract):
    from ..recovery import effective_records, baseline_id
    from ..gate import sha256_file, read_json
    found = set()
    for r in effective_records(gate, state, gate.runs / state["task_id"]):
        if r.get("suite") != "red" or not r.get("valid_red") or r.get("candidate_changed"):
            continue
        expected = red_subject(state, contract, r.get("target"))
        if not expected["test_sha256"] or (r.get("red_subject") != expected if "red_subject" in r else r.get("baseline_id") != baseline_id(state)):
            continue
        p = gate._safe_repo_path(r["evidence_path"])
        if p.is_file() and sha256_file(p) == r["evidence_sha256"] and read_json(p).get("valid_red"):
            found.add(r["target"])
    return sorted(set(contract.get("red_test_files", [])) - found)


def reopen_affected(state, contract, failed):
    """Keep unrelated completed work; invalidate the affected cards and dependants."""
    checks = {c["id"]: set(c.get("acceptance_items", [])) for c in contract.get("required_checks", [])}
    mapping = contract["method"].get("task_checks", {})
    items = state.get("work_items", [])
    affected = {w["id"] for w in items if set(failed) & set().union(*(checks.get(c, set()) for c in mapping.get(w["id"], [])))}
    if not affected:
        affected = {w["id"] for w in items if w["status"] == "in_progress"} or {w["id"] for w in items}
    while True:
        expanded = affected | {w["id"] for w in items if set(w["depends_on"]) & affected}
        if expanded == affected:
            break
        affected = expanded
    for work in items:
        if work["id"] in affected:
            work.update(status="not_started", evidence=[], updated_at=None)
            work.pop("observation", None)
    return sorted(affected)


def sync_plan(gate, state, run, contract):
    from ..gate import GateError, replace_file_atomic
    if not state.get("work_plan") or state.get("delivery_archive", {}).get("committed") or state.get("engineering_revision"):
        return {"updated": False}
    relative = f"specs/active/{contract['method']['slug']}/plan.md"
    path = gate._safe_repo_path(relative)
    original = path.read_text(encoding="utf-8-sig")
    value = project_plan(original, state["work_items"])
    completed = all(i["status"] == "completed" for i in state["work_items"])
    completed_at = max((w.get("updated_at") or "" for w in state["work_items"]), default="")
    done = (f"- 时间：{completed_at}\n- 回归判定：全部已登记检查与预期一致，证据保存在本任务。\n- 覆盖规格：" + ", ".join(sorted({s for ids in contract["method"]["acceptance_map"].values() for s in ids})) + "\n") if completed else ""
    value = re.sub(r"(^## 完成[ \t]*\n).*?(?=^## |\Z)", lambda m: m[1] + done, value, flags=re.M | re.S)
    if plan_semantics(original) != plan_semantics(value):
        raise GateError("Progress projection attempted a semantic task change")
    if value != original:
        import os, tempfile
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(value)
            replace_file_atomic(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return {"updated": value != original, "path": relative}
