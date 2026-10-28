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
    from .managed import enabled
    if enabled(contract):
        for name in ("zsspec-standard", "zsspec-change"):
            if not skill_path(root, name).is_file():
                errors.append(f"managed method support skill {name!r} is not installed")
    return errors


def observe_skill(root: Path, payload: dict[str, Any]) -> None:
    from .gate import Gate, sha256_file, utc_now, write_json_atomic
    if payload.get("hook_event_name") not in ("PreToolUse", "PostToolUse") or payload.get("tool_name") != "Skill":
        return
    # A piped diagnostic payload without native invocation identity is never
    # accepted into the business observation ledger.
    if not all(isinstance(payload.get(key), str) and payload[key].strip()
               for key in ("session_id", "tool_use_id", "transcript_path")):
        raise ValueError("Method observation lacks native session/tool/transcript identity; use doctor for read-only diagnostics")
    from .runtime import verify_skill_request
    if not verify_skill_request(root, payload):
        raise ValueError("Method observation has no matching native Skill request yet; no formal observation written")
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
    from .managed import enabled, package_manifest
    package = package_manifest(root, contract) if enabled(contract) else None
    for phase, names in contract.get("method", {}).get("stages", {}).items():
        expected_stage = "build" if phase == "review" else phase
        if name not in names or state["current_stage"] != expected_stage:
            continue
        path = skill_path(root, name)
        key = hashlib.sha256(f"{phase}:{name}".encode()).hexdigest()[:24]
        # Keep requests distinct from successful tool returns. Neither proves
        # downstream method compliance or business acceptance.
        suffix = "request" if payload["hook_event_name"] == "PreToolUse" else "loaded"
        write_json_atomic(run_dir / "method-observations" / f"{key}-{suffix}.json", {
            "phase": phase, "skill": name, "sha256": sha256_file(path),
            "runtime": "claude-code", "source": payload["hook_event_name"] + ":Skill",
            "tool_use_id": payload["tool_use_id"], "transcript_path": payload["transcript_path"],
            "session_id": payload.get("session_id"), "observed_at": utc_now(),
            "evidence_epoch": state.get("evidence_epoch", 0),
            **({"package_sha256": package["sha256"], "arguments": (payload.get("tool_input") or {}).get("args", "")} if package else {}),
        })


def method_status(root: Path, run_dir: Path, state: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    from .gate import read_json, sha256_file
    observed = [read_json(path) for path in (run_dir / "method-observations").glob("*.json")]
    from .managed import enabled, package_manifest
    package = package_manifest(root, contract) if enabled(contract) else None
    result = []
    for phase, names in contract.get("method", {}).get("stages", {}).items():
        for name in names:
            path = skill_path(root, name)
            digest = sha256_file(path) if path.is_file() else None
            matches = [item for item in observed if item.get("phase") == phase and item.get("skill") == name
                        and item.get("session_id") not in (None, "", "manual-probe")
                        and (state.get("execution_policy", {}).get("method_evidence") != "artifact_gated"
                             or (item.get("tool_use_id") and item.get("transcript_path")))
                        and digest is not None and item.get("sha256") == digest
                        and (package is None or item.get("package_sha256") == package["sha256"])
                        and (phase == "design" or item.get("evidence_epoch", 0) == state.get("evidence_epoch", 0))
                        ]
            found = any(item.get("source") == "PostToolUse:Skill" for item in matches)
            requested = any(item.get("source") == "PreToolUse:Skill" for item in matches)
            result.append({"phase": phase, "skill": name, "status": "observed" if found else "requested" if requested else "not_observed",
                           "required": bool(state.get("method_observations_required"))})
    return result
