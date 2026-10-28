from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .gate import GateError, sha256_file, write_json_atomic


def install(source_root: Path, target_root: Path) -> dict[str, Any]:
    source = source_root.resolve()
    target = target_root.resolve()
    if source == target:
        raise GateError("AIDevFlow cannot install over its framework source directory")
    git_root = __import__("subprocess").run(
        ["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True, check=False
    )
    if git_root.returncode or Path(git_root.stdout.strip()).resolve() != target:
        raise GateError("Install target must be the root of a Git repository")

    settings_path = target / ".claude" / "settings.json"
    source_settings = json.loads((source / ".claude" / "settings.json").read_text(encoding="utf-8"))
    try:
        target_settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except json.JSONDecodeError as exc:
        raise GateError(f"Existing Claude Code settings are not valid JSON: {settings_path}") from exc
    if not isinstance(target_settings, dict):
        raise GateError("Existing Claude Code settings must be an object")
    merge_settings(target_settings, source_settings)

    copies = [
        (source / "aidevflow", target / ".aidevflow" / "runtime" / "aidevflow"),
        (source / ".aidevflow" / "schemas", target / ".aidevflow" / "schemas"),
        (source / ".aidevflow" / "contract.example.json", target / ".aidevflow" / "contract.example.json"),
        (source / ".aidevflow" / "work-plan.example.json", target / ".aidevflow" / "work-plan.example.json"),
        (source / ".claude" / "agents" / "aidevflow-intent-analyst.md", target / ".claude" / "agents" / "aidevflow-intent-analyst.md"),
        (source / ".claude" / "agents" / "aidevflow-contract-planner.md", target / ".claude" / "agents" / "aidevflow-contract-planner.md"),
        (source / ".claude" / "agents" / "aidevflow-independent-verifier.md", target / ".claude" / "agents" / "aidevflow-independent-verifier.md"),
        (source / ".claude" / "skills" / "aidevflow-flow", target / ".claude" / "skills" / "aidevflow-flow"),
    ]
    missing = [str(src) for src, _ in copies if not src.exists()]
    if missing:
        raise GateError("AIDevFlow installation source is incomplete: " + ", ".join(missing))
    installed: list[str] = []
    for src, dest in copies:
        if not src.exists():
            raise GateError(f"AIDevFlow installation source is missing: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dest)
        installed.append(dest.relative_to(target).as_posix())

    scripts = target / "scripts"
    scripts.mkdir(exist_ok=True)
    wrappers = {
        "aidevflow.py": (
            "from pathlib import Path\nimport sys\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow' / 'runtime'))\n"
            "sys.path.insert(1, str(root))\n"
            "from aidevflow.cli import main\nraise SystemExit(main())\n"
        ),
        "aidevflow_hook.py": (
            "from pathlib import Path\nimport sys\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow' / 'runtime'))\n"
            "sys.path.insert(1, str(root))\n"
            "from aidevflow.hooks import main\nraise SystemExit(main())\n"
        ),
    }
    for name, content in wrappers.items():
        path = scripts / name
        path.write_text(content, encoding="utf-8", newline="\n")
        installed.append(path.relative_to(target).as_posix())

    gitignore_path = target / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
    ignore_block = (
        "# AIDevFlow local run data (keep generated state and user answers out of Git)\n"
        ".aidevflow/runs/\n"
        ".aidevflow/bundles/\n"
        ".aidevflow/maintenance/\n"
        ".aidevflow/active-run.json\n"
        ".aidevflow/events.jsonl\n"
        ".aidevflow/archive/\n"
        ".aidevflow/agent-observer.lock\n"
        ".aidevflow/runtime-sessions/\n"
        "# Keep the AIDevFlow Claude Code entrypoint and agents versioned even when .claude is ignored\n"
        "!.claude/\n"
        "!.claude/settings.json\n"
        "!.claude/agents/\n"
        "!.claude/agents/aidevflow-*.md\n"
        "!.claude/skills/\n"
        "!.claude/skills/aidevflow-flow/\n"
        "!.claude/skills/aidevflow-flow/**\n"
    )
    if "# AIDevFlow local run data" not in gitignore_text:
        separator = "" if not gitignore_text or gitignore_text.endswith(("\n", "\r")) else "\n"
        gitignore_path.write_text(gitignore_text + separator + ignore_block, encoding="utf-8", newline="\n")
    else:
        required_ignore_lines = ignore_block.splitlines()
        missing_ignore_lines = [line for line in required_ignore_lines if line and line not in gitignore_text]
        if missing_ignore_lines:
            separator = "" if not gitignore_text.endswith(("\n", "\r")) else ""
            gitignore_path.write_text(gitignore_text + separator + "\n" + "\n".join(missing_ignore_lines) + "\n", encoding="utf-8", newline="\n")
    installed.append(".gitignore")

    write_json_atomic(settings_path, target_settings)
    installed.append(settings_path.relative_to(target).as_posix())

    manifest_files = {}
    for rel in installed:
        path = target / rel
        if path.is_file():
            manifest_files[rel] = sha256_file(path)
        else:
            for child in path.rglob("*"):
                if child.is_file() and "__pycache__" not in child.parts and child.suffix not in (".pyc", ".pyo"):
                    manifest_files[child.relative_to(target).as_posix()] = sha256_file(child)
    from .diagnostics import OBSERVER_VERSION
    write_json_atomic(target / ".aidevflow" / "runtime-manifest.json", {"schema": "aidevflow/install-manifest-v1", "source": "local AIDevFlow framework", "reliability_version": 3, "observer_version": OBSERVER_VERSION, "files": manifest_files})
    return {"target": str(target), "installed": installed, "settings_path": settings_path.relative_to(target).as_posix()}


def merge_settings(target: dict[str, Any], source: dict[str, Any]) -> None:
    permissions = target.setdefault("permissions", {})
    # Remove only obsolete deny rules previously generated by AIDevFlow.
    # Edit(path) covers all file-editing tools; keep unrelated user rules.
    legacy_rules = {
        "Write(.aidevflow/**)",
        "MultiEdit(.aidevflow/**)",
        "Write(aidevflow/**)",
        "MultiEdit(aidevflow/**)",
        "Write(scripts/**)",
        "MultiEdit(scripts/**)",
        "Write(.claude/settings.json)",
        "Write(evals/acceptance/**)",
    }
    existing_deny = permissions.get("deny")
    if isinstance(existing_deny, list):
        permissions["deny"] = [rule for rule in existing_deny if rule not in legacy_rules]
    for key in ("deny", "ask", "allow"):
        if key in source.get("permissions", {}):
            entries = permissions.setdefault(key, [])
            for item in source["permissions"][key]:
                if item not in entries:
                    entries.append(item)
    hooks = target.setdefault("hooks", {})
    # Replace only exact commands generated by our installer, including old
    # matchers. A mixed group retains the user's hooks and all group metadata.
    owned = {f'python "${{CLAUDE_PROJECT_DIR}}/scripts/aidevflow_hook.py" {action}'
             for action in ("trace-agent", "trace-method", "guard", "guard-bash", "resume", "stop")}
    for event, groups in list(hooks.items()):
        preserved = []
        for group in groups:
            remaining = [h for h in group.get("hooks", [])
                         if not (h.get("type") == "command" and h.get("command") in owned)]
            if remaining:
                preserved.append({**group, "hooks": remaining})
        hooks[event] = preserved
    for event, groups in source.get("hooks", {}).items():
        entries = hooks.setdefault(event, [])
        for group in groups:
            if group not in entries:
                entries.append(group)
