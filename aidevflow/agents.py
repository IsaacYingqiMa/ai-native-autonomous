"""Claude observations -> durable role receipts; no business approvals here.

This establishes local workflow provenance, not hostile-user authentication.
No transcript scraping or synthetic Start events are used for recovery.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .locking import observer_lock
from .receipts import ROLE_ALIASES, execution_context

ROLES = set(ROLE_ALIASES)
RECEIPT_SCHEMA = "aidevflow/invocation-v2"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def parse_result(message: str) -> Any:
    text = message.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Subagent result must be a JSON object")
    return value


def _output(message: Any) -> dict[str, Any]:
    if not isinstance(message, str) or not message.strip():
        return {}
    try:
        return {"result": parse_result(message)}
    except ValueError as exc:
        error: dict[str, Any] = {"output_error": "invalid_json_object", "message_sha256": _digest(message)}
        if isinstance(exc, json.JSONDecodeError):
            error["output_error_location"] = {"line": exc.lineno, "column": exc.colno}
        return error


def _normalize(payload: dict[str, Any]) -> dict[str, Any] | None:
    event, tool = payload.get("hook_event_name"), payload.get("tool_name")
    lifecycle = event in ("SubagentStart", "SubagentStop")
    dispatch = tool == "Agent" and event in ("PreToolUse", "PostToolUse", "PostToolUseFailure")
    handback = tool == "SubagentHandback" and event == "PostToolUse"
    if not (lifecycle or dispatch or handback):
        return None
    inp = payload.get("tool_input")
    inp = inp if isinstance(inp, dict) else {}
    response = payload.get("tool_response")
    # Accept an explicitly serialized object, never extract IDs from prose.
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = None
    response = response if isinstance(response, dict) else {}
    role = inp.get("subagent_type") if dispatch else payload.get("agent_type")
    if dispatch and role not in ROLES:
        return None
    value: dict[str, Any] = {
        "kind": "dispatch" if dispatch else "handback" if handback else "start" if event == "SubagentStart" else "stop",
        "event": event, "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"), "agent_type": role,
    }
    if dispatch:
        if not isinstance(inp.get("prompt"), str) or not inp["prompt"].strip():
            raise ValueError("Role dispatch requires a nonempty prompt")
        # Missing/null/false defaults for optional inputs are equivalent. Some
        # platform versions expand defaults between pre and post observations.
        semantic = {"prompt": inp["prompt"], "subagent_type": role}
        for key in ("model", "name", "resume", "fork", "isolation", "run_in_background"):
            if inp.get(key) not in (None, False, ""):
                semantic[key] = inp[key]
        value.update(tool_use_id=payload.get("tool_use_id"),
                     input_digest=_digest(semantic),
                     display_name=inp.get("name"),
                     unsupported_request=bool(inp.get("resume") or inp.get("fork") or inp.get("isolation")))
        if event != "PreToolUse":
            value["agent_id"] = response.get("agentId") or response.get("agent_id")
            value["provider_status"] = ("cancelled" if payload.get("is_interrupt") else "failed") if event == "PostToolUseFailure" else response.get("status")
            value["execution_mode"] = "teammate" if response.get("status") == "teammate_spawned" or response.get("teammate_id") or response.get("team_name") else "subagent"
            value["resolved_model"] = response.get("resolvedModel")
            if response.get("status") == "completed":
                content = response.get("content")
                if isinstance(content, list):
                    message = "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
                else:
                    message = content
                value.update(_output(message))
            if response.get("isError") or response.get("error"):
                value["provider_status"] = "failed"
    elif handback:
        if response.get("isError") or response.get("error"):
            return None
        value.update(_output(inp.get("message")))
    elif event == "SubagentStop":
        value.update(_output(payload.get("last_assistant_message")))
    if not dispatch and role not in ROLES:
        value["unrecognized_type"] = True
    return value


def _receipt(identity: str, event: dict[str, Any], role: str, provenance: str) -> dict[str, Any]:
    context = event["context"]
    return {"id": identity, "receipt_schema": RECEIPT_SCHEMA, "runtime": "claude-code",
            "task_id": event["task_id"], "role": ROLE_ALIASES[role], "agent_type": role,
            "session_id": event["session_id"], "agent_id": None, "stage": context["stage"],
            "evidence_epoch": context["evidence_epoch"], "bundle_sha256": context["bundle_sha256"],
            "context": context, "provenance": provenance, "started_at": event["observed_at"],
            "dispatch_observed": provenance == "dispatch", "start_observed": False,
            "execution_finished": False, "sources": [], "status": "dispatched"}


def _reconcile(root: Path, run_dir: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold observations, so a late parent response can bridge early output."""
    from .gate import read_json, write_json_atomic
    calls: dict[tuple[str, str], dict[str, Any]] = {}
    agents: dict[tuple[str, str], dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []

    def diagnostic(code: str, event: dict[str, Any]) -> None:
        entry = {"code": code, "event_id": event["id"], "session_id": event.get("session_id"),
                 "agent_id": event.get("agent_id"), "tool_use_id": event.get("tool_use_id")}
        if entry not in diagnostics:
            diagnostics.append(entry)

    for event in events:
        if event["kind"] != "dispatch" or event["event"] != "PreToolUse":
            continue
        key = (event["session_id"], event["tool_use_id"])
        if key in calls:
            if calls[key]["input_digest"] != event["input_digest"] or calls[key]["context"] != event["context"]:
                calls[key]["conflict"] = "dispatch_identity_conflict"
            continue
        receipt = _receipt(_digest(["dispatch", *key])[:32], event, event["agent_type"], "dispatch")
        receipt.update(tool_use_id=event["tool_use_id"], input_digest=event["input_digest"],
                       display_name=event.get("display_name"), unsupported_mode=event["unsupported_request"])
        calls[key] = receipt

    # Provider IDs are associated by the parent tool response, never by role
    # name or "the latest running analyst".
    for event in events:
        if event["kind"] != "dispatch" or event["event"] == "PreToolUse":
            continue
        receipt = calls.get((event["session_id"], event["tool_use_id"]))
        if receipt is None:
            diagnostic("dispatch_pre_missing", event)
            continue
        if receipt["input_digest"] != event["input_digest"]:
            receipt["conflict"] = "dispatch_input_changed"
            continue
        if event.get("agent_id"):
            key = (event["session_id"], event["agent_id"])
            prior = agents.get(key)
            if (prior is not None and prior is not receipt) or (receipt["agent_id"] and receipt["agent_id"] != event["agent_id"]):
                receipt["conflict"] = "provider_identity_reused"
                if prior is not None:
                    prior["conflict"] = "provider_identity_reused"
                continue
            receipt["agent_id"] = event["agent_id"]
            agents[key] = receipt
        receipt["execution_mode"] = event["execution_mode"]
        receipt["resolved_model"] = event.get("resolved_model")
        receipt["unsupported_mode"] |= event["execution_mode"] == "teammate"
        if event.get("provider_status") not in ("completed", "async_launched", "teammate_spawned", "failed", "cancelled", "canceled"):
            diagnostic("unknown_provider_status", event)

    # An actual Start remains a supported legacy anchor. Orphan Stop/Handback
    # cannot create one. Repeated Start is idempotent.
    for event in events:
        if event["kind"] != "start" or event["agent_type"] not in ROLES:
            continue
        key = (event["session_id"], event["agent_id"])
        if key not in agents:
            if any(r["session_id"] == event["session_id"] and r["agent_type"] == event["agent_type"] for r in calls.values()):
                # Wait for the exact parent bridge; never bypass a pending
                # dispatch's input binding or unsupported-mode checks.
                continue
            receipt = _receipt(_digest(["lifecycle", *key])[:32], event, event["agent_type"], "lifecycle")
            receipt["agent_id"] = event["agent_id"]
            agents[key] = receipt

    receipts = {r["id"]: r for r in [*calls.values(), *agents.values()]}
    outputs: dict[str, list[tuple[int, dict[str, Any]]]] = {key: [] for key in receipts}
    for event in events:
        if event["kind"] == "dispatch":
            receipt = calls.get((event["session_id"], event["tool_use_id"]))
        else:
            receipt = agents.get((event["session_id"], event["agent_id"]))
        if receipt is None:
            diagnostic("unmatched_event", event)
            continue
        role = event.get("agent_type")
        if role and role != receipt["agent_type"] and role != receipt.get("display_name"):
            receipt["conflict"] = "role_identity_conflict"
            diagnostic("role_identity_conflict", event)
            continue
        receipt["sources"].append(event["id"])
        if event["kind"] == "start":
            receipt["start_observed"] = True
        status = event.get("provider_status")
        if event["kind"] == "stop" or status == "completed":
            receipt["execution_finished"] = True
            receipt["finished_at"] = event["observed_at"]
        if status in ("failed", "cancelled", "canceled"):
            receipt["execution_failed"] = status
        if "result" in event or "output_error" in event:
            priority = 3 if event["kind"] == "handback" else 2 if event["kind"] == "dispatch" else 1
            outputs[receipt["id"]].append((priority, event))

    directory = run_dir / "agent-invocations"
    for receipt in receipts.values():
        identity_path = run_dir / "runtime-observations" / f"{_digest(receipt['session_id'])}.json"
        if identity_path.is_file():
            receipt["runtime_identity"] = read_json(identity_path)
        choices = outputs[receipt["id"]]
        if choices:
            # Delivered report > foreground return > closing text.
            priority = max(p for p, _ in choices)
            preferred = [e for p, e in choices if p == priority]
            results = {_digest(e["result"]): e for e in preferred if "result" in e}
            if len(results) > 1 or (results and any("output_error" in e for e in preferred)):
                receipt["conflict"] = "conflicting_outputs"
            elif results:
                digest, event = next(iter(results.items()))
                output = run_dir / "agent-results" / f"{receipt['id']}.json"
                if not output.is_file() or read_json(output) != event["result"]:
                    write_json_atomic(output, event["result"])
                receipt.update(result_path=output.relative_to(root).as_posix(), result_sha256=digest,
                               output_source="SubagentHandback" if priority == 3 else "Agent.completed" if priority == 2 else "SubagentStop")
            else:
                receipt["output_error"] = "invalid_json_object"
                if "output_error_location" in preferred[-1]:
                    receipt["output_error_location"] = preferred[-1]["output_error_location"]
        if receipt.get("conflict"):
            receipt["status"] = "identity_conflict"
        elif receipt.get("unsupported_mode"):
            receipt["status"] = "unsupported_mode"
        elif receipt.get("execution_failed"):
            receipt["status"] = "execution_failed"
        elif receipt["execution_finished"]:
            receipt["status"] = "completed" if receipt.get("result_sha256") else "invalid_output" if receipt.get("output_error") else "missing_output"
        else:
            receipt["status"] = "output_received" if receipt.get("result_sha256") else "started" if receipt["start_observed"] or receipt.get("agent_id") else "dispatched"
        path = directory / f"{receipt['id']}.json"
        if not path.is_file() or read_json(path) != receipt:
            write_json_atomic(path, receipt)
    # Preserve every observation and legacy receipt. Only mark replaced v2
    # lifecycle projections; never delete or retrofit historical evidence.
    for path in directory.glob("*.json"):
        existing = read_json(path)
        if existing.get("receipt_schema") == RECEIPT_SCHEMA and existing.get("id") not in receipts:
            existing["status"] = "superseded"
            write_json_atomic(path, existing)
    summary = {"schema": "aidevflow/observer-status-v1", "observation_count": len(events),
               "diagnostics": diagnostics, "supported_modes": ["foreground_subagent", "background_subagent"],
               "recovery": "Use fresh ordinary role agents without name/resume/fork/isolation. Inspect pending observations; never fabricate receipts. Confirm unknown/unsupported executions have ended before redispatch."}
    write_json_atomic(run_dir / "agent-observer-status.json", summary)
    return summary


def capture_event(root: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    from .gate import Gate, read_json, write_json_atomic
    event = _normalize(payload)
    if event is None or not (root / ".aidevflow" / "active-run.json").is_file():
        return None
    session = event.get("session_id")
    if not isinstance(session, str) or not session:
        raise ValueError("Agent observation requires a nonempty session_id")
    identity_field = "tool_use_id" if event["kind"] == "dispatch" else "agent_id"
    if not isinstance(event.get(identity_field), str) or not event[identity_field]:
        raise ValueError(f"Agent observation requires a nonempty {identity_field}")
    with observer_lock(root / ".aidevflow"):
        gate = Gate(root)
        state, run_dir, _ = gate._load_active()
        if state.get("status") in ("needs_human", "archived", "delivery_ready"):
            return None
        if event["kind"] == "dispatch" and event["event"] == "PreToolUse":
            identity_path = run_dir / "runtime-observations" / f"{_digest(session)}.json"
            if not identity_path.is_file():
                from .diagnostics import runtime_identity
                write_json_atomic(identity_path, runtime_identity())
        directory = run_dir / "agent-observations"
        events = [read_json(p) for p in directory.glob("*.json")]
        if event.get("unrecognized_type"):
            known_ids = {e.get("agent_id") for e in events if e["kind"] == "dispatch" and e["session_id"] == session}
            named_request = any(e["kind"] == "dispatch" and e["session_id"] == session
                                and e.get("display_name") and e["display_name"] == event.get("agent_type") for e in events)
            if event["agent_id"] not in known_ids and not named_request:
                return None  # Never archive unrelated agent messages.
        event.update(schema="aidevflow/agent-observation-v1", task_id=state["task_id"], context=execution_context(state))
        identity = _digest(event)
        path = directory / f"{identity}.json"
        if not path.is_file():
            event.update(id=identity, sequence=max((e["sequence"] for e in events), default=0) + 1,
                         observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
            write_json_atomic(path, event)
            events.append(event)
            gate._event(state["task_id"], "agent_observation_received", observation_id=identity,
                        kind=event["kind"], provider_event=event["event"], agent_id=event.get("agent_id"))
        events.sort(key=lambda e: e["sequence"])
        _reconcile(root, run_dir, events)
        if event["kind"] == "start":
            context = f"AIDevFlow task {state['task_id']}, stage {state['current_stage']}. Return exactly one JSON object. Execution observations do not grant business acceptance."
            if event["agent_type"] == "aidevflow-independent-verifier" and state.get("verifier_bundle"):
                context += f" Read only {state['verifier_bundle']['path']}; bundle SHA-256 {state['verifier_bundle']['sha256']}."
            return {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": context}}
    return None
