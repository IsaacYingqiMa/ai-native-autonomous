"""Preparation-time method discovery, cache and task-local immutable sources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .method_registry import adapter_for, V2

SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".js", ".ts", ".sh", ".ps1", ".toml"}


def inventory(source: Path, names: set[str]) -> dict:
    from .gate import GateError, sha256_file
    paths = [source / "README.md"]
    for name in sorted(names):
        paths.append(source / name / "SKILL.md")
        paths.extend(p for p in (source / name).rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES
                     and "__pycache__" not in p.parts)
    files = {}
    for p in sorted(set(paths)):
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()):
            raise GateError("Method sources must be physical files inside their selected root")
        files[p.relative_to(source).as_posix()] = sha256_file(p) if p.is_file() else None
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"manifest_version": 3, "sha256": digest, "files": files}


def discover(root: Path) -> dict:
    from .runtimes import skills_root
    from .method_adapters.zsspec_docs import ZsspecDocsAdapter as ZsspecAdapter
    from .gate import read_json, write_json_atomic
    from .locking import writer_lock
    adapter = ZsspecAdapter()
    source = skills_root(root)
    names = {n for group in adapter.stages.values() for n in group} | set(adapter.support)
    package = inventory(source, names)
    key = package["sha256"] + "-resolver1-adapter1"
    cache = root / ".aidevflow/method-cache" / (key + ".json")
    with writer_lock(root / ".aidevflow"):
        # Revalidate protocol as well as cached bytes: a cache is an optimization,
        # never authority to weaken execution policy.
        description = adapter.describe(source)
        value = {**description, "package": package}
        hit = cache.is_file() and read_json(cache) == value
        if not hit:
            write_json_atomic(cache, value)
    return {**value, "cache_hit": hit, "skills_root": source.relative_to(root).as_posix(),
            "discovered_skills": sorted(p.parent.name for p in source.glob("zsspec-*/SKILL.md")),
            "excluded_from_execution": ["zsspec-feedback", "zsspec-doctor"]}


def source_for(root: Path, contract: dict | None = None) -> Path:
    from .runtimes import skills_root
    from .gate import read_json, safe_id
    if contract is None:
        active = root / ".aidevflow/active-run.json"
        if active.is_file():
            state = read_json(root / read_json(active)["state_path"])
            if state.get("contract_path"):
                contract = read_json(root / state["contract_path"])
    if adapter_for(contract):
        run = root / ".aidevflow/runs" / safe_id(contract["task_id"])
        pinned = run / "method-source"
        if (run / "method-source-ready.json").is_file():
            return pinned
    active = root / ".aidevflow/active-run.json"
    if active.is_file():
        state = read_json(root / read_json(active)["state_path"])
        if state.get("method_preparation"):
            return root / ".aidevflow/runs" / safe_id(state["task_id"]) / "method-source"
    return skills_root(root)


def pin(root: Path, run: Path, contract: dict) -> dict:
    from .gate import GateError, write_bytes_new, write_json_new, read_json, sha256_file
    adapter = adapter_for(contract)
    from .method_adapters.zsspec_docs import enabled as docs
    if docs(contract) and (run / "method-source-ready.json").is_file():
        source = run / "method-source"
        package = read_json(run / "method-source-ready.json")
        if inventory(source, {n for v in adapter.stages.values() for n in v} | set(adapter.support)) != package:
            raise GateError("Prepared method snapshot changed")
        resolved = {**adapter.describe(source), "package": package}
        path = run / "resolved-method.json"
        if path.exists():
            if read_json(path) != resolved:
                raise GateError("Interrupted method binding conflicts with prepared source")
        else:
            write_json_new(path, resolved)
        return {"package": package, "resolved": {"path": (run / "resolved-method.json").relative_to(root).as_posix(),
                "sha256": sha256_file(run / "resolved-method.json")}, "adapter_api_version": 1,
                "source_path": source.relative_to(root).as_posix()}
    resolved = discover(root)
    destination = run / "method-source"
    from .runtimes import skills_root
    source = skills_root(root)
    ready = run / "method-source-ready.json"
    if ready.exists():
        old = read_json(ready)
        if old != resolved["package"]:
            raise GateError("A different method snapshot already exists; inspect interrupted registration")
    for rel, expected in resolved["package"]["files"].items():
        if expected is None:
            raise GateError("Incomplete method source: " + rel)
        p = destination / rel
        if not p.exists():
            data = (source / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise GateError("Method source changed while snapshotting")
            write_bytes_new(p, data)
        if sha256_file(p) != expected:
            raise GateError("Interrupted method snapshot conflicts: " + rel)
    if inventory(source, {n for v in adapter.stages.values() for n in v} | set(adapter.support)) != resolved["package"]:
        raise GateError("Method package changed during registration")
    if not ready.exists():
        write_json_new(ready, resolved["package"])
    description = {k: v for k, v in resolved.items() if k != "cache_hit"}
    resolved_path = run / "resolved-method.json"
    if resolved_path.exists():
        if read_json(resolved_path) != description:
            raise GateError("Interrupted resolved method conflicts with the current package")
    else:
        write_json_new(resolved_path, description)
    return {"package": resolved["package"], "resolved": {"path": (run / "resolved-method.json").relative_to(root).as_posix(),
            "sha256": sha256_file(run / "resolved-method.json")}, "adapter_api_version": 1,
            "source_path": destination.relative_to(root).as_posix()}
