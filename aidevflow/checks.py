"""Immutable evidence for coordinator attestations; never pretend they are test runs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .managed import artifact_path


def snapshot_files(gate: Any, state: dict, contract: dict, paths: list[str], bucket: str) -> list[dict]:
    from .gate import GateError, sha256_file
    if not paths:
        raise GateError("Evidence must include a non-empty report or screenshot; a narrative alone is insufficient")
    prepared = []
    for relative in dict.fromkeys(paths):
        source = gate._safe_repo_path(relative)
        rel = source.relative_to(gate.root).as_posix()
        if not artifact_path(contract, rel) or not source.is_file() or not source.stat().st_size:
            raise GateError(f"Evidence must be a non-empty file under contract.evidence_paths: {relative}")
        prepared.append((source, rel, sha256_file(source)))
    records = []
    for source, rel, digest in prepared:
        # Short names avoid Windows path-length failures; logical identity is metadata.
        identity = hashlib.sha256(rel.encode()).hexdigest()[:12]
        target = gate.runs / state["task_id"] / bucket / (identity + "-" + digest[:24] + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise GateError(f"Evidence changed during capture: {relative}")
        if not target.exists():
            target.write_bytes(content)
        if sha256_file(target) != digest:
            raise GateError(f"Evidence snapshot integrity mismatch: {relative}")
        records.append({"path": target.relative_to(gate.root).as_posix(), "sha256": digest,
                        "source_path": rel, "source_sha256": digest})
    return records


def intact(gate: Any, entries: list[dict], run_dir: Path) -> bool:
    from .gate import GateError, sha256_file
    try:
        for entry in entries:
            path = gate._safe_repo_path(entry["path"])
            path.relative_to(run_dir)
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                return False
    except (GateError, ValueError, KeyError, TypeError, OSError):
        return False
    return True


def record_design_review(gate: Any, reviewer: str, reason: str, paths: list[str]) -> dict:
    from .gate import GateError, sha256_file, utc_now, write_json_atomic
    from .managed import enabled
    state, run_dir, contract = gate._load_active()
    gate._require_stage(state, "design")
    gate.integrity()
    if not enabled(contract):
        raise GateError("record-design-review requires zsspec-managed-v1")
    if state.get("frozen_tests"):
        raise GateError("Tests are already frozen; do not rewrite the design review baseline")
    if not reviewer.strip() or not reason.strip():
        raise GateError("An AI coordinator reviewer name and review reason are required")
    if not state.get("work_plan"):
        raise GateError("Register the TASK-derived work plan before recording the design review")
    gate._check_method_steps(state, contract, ("design",))
    subjects = design_subjects(gate, state, contract)
    if any(value is None for value in subjects.values()):
        raise GateError("All declared design artifacts and executable test files must exist before review")
    files = snapshot_files(gate, state, contract, paths, "design-review-evidence")
    report = {"schema": "aidevflow/design-review-v1", "task_id": state["task_id"],
              "source": "ai_coordinator_attestation", "reviewer": reviewer.strip(), "reason": reason.strip(),
              "subjects": subjects, "evidence": files, "recorded_at": utc_now()}
    path = run_dir / "design-review.json"
    write_json_atomic(path, report)
    state["design_review"] = {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)}
    gate._save(state, run_dir, "design_review_recorded", reviewer=reviewer.strip(), source=report["source"])
    return report


def design_subjects(gate: Any, state: dict, contract: dict) -> dict:
    from .gate import sha256_file
    result = gate._design_manifest(state, contract)
    result.update({rel: sha256_file(gate._safe_repo_path(rel)) if gate._safe_repo_path(rel).is_file() else None
                   for rel in contract["test_files"] + contract.get("test_support_files", [])})
    result[state["work_plan"]["path"]] = state["work_plan"]["sha256"]
    return result


def require_design_review(gate: Any, state: dict, run_dir: Path, contract: dict) -> None:
    from .gate import GateError, read_json, sha256_file
    from .managed import enabled
    if not enabled(contract):
        return
    reference = state.get("design_review")
    if not reference or not state.get("work_plan"):
        raise GateError("Managed Design requires record-design-review before freezing tests")
    path = gate._safe_repo_path(reference["path"])
    path.relative_to(run_dir)
    if not path.is_file() or sha256_file(path) != reference["sha256"]:
        raise GateError("Design review evidence changed")
    report = read_json(path)
    if (report.get("subjects") != design_subjects(gate, state, contract)
            or not intact(gate, report.get("evidence", []), run_dir)):
        raise GateError("Design review is stale or its evidence changed; review current documents/tests again before freezing")


def record_check(gate: Any, check_id: str, result: str, candidate: str,
                 reviewer: str, reason: str, paths: list[str]) -> dict:
    from .gate import GateError, sha256_file, utc_now, write_json_atomic
    state, run_dir, contract = gate._load_active()
    gate._require_executable(state)
    if state["current_stage"] not in ("build", "verify"):
        raise GateError("Non-command checks may be recorded only during Build or Verify")
    gate.integrity()
    check = next((entry for entry in contract.get("required_checks", []) if entry["id"] == check_id), None)
    if not check or check.get("kind", "command") not in ("static", "manual"):
        raise GateError("record-check accepts only declared static/manual checks; executable checks must use run-tests")
    if result not in ("passed", "failed", "blocked") or not reviewer.strip() or not reason.strip():
        raise GateError("Check result, AI reviewer name and reason are required")
    current = gate._candidate(state, contract)["sha256"]
    if candidate != current:
        raise GateError("Check candidate is stale; inspect the current candidate before recording new evidence")
    if check["kind"] == "manual" and result == "passed" and not any(Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") for path in paths):
        raise GateError("A passed AI-assisted manual check requires screenshot evidence")
    evidence = snapshot_files(gate, state, contract, paths, "check-evidence")
    if gate._candidate(state, contract)["sha256"] != current:
        raise GateError("Candidate changed during check capture")
    record = {"schema": "aidevflow/check-result-v1", "task_id": state["task_id"], "id": check_id,
              "kind": check["kind"], "result": result, "source": "ai_coordinator_attestation",
              "reviewer": reviewer.strip(), "reason": reason.strip(), "evidence": evidence,
              "candidate_sha256": current, "evidence_epoch": state.get("evidence_epoch", 0), "recorded_at": utc_now()}
    records = state.setdefault("check_results", [])
    path = run_dir / "check-results" / f"{len(records) + 1:03d}.json"
    write_json_atomic(path, record)
    records.append({**record, "path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)})
    state["verifier_bundle"] = None
    state.pop("verdict_ref", None)
    if state.get("status") in ("accepted", "retrying"):
        state["status"] = "active"
    gate._save(state, run_dir, "check_recorded", check_id=check_id, result=result, source=record["source"])
    return record


def attestation_status(gate: Any, state: dict, check: dict, candidate: str) -> dict:
    from .gate import sha256_file
    record = next((entry for entry in reversed(state.get("check_results", [])) if entry["id"] == check["id"]), None)
    status = "missing"
    if record:
        status = record["result"]
        path = gate._safe_repo_path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"] or not intact(gate, record["evidence"], gate.runs / state["task_id"]):
            status = "invalid"
        elif record["candidate_sha256"] != candidate or record["evidence_epoch"] != state.get("evidence_epoch", 0):
            status = "stale"
    return {"id": check["id"], "kind": check["kind"], "suite": check["kind"], "target": None,
            "status": status, "source": "ai_coordinator_attestation", "evidence_path": record["path"] if record else None}
