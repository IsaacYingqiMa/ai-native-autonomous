"""Read-only preflight; installed version is not a running-session assertion."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

OBSERVER_VERSION = "claude-observer-b1"


def runtime_identity() -> dict[str, Any]:
    executable = shutil.which("claude")
    installed = None
    if executable:
        try:
            result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=3, check=False)
            if result.returncode == 0:
                installed = result.stdout.strip()[:120]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"runtime": "claude-code", "installed_version": installed, "executable": executable,
            "running_version": None, "running_version_source": "not_supplied_by_hook",
            "observer_version": OBSERVER_VERSION, "python": sys.executable}


def doctor(root: Path) -> dict[str, Any]:
    from .gate import read_json, sha256_file
    root = root.resolve()
    settings_path = root / ".claude" / "settings.json"
    settings = read_json(settings_path) if settings_path.is_file() else {}
    expected = [("PreToolUse", "Agent"), ("PostToolUse", "Agent"),
                ("PostToolUse", "SubagentHandback"), ("PostToolUseFailure", "Agent"),
                ("SubagentStart", "*"), ("SubagentStop", "*")]
    hooks = settings.get("hooks", {})
    missing = [f"{event}:{matcher}" for event, matcher in expected
               if not any(group.get("matcher") == matcher and any("aidevflow_hook.py" in h.get("command", "") and "trace-agent" in h.get("command", "") for h in group.get("hooks", [])) for group in hooks.get(event, []))]
    teams = None
    settings_errors = []
    config_home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for path in (config_home / "settings.json", settings_path, root / ".claude" / "settings.local.json"):
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get("env", {}).get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")
                if value is not None:
                    teams = str(value)
            except (OSError, ValueError, AttributeError):
                settings_errors.append(str(path))
    teams = os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", teams)
    manifest = root / ".aidevflow" / "runtime-manifest.json"
    mismatch = []
    if manifest.is_file():
        for rel, digest in read_json(manifest).get("files", {}).items():
            path = (root / rel).resolve()
            path.relative_to(root)
            if not path.is_file() or sha256_file(path) != digest:
                mismatch.append(rel)
    roles = ["aidevflow-intent-analyst", "aidevflow-contract-planner", "aidevflow-independent-verifier"]
    missing_roles = [role for role in roles if not (root / ".claude" / "agents" / f"{role}.md").is_file()]
    active = root / ".aidevflow" / "active-run.json"
    run = None
    if active.is_file():
        from .gate import Gate
        from .receipts import execution_context, read_invocations
        state, run_dir, _ = Gate(root)._load_active()
        entries = read_invocations(run_dir)
        run = {"task_id": state["task_id"], "stage": state["current_stage"], "status": state["status"],
               "receipt_statuses": dict(Counter(e.get("status") for e in entries)),
               "stale_v2_receipts": sum(e.get("receipt_schema") == "aidevflow/invocation-v2" and e.get("context") != execution_context(state) for e in entries)}
        summary = run_dir / "agent-observer-status.json"
        if summary.is_file():
            run["observer"] = read_json(summary)
    warnings = ["Installed CLI version may differ from an already running session; restart after installation.",
                "No live role dispatch is performed by doctor. Hook presence does not establish runtime compatibility."]
    if teams == "1":
        warnings.append("Teams is enabled. Critical roles must be fresh ordinary subagents without name/resume/fork/isolation; teammate results are not accepted by this adapter.")
    return {"schema": "aidevflow/doctor-v1", "runtime": runtime_identity(),
            "ok": not (missing or missing_roles or mismatch or settings_errors),
            "missing_hooks": missing, "missing_roles": missing_roles, "manifest_mismatches": mismatch,
            "settings_errors": settings_errors, "teams_setting": teams,
            "zsspec_skill_count": len(list((root / ".claude" / "skills").glob("zsspec-*/SKILL.md"))),
            "active_run": run, "warnings": warnings}
