"""Project-local method sources and the OMP managed invocation boundary."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .gate import GateError

ROOTS = (".omp/skills", ".claude/skills")
MAINTENANCE_SKILLS = {"zsspec-doctor"}


def local_root(root: Path, value: str) -> Path:
    if (not isinstance(value, str) or not value.strip() or Path(value).is_absolute()
            or PureWindowsPath(value).drive):
        raise GateError("skills_root must be a project-relative directory")
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError("skills_root must stay inside the project") from exc
    if path == root.resolve():
        raise GateError("skills_root cannot be the project root")
    return path


def candidates(root: Path) -> dict[str, list[str]]:
    return {rel: sorted(p.parent.name for p in (root / rel).glob("zsspec-*/SKILL.md")) for rel in ROOTS}


def installation_root(root: Path, runtime: str, config: dict[str, Any], requested: str | None) -> str:
    from .gate import Gate
    active = (root / ".aidevflow/active-run.json").is_file()
    pinned = None
    if active:
        state, _, _ = Gate(root)._load_active()
        pinned = state.get("execution_binding", {}).get("skills_root", ".claude/skills")
    value = requested if requested not in (None, "auto") else config.get("skills_root")
    if pinned is not None:
        if requested == "auto" or (value is not None and local_root(root, value) != local_root(root, pinned)):
            raise GateError("An active task pins skills_root; archive it before changing the method source")
        return local_root(root, pinned).relative_to(root.resolve()).as_posix()
    if requested not in (None, "auto"):
        return local_root(root, requested).relative_to(root.resolve()).as_posix()
    if requested is None and value and any(local_root(root, value).glob("zsspec-*/SKILL.md")):
        return local_root(root, value).relative_to(root.resolve()).as_posix()
    found = [rel for rel, names in candidates(root).items() if names]
    if len(found) > 1:
        raise GateError("Multiple method roots found; install with --skills-root .omp/skills or .claude/skills")
    if found:
        return found[0]
    return local_root(root, value or (".omp/skills" if runtime == "omp" else ".claude/skills")).relative_to(root.resolve()).as_posix()


def source_status(root: Path) -> dict[str, Any]:
    from .runtimes import skills_root
    selected = skills_root(root)
    roots = candidates(root)
    names = {p.parent.name for p in selected.glob("zsspec-*/SKILL.md")}
    duplicates = {name: [str(p.resolve()) for p in (root / rel / name / "SKILL.md" for rel in ROOTS) if p.is_file()]
                  for name in names}
    duplicates = {name: paths for name, paths in duplicates.items() if len(set(paths)) > 1}
    return {"configured_root": selected.relative_to(root.resolve()).as_posix(), "exists": selected.is_dir(), "skill_count": len(names),
            "candidates": roots, "duplicates": duplicates,
            "maintenance_skills": sorted(names & MAINTENANCE_SKILLS),
            "native_resolution": "managed reads are routed to the pinned physical source; global discovery is not inferred"}


def managed_context(stage: str) -> str:
    return (
        f"AIDevFlow managed method invocation, stage {stage}. The following constraints apply to this load and its references. "
        "Read the method instructions and perform their applicable engineering work; loading is not completion. "
        "The approved Intent, Contract and Gate own scope and acceptance. Review ordinary artifacts as AI coordinator "
        "within the user's authorization; never invent human approval. Escalate material scope/risk/acceptance changes. "
        "Gate alone writes mapped REQ/TASK/CHK status cells. After Design, frozen tests and specifications cannot be repaired "
        "by a method skill; report a baseline issue instead. Reuse a passing check only when Gate reports it fresh. "
        "Perform all static review dimensions serially in the coordinator; do not spawn vendor-named reviewers or parallel workers. "
        "For auto browser checks run the installed aidevflow_browser.py plan runner through Gate run-tests. "
        "For manual AI-assisted checks, the managed equivalent of chrome-devtools-mcp is an isolated Playwright browser: "
        "execute the declared steps, inspect the actual screenshots, and record-check honestly with fresh candidate and evidence. "
        "A script pass alone is not visual approval. Subjective or inaccessible checks remain blocked. "
        "Native computer, arbitrary eval and unknown MCP tools remain unsupported; do not bypass tool policy. "
        "Vendor allowed-tools grants no runtime permission. Usage logging, identity collection, uploads and feedback require "
        "actual user/company authorization; they are not Gate prerequisites. Do not invoke maintenance zsspec-doctor. "
        "Resolve templates by exact physical path or skill URI, not glob patterns containing literal brackets."
    )


def load_resource(root: Path, inp: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a method read against one pinned root; never follow a provider alias."""
    from .gate import Gate, sha256_file, read_json
    from .runtimes import skills_root, selected
    if not isinstance(inp, dict):
        raise GateError("Method read input must be an object")
    raw = inp.get("path")
    if not isinstance(raw, str):
        raise GateError("Method read requires a path")
    suffix = re.search(r":\d+(?:-\d+)?$", raw)
    span = suffix.group(0) if suffix else ""
    path_text = raw[:-len(span)] if span else raw
    text = path_text.replace("\\", "/")
    if text.startswith("skill://"):
        parts = text[8:].split("/")
        index = next((i for i, part in enumerate(parts[:2]) if re.fullmatch(r"zsspec-[a-z0-9-]+", part)), None)
        if index is None:
            raise GateError("Not a managed method URI")
        parts = parts[index:]
    else:
        candidate = Path(path_text)
        if not candidate.is_absolute():
            candidate = root / candidate
        base = next((base for base in [skills_root(root), *(root / rel for rel in ROOTS)]
                     if candidate.resolve().is_relative_to(base.resolve())), None)
        if base is None:
            return None  # An ordinary business document with a similar directory name.
        parts = list(candidate.resolve().relative_to(base.resolve()).parts)
    if not parts or not re.fullmatch(r"zsspec-[a-z0-9-]+", parts[0]) or any(p in ("", ".", "..") for p in parts):
        raise GateError("Invalid method resource path")
    if parts[0] in MAINTENANCE_SKILLS:
        raise GateError("zsspec-doctor is maintenance-only; use aidevflow doctor for business preflight")
    rel = PurePosixPath(*parts, *(["SKILL.md"] if len(parts) == 1 else []))
    target = (skills_root(root) / rel).resolve()
    if not target.is_relative_to(skills_root(root)) or not target.is_file():
        raise GateError(f"Method resource is missing from the pinned source: {rel}")
    state, _, _ = Gate(root)._load_active()
    if selected(root, state) != "omp":
        raise GateError("Resume this task in its bound runtime")
    if state.get("method_policy"):
        policy = read_json(root / state["method_policy"]["path"])
        expected = policy["package"]["files"].get(rel.as_posix())
        if expected is None or sha256_file(target) != expected:
            raise GateError("Method resource is unpinned or changed; preserve the task and inspect method integrity")
    return {"input": {**inp, "path": str(target) + span},
            "additionalContext": managed_context(state["current_stage"]),
            "source": {"path": str(target), "sha256": sha256_file(target)}}
