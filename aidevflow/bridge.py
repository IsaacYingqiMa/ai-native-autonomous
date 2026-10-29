"""Bounded JSONL transport for the OMP extension. No model or network calls."""
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
READ_TOOLS = {"read", "grep", "glob", "web_search", "ask", "todo", "wait", "recall", "reflect"}
LSP_READ = {"diagnostics", "definition", "references", "hover", "symbols", "implementation", "type_definition", "status", "capabilities"}


def guard(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize OMP inputs before applying the shared Gate path policy."""
    from .hooks import guard as guard_file, guard_bash, _relative_to_root
    tool, inp = payload.get("toolName"), payload.get("input")
    if not isinstance(inp, dict):
        raise GateError("Tool input must be an object")
    active = (root / ".aidevflow/active-run.json").is_file()
    role = (payload.get("actor") or {}).get("name")
    if active and selected(root) != "omp":
        return {"block": True, "reason": "Active task belongs to another runtime; resume it there"}
    if role in ROLE_ALIASES:
        if tool not in {"read", "grep", "glob", "yield"}:
            return {"block": True, "reason": "AIDevFlow critical roles are read-only and may not delegate"}
        if role == "aidevflow-independent-verifier" and tool != "yield":
            state, _, _ = Gate(root)._load_active()
            bundle = (state.get("verifier_bundle") or {}).get("path")
            rel = _relative_to_root(inp.get("path", "."), root)
            if not bundle or rel is None or not (rel == bundle or rel.startswith(bundle + "/")):
                return {"block": True, "reason": "Verifier may read only its frozen evidence bundle"}
    if tool in READ_TOOLS or tool == "yield":
        return {"block": False}
    if not active:
        # Framework protection applies even before a business task exists.
        if tool not in {"edit", "apply_patch", "write", "notebook", "bash"}:
            return {"block": False}
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        if tool == "bash":
            command = inp.get("command")
            if not isinstance(command, str):
                raise GateError("bash requires a command")
            code = guard_bash({"tool_name": "Bash", "tool_input": inp}, root, command)
        elif tool in {"edit", "apply_patch", "write", "notebook"}:
            paths = []
            raw = inp.get("input", inp.get("_input"))
            if isinstance(raw, str):
                # Inspect every native hashline section and every move destination.
                for line in raw.lstrip("\ufeff").splitlines():
                    if re.fullmatch(r"\[.+#[0-9A-Fa-f]{4}\]", line):
                        paths.append(re.sub(r"#[0-9A-Fa-f]{4}$", "", line[1:-1]).strip("\"'"))
                    elif line.startswith("MV "):
                        paths.append(line[3:].strip().strip("\"'"))
                    elif re.match(r"^\*\*\* (?:Add File|Update File|Delete File|Move to|Edit File): ", line):
                        paths.append(line.split(": ", 1)[1])
            else:
                path = inp.get("path") or inp.get("file_path") or inp.get("notebook_path")
                if isinstance(path, str):
                    paths.append(path)
                for edit in inp.get("edits", []):
                    if isinstance(edit, dict) and isinstance(edit.get("rename"), str):
                        paths.append(edit["rename"])
            if not paths or any(not path for path in paths):
                raise GateError("File tool requires verifiable target paths")
            code = 0
            for path in paths:
                if "://" in path:
                    return {"block": True, "reason": "Virtual write capabilities require a dedicated AIDevFlow policy"}
                if _relative_to_root(path, root) is None:
                    return {"block": True, "reason": "Writes outside the project are outside this task contract"}
                code = guard_file({"tool_name": "Write", "tool_input": {"file_path": path}}, root)
                if code:
                    break
        elif tool == "lsp" and (inp.get("action") in LSP_READ
                or (inp.get("action") == "code_actions" and inp.get("apply") is not True)
                or (inp.get("action") in ("rename", "rename_file") and inp.get("apply") is False)):
            return {"block": False}
        else:
            reason = ("Managed review is serial in the coordinator; only the three Gate roles may use task" if tool == "task"
                      else "Use scripts/aidevflow_browser.py through Gate run-tests and inspect its screenshots; native browser/computer/eval is not enabled"
                      if tool in {"eval", "computer", "browser"} or str(tool).startswith("xd://")
                      else f"{tool} has no verified mutation adapter; use scoped edit/write or Gate commands")
            return {"block": True, "reason": reason}
    return {"block": bool(code), "reason": stderr.getvalue().strip() if code else ""}


def dispatch(root: Path, action: str, payload: dict[str, Any]) -> Any:
    gate = Gate(root)
    if action == "ping":
        return {"protocol": 1, "runtime": selected(root), "python": sys.executable}
    if action == "status":
        if not gate.active_path.is_file():
            return {"active_run": None}
        from .method_resources import managed_context
        summary = gate.status(summary=True)
        return {**summary, "method_context": managed_context(summary["current_stage"])}
    if action == "preflight":
        from .diagnostics import doctor_omp
        report = doctor_omp(root)
        tools = payload.get("tools", [])
        commands = payload.get("skill_commands", [])
        if not isinstance(tools, list) or not isinstance(commands, list) or any(not isinstance(x, str) for x in tools + commands):
            raise GateError("Invalid native capability snapshot")
        report["native_session"] = {"tools": tools, "skill_commands": commands,
                                    "physical_source_binding": "enforced on managed method reads"}
        if not {"read", "bash", "task"}.issubset(tools):
            report["ok"] = False
            report["settings_errors"].append("Full delivery requires native read, bash and task in this session")
        if not report["readiness"]["initial_commit"]:
            report["ok"] = False
        return report
    if action == "guard":
        return guard(root, payload)
    if action == "role-context":
        from .contexts import role_context
        return role_context(root, payload.get("role"))
    if action == "method-load":
        from .method_resources import load_resource
        return load_resource(root, payload.get("input", {}))
    if action == "observe":
        from .omp import observe
        return observe(root, payload)
    if action == "recover":
        from .runtimes import recover
        if not gate.active_path.is_file():
            return {"recovered_events": 0}
        result = recover(root)
        if selected(root) == "omp" and payload.get("entries"):
            from .omp import recover_branch
            result["recovered_events"] += recover_branch(root, payload["session_id"], payload["entries"])
        return result
    if action == "stop":
        if not gate.active_path.is_file() or selected(root) != "omp":
            return None
        from .continuation import decide
        result = decide(root, {})
        # OMP advisory continuations have a native bound; never use an unlimited refusal loop.
        if result and result.get("decision") == "block":
            return {"continue": True, "additionalContext": result["reason"]}
        return None
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
            if (not leases and request.get("action") in ("guard", "observe")
                    and payload.get("actor", {}).get("kind") == "main"
                    and (root / ".aidevflow/active-run.json").is_file()):
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
