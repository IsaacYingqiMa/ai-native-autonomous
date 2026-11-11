"""Native tool normalization and scoped guard. Business decisions remain in Gate."""
from __future__ import annotations
import contextlib
import io
import re
from pathlib import Path
from typing import Any
from ....gate import Gate, GateError
from ....receipts import ROLE_ALIASES
from ....runtimes import selected

READ_TOOLS = {"read", "grep", "glob", "web_search", "ask", "todo", "wait", "recall", "reflect"}
LSP_READ = {"diagnostics", "definition", "references", "hover", "symbols", "implementation", "type_definition", "status", "capabilities"}


def preflight(root: Path, payload: dict[str, Any], runtime: str = "omp") -> dict[str, Any]:
    from .diagnostics import doctor
    report = doctor(root, runtime)
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


def recover_live_entries(root: Path, payload: dict[str, Any], runtime: str = "omp") -> int:
    from .observations import recover_branch
    return recover_branch(root, payload["session_id"], payload["entries"], runtime)


def advisory_stop(root: Path) -> dict[str, Any] | None:
    from ....continuation import decide
    result = decide(root, {})
    if result and result.get("decision") == "block":
        return {"continue": True, "additionalContext": result["reason"]}
    return None


def guard(root: Path, payload: dict[str, Any], runtime: str = "omp") -> dict[str, Any]:
    """Normalize native inputs before applying the shared Gate path policy."""
    from ....hooks import guard as guard_file, guard_bash, _relative_to_root
    tool, inp = payload.get("toolName"), payload.get("input")
    if not isinstance(inp, dict):
        raise GateError("Tool input must be an object")
    discovering = (root / ".aidevflow/active-discovery.json").is_file()
    active = (root / ".aidevflow/active-run.json").is_file() or discovering
    role = (payload.get("actor") or {}).get("name")
    if active and selected(root) != runtime:
        return {"block": True, "reason": "Active task belongs to another runtime; resume it there"}
    if role in ROLE_ALIASES:
        if tool not in {"read", "grep", "glob", "yield"}:
            return {"block": True, "reason": "AIDevFlow critical roles are read-only and may not delegate"}
        if role == "aidevflow-artifact-reviewer" and tool != "yield":
            from ....gate import read_json
            state, _, _ = Gate(root)._load_active()
            ref = state.get("artifact_review_target")
            request = read_json(root / ref["path"]) if ref else {}
            if request.get("kind") == "blind":
                allowed = {ref["path"], request["intent_path"], request["request_path"]}
                if request.get("requirements_ref"):
                    from ....discovery import verify_reference
                    manifest = verify_reference(root, request["requirements_ref"])
                    allowed.update(manifest["files"])
                    allowed.add(request["requirements_ref"]["path"])
                rel = _relative_to_root(inp.get("path", "."), root)
                if rel not in allowed and not (rel == "specs/MAP.md" or rel and rel.startswith("specs/modules/")):
                    return {"block": True, "reason": "Blind probe may read only approved requirements and module knowledge"}
        if role == "aidevflow-independent-verifier" and tool != "yield":
            state, _, _ = Gate(root)._load_active()
            bundle = (state.get("verifier_bundle") or {}).get("path")
            rel = _relative_to_root(inp.get("path", "."), root)
            if not bundle or rel is None or not (rel == bundle or rel.startswith(bundle + "/")):
                return {"block": True, "reason": "Verifier may read only its frozen evidence bundle"}
    if tool in READ_TOOLS or tool == "yield":
        return {"block": False}
    if discovering and tool not in {"bash", "task"}:
        return {"block": True, "reason": "Discovery permits read-only investigation and Gate-owned draft persistence"}
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
            reason = ("Managed review is serial in the coordinator; only registered read-only Gate roles may use task" if tool == "task"
                      else "Use scripts/aidevflow_browser.py through Gate run-tests and inspect its screenshots; native browser/computer/eval is not enabled"
                      if tool in {"eval", "computer", "browser"} or str(tool).startswith("xd://")
                      else f"{tool} has no verified mutation adapter; use scoped edit/write or Gate commands")
            return {"block": True, "reason": reason}
    return {"block": bool(code), "reason": stderr.getvalue().strip() if code else ""}

