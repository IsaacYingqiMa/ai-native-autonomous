from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from .gate import Gate, GateError, read_json, write_json_atomic


PROTECTED_PREFIXES = (
    ".aidevflow/", "aidevflow/", "scripts/", ".claude/settings.json",
    ".claude/agents/aidevflow-", ".claude/skills/aidevflow-", "evals/acceptance/",
)


def _relative_to_root(raw: str, root: Path) -> str | None:
    candidate = Path(raw)
    if not candidate.is_absolute() and not (len(raw) > 2 and raw[1] == ":"):
        candidate = root / candidate
    try:
        return candidate.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        # Claude Code on Windows may provide /c/path while Python runs on Windows.
        win = PureWindowsPath(raw)
        if win.is_absolute() and root.drive and win.drive.casefold() == root.drive.casefold():
            try:
                return Path(str(win)).resolve(strict=False).relative_to(root.resolve()).as_posix()
            except (ValueError, OSError):
                return None
        return None


def _event(root: Path, event: str, **fields: Any) -> None:
    path = root / ".aidevflow" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False) + "\n")


def _block(message: str, root: Path, rel: str, rule: str, payload: dict[str, Any]) -> int:
    try:
        _event(root, "hook_block", rule=rule, path=rel, tool=payload.get("tool_name"), agent_type=payload.get("agent_type"), session_id=payload.get("session_id"))
    except OSError:
        pass
    print(message, file=sys.stderr)
    return 2


def guard(payload: dict[str, Any], root: Path) -> int:
    tool_input = payload.get("tool_input") or {}
    if payload.get("tool_name") in ("Bash", "PowerShell"):
        return guard_bash(payload, root, str(tool_input.get("command", "")))
    candidates = [tool_input.get("file_path"), tool_input.get("notebook_path")]
    candidates.extend(edit.get("file_path") for edit in tool_input.get("edits", []) if isinstance(edit, dict))
    for raw in filter(lambda value: isinstance(value, str) and value, candidates):
        rel = _relative_to_root(raw, root)
        if rel is None:
            continue
        if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            return _block(f"AIDevFlow protects {rel}; change it through the AIDevFlow maintainer workflow.", root, rel, "protected-path", payload)
    active_path = root / ".aidevflow" / "active-run.json"
    if not active_path.is_file():
        return 0
    try:
        gate = Gate(root)
        state, _, contract = gate._load_active()
    except (GateError, KeyError, OSError, ValueError) as exc:
        return _block(f"AIDevFlow state cannot be trusted ({exc}); stop and repair the run state.", root, ".aidevflow/active-run.json", "invalid-state", payload)
    if state.get("status") in ("needs_human", "archived", "delivery_ready"):
        return _block(f"AIDevFlow run is {state['status']}; product writes are stopped. Explain the blocker and await an explicit resume-run instruction.", root, "", "run-stopped", payload)
    frozen = set(state.get("frozen_tests", {}))
    for raw in filter(lambda value: isinstance(value, str) and value, candidates):
        rel = _relative_to_root(raw, root)
        if rel is None:
            continue
        if rel in frozen:
            return _block(f"AIDevFlow froze {rel}; route an integrity issue or update the test contract with human approval.", root, rel, "frozen-test", payload)
        if rel == f"specs/aidevflow-intake/{state['task_id']}/work-plan.json" and state.get("current_stage") == "design":
            continue
        if contract is None:
            if rel.startswith("specs/aidevflow-intake/") and state.get("current_stage") == "intent":
                continue
            return _block("Product and project files are read-only while Intent is being resolved; only generated drafts under specs/aidevflow-intake/ may be written.", root, rel, "intake-read-only", payload)
        from .managed import enabled, artifact_path
        if enabled(contract) and artifact_path(contract, rel):
            if state.get("current_stage") in ("design", "build", "verify"):
                continue  # Reports/screenshots only; cannot overlap product/spec/test paths.
        matches_code = any(_matches(rel, pattern) for pattern in contract.get("code_paths", []))
        is_design_or_test = rel in set(contract.get("design_artifacts", [])) | set(contract.get("test_files", [])) | set(contract.get("test_support_files", []))
        if not matches_code and not is_design_or_test:
            return _block(f"AIDevFlow blocked an out-of-scope change to {rel}; the pinned Contract does not declare this path.", root, rel, "out-of-scope-path", payload)
        if is_design_or_test:
            if state.get("current_stage") != "design":
                return _block(f"Specification content can only change during Design. For TASK/CHK status use work-item commands, run-tests, record-check or sync-projections; Gate writes mapped status cells. Current stage: {state.get('current_stage')}.", root, rel, "design-artifact-offstage", payload)
            continue
        if matches_code:
            if state.get("current_stage") not in ("build",):
                return _block(f"Implementation changes are blocked during {state.get('current_stage')}; pass the Design gate first.", root, rel, "code-offstage", payload)
            if not state.get("frozen_tests"):
                return _block("Implementation changes are blocked until required tests are frozen.", root, rel, "tests-not-frozen", payload)
            required = set(contract.get("red_test_files", contract.get("test_files", [])))
            red = {item.get("target") for item in state.get("test_runs", []) if item.get("suite") == "red" and item.get("valid_red")}
            if required - red:
                return _block("Implementation changes are blocked until every required test has valid Red evidence.", root, rel, "red-evidence-missing", payload)
    return 0


def guard_bash(payload: dict[str, Any], root: Path, command: str) -> int:
    # The CLI itself is the supported state writer; shell parsing is deliberately a
    # best-effort guard. Gate hashes and transition checks remain authoritative.
    normalized = command.strip().replace("\\", "/").lower()
    # Descriptor duplication and null sinks do not write repository files.
    scanned = re.sub(r"(?:\d|\*)?>&(?:\d|-)\b", "", normalized)
    scanned = re.sub(r"(?:\d|\*)?>>?\s*(?:/dev/null|\$null|nul)(?=\s|$|[|;&])", "", scanned)
    supported_gate_call = scanned.startswith(("python scripts/aidevflow.py ", "python3 scripts/aidevflow.py ", "py scripts/aidevflow.py "))
    if supported_gate_call and not any(token in scanned for token in ("&", ";", "|", ">", "`", "$(")):
        return 0
    redirect_targets = [match.group(1).strip("\"'") for match in re.finditer(r">{1,2}\s*([^\s|;&]+)", scanned)]
    write_markers = ("set-content", "out-file", "add-content", "remove-item", "copy-item", "move-item", "tee-object", "sed -i", "git checkout", "git restore", "git reset --hard", ".write_text(", ".write_bytes(", "writefile", "unlink(")
    has_mutation = any(marker in scanned for marker in write_markers)
    if redirect_targets or has_mutation:
        # For redirection, inspect its destination rather than read-only argv.
        destinations = scanned if has_mutation else " ".join(redirect_targets)
        for prefix in (".aidevflow/", "aidevflow/", "scripts/", ".claude/settings.json", "evals/acceptance/"):
            if prefix in destinations:
                return _block(f"AIDevFlow blocked a shell command that may modify protected path {prefix}.", root, prefix, "protected-shell-write", payload)
        try:
            state, _, _ = Gate(root)._load_active()
        except GateError:
            state = None
        if state:
            if state.get("status") in ("needs_human", "archived", "delivery_ready"):
                return _block(f"AIDevFlow run is {state['status']}; shell writes are stopped.", root, "", "run-stopped", payload)
            for test_path in state.get("frozen_tests", {}):
                if test_path.lower() in destinations:
                    return _block(f"AIDevFlow blocked a shell command that may modify frozen test {test_path}.", root, test_path, "frozen-test-shell-write", payload)
    return 0


def _matches(path: str, pattern: str) -> bool:
    import fnmatch
    normalized = pattern.replace("\\", "/")
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, normalized)


def resume(root: Path, payload: dict[str, Any] | None = None) -> int:
    if payload:
        from .runtime import remember_session
        remember_session(root, payload)
    active = root / ".aidevflow" / "active-run.json"
    if not active.is_file():
        return 0
    try:
        state, _, _ = Gate(root)._load_active()
    except (GateError, KeyError, OSError, ValueError) as exc:
        context = f"AIDevFlow could not restore state: {exc}. Do not change code until the state is repaired."
    else:
        context = (f"AIDevFlow active task {state['task_id']} | stage {state['current_stage']} | status {state['status']}. "
                   "Run `python scripts/aidevflow.py status` and follow the Gate's next-action requirements; do not infer progress from conversation history.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}, ensure_ascii=False))
    return 0


def stop_check(root: Path, payload: dict[str, Any] | None = None) -> int:
    active = root / ".aidevflow" / "active-run.json"
    if not active.is_file():
        return 0
    try:
        state, _, _ = Gate(root)._load_active()
    except (GateError, KeyError, OSError, ValueError) as exc:
        print(f"AIDevFlow state is invalid: {exc}.", file=sys.stderr)
        return 0  # A turn ending is not a business transition; Gate still rejects invalid state.
    if state.get("current_stage") == "verify" and state.get("status") not in ("accepted", "needs_human"):
        print("AIDevFlow verification is incomplete; this turn ending does not accept the task. Resume from Gate status. Only a recorded failure or unresolved decision may escalate the task.", file=sys.stderr)
    from .continuation import decide
    try:
        result = decide(root, payload or {})
    except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"AIDevFlow continuation unavailable: {exc}; Gate state is unchanged.", file=sys.stderr)
        return 0
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    action = sys.argv[1] if len(sys.argv) > 1 else "guard"
    observational = action in ("trace-agent", "trace-method", "stop")
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    if action not in ("guard", "guard-bash", "stop", "trace-agent", "trace-method", "resume"):
        print(f"Unknown hook action: {action}", file=sys.stderr)
        return 2
    try:
        raw = sys.stdin.buffer.read()
    except (AttributeError, OSError) as exc:
        return _input_error(root, b"", "stdin_read_error", str(exc), block=not observational)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return _input_error(root, raw, "utf8_decode_error", f"byte_offset={exc.start}", block=not observational)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return _input_error(root, raw, "json_decode_error", f"line={exc.lineno} column={exc.colno} position={exc.pos}", block=not observational)
    if not isinstance(payload, dict):
        return _input_error(root, raw, "invalid_payload_shape", "top_level_must_be_object", block=not observational)
    if action == "resume":
        return resume(root, payload)
    if action == "stop":
        return stop_check(root, payload)
    if action == "trace-method":
        from .methods import observe_skill
        try:
            observe_skill(root, payload)
        except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
            try:
                _event(root, "method_observer_error", error_type=type(exc).__name__, detail=str(exc))
            except OSError:
                pass
            print(f"AIDevFlow method observation could not be captured: {exc}", file=sys.stderr)
        return 0  # Observation never blocks the tool; new-run gates check it.
    if action == "trace-agent":
        from .agents import capture_event
        try:
            result = capture_event(root, payload)
        except (GateError, OSError, ValueError, KeyError, TypeError) as exc:
            try:
                _event(root, "agent_observer_error", error_type=type(exc).__name__, detail=str(exc))
            except OSError:
                pass
            print(f"AIDevFlow agent receipt could not be captured: {exc}", file=sys.stderr)
            return 0  # Gate rejects missing receipts; never trap an agent in Stop.
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    if not isinstance(payload.get("tool_name"), str) or not isinstance(payload.get("tool_input"), dict):
        return _input_error(root, raw, "invalid_payload_shape", "tool_name_and_tool_input_are_required")
    tool_name = payload["tool_name"]
    tool_input = payload["tool_input"]
    if tool_name not in ("Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        return _input_error(root, raw, "invalid_payload_shape", "unsupported_tool_name")
    if tool_name in ("Bash", "PowerShell") and not isinstance(tool_input.get("command"), str):
        return _input_error(root, raw, "invalid_payload_shape", "shell_tool_requires_string_command")
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        has_path = any(isinstance(tool_input.get(key), str) and tool_input[key] for key in ("file_path", "notebook_path"))
        edits = tool_input.get("edits", [])
        has_edits = isinstance(edits, list) and bool(edits) and all(isinstance(edit, dict) and isinstance(edit.get("file_path"), str) and edit["file_path"] for edit in edits)
        if not has_path and not has_edits:
            return _input_error(root, raw, "invalid_payload_shape", "file_tool_requires_a_valid_path")
    return guard(payload, root)


def _input_error(root: Path, raw: bytes, error_type: str, detail: str, block: bool = True) -> int:
    """Record safe diagnostics without persisting Claude's potentially sensitive payload."""
    try:
        _event(
            root,
            "hook_input_error",
            error_type=error_type,
            detail=detail,
            input_bytes=len(raw),
            input_sha256=hashlib.sha256(raw).hexdigest(),
            python=sys.executable,
            stdin_encoding=getattr(sys.stdin, "encoding", None),
            action_blocked=block,
        )
    except OSError:
        pass
    print(
        f"AIDevFlow hook input rejected ({error_type}; {detail}; bytes={len(raw)}). "
        + ("The tool call was blocked; " if block else "Observation failed; Gate still requires execution evidence; ")
        + "inspect .aidevflow/events.jsonl for safe diagnostics.",
        file=sys.stderr,
    )
    return 2 if block else 0


if __name__ == "__main__":
    raise SystemExit(main())
