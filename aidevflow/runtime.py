"""Bounded Claude transcript adapter; no model calls and no free-text ID scraping.

Hooks are the fast path. This adapter can recover cancelled/missing Hook delivery
from native tool_use + structured toolUseResult records. It is local provenance,
not authentication against a user who can edit their own files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import datetime as dt
from pathlib import Path
from typing import Any

MAX_BYTES = 32 * 1024 * 1024
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").resolve()


def _path(session: str, raw: str | None = None, *, allow_missing: bool = False) -> Path | None:
    if not isinstance(session, str) or not SAFE_ID.fullmatch(session):
        return None
    projects = _home() / "projects"
    if raw:
        path = Path(raw).resolve()
        try:
            path.relative_to(projects)
        except ValueError:
            return None
        return path if path.name == session + ".jsonl" and (allow_missing or path.is_file()) else None
    paths = list(projects.glob("*/" + session + ".jsonl"))
    return _path(session, str(paths[0])) if len(paths) == 1 else None


def remember_session(root: Path, payload: dict[str, Any]) -> None:
    """Store an actual Hook's locator, never accept caller-supplied observations."""
    session = payload.get("session_id")
    path = _path(session, payload.get("transcript_path"), allow_missing=True)
    if path is None:
        return
    from .gate import write_json_atomic
    directory = root / ".aidevflow/runtime-sessions"
    target = directory / (hashlib.sha256(session.encode()).hexdigest() + ".json")
    value = {"session_id": session, "transcript_path": str(path), "root": str(root.resolve())}
    if not target.is_file() or json.loads(target.read_text(encoding="utf-8")) != value:
        write_json_atomic(target, value)


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Native transcript exceeds bounded recovery limit (32 MiB); use a fresh session, preserve the run")
    rows = []
    with path.open("rb") as stream:
        for raw in stream:
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeError):
                if not raw.endswith(b"\n"):
                    break  # Current writer may not have finished its last record.
                raise ValueError("Native transcript contains a corrupt complete record")
            if not isinstance(row, dict):
                continue
            if row.get("type") in ("assistant", "user", "attachment", "system"):
                row["_record_sha256"] = hashlib.sha256(raw).hexdigest()
                rows.append(row)
    return rows


def _blocks(row: dict[str, Any]) -> list[dict[str, Any]]:
    message = row.get("message")
    content = message.get("content", []) if isinstance(message, dict) else []
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _belongs(row: dict[str, Any], root: Path, session: str, agent: str | None = None) -> bool:
    if row.get("sessionId", row.get("session_id")) != session or not isinstance(row.get("cwd"), str):
        return False
    if any(row.get(key) not in (None, session) for key in ("sessionId", "session_id")):
        return False
    if Path(row["cwd"]).resolve() != root.resolve():
        return False
    return row.get("agentId") == agent if agent else row.get("isSidechain") is not True


def _source(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {"path": str(path), "uuid": row.get("uuid"), "timestamp": row.get("timestamp"),
            "record_sha256": row["_record_sha256"], "version": row.get("version")}


def verify_skill_request(root: Path, payload: dict[str, Any]) -> bool:
    path = _path(payload.get("session_id"), payload.get("transcript_path"))
    if path is None:
        return False
    wanted = payload.get("tool_input") or {}
    if not isinstance(wanted, dict):
        return False
    return any(row.get("type") == "assistant" and _belongs(row, root, payload["session_id"])
               and block.get("type") == "tool_use" and block.get("name") == "Skill"
               and block.get("id") == payload.get("tool_use_id")
               and isinstance(block.get("input"), dict)
               and block.get("input", {}).get("skill") == wanted.get("skill")
               and (block.get("input", {}).get("args") or "") == (wanted.get("args") or "")
               for row in _rows(path) for block in _blocks(row))


def _context_at(gate: Any, state: dict, timestamp: str) -> dict | None:
    from .receipts import execution_context
    if not timestamp or not gate.events_path.is_file() or gate.events_path.stat().st_size > MAX_BYTES:
        return None
    snapshots = []
    instant = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    for raw in gate.events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(raw)
        if (event.get("task_id") == state["task_id"] and isinstance(event.get("state_snapshot"), dict)
                and dt.datetime.fromisoformat(event["ts"].replace("Z", "+00:00")) <= instant):
            snapshots.append(event)
    if not snapshots:
        return None
    return execution_context(max(snapshots, key=lambda e: e["ts"])["state_snapshot"])


def inspect_session(root: Path, session: str) -> dict[str, Any]:
    """Read-only; parse only native records and expose compact diagnostics."""
    from .agents import _normalize
    from .gate import Gate
    from .receipts import execution_context, read_invocations
    path = _path(session)
    if path is None:
        return {"session_id": session, "available": False, "events": [], "warnings": ["Native session transcript not found in Claude config projects"]}
    gate = Gate(root)
    state, run_dir, _ = gate._load_active()
    current = execution_context(state)
    existing = read_invocations(run_dir)
    rows = [r for r in _rows(path) if _belongs(r, root, session)]
    calls: dict[str, dict] = {}
    result_rows: dict[str, list[dict]] = {}
    diagnostics: list[str] = []
    for row in rows:
        for block in _blocks(row):
            if row["type"] == "assistant" and block.get("type") == "tool_use" and block.get("name") == "Agent":
                calls[block.get("id", "")] = {"row": row, "input": block.get("input", {})}
            elif row["type"] == "user" and block.get("type") == "tool_result":
                if isinstance(row.get("toolUseResult"), dict) and not block.get("is_error"):
                    result_rows.setdefault(block.get("tool_use_id", ""), []).append(row)
    events = []
    if len(calls) > 64:
        diagnostics.append("Recovery inspects at most the latest 64 Agent calls; preserve old sessions for diagnosis")
    for call, item in list(calls.items())[-64:]:
        if not call:
            continue
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": session,
                   "tool_use_id": call, "tool_input": item["input"]}
        try:
            event = _normalize(payload)
        except ValueError:
            diagnostics.append("Native Agent request lacks required dispatch fields: " + call)
            continue
        if event is None:
            continue
        anchors = [r for r in existing if r.get("session_id") == session and r.get("tool_use_id") == call
                   and r.get("input_digest") == event["input_digest"]]
        context = anchors[0].get("context") if anchors else _context_at(gate, state, item["row"].get("timestamp", ""))
        if context != current:
            continue
        common = {"transport": "claude-transcript-v1", "context": context}
        event.update(common, event="Transcript:Agent.request", source_records=[_source(path, item["row"])])
        events.append(event)
        for returned in result_rows.get(call, []):
            response = returned["toolUseResult"]
            if isinstance(response.get("prompt"), str) and response["prompt"] != item["input"].get("prompt"):
                diagnostics.append("Native parent result prompt conflicts with dispatch: " + call)
                continue
            post = _normalize({**payload, "hook_event_name": "PostToolUse", "tool_response": response})
            if post is None:
                continue
            post.update(common, event="Transcript:Agent.result", source_records=[_source(path, returned)])
            events.append(post)
            agent = post.get("agent_id")
            if not isinstance(agent, str) or not SAFE_ID.fullmatch(agent):
                continue
            child = path.with_suffix("") / "subagents" / ("agent-" + agent + ".jsonl")
            if not child.is_file() or not child.resolve().is_relative_to(path.parent.resolve()):
                continue
            child_rows = [r for r in _rows(child) if _belongs(r, root, session, agent)]
            handbacks = {}
            for row in child_rows:
                for block in _blocks(row):
                    if row["type"] == "assistant" and block.get("type") == "tool_use" and block.get("name") == "SubagentHandback":
                        handbacks[block.get("id")] = (block.get("input", {}), row)
                    if row["type"] != "user" or block.get("type") != "tool_result" or block.get("is_error"):
                        continue
                    delivery = row.get("toolUseResult")
                    request = handbacks.get(block.get("tool_use_id"))
                    if not request or not isinstance(delivery, dict) or delivery.get("success") is not True:
                        continue
                    report = _normalize({"hook_event_name": "PostToolUse", "tool_name": "SubagentHandback",
                                         "session_id": session, "agent_id": agent, "agent_type": event["agent_type"],
                                         "tool_input": request[0], "tool_response": delivery})
                    if report:
                        report.update(common, event="Transcript:SubagentHandback.result",
                                      terminal_return=row.get("toolEndsTurn") is True,
                                      source_records=[_source(child, request[1]), _source(child, row)])
                        events.append(report)
            diagnostics.extend("Hook cancelled: " + str(r.get("attachment", {}).get("hookName")) for r in child_rows
                               if r.get("attachment", {}).get("type") == "hook_cancelled")
    return {"session_id": session, "available": True, "events": events,
            "running_versions": sorted({r["version"] for r in rows if isinstance(r.get("version"), str)}),
            "models": sorted({r["message"]["model"] for r in rows if isinstance(r.get("message"), dict)
                              and isinstance(r["message"].get("model"), str)}),
            "warnings": sorted(set(diagnostics))}


def recover(root: Path, session: str | None = None) -> dict[str, Any]:
    from .agents import capture_transcript_event
    from .gate import Gate
    from .receipts import read_invocations
    state, run_dir, _ = Gate(root)._load_active()
    if state.get("status") in ("needs_human", "archived", "delivery_ready"):
        return {"recovered_events": 0, "warnings": ["Run is stopped; no observation recovery performed"]}
    sessions = {session} if session else {r.get("session_id") for r in read_invocations(run_dir)}
    if session is None:
        for locator in (root / ".aidevflow/runtime-sessions").glob("*.json"):
            value = json.loads(locator.read_text(encoding="utf-8"))
            if value.get("root") == str(root.resolve()):
                sessions.add(value.get("session_id"))
    warnings = []
    before = len(list((run_dir / "agent-observations").glob("*.json")))
    for target in sorted(s for s in sessions if isinstance(s, str))[-8:]:
        report = inspect_session(root, target)
        for event in report["events"]:
            capture_transcript_event(root, event)
        warnings.extend(report["warnings"])
    after = len(list((run_dir / "agent-observations").glob("*.json")))
    return {"recovered_events": after - before, "warnings": sorted(set(warnings))}
