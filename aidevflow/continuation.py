"""Bounded turn continuation, separate from business state and acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MAX_STALLED = 2
MAX_TOTAL = 30


def decide(root: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    from .gate import Gate, read_json, sha256_file, write_json_atomic
    from .locking import observer_lock
    gate = Gate(root)
    state, run_dir, contract = gate._load_active()
    if state.get("execution_policy", {}).get("continuation_owner") != "bounded_stop":
        return None
    if payload.get("agent_id") or state.get("status") not in ("active", "intake_started", "intent_review", "intent_approved", "retrying"):
        return None
    # A blocked write is not permission to ask another tool to do the same thing.
    # Never resume automatically immediately after a recorded protection block.
    recent_events = gate.events_path.read_text(encoding="utf-8").splitlines()[-1:] if gate.events_path.is_file() else []
    if recent_events and json.loads(recent_events[0]).get("event") == "hook_block":
        return None
    paths = list((run_dir / "agent-invocations").glob("*.json"))
    if contract:
        paths += [root / rel for key in ("design_artifacts", "test_files", "test_support_files") for rel in contract.get(key, [])]
    paths += list((root / "specs/aidevflow-intake" / state["task_id"]).glob("*.json"))
    signature = {"revision": state.get("revision", 0), "updated_at": state.get("updated_at"), "stage": state["current_stage"], "status": state["status"],
                 "files": {str(p.relative_to(root)): sha256_file(p) for p in paths if p.is_file()}}
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    with observer_lock(gate.control):
        path = run_dir / "continuation.json"
        previous = read_json(path) if path.is_file() else {}
        stalled = previous.get("stalled", 0) + 1 if previous.get("fingerprint") == fingerprint else 0
        total = previous.get("total", 0)
        limited = stalled >= MAX_STALLED or total >= MAX_TOTAL
        write_json_atomic(path, {"owner": "bounded_stop", "fingerprint": fingerprint, "stalled": stalled,
                                 "total": total if limited else total + 1, "attention_required": limited,
                                 "reason": "No durable progress or continuation budget exhausted" if limited else "Continue authorized work"})
    if limited:
        # No needs_human transition or reset of repair budgets. The user sees the
        # checkpoint/limit through status; native /goal can own future turns.
        return {"systemMessage": "AIDevFlow continuation paused after repeated stops/budget exhaustion; inspect status.continuation. Business state and evidence are preserved."}
    actions = gate._next_actions(state, contract)
    return {"decision": "block", "reason": (
        "AIDevFlow task is unfinished and ordinary work is authorized. Perform the next action now; "
        "do not end with a promise to act. " + " | ".join(actions) +
        ". If a role is still running, wait for its native completion; if it hit its turn limit, continue that role once with a compact output request. "
        "Use recover-runtime before repeating completed analysis. Stop for real user decisions, unavailable permissions or persistent protocol failure; never fabricate evidence. "
        "If native /goal owns continuation, set continuation --owner native to avoid two loops.")}


def set_owner(root: Path, owner: str) -> dict[str, Any]:
    from .locking import writer_lock
    with writer_lock(root.resolve() / ".aidevflow"):
        return _set_owner(root, owner)


def _set_owner(root: Path, owner: str) -> dict[str, Any]:
    from .gate import Gate, GateError
    gate = Gate(root)
    state, run_dir, _ = gate._load_active()
    gate._require_executable(state)
    if owner not in ("native", "bounded_stop", "manual"):
        raise GateError("Unknown continuation owner")
    # Explicit CLI choice only. No automatic migration of old acceptance rules.
    policy = state.setdefault("execution_policy", {})
    policy["continuation_owner"] = owner
    gate._save(state, run_dir, "continuation_owner_selected", owner=owner)
    return {"owner": owner, "task_id": state["task_id"]}
