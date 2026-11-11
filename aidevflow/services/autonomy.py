"""Routine repairs retain the approved business boundary and never mint approval."""
from __future__ import annotations

import hashlib
import json

from ..locking import writes
from ..method_adapters.zsspec_docs import enabled


def routine_checkpoint(state, contract):
    blocker = state.get("blocker", {})
    return bool(enabled(contract) and state.get("status") == "needs_human" and
                (blocker.get("kind") == "repair_budget" or blocker.get("kind") == "product" and
                 blocker.get("failure_type") in ("review_finding", "acceptance_test_failure") and
                 contract.get("routing", {}).get(blocker["failure_type"], {}).get("to") == "build"))


def attempt(state, contract, kind, subject):
    """Count unchanged repair attempts, rather than spending one shared allowance."""
    attempts = state.setdefault("repair_attempts", {})
    attempts[kind] = attempts.get(kind, 0) + 1
    if not enabled(contract):
        return sum(attempts.values()) <= contract.get("max_repair_attempts", 3) + state.get("repair_budget_extension", 0)
    fingerprint = hashlib.sha256(json.dumps(subject, sort_keys=True).encode()).hexdigest()
    progress = state.setdefault("routine_repair_progress", {})
    previous = progress.get(kind, {})
    repeated = previous.get("unchanged", 0) + 1 if previous.get("fingerprint") == fingerprint else 1
    progress[kind] = {"fingerprint": fingerprint, "unchanged": repeated}
    if repeated > contract.get("max_repair_attempts", 3):
        state.update(status="automation_stalled", blocker={"kind": "automation_stalled", "repair_kind": kind,
                     "reason": "Repeated repair attempts made no change to the failing candidate/subjects. Diagnose and replan; ordinary repair needs no approval."})
        return False
    return True


@writes
def recover_routine(gate):
    """Migrate a legacy shared-budget stop, without overriding real user decisions."""
    from ..gate import GateError
    state, run, contract = gate._load_active()
    blocker = state.get("blocker", {})
    if not routine_checkpoint(state, contract):
        raise GateError("Only a current-method ordinary repair budget checkpoint can recover automatically; preserve business decisions, user pauses and integrity incidents")
    if state.get("delivery_archive", {}).get("committed"):
        raise GateError("A delivered task cannot recover in place")
    gate.integrity()
    if state.get("delivery_archive"):
        from .delivery import reset
        reset(gate)
        state, run, contract = gate._load_active()
    state.update(status="active", current_stage="build" if state["current_stage"] == "verify" else state["current_stage"])
    state.pop("blocker", None)
    state.pop("verdict_ref", None)
    state["verifier_bundle"] = None
    state["evidence_epoch"] = state.get("evidence_epoch", 0) + 1
    if state["current_stage"] == "build" and not state.get("engineering_revision"):
        from .execution import reopen_affected
        from ..gate import read_json
        report = run / "verifier-report.json"
        failed = [i["id"] for i in read_json(report)["items"] if i["verdict"] not in ("pass", "waived")] if report.is_file() else []
        reopen_affected(state, contract, failed)
    gate._save(state, run, "routine_repair_recovered", authorization="approved_requirements", previous_blocker=blocker)
    return {"task_id": state["task_id"], "status": state["status"], "authorization": "approved_requirements"}
