"""Shared bounded JSONL transport. Native runtime policies live in providers."""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

from .gate import Gate, GateError
from .receipts import ROLE_ALIASES
from .runtimes import selected

MAX_FRAME = 8 * 1024 * 1024
def guard(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from .runtimes.registry import get_runtime
    return get_runtime(selected(root)).guard(root, payload)


def dispatch(root: Path, action: str, payload: dict[str, Any]) -> Any:
    bound_runtime = selected(root)
    if (bound_runtime == "zscode" and payload.get("runtime") != bound_runtime) or (
            payload.get("runtime") is not None and payload["runtime"] != bound_runtime):
        raise GateError("Native host identity differs from the project's bound runtime; resume in the configured host")
    gate = Gate(root)
    if action == "resume-checkpoint":
        from .services.checkpoints import resume_checkpoint
        return resume_checkpoint(root)
    if action in ("flow-enter", "review-prepare", "review-respond"):
        from .workflow import Workflow
        workflow = Workflow(root)
        if action == "flow-enter":
            return workflow.enter(payload.get("text", ""), payload.get("discussion_only", False), payload.get("approved_action", ""))
        if action == "review-prepare":
            return workflow.prepare_review(payload["session_id"])
        return workflow.respond(payload["discovery_id"], payload["token"], payload["session_id"], payload["choice"], payload.get("feedback", ""))
    if action == "discovery-approve":
        from .discovery import Discovery
        return Discovery(root).approve(payload["id"], payload["revision"], payload["sha256"],
                                       selected(root) + " user", payload["utterance"], "native_user_command")
    if action == "method-accept":
        from .method_adapters.zsspec_docs import enabled
        state, _, contract = gate._load_active()
        if enabled(contract):
            raise GateError("Current zsspec closure is automatic after independent verification; use the delivery archive workflow")
        from .method_execution import execute
        return execute(root, "close", payload["module"], by=selected(root) + " user", utterance=payload["utterance"])
    if action == "ping":
        return {"protocol": 1, "runtime": selected(root), "python": sys.executable}
    if action == "status":
        if (root / ".aidevflow/discovery-handoff.json").exists():
            return {"status": "handoff_pending", "next_action": "discovery-start-delivery"}
        if (root / ".aidevflow/active-discovery.json").is_file():
            from .discovery import Discovery
            return Discovery(root).load()[0]
        if not gate.active_path.is_file():
            return {"active_run": None}
        from .method_resources import managed_context
        summary = gate.status(summary=True)
        return {**summary, "method_context": managed_context(summary["current_stage"])}
    if action == "preflight":
        from .runtimes.registry import get_runtime
        return get_runtime(selected(root)).preflight(root, payload)
    if action == "guard":
        return guard(root, payload)
    if action == "role-context":
        from .contexts import role_context
        return role_context(root, payload.get("role"))
    if action == "method-load":
        from .method_resources import load_resource
        return load_resource(root, payload.get("input", {}))
    if action == "observe":
        from .runtimes.registry import get_runtime
        return get_runtime(selected(root)).observe(root, payload)
    if action == "recover":
        if (root / ".aidevflow/discovery-handoff.json").exists():
            return {"recovered_events": 0, "warnings": ["Recover the interrupted handoff with discovery-start-delivery before dispatching roles"]}
        from .runtimes import recover
        if not gate.active_path.is_file() and not (root / ".aidevflow/active-discovery.json").is_file():
            return {"recovered_events": 0}
        result = recover(root)
        from .runtimes.registry import get_runtime
        result["recovered_events"] += get_runtime(selected(root)).recover_live_entries(root, payload)
        return result
    if action == "stop":
        if not gate.active_path.is_file():
            return None
        from .runtimes.registry import get_runtime
        return get_runtime(selected(root)).advisory_stop(root)
    raise GateError(f"Unknown bridge action: {action}")


def serve(root: Path) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    lease_holder: list[Any] = []
    try:
        return _serve(root, lease_holder)
    finally:
        for lease in lease_holder:
            lease.__exit__(None, None, None)


def _serve(root: Path, leases: list[Any]) -> int:
    while True:
        raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
        if not raw:
            return 0
        if len(raw) > MAX_FRAME:
            return 2  # Do not parse or execute a partial oversized frame.
        request = {}
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict) or request.get("protocol") != 1 or not isinstance(request.get("id"), str):
                raise GateError("Invalid bridge protocol frame")
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise GateError("Bridge payload must be an object")
            if (not leases and request.get("action") in ("guard", "observe", "flow-enter", "review-prepare", "review-respond")
                    and payload.get("actor", {}).get("kind") == "main"
                    and (request.get("action") == "flow-enter" or (root / ".aidevflow/active-run.json").is_file() or (root / ".aidevflow/active-discovery.json").is_file())):
                from .locking import observer_lock
                lease = observer_lock(root / ".aidevflow", timeout=0, name="coordinator.lock")
                lease.__enter__()
                leases.append(lease)
            result = dispatch(root, request.get("action", ""), payload)
            reply = {"id": request["id"], "ok": True, "result": result}
        except (GateError, ValueError, OSError, KeyError, TypeError, TimeoutError) as exc:
            reply = {"id": request.get("id") if isinstance(request, dict) else None, "ok": False, "error": str(exc)}
        print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(serve(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()))
