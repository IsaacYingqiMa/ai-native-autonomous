"""Versioned test/spec baselines and recoverable, operator-authorized revisions."""
from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
from pathlib import Path

from .gate import GateError, read_json, sha256_file, sha256_bytes, write_json_new, replace_file_atomic, utc_now, validate_contract, validate_work_plan
from .recovery import baseline_id, digest, put_blob, owned_json


def support_files(gate, contract: dict) -> list[str]:
    """Discover common execution controls, not an assumed complete Python import graph."""
    found = set()
    for rel in contract.get("test_files", []):
        parent = gate._safe_repo_path(rel).parent
        while parent.is_relative_to(gate.root):
            helper = parent / "conftest.py"
            if helper.is_file():
                found.add(helper.relative_to(gate.root).as_posix())
            if parent == gate.root:
                break
            parent = parent.parent
    for name, marker in (("pytest.ini", None), ("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        path = gate.root / name
        if path.is_file() and (marker is None or marker in path.read_text(encoding="utf-8")):
            found.add(name)
    for argv in contract.get("test_commands", {}).values():
        for arg in argv:
            if arg.startswith("-") or "{" in arg or Path(arg).is_absolute():
                continue
            try:
                path = gate._safe_repo_path(arg)
                if path.suffix in (".py", ".sh", ".ps1", ".js", ".ts") and path.is_file() and not arg.startswith(("scripts/aidevflow", ".aidevflow/")):
                    found.add(path.relative_to(gate.root).as_posix())
            except (OSError, ValueError):
                continue
    return sorted(found - set(contract.get("test_files", [])) - set(contract.get("test_support_files", [])))


def require_support(gate, contract: dict) -> None:
    missing = support_files(gate, contract)
    if missing:
        raise GateError("Undeclared test execution controls: " + ", ".join(missing) +
                        ". Add them through a baseline revision before freezing; do not edit a pinned Contract")


def snapshot(gate, state: dict, contract: dict, identity: str | None = None) -> dict:
    _, run, _ = gate._load_active()
    identity = identity or "BL-" + secrets.token_hex(12)
    files = {}
    for rel in sorted(set(contract.get("test_files", []) + contract.get("test_support_files", []) + contract.get("design_artifacts", []))):
        path = gate._safe_repo_path(rel)
        blob = put_blob(run / "baseline-objects", path.read_bytes())
        files[rel] = {"path": Path(blob["path"]).relative_to(gate.root).as_posix(), "sha256": blob["sha256"]}
    value = {"schema": "aidevflow/baseline-v1", "id": identity, "task_id": state["task_id"],
             "created_at": utc_now(), "parent": state.get("baseline_ref"), "files": files,
             "contract": {"path": state["contract_path"], "sha256": state["contract_sha256"]},
             "work_plan": state.get("work_plan"), "method_policy": state.get("method_policy")}
    path = run / "baselines" / identity / "manifest.json"
    write_json_new(path, value)
    return {"id": identity, "path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)}


def subject(gate, state: dict, contract: dict) -> str:
    paths = set(state.get("frozen_tests", {})) | set(contract.get("design_artifacts", []))
    # Include actual bytes: an interrupted/unauthorized edit cannot silently become the old baseline.
    return digest({"state": {key: state.get(key) for key in ("task_id", "revision", "contract_sha256", "work_plan", "baseline_ref", "evidence_epoch")},
                   "actual": {rel: sha256_file(gate._safe_repo_path(rel)) if gate._safe_repo_path(rel).is_file() else None for rel in sorted(paths)}})


def draft_file(gate, state: dict, rel: str) -> Path:
    path = gate._safe_repo_path(rel)
    base = gate.root / "specs/aidevflow-intake" / state["task_id"] / "baseline-drafts"
    if not path.is_relative_to(base) or not path.is_file():
        raise GateError("Revision drafts/reviews must be files under this task's specs/aidevflow-intake/<id>/baseline-drafts/")
    return path


def allowed_target(gate, path: str, allowed: set[str]) -> Path:
    result = gate._safe_repo_path(path)
    if path not in allowed or result.relative_to(gate.root).as_posix() != path or result.relative_to(gate.root).parts[0] in (".git", ".aidevflow", ".claude", ".omp", "aidevflow") or path.startswith("scripts/aidevflow") or path == "specs/zsspec.yml":
        raise GateError("Baseline revision may change only declared test/support/spec files, never implementation or method configuration")
    return result


def plan_revision(gate, draft_path: str) -> dict:
    state, run, contract = gate._load_active()
    if not contract or state["current_stage"] not in ("design", "build", "verify") or state["status"] in ("archived", "delivery_ready", "delivery_ready_with_exceptions"):
        raise GateError("A baseline revision requires an open Design/Build/Verify task")
    draft = read_json(draft_file(gate, state, draft_path))
    if (not isinstance(draft, dict) or set(draft) - {"schema", "reason", "changes", "contract_draft", "work_plan_draft"}
            or draft.get("schema") != "aidevflow/baseline-revision-v1" or not isinstance(draft.get("reason"), str) or not draft["reason"].strip()):
        raise GateError("Revision draft requires schema, reason, changes and optional contract_draft/work_plan_draft")
    if any(key in draft and (not isinstance(draft[key], str) or not draft[key].strip()) for key in ("contract_draft", "work_plan_draft")):
        raise GateError("Contract/work-plan draft references must be non-empty paths")
    new_contract = read_json(draft_file(gate, state, draft["contract_draft"])) if draft.get("contract_draft") else copy.deepcopy(contract)
    if not isinstance(new_contract, dict):
        raise GateError("Revised Contract must be a JSON object")
    mutable = {"test_files", "test_support_files", "red_test_files", "test_commands", "required_checks", "design_artifacts", "acceptance_items", "evidence_paths", "test_policy"}
    if any(new_contract.get(key) != contract.get(key) for key in set(new_contract) | set(contract) if key not in mutable):
        raise GateError("Baseline revision cannot change task identity, product scope, method/runtime or repair budgets")
    errors = validate_contract(new_contract) + gate._validate_available_method_skills(new_contract)
    if errors:
        raise GateError("Invalid revised Contract: " + "; ".join(errors))
    plan = read_json(draft_file(gate, state, draft["work_plan_draft"])) if draft.get("work_plan_draft") else gate._plan(state)
    if new_contract.get("work_plan_required") and not plan:
        raise GateError("A revised work plan is required")
    if plan:
        errors = validate_work_plan(plan, state["task_id"], new_contract)
        from .adapters import validate_projections
        errors += validate_projections(plan, new_contract)
        if errors:
            raise GateError("Invalid revised work plan: " + "; ".join(errors))
    changes = draft.get("changes")
    if not isinstance(changes, list):
        raise GateError("changes must be an array of path/draft entries")
    allowed = set(new_contract.get("test_files", []) + new_contract.get("test_support_files", []) + new_contract.get("design_artifacts", []))
    replacements = {}
    for change in changes:
        if (not isinstance(change, dict) or set(change) != {"path", "draft"}
                or not all(isinstance(change.get(key), str) and change[key].strip() for key in ("path", "draft"))
                or change["path"] in replacements):
            raise GateError("Each revision change needs a unique path and draft")
        allowed_target(gate, change["path"], allowed)
        blob = put_blob(run / "baseline-objects", draft_file(gate, state, change["draft"]).read_bytes())
        blob["path"] = Path(blob["path"]).relative_to(gate.root).as_posix()
        replacements[change["path"]] = blob
    # Common execution controls must be in the new Contract, including ones still present in the old tree.
    require_support(gate, new_contract)
    files = {}
    for rel in sorted(allowed):
        allowed_target(gate, rel, allowed)
        if rel in replacements:
            files[rel] = replacements[rel]
        else:
            path = gate._safe_repo_path(rel)
            if not path.is_file():
                raise GateError(f"Missing revised baseline file: {rel}")
            blob = put_blob(run / "baseline-objects", path.read_bytes())
            files[rel] = {"path": Path(blob["path"]).relative_to(gate.root).as_posix(), "sha256": blob["sha256"]}
    if plan:
        from .adapters import replace_status
        for binding in plan.get("projections", []):
            replace_status(gate._safe_repo_path(files[binding["path"]]["path"]).read_text(encoding="utf-8"), binding["row_id"], "待执行")
        acceptance = {item["id"] for item in new_contract.get("acceptance_items", [])}
        if acceptance - {ref for item in plan["items"] for ref in item.get("acceptance_items", [])}:
            raise GateError("Revised work plan must cover all acceptance items")
    proposal = {"schema": "aidevflow/baseline-proposal-v1", "id": "BL-" + secrets.token_hex(12),
                "task_id": state["task_id"], "subject_sha256": subject(gate, state, contract),
                "reason": draft["reason"], "old_baseline_id": baseline_id(state), "old_contract": contract,
                "old_frozen_tests": state.get("frozen_tests", {}),
                "contract": new_contract, "work_plan": plan, "files": files,
                "actual_before": {rel: sha256_file(gate._safe_repo_path(rel)) if gate._safe_repo_path(rel).is_file() else None for rel in files},
                "changed_paths": sorted(replacements), "created_at": utc_now()}
    path = run / "baseline-proposals" / f"{proposal['id']}.json"
    write_json_new(path, proposal)
    gate._event(state["task_id"], "baseline_revision_proposed", proposal_id=proposal["id"], reason=proposal["reason"])
    return {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path), "proposal": proposal}


def _bytes_atomic(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file_atomic(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _blob(gate, run: Path, ref: dict | None) -> bytes | None:
    if ref is None:
        return None
    path = gate._safe_repo_path(ref["path"])
    if not path.is_relative_to(run / "baseline-objects") or not path.is_file() or sha256_file(path) != ref["sha256"]:
        raise GateError("Baseline object changed; preserve the revision for diagnosis")
    return path.read_bytes()


def apply_revision(gate, proposal_path: str, sha: str, by: str, utterance: str, reviewer: str, review_path: str) -> dict:
    state, run, contract = gate._load_active()
    proposal_ref = {"path": proposal_path, "sha256": sha}
    proposal = owned_json(gate, run, proposal_ref, "baseline-proposals")
    if state.get("baseline_ref"):
        current = owned_json(gate, run, state["baseline_ref"], "baselines")
        if current.get("proposal") == proposal_ref:
            return {"mode": "finish", "baseline": state["baseline_ref"], "current_stage": state["current_stage"]}
    if not all(value.strip() for value in (by, utterance, reviewer)):
        raise GateError("A reviewed concrete proposal and actual operator authorization are required")
    if proposal["task_id"] != state["task_id"] or proposal["subject_sha256"] != subject(gate, state, contract):
        raise GateError("Baseline proposal is stale; prepare and review a new proposal")
    if any((sha256_file(gate._safe_repo_path(rel)) if gate._safe_repo_path(rel).is_file() else None) != sha
           for rel, sha in proposal["actual_before"].items()):
        raise GateError("A proposed target changed since review; prepare a new proposal")
    review = draft_file(gate, state, review_path).read_bytes()
    if not review.strip():
        raise GateError("The baseline review report cannot be empty")
    new = copy.deepcopy(state)
    identity = proposal["id"] + "-" + secrets.token_hex(4)
    directory = run / "baselines" / identity
    directory.mkdir(parents=True, exist_ok=True)
    contract_path = directory / "contract.json"
    write_json_new(contract_path, proposal["contract"])
    new["contract_path"] = contract_path.relative_to(gate.root).as_posix()
    new["contract_sha256"] = sha256_file(contract_path)
    if proposal["work_plan"]:
        plan_path = directory / "work-plan.json"
        write_json_new(plan_path, proposal["work_plan"])
        new["work_plan"] = {"path": plan_path.relative_to(gate.root).as_posix(), "sha256": sha256_file(plan_path)}
        new["work_items"] = [{**item, "depends_on": item.get("depends_on", []), "status": "not_started", "evidence": []}
                             for item in proposal["work_plan"]["items"]]
    new["frozen_tests"] = {rel: proposal["files"][rel]["sha256"] for rel in
                           proposal["contract"]["test_files"] + proposal["contract"].get("test_support_files", [])}
    authorization = {"by": by, "utterance": utterance, "source": "operator_assertion", "proposal": proposal_ref}
    manifest = {"schema": "aidevflow/baseline-v1", "id": identity, "proposal": proposal_ref, "task_id": state["task_id"],
                "parent": state.get("baseline_ref"), "previous_id": baseline_id(state), "files": proposal["files"],
                "contract": {"path": new["contract_path"], "sha256": new["contract_sha256"]},
                "authorization": authorization, "reason": proposal["reason"], "created_at": utc_now()}
    manifest_path = directory / "manifest.json"
    write_json_new(manifest_path, manifest)
    new["baseline_ref"] = {"id": identity, "path": manifest_path.relative_to(gate.root).as_posix(), "sha256": sha256_file(manifest_path)}
    new["design_manifest"] = None
    new["verifier_bundle"] = None
    new.pop("verdict_ref", None)
    new.pop("accepted_with_exceptions", None)
    new.pop("waived_acceptance_items", None)
    new["evidence_epoch"] = new.get("evidence_epoch", 0) + 1
    new["evidence_recovery_pending"] = []  # New baseline requires an entirely fresh check set.
    new["test_waivers"] = []
    new["current_stage"] = "design"
    new["status"] = "active"
    new.pop("blocker", None)
    if sum(state.get("repair_attempts", {}).values()) > contract.get("max_repair_attempts", 3) + state.get("repair_budget_extension", 0):
        new["status"] = "needs_human"
        new["blocker"] = {"kind": "repair_budget", "reason": "Baseline revised; exhausted repair budget still requires explicit resume-run"}
    new["revision"] = state.get("revision", 0) + 1
    new["updated_at"] = utc_now()
    report_blob = put_blob(run / "baseline-objects", review)
    report_blob["path"] = Path(report_blob["path"]).relative_to(gate.root).as_posix()
    from .adapters import semantic_content
    subjects = {rel: sha256_bytes(semantic_content(rel, _blob(gate, run, blob), proposal["work_plan"]))
                for rel, blob in proposal["files"].items() if rel in proposal["contract"]["design_artifacts"]}
    subjects.update(new["frozen_tests"])
    if new.get("work_plan"):
        subjects[new["work_plan"]["path"]] = new["work_plan"]["sha256"]
    review_record = {"schema": "aidevflow/design-review-v1", "task_id": state["task_id"],
        "source": "ai_coordinator_attestation", "reviewer": reviewer, "reason": proposal["reason"], "subjects": subjects,
        "evidence": [{**report_blob, "source_path": review_path, "source_sha256": report_blob["sha256"]}], "authorization": authorization}
    # checks.intact restricts review snapshots to the run directory, which includes baseline objects.
    review_record_path = directory / "design-review.json"
    write_json_new(review_record_path, review_record)
    new["design_review"] = {"path": review_record_path.relative_to(gate.root).as_posix(), "sha256": sha256_file(review_record_path)}
    writes = []
    for rel, after in proposal["files"].items():
        target = allowed_target(gate, rel, set(proposal["files"]))
        before = put_blob(run / "baseline-objects", target.read_bytes()) if target.is_file() else None
        if before:
            before["path"] = Path(before["path"]).relative_to(gate.root).as_posix()
        writes.append({"path": rel, "before": before, "after": after})
    state_path = run / "run-state.json"
    before = put_blob(run / "baseline-objects", state_path.read_bytes())
    after = put_blob(run / "baseline-objects", (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for blob in (before, after):
        blob["path"] = Path(blob["path"]).relative_to(gate.root).as_posix()
    writes.append({"path": state_path.relative_to(gate.root).as_posix(), "before": before, "after": after})
    test_manifest = run / "test-manifest.json"
    before = put_blob(run / "baseline-objects", test_manifest.read_bytes()) if test_manifest.is_file() else None
    after = put_blob(run / "baseline-objects", (json.dumps({"schema": "aidevflow/test-manifest-v1", "files": new["frozen_tests"], "baseline_id": identity}) + "\n").encode("utf-8"))
    for blob in (before, after):
        if blob:
            blob["path"] = Path(blob["path"]).relative_to(gate.root).as_posix()
    writes.insert(-1, {"path": test_manifest.relative_to(gate.root).as_posix(), "before": before, "after": after})
    journal = {"schema": "aidevflow/baseline-transaction-v1", "task_id": state["task_id"], "id": identity,
               "writes": writes, "authorization": authorization, "new_state": new}
    journal_path = run / "baseline-transactions" / f"{identity}.json"
    write_json_new(journal_path, journal)
    pending = run / "baseline-transaction.json"
    write_json_new(pending, {"path": journal_path.relative_to(gate.root).as_posix(), "sha256": sha256_file(journal_path)})
    # An interruption from here leaves an explicit, recoverable transaction; no normal Gate command may proceed.
    return recover_transaction(gate, "finish", by, utterance)


def recover_transaction(gate, mode: str, by: str, utterance: str, accept_conflicts: bool = False) -> dict:
    if mode not in ("finish", "rollback") or not by.strip() or not utterance.strip():
        raise GateError("Transaction recovery requires finish/rollback and actual operator authorization")
    active = read_json(gate.active_path)
    from .gate import safe_id
    task = safe_id(active["task_id"])
    run = gate.runs / task
    pending = run / "baseline-transaction.json"
    if not pending.is_file():
        raise GateError("No interrupted baseline transaction")
    journal = owned_json(gate, run, read_json(pending), "baseline-transactions")
    if journal["task_id"] != task:
        raise GateError("Baseline transaction belongs to another task")
    images = []
    conflicts = []
    for write in journal["writes"]:
        target = gate._safe_repo_path(write["path"])
        if target not in (run / "run-state.json", run / "test-manifest.json"):
            allowed_target(gate, write["path"], set(journal["new_state"]["frozen_tests"]) |
                           set(read_json(gate._safe_repo_path(journal["new_state"]["contract_path"]))["design_artifacts"]))
        before, after = _blob(gate, run, write["before"]), _blob(gate, run, write["after"])
        current = target.read_bytes() if target.is_file() else None
        if current not in (before, after):
            if not accept_conflicts:
                raise GateError(f"Revision conflict at {write['path']}; preserve changes or explicitly use --accept-conflicts to snapshot them")
            if current is not None:
                blob = put_blob(run / "baseline-objects", current)
                blob["path"] = Path(blob["path"]).relative_to(gate.root).as_posix()
            else:
                blob = None
            conflicts.append({"path": write["path"], "snapshot": blob})
        images.append((target, after if mode == "finish" else before))
    attempt = run / "baseline-recovery-attempts" / (secrets.token_hex(16) + ".json")
    write_json_new(attempt, {"schema": "aidevflow/baseline-recovery-attempt-v1", "transaction": read_json(pending),
                            "mode": mode, "by": by, "utterance": utterance, "conflicts": conflicts, "created_at": utc_now()})
    for target, image in images:
        _bytes_atomic(target, image)
    recovered = read_json(run / "run-state.json")
    gate._event(task, "baseline_revision_committed" if mode == "finish" else "baseline_revision_rolled_back",
                transaction_id=journal["id"], by=by, utterance=utterance, state_snapshot=recovered,
                recovery_attempt={"path": attempt.relative_to(gate.root).as_posix(), "sha256": sha256_file(attempt)})
    pending.unlink()
    return {"mode": mode, "baseline": recovered.get("baseline_ref"), "current_stage": recovered["current_stage"]}
