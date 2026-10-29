"""OMP native task observations -> versioned, input-bound durable receipts.

Only typed task results establish completion. Launches and prose never do.
Each update reads one invocation rather than replaying the whole event history.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .locking import observer_lock
from .receipts import ROLE_ALIASES, execution_context
from .runtimes import digest, selected

SCHEMA = "aidevflow/invocation-v3"
MAX_BYTES = 8 * 1024 * 1024


def _request(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from .gate import GateError
    session, call = payload.get("session_id"), payload.get("toolCallId")
    if not all(isinstance(x, str) and x.strip() and len(x) <= 256 for x in (session, call)):
        raise GateError("OMP task observation requires exact session and native call IDs")
    inp = payload.get("input")
    if isinstance(inp, dict) and isinstance(inp.get("tasks"), list) and len(inp["tasks"]) == 1:
        item = inp["tasks"][0]
        inp = {**item, "shared_context": inp.get("context", "")} if isinstance(item, dict) else None
    if not isinstance(inp, dict) or inp.get("agent") not in ROLE_ALIASES:
        raise GateError("Critical OMP roles require a single native task call")
    if not isinstance(inp.get("task"), str) or not inp["task"].strip():
        raise GateError("OMP role assignment is empty")
    if inp.get("isolated") or inp.get("tasks") or inp.get("sourceSession") or "tools" in inp or "outputSchema" in inp:
        raise GateError("Critical OMP roles must use a fresh single non-isolated task")
    return digest(["omp", session, call])[:32], inp


def observe(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from .gate import Gate, GateError, read_json, write_json_atomic, utc_now
    from .agents import parse_result
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_BYTES:
        raise GateError("OMP observation exceeds size limit")
    ident, inp = _request(payload)
    if payload.get("type") not in ("tool_call", "tool_result"):
        raise GateError("Unsupported OMP observation event")
    with observer_lock(root / ".aidevflow"):
        gate = Gate(root)
        state, run, _ = gate._load_active()
        if selected(root, state) != "omp":
            raise GateError("This task is pinned to a different runtime")
        path = run / "agent-invocations" / f"{ident}.json"
        source = run / "omp-observations" / f"{digest(payload)}.json"
        if source.is_file() and path.is_file():
            return read_json(path)
        sequence = read_json(path).get("native_sequence", 0) + 1 if path.is_file() else 1
        event = payload.get("type")
        request_digest = digest(inp)
        if event == "tool_call":
            if state.get("status") in ("needs_human", "archived", "delivery_ready", "delivery_ready_with_exceptions", "awaiting_user_input"):
                raise GateError("OMP role dispatch is stopped by Gate state")
            role = ROLE_ALIASES[inp["agent"]]
            if ((role == "intent_analyst" and state["current_stage"] != "intent")
                    or (role == "contract_planner" and state.get("status") != "intent_approved")
                    or (role == "verifier" and state["current_stage"] != "verify")):
                raise GateError("Critical role is not valid at this Gate checkpoint")
            if inp["agent"] == "aidevflow-independent-verifier":
                bundle = state.get("verifier_bundle") or {}
                assignment = inp.get("shared_context", "") + "\n" + inp["task"]
                if not bundle.get("sha256") or bundle["sha256"] not in assignment or bundle["path"] not in assignment:
                    raise GateError("Verifier dispatch must include the exact prepared bundle path and SHA-256")
            receipt = {"id": ident, "receipt_schema": SCHEMA, "runtime": "omp",
                       "task_id": state["task_id"], "session_id": payload["session_id"],
                       "tool_use_id": payload["toolCallId"], "agent_type": inp["agent"],
                       "role": ROLE_ALIASES[inp["agent"]], "context": execution_context(state),
                       "evidence_epoch": state.get("evidence_epoch", 0),
                       "bundle_sha256": (state.get("verifier_bundle") or {}).get("sha256"),
                       "input_digest": request_digest, "dispatch_observed": True,
                       "execution_finished": False, "status": "dispatched", "started_at": utc_now()}
            if path.is_file():
                prior = read_json(path)
                if prior["input_digest"] != request_digest or prior["context"] != receipt["context"]:
                    prior.update(status="identity_conflict", conflict="dispatch_identity_conflict")
                    write_json_atomic(path, prior)
                    raise GateError("OMP native call identity was reused with different inputs")
                return prior
        else:
            if not path.is_file():
                raise GateError("OMP result has no observed dispatch; run recover-runtime")
            receipt = read_json(path)
            if receipt["input_digest"] != request_digest:
                receipt.update(status="identity_conflict", conflict="dispatch_input_changed")
            elif receipt["context"] != execution_context(state):
                receipt["status"] = "stale"
            elif receipt.get("conflict"):
                return receipt
            else:
                details = payload.get("details") or {}
                results = details.get("results", []) if isinstance(details, dict) else []
                if not isinstance(results, list):
                    raise GateError("Invalid native OMP task results")
                if len(results) > 1:
                    raise GateError("Single critical task returned multiple native results")
                if not results:
                    # A detached launch is not completion, even if content looks like JSON.
                    if payload.get("isError"):
                        receipt.update(status="execution_failed", execution_finished=True)
                else:
                    result = results[0]
                    if (not isinstance(result, dict) or result.get("agent") != inp["agent"]
                            or not isinstance(result.get("id"), str) or not result["id"].strip() or len(result["id"]) > 256):
                        raise GateError("OMP result role/identity does not match its dispatch")
                    if receipt.get("agent_id") not in (None, result["id"]):
                        receipt.update(status="identity_conflict", conflict="provider_identity_changed")
                    else:
                        receipt.update(agent_id=result["id"], execution_finished=True, finished_at=utc_now(),
                                       resolved_model=result.get("resolvedModel"))
                        owner_path = run / "omp-agent-identities" / f"{digest([payload['session_id'], result['id']])}.json"
                        if owner_path.is_file() and read_json(owner_path)["invocation_id"] != ident:
                            receipt.update(status="identity_conflict", conflict="provider_identity_reused")
                            receipt["native_sequence"] = sequence
                            write_json_atomic(source, {"payload": payload, "dispatch_context": receipt["context"], "receipt": receipt})
                            write_json_atomic(path, receipt)
                            return receipt
                        write_json_atomic(owner_path, {"invocation_id": ident})
                        result_digest = digest({key: result.get(key) for key in ("id", "agent", "exitCode", "aborted", "error", "structuredOutput", "output")})
                        changed_result = receipt.get("native_result_digest") not in (None, result_digest)
                        receipt["native_result_digest"] = receipt.get("native_result_digest", result_digest)
                        if result.get("aborted") or result.get("error") or type(result.get("exitCode")) is not int or result.get("exitCode") != 0 or payload.get("isError"):
                            receipt.update(status="execution_failed", termination_reason="cancelled" if result.get("aborted") else "failed")
                        elif receipt.get("termination_reason") in ("cancelled", "failed"):
                            pass  # A late success cannot erase a terminal failure.
                        elif changed_result:
                            receipt.update(status="identity_conflict", conflict="conflicting_native_results")
                        else:
                            structured = result.get("structuredOutput")
                            try:
                                if structured is not None:
                                    if not isinstance(structured, dict) or structured.get("status") != "valid":
                                        raise ValueError("Native structured output did not validate")
                                    value = structured.get("data")
                                else:
                                    value = parse_result(result.get("output", ""))
                                if not isinstance(value, dict):
                                    raise ValueError("Role result must be one JSON object")
                            except (ValueError, TypeError, AttributeError):
                                receipt.update(status="invalid_output", output_error="invalid_json_object")
                            else:
                                sha = digest(value)
                                if receipt.get("result_sha256") not in (None, sha):
                                    receipt.update(status="identity_conflict", conflict="conflicting_outputs")
                                else:
                                    output = run / "agent-results" / f"{ident}.json"
                                    write_json_atomic(output, value)
                                    receipt.update(status="completed", result_path=output.relative_to(root).as_posix(), result_sha256=sha)
        receipt["native_sequence"] = sequence
        if not source.is_file():
            write_json_atomic(source, {"payload": payload, "dispatch_context": receipt["context"], "receipt": receipt})
        write_json_atomic(path, receipt)
        return receipt


def recover(root: Path, session: str | None = None) -> dict[str, Any]:
    """Replay only adapter-owned native observations, never reinterpret old inputs.

    The dispatch anchor is persisted before execution. OMP result callbacks also
    persist their exact native payload before acknowledgement. Unknown/missing
    native results stay pending rather than manufacturing completion.
    """
    with observer_lock(root / ".aidevflow"):
        return _recover(root, session)


def _recover(root: Path, session: str | None) -> dict[str, Any]:
    from .gate import Gate, GateError, read_json
    _, run, _ = Gate(root)._load_active()
    recovered = 0
    warnings = []
    latest: dict[str, dict[str, Any]] = {}
    for path in (run / "omp-observations").glob("*.json"):
        record = read_json(path)
        payload = record["payload"]
        if path.stem != digest(payload):
            raise GateError("OMP observation source digest changed; preserve the run for diagnosis")
        if session and payload.get("session_id") != session:
            continue
        ident, inp = _request(payload)
        snapshot = record["receipt"]
        if snapshot.get("id") != ident or snapshot.get("input_digest") != digest(inp):
            raise GateError("OMP recovery snapshot does not match its native input")
        if (ident in latest and snapshot.get("native_sequence", 0) == latest[ident].get("native_sequence", 0)
                and snapshot != latest[ident]):
            raise GateError("Conflicting OMP recovery snapshots at the same sequence")
        if ident not in latest or snapshot.get("native_sequence", 0) > latest[ident].get("native_sequence", 0):
            latest[ident] = snapshot
    for ident, snapshot in latest.items():
        target = run / "agent-invocations" / f"{ident}.json"
        before = target.read_bytes() if target.is_file() else None
        from .gate import write_json_atomic
        if not target.is_file() or read_json(target).get("native_sequence", 0) < snapshot.get("native_sequence", 0):
            write_json_atomic(target, snapshot)
        if target.is_file() and target.read_bytes() != before:
            recovered += 1
    return {"runtime": "omp", "recovered_events": recovered, "warnings": warnings}


def inspect_session(root: Path, session: str) -> dict[str, Any]:
    from .gate import Gate, read_json
    _, run, _ = Gate(root)._load_active()
    events = [read_json(path)["payload"] for path in (run / "omp-observations").glob("*.json")]
    return {"session_id": session, "runtime": "omp", "events": [event for event in events if event.get("session_id") == session],
            "warnings": ["Only adapter-captured native observations are inspected; missing native output is not inferred."]}


def recover_branch(root: Path, session: str, entries: list[dict[str, Any]]) -> int:
    """Recover missed result callbacks from the runtime's selected native branch.

    Existing durable dispatches are mandatory. No timestamp or prose matching.
    The caller is the extension's read-only SessionManager, not a model tool.
    """
    from .gate import Gate, read_json
    _, run, _ = Gate(root)._load_active()
    requests = {}
    recovered = 0
    for entry in entries:
        message = entry.get("message", {})
        if message.get("role") == "assistant":
            for block in message.get("content", []):
                if isinstance(block, dict) and block.get("type") == "toolCall" and block.get("name") == "task":
                    requests[block.get("id")] = block.get("arguments")
        elif message.get("role") == "toolResult" and message.get("toolName") == "task":
            call = message.get("toolCallId")
            inp = requests.get(call)
            if not isinstance(inp, dict):
                continue
            candidate = inp.get("tasks", [inp])
            if not isinstance(candidate, list) or len(candidate) != 1 or not isinstance(candidate[0], dict) or candidate[0].get("agent") not in ROLE_ALIASES:
                continue
            path = run / "agent-invocations" / f"{digest(['omp', session, call])[:32]}.json"
            if not path.is_file() or read_json(path).get("status") not in ("dispatched", "started"):
                continue
            observe(root, {"type": "tool_result", "session_id": session, "toolCallId": call,
                           "input": inp, "details": message.get("details"), "isError": message.get("isError", False)})
            recovered += 1
    return recovered
