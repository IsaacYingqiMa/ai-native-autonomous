"""Shared installation renderer for explicitly registered native task hosts."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from ....gate import GateError, sha256_file, write_json_atomic
from ....installer import contract_example


def install(source_root: Path, target_root: Path, *, skills_root: str | None = None, workflow_profile: str | None = None, runtime: str = "omp") -> dict[str, Any]:
    """Install project-local native assets without changing global host settings."""
    import subprocess
    import sys
    from ....runtimes import project_config
    from ....runtimes.registry import get_runtime
    adapter = get_runtime(runtime)
    native = adapter.native_root
    source, target = source_root.resolve(), target_root.resolve()
    if source == target:
        raise GateError("AIDevFlow cannot install over its framework source directory")
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=target, capture_output=True, text=True, check=False)
    if result.returncode or Path(result.stdout.strip()).resolve() != target:
        raise GateError("Install target must be the root of a Git repository")
    config = project_config(target)
    if any((target / ".aidevflow" / n).exists() for n in ("active-run.json", "active-discovery.json", "discovery-handoff.json")) and config.get("runtime", "claude-code") != runtime:
        raise GateError("An active context cannot migrate to another runtime")
    from ....method_resources import installation_root
    if workflow_profile not in (None, "legacy", "reviewed-discovery-v1"):
        raise GateError("Unsupported workflow_profile")
    if workflow_profile is not None:
        if any((target / ".aidevflow" / name).exists() for name in ("active-run.json", "active-discovery.json", "discovery-handoff.json")) and workflow_profile != config.get("workflow_profile", "legacy"):
            raise GateError("Cannot switch workflow profile with an active context")
        config["workflow_profile"] = workflow_profile
    config.setdefault("workflow_profile", "legacy" if any((target / ".aidevflow" / n).exists() for n in ("active-run.json", "active-discovery.json", "discovery-handoff.json")) else "reviewed-discovery-v1")
    config["skills_root"] = installation_root(target, runtime, config, skills_root)
    config.update(schema="aidevflow/project-v1", runtime=runtime, python=sys.executable)
    # Resolve and validate before copying any files. Vendor assets are not installed.
    method_root = (target / config["skills_root"]).resolve()
    method_root.relative_to(target)
    if method_root == target or Path(config["skills_root"]).is_absolute():
        raise GateError("skills_root must be inside the project")
    copies = [(source / "aidevflow", target / ".aidevflow/engine/python/aidevflow"),
              (source / "assets/schemas", target / ".aidevflow/schemas"),
              (source / "assets", target / ".aidevflow/engine/assets"),
              (source / "integrations/shared", target / ".aidevflow/engine/host/shared"),
              (source / f"integrations/{runtime}", target / f"{native}/extensions/aidevflow")]
    for name in ("work-plan.example.json",):
        copies.append((source / ".aidevflow" / name, target / ".aidevflow" / name))
    copies.append((contract_example(source, method_root), target / ".aidevflow/contract.example.json"))
    roles = ("intent-analyst", "contract-planner", "independent-verifier", "requirements-analyst", "artifact-reviewer")
    required = [p for p, _ in copies] + [source / f"assets/roles/aidevflow-{role}.md" for role in roles]
    required += [source / "assets/flows/aidevflow-flow/SKILL.md", source / "aidevflow/browser.py", source / "integrations/shared/client.ts",
                 source / f"integrations/{runtime}/index.ts", source / f"integrations/{runtime}/client.ts"]
    if any(not path.exists() for path in required):
        raise GateError("AIDevFlow installation source is incomplete")
    manifest_path = target / ".aidevflow/runtime-manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if not isinstance(previous, dict) or not isinstance(previous.get("files", {}), dict):
        raise GateError("Existing installation manifest is invalid")
    files: dict[str, str] = {}

    def write_owned(path: Path, text: str) -> None:
        from ....gate import replace_file_atomic
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

    pending: dict[str, str] = {}
    def put(path: Path, text: str) -> None:
        import hashlib
        path.resolve().relative_to(target)
        relative = path.relative_to(target).as_posix()
        normalized = text.replace("\r\n", "\n")
        pending[relative] = normalized
        files[relative] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    for src, dest in copies:
        if src.is_dir():
            for child in src.rglob("*"):
                if src == source / "assets" and child.relative_to(src).parts[0] == "schemas":
                    continue
                if child.is_file() and "__pycache__" not in child.parts and child.suffix not in (".pyc", ".pyo"):
                    put(dest / child.relative_to(src), child.read_text(encoding="utf-8"))
        else:
            put(dest, src.read_text(encoding="utf-8"))
    for role in roles:
        original = (source / f"assets/roles/aidevflow-{role}.md").read_text(encoding="utf-8")
        body = original
        if role == "independent-verifier":
            body += ("\nEvidence strings must exactly equal BUNDLE.json files[].path values, relative to the bundle "
                     "(for example objects/<sha256>). Never prepend the bundle directory or append descriptions, "
                     "parentheses or line numbers; put explanations in reason.\n")
        schema_file = {"intent-analyst": "intent-draft", "contract-planner": "contract", "independent-verifier": "verifier-verdict", "requirements-analyst": "requirements", "artifact-reviewer": "artifact-review"}[role]
        output_schema = json.loads((source / f"assets/schemas/{schema_file}.schema.json").read_text(encoding="utf-8"))
        output_schema.pop("$schema", None)
        output_schema.pop("$id", None)
        header = (f"---\nname: aidevflow-{role}\ndescription: AIDevFlow {role}; read-only, exact JSON output.\n"
                  "tools: [read, grep, glob]\nblocking: true\nstructured: true\nadvisor: false\n"
                  "readSummarize: false\noutput: " + json.dumps(output_schema, ensure_ascii=False) + "\n---\n")
        put(target / f"{native}/agents/aidevflow-{role}.md", header + body +
            '\nNative transport: call the native yield tool as {"data": <the complete JSON object>}. '
            'The data envelope is transport only, not part of the role result. Do not call yield with '
            'schema/task_id/etc as top-level arguments, and never call yield with {}. Preserve actual '
            'JSON arrays. Do not split the object into sections.\n')
    put(target / f"{native}/extensions/aidevflow/client.ts", 'export { Bridge } from "../../../.aidevflow/engine/host/shared/client";\n')
    # Render only the native import boundary; role and workflow contents are shared verbatim.
    entry = ('import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";\n'
             'import { createAidevflow } from "../../../.aidevflow/engine/host/shared/omp-flow";\n'
             'import { Bridge } from "./client";\n\n'
             'export default function aidevflow(pi: ExtensionAPI): void {\n'
             '  createAidevflow(pi, ' + json.dumps({"runtime": runtime, "nativeRoot": native, "displayName": runtime}, ensure_ascii=False) +
             ', root => new Bridge(root, ' + json.dumps(runtime) + '));\n}\n')
    put(target / f"{native}/extensions/aidevflow/index.ts", entry)
    put(target / ".aidevflow/local.json", json.dumps({"python": sys.executable}, indent=2) + "\n")
    flow_root = source / "assets/flows/aidevflow-flow"
    for path in flow_root.rglob("*"):
        if path.is_file():
            put(target / f"{native}/skills/aidevflow-flow" / path.relative_to(flow_root), path.read_text(encoding="utf-8"))
    for name, module, tail in (("aidevflow.py", "cli", "main()"),
                               ("aidevflow_bridge.py", "bridge", "serve(root)"),
                               ("aidevflow_browser.py", "browser", "main()")):
        put(target / "scripts" / name,
            "from pathlib import Path\nimport sys\nroot = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow/engine/python'))\n"
            f"from aidevflow.{module} import {'serve' if module == 'bridge' else 'main'}\nraise SystemExit({tail})\n")
    config.pop("python", None)  # Interpreter belongs to local.json, not shared project configuration.
    put(target / ".aidevflow/project.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    ignores = [".aidevflow/local.json", ".aidevflow/method-operation.lock", ".aidevflow/method-cache/", ".aidevflow/discoveries/", ".aidevflow/active-discovery.json", ".aidevflow/discovery-handoff.json",
               ".aidevflow/runs/", ".aidevflow/bundles/", ".aidevflow/maintenance/", ".aidevflow/active-run.json",
               ".aidevflow/events.jsonl", ".aidevflow/archive/", ".aidevflow/agent-observer.lock", ".aidevflow/runtime-sessions/",
               ".aidevflow/gate-write.lock", ".aidevflow/coordinator.lock", ".aidevflow/event-log.lock",
               f"{native}/sessions/", f"{native}/logs/", f"!{native}/", f"!{native}/extensions/", f"!{native}/extensions/aidevflow/",
               f"!{native}/extensions/aidevflow/**", f"!{native}/agents/", f"!{native}/agents/aidevflow-*.md",
               f"!{native}/skills/", f"!{native}/skills/aidevflow-flow/", f"!{native}/skills/aidevflow-flow/**"]
    ignore_path = target / ".gitignore"
    old = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    extra = [line for line in ignores if line not in old.splitlines()]
    if extra:
        put(ignore_path, old.rstrip("\n") + f"\n# AIDevFlow local data and native {runtime} assets\n" + "\n".join(extra) + "\n")
    else:
        files[".gitignore"] = sha256_file(ignore_path)
    # Validate the complete rendered plan before changing any owned runtime file.
    active_context = any((target / ".aidevflow" / n).exists() for n in ("active-run.json", "active-discovery.json", "discovery-handoff.json"))
    if active_context and previous.get("files"):
        changed = [rel for rel, digest in files.items() if rel in previous["files"] and previous["files"][rel] != digest and rel not in (".gitignore", ".aidevflow/local.json")]
        if changed:
            raise GateError("An active context pins installed code; finish/archive it before upgrading: " + ", ".join(changed[:5]))
    for rel, text in pending.items():
        write_owned(target / rel, text)
    # Per-runtime ownership is independent; the compatibility aggregate remains for older readers.
    native_files = {k: v for k, v in files.items() if k.startswith(f"{native}/")}
    common = {k: v for k, v in files.items() if not k.startswith((".omp/", ".zscode/", ".claude/")) and k not in (".gitignore", ".aidevflow/project.json", ".aidevflow/local.json")}
    write_json_atomic(target / ".aidevflow/install/common.manifest.json", {"schema": "aidevflow/owned-files-v1", "files": common})
    write_json_atomic(target / f".aidevflow/install/runtimes/{runtime}.manifest.json", {"schema": "aidevflow/owned-files-v1", "runtime": runtime, "files": native_files})
    # Keep the other backend's owned assets in a mixed installation.
    all_files = {**previous.get("files", {}), **files}
    write_json_atomic(manifest_path, {"schema": "aidevflow/install-manifest-v1", "runtime": runtime, "observer_version": adapter.adapter_version,
                                     "reliability_version": 4, "files": all_files})
    return {"runtime": runtime, "target": str(target), "installed": sorted(files), "settings_path": ".aidevflow/project.json"}
