from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .gate import GateError, sha256_file, write_json_atomic


def install(source_root: Path, target_root: Path, runtime: str = "claude-code", *, skills_root: str | None = None) -> dict[str, Any]:
    if runtime == "omp":
        return install_omp(source_root, target_root, skills_root=skills_root)
    if runtime != "claude-code":
        raise GateError(f"Unknown runtime: {runtime}")
    source = source_root.resolve()
    target = target_root.resolve()
    if source == target:
        raise GateError("AIDevFlow cannot install over its framework source directory")
    git_root = __import__("subprocess").run(
        ["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True, check=False
    )
    if git_root.returncode or Path(git_root.stdout.strip()).resolve() != target:
        raise GateError("Install target must be the root of a Git repository")

    from .runtimes import project_config
    from .method_resources import installation_root
    config = project_config(target)
    config["skills_root"] = installation_root(target, runtime, config, skills_root)

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
        "aidevflow_browser.py": (
            "from pathlib import Path\nimport sys\n"
            "root = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow' / 'runtime'))\n"
            "sys.path.insert(1, str(root))\n"
            "from aidevflow.browser import main\nraise SystemExit(main())\n"
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
        ".aidevflow/gate-write.lock\n"
        ".aidevflow/event-log.lock\n"
        ".aidevflow/coordinator.lock\n"
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
    from .runtimes import project_config
    import sys
    config.update(schema="aidevflow/project-v1", runtime="claude-code", python=sys.executable)
    config_path = target / ".aidevflow/project.json"
    write_json_atomic(config_path, config)
    manifest_files[".aidevflow/project.json"] = sha256_file(config_path)
    manifest_path = target / ".aidevflow/runtime-manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest_files = {**previous.get("files", {}), **manifest_files}
    write_json_atomic(manifest_path, {"schema": "aidevflow/install-manifest-v1", "runtime": "claude-code", "source": "local AIDevFlow framework", "reliability_version": 4, "observer_version": OBSERVER_VERSION, "files": manifest_files})
    return {"target": str(target), "installed": installed, "settings_path": settings_path.relative_to(target).as_posix()}


def install_omp(source_root: Path, target_root: Path, *, skills_root: str | None = None) -> dict[str, Any]:
    """Install native OMP assets without writing Claude or global OMP settings."""
    import subprocess
    import sys
    from .runtimes import project_config
    source, target = source_root.resolve(), target_root.resolve()
    if source == target:
        raise GateError("AIDevFlow cannot install over its framework source directory")
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True, check=False)
    if result.returncode or Path(result.stdout.strip()).resolve() != target:
        raise GateError("Install target must be the root of a Git repository")
    config = project_config(target)
    from .method_resources import installation_root
    config["skills_root"] = installation_root(target, "omp", config, skills_root)
    config.update(schema="aidevflow/project-v1", runtime="omp", python=sys.executable)
    # Resolve and validate before copying any files. Vendor assets are not installed.
    method_root = (target / config["skills_root"]).resolve()
    method_root.relative_to(target)
    if method_root == target or Path(config["skills_root"]).is_absolute():
        raise GateError("skills_root must be inside the project")
    copies = [(source / "aidevflow", target / ".aidevflow/runtime/aidevflow"),
              (source / ".aidevflow/schemas", target / ".aidevflow/schemas"),
              (source / "integrations/omp", target / ".omp/extensions/aidevflow")]
    for name in ("contract.example.json", "work-plan.example.json"):
        copies.append((source / ".aidevflow" / name, target / ".aidevflow" / name))
    roles = ("intent-analyst", "contract-planner", "independent-verifier")
    required = [p for p, _ in copies] + [source / f".claude/agents/aidevflow-{role}.md" for role in roles]
    required += [source / ".claude/skills/aidevflow-flow/SKILL.md",
                 source / ".claude/skills/aidevflow-flow/references/zsspec-managed.md",
                 source / ".claude/skills/aidevflow-flow/references/omp-capabilities.md",
                 source / "aidevflow/browser.py",
                 source / "integrations/omp/index.ts", source / "integrations/omp/client.ts"]
    required += [source / f".aidevflow/schemas/{name}.schema.json" for name in ("intent-draft", "contract", "verifier-verdict")]
    if any(not path.exists() for path in required):
        raise GateError("AIDevFlow installation source is incomplete")
    manifest_path = target / ".aidevflow/runtime-manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if not isinstance(previous, dict) or not isinstance(previous.get("files", {}), dict):
        raise GateError("Existing installation manifest is invalid")
    files: dict[str, str] = {}

    def put(path: Path, text: str) -> None:
        from .gate import replace_file_atomic
        import tempfile
        import os
        path.resolve().relative_to(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            replace_file_atomic(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        files[path.relative_to(target).as_posix()] = sha256_file(path)

    for src, dest in copies:
        if src.is_dir():
            for child in src.rglob("*"):
                if child.is_file() and "__pycache__" not in child.parts and child.suffix not in (".pyc", ".pyo"):
                    put(dest / child.relative_to(src), child.read_text(encoding="utf-8"))
        else:
            put(dest, src.read_text(encoding="utf-8"))
    for role in roles:
        original = (source / f".claude/agents/aidevflow-{role}.md").read_text(encoding="utf-8")
        body = original.split("---", 2)[2].replace("Claude", "OMP")
        if role == "independent-verifier":
            body += ("\nEvidence strings must exactly equal BUNDLE.json files[].path values, relative to the bundle "
                     "(for example objects/<sha256>). Never prepend the bundle directory or append descriptions, "
                     "parentheses or line numbers; put explanations in reason.\n")
        schema_file = {"intent-analyst": "intent-draft", "contract-planner": "contract", "independent-verifier": "verifier-verdict"}[role]
        output_schema = json.loads((source / f".aidevflow/schemas/{schema_file}.schema.json").read_text(encoding="utf-8"))
        output_schema.pop("$schema", None)
        output_schema.pop("$id", None)
        header = (f"---\nname: aidevflow-{role}\ndescription: AIDevFlow {role}; read-only, exact JSON output.\n"
                  "tools: [read, grep, glob]\nblocking: true\nstructured: true\nadvisor: false\n"
                  "readSummarize: false\noutput: " + json.dumps(output_schema, ensure_ascii=False) + "\n---\n")
        put(target / f".omp/agents/aidevflow-{role}.md", header + body +
            '\nOMP transport: call the native yield tool as {"data": <the complete JSON object>}. '
            'The data envelope is transport only, not part of the role result. Do not call yield with '
            'schema/task_id/etc as top-level arguments, and never call yield with {}. Preserve actual '
            'JSON arrays. Do not split the object into sections.\n')
    flow = (source / ".claude/skills/aidevflow-flow/SKILL.md").read_text(encoding="utf-8")
    start, end = flow.index("## Claude execution compatibility"), flow.index("## Keep the run proportionate")
    omp_context = """## OMP execution adapter

Run doctor/status first. Use the native task tool, one critical role per call:
use a one-item tasks array when the active schema requires batch form; otherwise
use the flat form. agent: exact role; task: complete assignment; solutionSpace: bounded.
Critical roles are installed as blocking read-only specialists. Do not override their
tools, output schema, isolation, or advisor. Require one native structured JSON result.
Verifier task must include prepare-verifier's exact bundle path and SHA-256. It may
read only that bundle. Never use an earlier role's output with a new input epoch.
The extension captures native task IDs, exitCode and structuredOutput validation.
Background launch alone is pending; do not synthesize completion or edit returned JSON.
The native result hook supplies the immutable result_path. Pass that path directly
to record-intent --draft, register-contract --draft, or record-verdict --path.
Do not transcribe the rendered tree, parse private observations, or fix role JSON.
Use CLI --help, schemas, examples and this playbook for normal delivery. Do not
read framework implementation files to reverse-engineer an ordinary workflow;
reserve runtime source inspection for an actual protocol failure.
Project initialization and method configuration belong before intake. Once the
Contract is registered, specs/zsspec.yml (including absence) is immutable. Do not
run zsspec-init or generate that file during Design; use the pinned effective policy.
Before each critical role, run role-context --role intent_analyst|contract_planner|verifier.
Use its task_call directly in the native task tool. It already includes the assigned
task ID and the exact public input paths; do not reverse-engineer a dispatch prompt.
Use recover-runtime and inspect status before redispatching; unknown execution must
not cause duplicate work. Runtime switching for an active task is unsupported.
The user can invoke /skill:<name> interactively. As the autonomous coordinator,
use read skill://<name> and read its referenced resources. The extension routes
managed method reads to the pinned physical source and attaches the managed policy
to each load. Do not type slash commands into bash. Never invoke maintenance-only
zsspec-doctor. Method observations are advisory for artifact-gated runs.
Vendor frontmatter is not an authorization to register identities, send feedback
or external telemetry. Follow the actual user/company authorization for those actions.
Use edit/write for scoped mutations. Other mutation tools need a verified policy;
never use eval, virtual writes or another tool to bypass a denied change.
Native /goal may own continuation after continuation --owner native. Otherwise use
bounded_stop or manual, never two owners. Goal completion is not Gate acceptance.

## OMP method capabilities

The managed host mapping overrides vendor host-specific mechanics, preserving its
engineering checks. Complete static review dimensions serially in the coordinator;
do not dispatch orchestrator/correctness/finding-verifier or other vendor-named agents.
Gate-owned status projections replace direct TASK/CHK/REQ status writes.
Chrome-devtools-mcp is not required by this managed host mapping: use the isolated
Playwright runner scripts/aidevflow_browser.py with a declared, Design-frozen JSON
plan. Run it through Gate run-tests, capture report.json and each screenshot using
--evidence, then inspect the images before recording any manual AI attestation.
Static/manual records remain AI claims, not human approvals. Subjective judgments
or unavailable environments stay blocked. Native computer/eval remains unsupported.
See references/omp-capabilities.md for the plan format and actual argv.

"""
    flow = flow[:start] + omp_context + flow[end:]
    flow = flow.replace("Claude coordinator", "OMP coordinator").replace("Claude Code", "OMP")
    flow = flow.replace("Save the returned JSON unchanged to `specs/aidevflow-intake/<TASK_ID>/intent-draft.json`, then call `record-intent --draft <path>`.",
                        "Use the immutable native receipt result_path directly in `record-intent --draft <result_path>`.")
    flow = flow.replace("Save the Planner's JSON unchanged to `specs/aidevflow-intake/<TASK_ID>/contract-draft.json`; call `register-contract --draft <path>`.",
                        "Use the Planner receipt result_path directly in `register-contract --draft <result_path>`.")
    flow = flow.replace("Save its exact JSON at `specs/aidevflow-intake/<TASK_ID>/verdict.json` and call `record-verdict`.",
                        "Use its immutable receipt result_path directly in `record-verdict --path <result_path>`.")
    put(target / ".omp/skills/aidevflow-flow/SKILL.md", flow)
    for child in (source / ".claude/skills/aidevflow-flow/references").rglob("*"):
        if child.is_file():
            text = child.read_text(encoding="utf-8").replace("Claude coordinator", "OMP coordinator")
            text = text.replace("confirm actual tools in the Claude session", "confirm actual tools in the OMP session")
            text = text.replace("The profile does not add support for CodeBuddy or another runtime.", "OMP runtime support is supplied by the AIDevFlow extension.")
            text = text.replace("Project-local `.claude/skills` only in this first profile.",
                                "Use the project-local skills_root pinned by Gate, including native `.omp/skills`.")
            text = text.replace("Missing browser MCP or real external access stays blocked", "Missing the managed Playwright browser capability or real external access stays blocked")
            put(target / ".omp/skills/aidevflow-flow/references" / child.relative_to(source / ".claude/skills/aidevflow-flow/references"), text)
    for name, module, tail in (("aidevflow.py", "cli", "main()"),
                               ("aidevflow_bridge.py", "bridge", "serve(root)"),
                               ("aidevflow_browser.py", "browser", "main()")):
        put(target / "scripts" / name,
            "from pathlib import Path\nimport sys\nroot = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow/runtime'))\n"
            f"from aidevflow.{module} import {'serve' if module == 'bridge' else 'main'}\nraise SystemExit({tail})\n")
    put(target / ".aidevflow/project.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    ignores = [".aidevflow/runs/", ".aidevflow/bundles/", ".aidevflow/maintenance/", ".aidevflow/active-run.json",
               ".aidevflow/events.jsonl", ".aidevflow/archive/", ".aidevflow/agent-observer.lock", ".aidevflow/runtime-sessions/",
               ".aidevflow/gate-write.lock", ".aidevflow/coordinator.lock", ".aidevflow/event-log.lock",
               ".omp/sessions/", ".omp/logs/", "!.omp/", "!.omp/extensions/", "!.omp/extensions/aidevflow/",
               "!.omp/extensions/aidevflow/**", "!.omp/agents/", "!.omp/agents/aidevflow-*.md",
               "!.omp/skills/", "!.omp/skills/aidevflow-flow/", "!.omp/skills/aidevflow-flow/**"]
    ignore_path = target / ".gitignore"
    old = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    extra = [line for line in ignores if line not in old.splitlines()]
    if extra:
        put(ignore_path, old.rstrip("\n") + "\n# AIDevFlow local data and native OMP assets\n" + "\n".join(extra) + "\n")
    else:
        files[".gitignore"] = sha256_file(ignore_path)
    # Keep the other backend's owned assets in a mixed installation.
    all_files = {**previous.get("files", {}), **files}
    write_json_atomic(manifest_path, {"schema": "aidevflow/install-manifest-v1", "runtime": "omp", "observer_version": "omp-v1",
                                     "reliability_version": 4, "files": all_files})
    return {"runtime": "omp", "target": str(target), "installed": sorted(files), "settings_path": ".aidevflow/project.json"}


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
