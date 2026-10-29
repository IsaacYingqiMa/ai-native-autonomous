"""Audited recovery of execution evidence, without rewriting a historical result."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .gate import GateError, read_json, sha256_bytes, sha256_file, utc_now, write_json_new, write_bytes_new


def digest(value: Any) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def execution_id(record: dict) -> str:
    return record.get("id") or "legacy-" + digest({key: record.get(key) for key in
        ("evidence_path", "evidence_sha256", "suite", "target", "finished_at")})[:24]


def baseline_id(state: dict) -> str:
    return (state.get("baseline_ref") or {}).get("id") or "legacy-" + digest(state.get("frozen_tests", {}))[:24]


def put_blob(directory: Path, content: bytes) -> dict:
    sha = sha256_bytes(content)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / sha
    if path.exists():
        if sha256_file(path) != sha:
            raise GateError("Immutable recovery object changed; preserve it for diagnosis")
    else:
        write_bytes_new(path, content)
    return {"path": str(path), "sha256": sha}


def owned_json(gate, run: Path, reference: dict, bucket: str) -> dict:
    path = gate._safe_repo_path(reference["path"])
    if not path.is_relative_to(run / bucket) or not path.is_file() or sha256_file(path) != reference["sha256"]:
        raise GateError(f"Immutable {bucket} reference changed")
    return read_json(path)


def quarantined(gate, state: dict, run: Path) -> set[str]:
    result = set()
    for ref in state.get("evidence_repairs", []):
        repair = owned_json(gate, run, ref, "evidence-repairs")
        for incident in repair["incidents"]:
            for blob in incident.get("snapshots", []):
                path = gate._safe_repo_path(blob["path"])
                if not path.is_relative_to(run / "recovery-objects") or not path.is_file() or sha256_file(path) != blob["sha256"]:
                    raise GateError("Evidence incident snapshot changed")
            result.add(incident["execution_id"])
    return result


def effective_records(gate, state: dict, run: Path) -> list[dict]:
    excluded = quarantined(gate, state, run)
    return [item for item in state.get("test_runs", []) if execution_id(item) not in excluded]


def diagnose(gate, *, history: bool = True) -> dict:
    state, run, _ = gate._load_active()
    excluded = quarantined(gate, state, run)
    anomalies = []
    identities = set()
    for item in state.get("test_runs", []):
        identity = execution_id(item)
        if identity in identities:
            raise GateError("Duplicate execution identities in the ledger; preserve state for maintenance")
        identities.add(identity)
        if identity in excluded:
            continue
        failures = []
        for ref in [{"path": item["evidence_path"], "sha256": item.get("evidence_sha256")}, *item.get("artifacts", [])]:
            path = gate._safe_repo_path(ref["path"])
            if not path.is_relative_to(run):
                raise GateError("Execution evidence is outside its task directory")
            actual = sha256_file(path) if path.is_file() else None
            if actual != ref["sha256"]:
                failures.append({"path": ref["path"], "expected_sha256": ref["sha256"],
                                 "observed_sha256": actual, "kind": "missing" if actual is None else "hash_mismatch"})
        if failures:
            anomalies.append({"execution_id": identity, "suite": item["suite"], "target": item.get("target"),
                              "record": item, "failures": failures})
    orphans = []
    if history and gate.events_path.is_file():
        seen = set(identities)
        lines = gate.events_path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                if number == len(lines):
                    break
                raise GateError(f"Event log is corrupt at line {number}; preserve it for maintenance") from exc
            if event.get("task_id") != state["task_id"] or event.get("event") != "test_executed":
                continue
            snapshot = event.get("state_snapshot", {})
            for item in snapshot.get("test_runs", [])[-1:]:
                identity = execution_id(item)
                if identity not in seen:
                    seen.add(identity)
                    orphans.append({"execution_id": identity, "record": item, "event_time": event.get("ts")})
    return {"task_id": state["task_id"], "ok": not anomalies,
            "anomalies": anomalies, "quarantined_ids": sorted(excluded), "event_only_executions": orphans,
            "pending_replacements": state.get("evidence_recovery_pending", [])}


def subject(state: dict) -> str:
    return digest({key: state.get(key) for key in ("task_id", "contract_sha256", "execution_binding_ref", "baseline_ref",
        "frozen_tests", "evidence_epoch", "test_runs", "evidence_repairs")})


def plan_repair(gate, reason: str) -> dict:
    if not reason.strip():
        raise GateError("Evidence repair requires a reason")
    state, run, _ = gate._load_active()
    if state["status"] in ("archived", "delivery_ready", "delivery_ready_with_exceptions"):
        raise GateError("A closed task cannot be repaired in place")
    diagnosis = diagnose(gate)
    if not diagnosis["anomalies"]:
        raise GateError("No missing or changed execution evidence; ordinary test failures cannot be quarantined")
    plan = {"schema": "aidevflow/evidence-repair-plan-v1", "id": secrets.token_hex(16),
            "task_id": state["task_id"], "subject_sha256": subject(state), "reason": reason,
            "created_at": utc_now(), "incidents": diagnosis["anomalies"],
            "event_only_executions": diagnosis["event_only_executions"]}
    for incident in plan["incidents"]:
        incident["snapshots"] = []
        for failure in incident["failures"]:
            if failure["observed_sha256"] is not None:
                blob = put_blob(run / "recovery-objects", gate._safe_repo_path(failure["path"]).read_bytes())
                blob["path"] = Path(blob["path"]).relative_to(gate.root).as_posix()
                incident["snapshots"].append(blob)
    path = run / "evidence-repair-plans" / f"{plan['id']}.json"
    write_json_new(path, plan)
    gate._event(state["task_id"], "evidence_repair_planned", plan_id=plan["id"], reason=reason)
    return {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path), "plan": plan}


def apply_repair(gate, plan_path: str, sha: str, by: str, utterance: str) -> dict:
    state, run, _ = gate._load_active()
    if state["status"] in ("archived", "delivery_ready", "delivery_ready_with_exceptions"):
        raise GateError("A closed task cannot be repaired in place")
    if not by.strip() or not utterance.strip():
        raise GateError("Repair requires an operator and their actual authorization statement")
    ref = {"path": plan_path, "sha256": sha}
    plan = owned_json(gate, run, ref, "evidence-repair-plans")
    for existing in state.get("evidence_repairs", []):
        result = owned_json(gate, run, existing, "evidence-repairs")
        if result["plan"] == ref:
            return result
    if plan["task_id"] != state["task_id"] or plan["subject_sha256"] != subject(state):
        raise GateError("Evidence repair plan is stale; diagnose and prepare a fresh plan")
    current = {item["execution_id"]: item for item in diagnose(gate, history=False)["anomalies"]}
    for incident in plan["incidents"]:
        actual = current.get(incident["execution_id"])
        if not actual or actual["failures"] != incident["failures"]:
            raise GateError("Observed evidence changed since planning; prepare a new repair plan")
    result = {"schema": "aidevflow/evidence-repair-v1", "id": secrets.token_hex(16), "plan": ref,
              "incidents": plan["incidents"], "event_only_executions": plan["event_only_executions"],
              "reason": plan["reason"], "authorization": {"by": by, "utterance": utterance, "source": "operator_assertion"},
              "applied_at": utc_now()}
    path = run / "evidence-repairs" / f"{result['id']}.json"
    write_json_new(path, result)
    state.setdefault("evidence_repairs", []).append({"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)})
    pending = state.setdefault("evidence_recovery_pending", [])
    for incident in plan["incidents"]:
        pending.append({"execution_id": incident["execution_id"], "suite": incident["suite"], "target": incident["target"]})
    state["verifier_bundle"] = None
    state.pop("verdict_ref", None)
    state.pop("accepted_with_exceptions", None)
    state.pop("waived_acceptance_items", None)
    if state.get("blocker", {}).get("kind") == "evidence_integrity":
        state["status"] = "active"
    if state.get("status") == "accepted":
        state["status"] = "active"
    if state.get("blocker", {}).get("kind") in (None, "evidence_integrity", "evidence_recovery"):
        state["blocker"] = {"kind": "evidence_recovery", "reason": "Real replacement executions are required", "repair_id": result["id"]}
    gate._save(state, run, "evidence_repair_applied", repair_id=result["id"], authorization=result["authorization"])
    return result


def replaced(state: dict, record: dict) -> None:
    if not record["passed"] and not record.get("accepted_with_exceptions") and not (record["suite"] == "red" and record["valid_red"]):
        return
    resolved = [item for item in state.get("evidence_recovery_pending", [])
                if (item["suite"], item["target"]) == (record["suite"], record.get("target"))]
    if resolved:
        record["replaces_incidents"] = [item["execution_id"] for item in resolved]
        state["evidence_recovery_pending"] = [item for item in state["evidence_recovery_pending"] if item not in resolved]
        if not state["evidence_recovery_pending"] and state.get("blocker", {}).get("kind") == "evidence_recovery":
            state.pop("blocker", None)


def require_healthy(gate, state: dict, run: Path) -> None:
    report = diagnose(gate, history=False)
    if report["anomalies"]:
        state["blocker"] = {"kind": "evidence_integrity", "reason": "Execution evidence is missing or changed",
                            "execution_ids": [item["execution_id"] for item in report["anomalies"]],
                            "next_action": "diagnose-evidence; plan-evidence-repair; apply-evidence-repair; rerun affected checks"}
        state["status"] = "needs_human"
        gate._save(state, run, "evidence_integrity_blocked", incidents=state["blocker"]["execution_ids"])
        raise GateError("Execution evidence is damaged; use diagnose-evidence and the audited evidence repair workflow")
    if state.get("evidence_recovery_pending"):
        raise GateError("Evidence repair still requires fresh executions; damaged Red needs a reviewed baseline revision/replay")
