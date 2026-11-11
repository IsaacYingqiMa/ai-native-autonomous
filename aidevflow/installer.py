"""Shared installation API; runtime renderers own their native files."""
from __future__ import annotations
from pathlib import Path
from typing import Any


def contract_example(source: Path, method_root: Path) -> Path:
    """New execution bindings use the current five-skill document protocol."""
    return source / "assets/examples/contract.json"


def install(source_root: Path, target_root: Path, runtime: str = "claude-code", *, skills_root: str | None = None, workflow_profile: str | None = None) -> dict[str, Any]:
    from .runtimes.registry import get_runtime
    return get_runtime(runtime).install(source_root, target_root, skills_root=skills_root, workflow_profile=workflow_profile)


def install_omp(source_root: Path, target_root: Path, *, skills_root: str | None = None, workflow_profile: str | None = None) -> dict[str, Any]:
    return install(source_root, target_root, "omp", skills_root=skills_root, workflow_profile=workflow_profile)


def merge_settings(target: dict[str, Any], source: dict[str, Any]) -> None:
    from .runtimes.claude_code.installation import merge_settings as merge
    merge(target, source)
