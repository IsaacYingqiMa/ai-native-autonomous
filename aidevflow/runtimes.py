"""Runtime selection and immutable run bindings; legacy runs remain Claude runs."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

NAMES = ("omp", "claude-code")


def project_config(root: Path) -> dict[str, Any]:
    from .gate import read_json, GateError
    path = root / ".aidevflow/project.json"
    value = read_json(path) if path.is_file() else {}
    if not isinstance(value, dict) or value.get("runtime", "claude-code") not in NAMES:
        raise GateError("Invalid AIDevFlow project runtime configuration")
    for field in ("skills_root", "python"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise GateError(f"Invalid project configuration field: {field}")
    return value


def selected(root: Path, state: dict[str, Any] | None = None) -> str:
    if state is not None:
        bound = state.get("execution_binding", {})
        if not isinstance(bound, dict):
            raise ValueError("Invalid execution binding")
        name = bound.get("runtime", "claude-code")
    else:
        from .gate import read_json
        active = root / ".aidevflow/active-run.json"
        if active.is_file():
            from .gate import safe_id
            task = safe_id(read_json(active).get("task_id", ""))
            return selected(root, read_json(root / ".aidevflow/runs" / task / "run-state.json"))
        name = project_config(root).get("runtime", "claude-code")
    if name not in NAMES:
        raise ValueError(f"Unsupported execution runtime: {name}")
    return name


def binding(root: Path) -> dict[str, Any]:
    name = project_config(root).get("runtime", "claude-code")
    return {"schema": "aidevflow/execution-binding-v1", "runtime": name,
            "adapter_version": "omp-v1" if name == "omp" else "claude-observer-b2",
            "skills_root": project_config(root).get("skills_root", ".claude/skills")}


def skills_root(root: Path) -> Path:
    from .gate import read_json, safe_id
    active = root / ".aidevflow/active-run.json"
    value = None
    if active.is_file():
        task = safe_id(read_json(active).get("task_id", ""))
        state = read_json(root / ".aidevflow/runs" / task / "run-state.json")
        value = state.get("execution_binding", {}).get("skills_root", ".claude/skills")
    if value is None:
        value = project_config(root).get("skills_root", ".claude/skills")
    if not isinstance(value, str):
        raise ValueError("skills_root must be a project-relative path")
    path = (root / value).resolve()
    path.relative_to(root.resolve())
    if Path(value).is_absolute() or path == root.resolve():
        raise ValueError("skills_root must be a directory inside the project")
    return path


def recover(root: Path, session: str | None = None) -> dict[str, Any]:
    if selected(root) == "omp":
        from .omp import recover as recover_omp
        return recover_omp(root, session)
    from .runtime import recover as recover_claude
    return recover_claude(root, session)


def identity(name: str) -> dict[str, Any]:
    executable = shutil.which("omp" if name == "omp" else "claude")
    version, error = None, None
    if executable:
        try:
            process = subprocess.run([executable, "--version"], capture_output=True, timeout=4,
                                     encoding="utf-8", errors="replace", check=False)
            if process.returncode == 0:
                version = process.stdout.strip()[:120]
            else:
                error = "version_probe_failed"
        except (OSError, subprocess.TimeoutExpired):
            error = "version_probe_failed"
    return {"runtime": name, "executable": executable, "installed_version": version,
            "running_version": None, "version_probe_error": error,
            "adapter_version": "omp-v1" if name == "omp" else "claude-observer-b2"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
