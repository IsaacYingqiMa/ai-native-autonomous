"""Read-only preflight; installed version is not a running-session assertion."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

OBSERVER_VERSION = "claude-observer-b2"


def runtime_identity() -> dict[str, Any]:
    executable = shutil.which("claude")
    installed = None
    probe = executable
    probe_error = None
    if executable:
        # npm's Windows cmd shim can outlive a timed-out wrapper via inherited
        # pipes. Prefer the actual binary used by the installed shim.
        if Path(executable).suffix.lower() in (".cmd", ".bat"):
            native = Path(executable).parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            probe = str(native) if native.is_file() else None
            if probe is None:
                probe_error = "native_version_probe_unavailable"
    if probe:
        try:
            # File-backed output avoids communicate() waiting forever on an
            # inherited stdout pipe even after the direct process was killed.
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen([probe, "--version"], stdout=output, stderr=subprocess.DEVNULL)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    probe_error = "version_probe_timeout"
                    process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode == 0:
                    output.seek(0)
                    installed = output.read(512).decode("utf-8", errors="replace").strip()[:120]
        except (OSError, subprocess.TimeoutExpired):
            probe_error = "version_probe_failed"
    return {"runtime": "claude-code", "installed_version": installed, "executable": executable,
            "version_probe_executable": probe, "version_probe_error": probe_error,
            "running_version": None, "running_version_source": "not_supplied_by_hook",
            "observer_version": OBSERVER_VERSION, "python": sys.executable}


def doctor(root: Path) -> dict[str, Any]:
    from .runtimes import selected
    if selected(root) == "omp":
        return doctor_omp(root)
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
    configuration_drift = []
    if manifest.is_file():
        for rel, digest in read_json(manifest).get("files", {}).items():
            path = (root / rel).resolve()
            path.relative_to(root)
            if rel == ".gitignore" and path.is_file() and sha256_file(path) != digest:
                # Shared project configuration is not an immutable runtime file.
                # Keep owned ignore obligations, permit unrelated project rules.
                required = (".aidevflow/runs/", ".aidevflow/events.jsonl", ".aidevflow/active-run.json")
                lines = set(path.read_text(encoding="utf-8-sig").splitlines())
                if all(line in lines for line in required):
                    configuration_drift.append(rel)
                    continue
            if not path.is_file() or sha256_file(path) != digest:
                mismatch.append(rel)
    roles = ["aidevflow-intent-analyst", "aidevflow-contract-planner", "aidevflow-independent-verifier"]
    missing_roles = [role for role in roles if not (root / ".claude" / "agents" / f"{role}.md").is_file()]
    active = root / ".aidevflow" / "active-run.json"
    run = None
    method_contract = None
    if active.is_file():
        from .gate import Gate
        from .receipts import execution_context, read_invocations
        state, run_dir, method_contract = Gate(root)._load_active()
        entries = read_invocations(run_dir)
        run = {"task_id": state["task_id"], "stage": state["current_stage"], "status": state["status"],
               "receipt_statuses": dict(Counter(e.get("status") for e in entries)),
               "stale_v2_receipts": sum(e.get("receipt_schema") == "aidevflow/invocation-v2" and e.get("context") != execution_context(state) for e in entries)}
        summary = run_dir / "agent-observer-status.json"
        if summary.is_file():
            run["observer"] = read_json(summary)
    warnings = ["Installed CLI version may differ from an already running session; restart after installation.",
                "No live role dispatch is performed by doctor. Hook presence does not establish runtime compatibility."]
    if configuration_drift:
        warnings.append("Shared project configuration changed outside AIDevFlow-owned rules: " + ", ".join(configuration_drift) + "; no reinstall is needed for this drift")
    method_hooks = [f"{event}:Skill" for event in ("PreToolUse", "PostToolUse")
                    if any(g.get("matcher") == "Skill" and any("trace-method" in h.get("command", "") for h in g.get("hooks", [])) for g in hooks.get(event, []))]
    warnings.append("Skill hooks provide advisory invocation telemetry for new autonomous runs; artifact review, frozen tests and independent acceptance remain required")
    if teams == "1":
        warnings.append("Teams is enabled. Critical roles must be fresh ordinary subagents without name/resume/fork/isolation; teammate results are not accepted by this adapter.")
    example = root / ".aidevflow/contract.example.json"
    if method_contract is None and example.is_file():
        method_contract = read_json(example)
    method_errors = []
    policy = None
    if method_contract:
        from .gate import Gate, validate_contract
        method_errors = validate_contract(method_contract) + Gate(root)._validate_available_method_skills(method_contract)
        from .managed import enabled, read_policy, package_manifest
        if enabled(method_contract) and not method_errors:
            policy = read_policy(root)
            policy["package_sha256"] = package_manifest(root, method_contract)["sha256"]
            warnings.append("Managed v1 is project-local, serial, strict, AI-reviewed and Design-frozen; configured parallel auto is not used.")
            warnings.append("Browser MCP availability, service startup and live test execution must be checked in the actual Claude session.")
    identity = runtime_identity()
    if identity.get("version_probe_error"):
        warnings.append("CLI version probe was unavailable or timed out; configuration results do not confirm the running CLI version.")
    return {"schema": "aidevflow/doctor-v1", "runtime": identity,
            "configuration_drift": configuration_drift,
            "capabilities": {"skill_hooks_configured": method_hooks, "live_skill_hook_support": "unverified",
                             "role_recovery": "bounded structured native transcript; unknown formats remain diagnostic",
                             "continuation": "one selected owner: bounded_stop, native or manual"},
            "ok": not (missing or missing_roles or mismatch or settings_errors or method_errors),
            "missing_hooks": missing, "missing_roles": missing_roles, "manifest_mismatches": mismatch,
            "settings_errors": settings_errors, "teams_setting": teams,
            "zsspec_skill_count": len(list((root / ".claude" / "skills").glob("zsspec-*/SKILL.md"))),
            "method_errors": method_errors, "method_policy": policy,
            "active_run": run, "warnings": warnings}


def doctor_omp(root: Path) -> dict[str, Any]:
    from .gate import Gate, read_json, sha256_file
    from .runtimes import identity, skills_root, project_config
    from .method_resources import source_status
    root = root.resolve()
    missing = [rel for rel in (".omp/extensions/aidevflow/index.ts", ".omp/extensions/aidevflow/client.ts",
                              "scripts/aidevflow_bridge.py", ".omp/skills/aidevflow-flow/SKILL.md") if not (root / rel).is_file()]
    roles = [f".omp/agents/aidevflow-{role}.md" for role in ("intent-analyst", "contract-planner", "independent-verifier")]
    absent_roles = [rel for rel in roles if not (root / rel).is_file()]
    manifest_path = root / ".aidevflow/runtime-manifest.json"
    mismatch, drift = [], []
    if manifest_path.is_file():
        for rel, expected in read_json(manifest_path).get("files", {}).items():
            path = (root / rel).resolve()
            path.relative_to(root)
            if not path.is_file() or sha256_file(path) != expected:
                if rel == ".gitignore" and path.is_file() and all(line in path.read_text(encoding="utf-8").splitlines() for line in (".aidevflow/runs/", ".aidevflow/events.jsonl", ".aidevflow/active-run.json")):
                    drift.append(rel)
                else:
                    mismatch.append(rel)
    else:
        mismatch.append(".aidevflow/runtime-manifest.json")
    gate = Gate(root)
    state, contract = None, None
    if gate.active_path.is_file():
        state, _, contract = gate._load_active()
    example = root / ".aidevflow/contract.example.json"
    if contract is None and example.is_file():
        contract = read_json(example)
    errors = gate._validate_available_method_skills(contract) if contract else []
    sources = source_status(root)
    actions = []
    if not sources["skill_count"]:
        found = [rel for rel, names in sources["candidates"].items() if names]
        if found:
            actions.append("Stop the OMP session, then reinstall with --runtime omp --skills-root " + found[0] + "; do not move or edit an active task's pinned assets")
        else:
            actions.append("Install the complete vendor method distribution in the selected root, excluding maintenance-only zsspec-doctor")
    warnings = ["doctor does not dispatch roles or establish live compatibility.",
                "Managed method reads bind physical files; native global/disabled provider state is checked only in an actual session.",
                "Use /aidevflow-doctor inside OMP to inspect the active tool list.",
                "Browser reports are execution evidence; manual visual acceptance still needs actual image inspection."]
    if sources["duplicates"]:
        warnings.append("Duplicate project method names found; managed reads use the pinned root, and bare native skill commands may select another copy")
    if sources["maintenance_skills"]:
        warnings.append("Maintenance-only zsspec-doctor is present; exclude it from business distributions. Managed method loading blocks it")
    settings_errors = []
    configured_python = project_config(root).get("python")
    if not configured_python or not Path(configured_python).is_file():
        settings_errors.append("Configured Python is unavailable; reinstall from the intended Python environment")
    try:
        baseline = subprocess.run(["git", "-c", "core.fsmonitor=false", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True, timeout=5, check=False)
        initial_commit = baseline.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        initial_commit = False
        warnings.append("Git baseline probe was unavailable or timed out")
    if not initial_commit:
        actions.append("Review the prepared project and create an initial Git commit before start-intake; doctor does not commit for you")
    import importlib.util
    playwright = "not_probed_in_configured_interpreter"
    if configured_python and Path(configured_python).resolve() == Path(sys.executable).resolve():
        playwright = "installed" if importlib.util.find_spec("playwright") else "not_installed"
    info = identity("omp")
    if not info["executable"]:
        errors.append("OMP executable not found on PATH")
    return {"schema": "aidevflow/doctor-v1", "runtime": info,
            "ok": not (missing or absent_roles or mismatch or errors or settings_errors),
            "missing_hooks": missing, "missing_roles": absent_roles,
            "manifest_mismatches": mismatch, "configuration_drift": drift,
            "method_errors": errors, "settings_errors": settings_errors,
            "method_sources": sources, "next_actions": actions,
            "readiness": {"initial_commit": initial_commit, "python_playwright": playwright,
                          "chromium": "not_probed", "browser_smoke": "run the declared plan against the actual local service"},
            "zsspec_skill_count": len(list(skills_root(root).glob("zsspec-*/SKILL.md"))),
            "active_run": {key: state.get(key) for key in ("task_id", "current_stage", "status")} if state else None,
            "capabilities": {"adapter": "omp-v1", "tested_protocol_version": "18.13.2",
                             "live_dispatch": "unverified", "continuation": ["native", "bounded_stop", "manual"],
                             "browser": "frozen local Playwright plan; command evidence plus separate visual attestation",
                             "static_review": "serial coordinator dimensions",
                             "mutation_policy": "scoped edit/write/bash; unclassified mutations blocked"},
            "warnings": warnings}
