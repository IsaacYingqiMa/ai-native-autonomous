"""Read-only native installation and session-readiness diagnostics."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path
from typing import Any


def doctor(root: Path, runtime: str = "omp") -> dict[str, Any]:
    from ....gate import Gate, GateError, read_json, sha256_file
    from ....runtimes import identity, skills_root, project_config
    from ....method_resources import source_status
    from ....runtimes.registry import get_runtime
    adapter = get_runtime(runtime)
    native = adapter.native_root
    root = root.resolve()
    missing = [rel for rel in (f"{native}/extensions/aidevflow/index.ts", f"{native}/extensions/aidevflow/client.ts",
                              "scripts/aidevflow_bridge.py", f"{native}/skills/aidevflow-flow/SKILL.md") if not (root / rel).is_file()]
    roles = [f"{native}/agents/aidevflow-{role}.md" for role in ("intent-analyst", "contract-planner", "independent-verifier", "artifact-reviewer")]
    if project_config(root).get("workflow_profile") == "reviewed-discovery-v1":
        roles.append(f"{native}/agents/aidevflow-requirements-analyst.md")
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
    if gate.active_path.is_file() and not (root / ".aidevflow/discovery-handoff.json").exists():
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
            actions.append(f"Stop the {runtime} session, then reinstall with --runtime {runtime} --skills-root " + found[0] + "; do not move or edit an active task's pinned assets")
        else:
            actions.append("Install the complete vendor method distribution in the selected root, excluding maintenance-only zsspec-doctor")
    warnings = ["doctor does not dispatch roles or establish live compatibility.",
                "Managed method reads bind physical files; native global/disabled provider state is checked only in an actual session.",
                f"Use /aidevflow-doctor inside {runtime} to inspect the active tool list.",
                "Browser reports are execution evidence; manual visual acceptance still needs actual image inspection."]
    if sources["duplicates"]:
        warnings.append("Duplicate project method names found; managed reads use the pinned root, and bare native skill commands may select another copy")
    if sources["maintenance_skills"]:
        warnings.append("Maintenance-only zsspec-doctor is present; exclude it from business distributions. Managed method loading blocks it")
    settings_errors = []
    discovery_state = None
    if (root / ".aidevflow/active-discovery.json").exists():
        try:
            from ....discovery import Discovery
            discovery_state = Discovery(root).load()[0]
        except (ValueError, KeyError, OSError, GateError) as exc:
            settings_errors.append("Invalid discovery: " + str(exc))
    if (root / ".aidevflow/discovery-handoff.json").exists():
        actions.append("Run discovery-start-delivery to recover the interrupted requirements handof")
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
    info = identity(runtime)
    if not info["executable"]:
        errors.append(f"{adapter.executable} executable not found on PATH")
    return {"schema": "aidevflow/doctor-v1", "runtime": info,
            "ok": not (missing or absent_roles or mismatch or errors or settings_errors),
            "missing_hooks": missing, "missing_roles": absent_roles,
            "manifest_mismatches": mismatch, "configuration_drift": drift,
            "method_errors": errors, "settings_errors": settings_errors,
            "method_sources": sources, "next_actions": actions,
            "workflow_profile": project_config(root).get("workflow_profile", "legacy"),
            "active_discovery": discovery_state,
            "readiness": {"initial_commit": initial_commit, "python_playwright": playwright,
                          "chromium": "not_probed", "browser_smoke": "run the declared plan against the actual local service"},
            "zsspec_skill_count": len(list(skills_root(root).glob("zsspec-*/SKILL.md"))),
            "active_run": {key: state.get(key) for key in ("task_id", "current_stage", "status")} if state else None,
            "capabilities": {"adapter": adapter.adapter_version, "tested_protocol_version": adapter.tested_version,
                             "live_dispatch": "unverified", "continuation": ["native", "bounded_stop", "manual"],
                             "browser": "frozen local Playwright plan; command evidence plus separate visual attestation",
                             "static_review": "serial coordinator dimensions",
                             "mutation_policy": "scoped edit/write/bash; unclassified mutations blocked"},
            "warnings": warnings}
