"""Preauthorized engineering corrections; original business approval is never rewritten."""
from __future__ import annotations

from ..locking import writes

import copy
import secrets


@writes
def begin(gate, reason):
    from ..gate import GateError, sha256_file, write_bytes_new, write_json_new
    from ..method_adapters.zsspec_docs import enabled, parse_plan, project_plan
    state, run, contract = gate._load_active()
    if not enabled(contract) or state["current_stage"] not in ("design", "build", "verify") or state["status"] in ("needs_human", "delivery_ready"):
        raise GateError("Engineering revision requires an executable current-method Design/Build/Verify task")
    if not reason.strip() or state.get("delivery_archive", {}).get("committed"):
        raise GateError("Provide a correction reason before final closure")
    if state.get("engineering_revision"):
        return state["engineering_revision"]
    if state.get("delivery_archive") or (run / "archive-input.json").exists():
        from .delivery import reset
        reset(gate)
        state, run, contract = gate._load_active()
    gate.integrity()
    identity = "ENG-" + secrets.token_hex(8)
    directory = run / "engineering-revisions" / identity
    targets = contract["design_artifacts"] + contract["test_files"] + contract.get("test_support_files", [])
    files, before = {}, {}
    for rel in targets:
        p = gate._safe_repo_path(rel)
        if not p.is_file():
            continue
        before[rel] = sha256_file(p)
        draft = directory / "drafts" / rel
        if p.is_file():
            content = p.read_bytes()
            if rel.endswith("/plan.md"):
                items = [{"id": t["id"], "status": "not_started"} for t in parse_plan(content.decode("utf-8-sig"))]
                content = project_plan(content.decode("utf-8-sig"), items).encode("utf-8")
            write_bytes_new(draft, content)
        files[rel] = draft.relative_to(gate.root).as_posix()
    contract_draft = directory / "execution-contract-draft.json"
    write_json_new(contract_draft, contract)
    value = {"contract_draft": contract_draft.relative_to(gate.root).as_posix(), "id": identity, "reason": reason, "files": files, "before": before,
             "candidate": gate._candidate(state, contract)["sha256"], "previous_reviews": copy.deepcopy(state.get("artifact_reviews", {}))}
    state["engineering_revision"] = value
    state["artifact_reviews"] = {}
    state.pop("artifact_review_target", None)
    state["status"] = "active"
    gate._save(state, run, "engineering_revision_started", reason=reason, revision_id=identity)
    return value


@writes
def commit(gate):
    from ..gate import GateError, read_json, sha256_file, sha256_bytes, write_json_new, write_bytes_new, utc_now, validate_work_plan
    from ..method_adapters.zsspec_docs import parse_plan, ZsspecDocsAdapter
    from .design import require_review
    state, run, contract = gate._load_active()
    revision = state.get("engineering_revision")
    if not revision:
        raise GateError("No engineering revision is active")
    original_contract = contract
    contract = read_json(gate._safe_repo_path(revision["contract_draft"]))
    from ..gate import validate_contract
    mutable = {"code_paths", "test_files", "test_support_files", "red_test_files", "test_commands", "required_checks", "test_timeout_seconds", "evidence_paths"}
    if any(contract.get(k) != original_contract.get(k) for k in set(contract) | set(original_contract) if k not in mutable | {"method"}):
        raise GateError("Engineering corrections cannot change business acceptance, identity or approval")
    if any(contract.get("method", {}).get(k) != original_contract["method"].get(k) for k in set(contract.get("method", {})) | set(original_contract["method"]) if k not in ("task_tests", "task_checks", "acceptance_map")):
        raise GateError("Engineering corrections cannot replace the method identity")
    if any(p in (".", "*", "**", "./**") for p in contract.get("code_paths", [])):
        raise GateError("Engineering scope must use bounded technical paths")
    errors = validate_contract(contract)
    if errors:
        raise GateError("Invalid technical execution update: " + "; ".join(errors))
    policy = read_json(gate._safe_repo_path(state["method_policy"]["path"]))["effective"]
    if policy.get("manual") == "disabled" and any(c.get("kind") == "manual" for c in contract["required_checks"]):
        raise GateError("Technical correction conflicts with the fixed manual-check policy")
    if policy.get("e2e") == "disabled" and any(c.get("mode") == "auto" for c in contract["required_checks"]):
        raise GateError("Technical correction conflicts with the fixed automatic browser policy")
    spec_review = require_review(gate, state, "spec")
    require_review(gate, state, "plan")
    if spec_review["coverage"] != contract["method"]["acceptance_map"]:
        raise GateError("Engineering correction cannot change approved acceptance binding; propose a requirements revision")
    if gate._candidate(state, original_contract)["sha256"] != revision["candidate"]:
        raise GateError("Product changed during engineering review")
    for rel, expected in revision["before"].items():
        p = gate._safe_repo_path(rel)
        if (sha256_file(p) if p.is_file() else None) != expected:
            raise GateError("Concurrent engineering revision edit: " + rel)
    plan_relative = f"specs/active/{contract['method']['slug']}/plan.md"
    tasks = parse_plan(gate._safe_repo_path(revision["files"][plan_relative]).read_text(encoding="utf-8-sig"))
    items = []
    for task in tasks:
        sid = task["id"].split("-")[0]
        refs = [a for a, ids in spec_review["coverage"].items() if sid in ids] if sid.startswith("S") else list(spec_review["coverage"])
        items.append({"id": task["id"], "title": task["title"], "done_criteria": [task["expected"]],
                      "acceptance_items": refs, "depends_on": [items[-1]["id"]] if items else []})
    if {f for values in contract["method"].get("task_tests", {}).values() for f in values} != set(contract["test_files"]):
        raise GateError("Technical test updates need complete task_tests ownership")
    task_ids = {t["id"] for t in tasks}
    task_checks = contract["method"].get("task_checks", {})
    check_ids = {c["id"] for c in contract["required_checks"]}
    if not isinstance(task_checks, dict) or any(k not in task_ids or not isinstance(v, list) or not v or any(not isinstance(c, str) or c not in check_ids for c in v) for k, v in task_checks.items()):
        raise GateError("Revised task checks must name real cards and executable check definitions")
    if len([t for t in task_ids if not t.startswith("R")]) > 1 and any(t not in task_checks for t in task_ids if not t.startswith("R")):
        raise GateError("Every non-regression card needs scoped checks in a multi-card plan")
    if any(k not in {t["id"] for t in tasks} for k in contract["method"].get("task_tests", {})):
        raise GateError("Revised plan cannot orphan existing task/test bindings")
    plan = {"schema": "aidevflow/work-plan-v1", "task_id": state["task_id"], "method_profile": contract["method"]["profile"], "items": items, "projections": []}
    errors = validate_work_plan(plan, state["task_id"], contract)
    if errors:
        raise GateError("Invalid revised work plan: " + "; ".join(errors))
    directory = run / "engineering-revisions" / revision["id"]
    plan_path = directory / "work-plan.json"
    if plan_path.exists():
        if read_json(plan_path) != plan:
            raise GateError("Interrupted engineering plan conflicts with current proposal")
    else:
        write_json_new(plan_path, plan)
    new = copy.deepcopy(state)
    new["artifact_reviews"] = copy.deepcopy(state.get("artifact_reviews", {}))
    contract_path = directory / "execution-contract.json"
    if contract_path.exists():
        if contract_path.read_bytes() != gate._safe_repo_path(revision["contract_draft"]).read_bytes():
            raise GateError("Interrupted reviewed Contract conflicts with current proposal")
    else:
        write_bytes_new(contract_path, gate._safe_repo_path(revision["contract_draft"]).read_bytes())
    new["contract_path"] = contract_path.relative_to(gate.root).as_posix()
    new["contract_sha256"] = sha256_file(contract_path)
    new["engineering_contract_subject"] = {"path": new["contract_path"], "sha256": new["contract_sha256"]}
    revision["previous_contract"] = {"path": state["contract_path"], "sha256": state["contract_sha256"]}
    new["work_plan"] = {"path": plan_path.relative_to(gate.root).as_posix(), "sha256": sha256_file(plan_path)}
    new["work_items"] = [{"id": i["id"], "title": i["title"], "depends_on": i["depends_on"], "acceptance_items": i["acceptance_items"],
                          "status": "not_started", "evidence": [], "updated_at": None} for i in items]
    new["frozen_tests"], new["task_test_reviews"] = {}, {}
    new["evidence_epoch"] = state.get("evidence_epoch", 0) + 1
    new["verifier_bundle"] = None
    new.pop("verdict_ref", None)
    new.pop("artifact_review_target", None)
    new.pop("blocker", None)
    new["current_stage"], new["status"] = "build", "active"
    new.setdefault("engineering_history", []).append(revision)
    new.pop("engineering_revision")
    # References still point to the real draft review inputs; canonical subjects are identical bytes.
    inverse = {v: k for k, v in revision["files"].items()}
    inverse[revision["contract_draft"]] = new["contract_path"]
    for ref in new["artifact_reviews"].values():
        ref["subjects"] = {inverse.get(k, k): v for k, v in ref["subjects"].items()}
    new["design_manifest"] = {rel: sha256_bytes(ZsspecDocsAdapter().semantic_content(rel, gate._safe_repo_path(revision["files"][rel]).read_bytes(), plan)) for rel in contract["design_artifacts"]}
    new["revision"], new["updated_at"] = state.get("revision", 0) + 1, utc_now()
    from .autonomy import attempt
    if not attempt(new, contract, "engineering_correction", {"contract": contract, "files": new["design_manifest"],
               "tests": {p: sha256_file(gate._safe_repo_path(revision["files"][p])) for p in contract["test_files"] if p in revision["files"]}}):
        state.update(status=new["status"], blocker=new["blocker"], routine_repair_progress=new["routine_repair_progress"], repair_attempts=new["repair_attempts"])
        gate._save(state, run, "automation_stalled")
        raise GateError("Engineering correction made no progress; diagnose the preserved proposal, ordinary repairs need no human authorization")
    journal = {"schema": "aidevflow/engineering-transaction-v1", "files": revision["files"], "before": revision["before"],
               "after": {rel: sha256_file(gate._safe_repo_path(p)) for rel, p in revision["files"].items()}, "state": new}
    write_json_new(run / "engineering-transaction.json", journal)
    return recover(gate)


def recover(gate):
    from ..gate import GateError, read_json, sha256_file, replace_file_atomic, write_json_atomic
    from ..locking import writer_lock
    import os, tempfile
    with writer_lock(gate.control):
        active = read_json(gate.active_path)
        state_path = gate._safe_repo_path(active["state_path"])
        run = state_path.parent
        journal_path = run / "engineering-transaction.json"
        value = read_json(journal_path)
        for rel, digest in value["after"].items():
            source = gate._safe_repo_path(value["files"][rel])
            source.relative_to(run / "engineering-revisions")
            if sha256_file(source) != digest:
                raise GateError("Reviewed engineering draft changed")
            target = gate._safe_repo_path(rel)
            if (sha256_file(target) if target.is_file() else None) not in (value["before"][rel], digest):
                raise GateError("Engineering transaction conflicts with newer edits: " + rel)
        for rel, draft in value["files"].items():
            target = gate._safe_repo_path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(gate._safe_repo_path(draft).read_bytes())
                replace_file_atomic(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        write_json_atomic(state_path, value["state"])
        journal_path.unlink()
        gate._event(value["state"]["task_id"], "engineering_revision_committed", state_snapshot=value["state"])
        return {"status": "active", "current_stage": "build", "evidence_epoch": value["state"]["evidence_epoch"]}
