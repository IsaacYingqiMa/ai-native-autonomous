"""Observe delivery-method loading without editing the method's Skill assets."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def skill_path(root: Path, name: str) -> Path:
    directory = (root / ".claude" / "skills").resolve()
    path = (directory / name / "SKILL.md").resolve()
    path.relative_to(directory)
    return path


def available_skill_errors(root: Path, contract: dict[str, Any]) -> list[str]:
    method = contract.get("method")
    if not isinstance(method, dict) or not isinstance(method.get("stages"), dict):
        return []
    errors = []
    for stage, names in method["stages"].items():
        if not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            try:
                if not skill_path(root, name).is_file():
                    errors.append(f"method skill {name!r} for stage {stage!r} is not installed")
            except ValueError:
                errors.append(f"method skill {name!r} has an invalid path")
    return errors


def observe_skill(root: Path, payload: dict[str, Any]) -> None:
    from .gate import Gate, sha256_file, utc_now, write_json_atomic
    if payload.get("hook_event_name") != "PostToolUse" or payload.get("tool_name") != "Skill":
        return
    if not (root / ".aidevflow" / "active-run.json").is_file():
        return
    gate = Gate(root)
    state, run_dir, contract = gate._load_active()
    if not contract or state.get("status") in ("needs_human", "archived", "delivery_ready"):
        return
    name = (payload.get("tool_input") or {}).get("skill")
    if not isinstance(name, str):
        return
    response = payload.get("tool_response")
    if isinstance(response, dict) and (response.get("isError") or response.get("error")):
        return
    for phase, names in contract.get("method", {}).get("stages", {}).items():
        expected_stage = "build" if phase == "review" else phase
        if name not in names or state["current_stage"] != expected_stage:
            continue
        path = skill_path(root, name)
        key = hashlib.sha256(f"{phase}:{name}".encode()).hexdigest()[:24]
        write_json_atomic(run_dir / "method-observations" / f"{key}.json", {
            "phase": phase, "skill": name, "sha256": sha256_file(path),
            "runtime": "claude-code", "source": "PostToolUse:Skill",
            "session_id": payload.get("session_id"), "observed_at": utc_now(),
            "evidence_epoch": state.get("evidence_epoch", 0),
        })


def method_status(root: Path, run_dir: Path, state: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    from .gate import read_json, sha256_file
    observed = [read_json(path) for path in (run_dir / "method-observations").glob("*.json")]
    result = []
    for phase, names in contract.get("method", {}).get("stages", {}).items():
        for name in names:
            path = skill_path(root, name)
            digest = sha256_file(path) if path.is_file() else None
            found = any(item.get("phase") == phase and item.get("skill") == name
                        and digest is not None and item.get("sha256") == digest
                        and (phase == "design" or item.get("evidence_epoch", 0) == state.get("evidence_epoch", 0))
                        for item in observed)
            result.append({"phase": phase, "skill": name, "status": "observed" if found else "not_observed",
                           "required": bool(state.get("method_observations_required"))})
    return result
