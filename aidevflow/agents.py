"""Claude lifecycle receipts, kept separate from the coordinator's state writer.

These establish observed dispatch/output in the local harness, not a hostile-user
identity boundary. No receipt can be submitted through an ordinary Gate command.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path
from typing import Any

from .receipts import ROLE_ALIASES


ROLES = {"aidevflow-intent-analyst", "aidevflow-contract-planner", "aidevflow-independent-verifier"}


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


def capture_event(root: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    from .gate import Gate, GateError, read_json, sha256_bytes, write_json_atomic

    role = payload.get("agent_type")
    if not isinstance(role, str) or role not in ROLES or not (root / ".aidevflow" / "active-run.json").is_file():
        return None
    gate = Gate(root)
    state, run_dir, _ = gate._load_active()
    agent_id, session = payload.get("agent_id"), payload.get("session_id")
    if not isinstance(agent_id, str) or not agent_id or not isinstance(session, str) or not session:
        gate._event(state["task_id"], "agent_receipt_error", detail="Missing agent_id or session_id", agent_type=role)
        return None
    event = payload.get("hook_event_name")
    directory = run_dir / "agent-invocations"
    directory.mkdir(parents=True, exist_ok=True)
    if event == "SubagentStart":
        identity = sha256_bytes(f"{session}:{agent_id}".encode())[:20] + "-" + secrets.token_hex(4)
        receipt = {
            "id": identity, "agent_id": agent_id, "session_id": session,
            "agent_type": role, "stage": state["current_stage"],
            "role": ROLE_ALIASES[role], "runtime": "claude-code",
            "receipt_schema": "aidevflow/invocation-v1",
            "evidence_epoch": state.get("evidence_epoch", 0),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "status": "started", "bundle_sha256": None,
        }
        if role == "aidevflow-independent-verifier":
            bundle = state.get("verifier_bundle")
            if bundle:
                receipt["bundle_sha256"] = bundle["sha256"]
        write_json_atomic(directory / f"{identity}.json", receipt)
        gate._event(state["task_id"], "agent_started", **receipt)
        context = f"AIDevFlow task {state['task_id']}, stage {state['current_stage']}. Return exactly one JSON object; the harness records the returned object."
        if role == "aidevflow-independent-verifier" and state.get("verifier_bundle"):
            context += f" Read only {state['verifier_bundle']['path']}; bundle SHA-256 {state['verifier_bundle']['sha256']}."
        return {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": context}}
    candidates = []
    for path in directory.glob("*.json"):
        receipt = read_json(path)
        if receipt.get("agent_id") == agent_id and receipt.get("session_id") == session and receipt.get("agent_type") == role and receipt.get("status") in ("started", "output_received"):
            candidates.append((receipt["started_at"], path, receipt))
    if not candidates:
        gate._event(state["task_id"], "agent_receipt_error", detail="No matching active SubagentStart", agent_type=role, agent_id=agent_id)
        return None
    _, path, receipt = max(candidates, key=lambda entry: entry[0])
    message = payload.get("last_assistant_message", "")
    if event == "PostToolUse":
        message = payload.get("tool_input", {}).get("message", "")
    if isinstance(message, str) and message.strip() and not (event == "SubagentStop" and receipt.get("output_source") == "SubagentHandback"):
        try:
            result = parse_result(message)
        except (ValueError, json.JSONDecodeError) as exc:
            result = None
            receipt["output_error"] = "invalid_json_object"
            if isinstance(exc, json.JSONDecodeError):
                receipt["output_error_location"] = {"line": exc.lineno, "column": exc.colno}
        if result is not None:
            receipt.pop("output_error", None)
            receipt.pop("output_error_location", None)
            output = run_dir / "agent-results" / f"{receipt['id']}.json"
            write_json_atomic(output, result)
            receipt["result_path"] = output.relative_to(root).as_posix()
            receipt["result_sha256"] = sha256_bytes(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8"))
            receipt["output_source"] = "SubagentHandback" if event == "PostToolUse" else "SubagentStop"
            receipt["status"] = "output_received"
    if event == "SubagentStop":
        receipt["status"] = "completed" if receipt.get("result_sha256") else ("invalid_output" if receipt.get("output_error") else "missing_output")
        receipt["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        gate._event(state["task_id"], "agent_finished", **receipt)
    write_json_atomic(path, receipt)
    return None
