"""Opt-in zsspec managed profile. Legacy runs keep their original rules."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

PROFILE = "zsspec-managed-v1"
DESIGN_SKILLS = ["zsspec-req", "zsspec-des", "zsspec-task", "zsspec-chk", "zsspec-verify"]
DEFAULTS = {"paths": {"skills_root": "auto"}, "test": {"e2e": "enabled", "manual": "enabled"},
            "parallel": {"mode": "auto", "max": "3"}, "gate": {"mode": "strict"}}


def enabled(contract: dict[str, Any] | None) -> bool:
    method = contract.get("method") if isinstance(contract, dict) else None
    return isinstance(method, dict) and method.get("profile") == PROFILE


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def package_manifest(root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint actual method instructions, references and templates, not telemetry."""
    base = (root / ".claude/skills").resolve()
    names = {name for values in contract.get("method", {}).get("stages", {}).values() for name in values}
    names.update(("zsspec-standard", "zsspec-change"))
    files: dict[str, str | None] = {}
    for name in sorted(names):
        directory = base / name
        # SKILL.md is always present in the manifest, even if missing on disk.
        files[f"{name}/SKILL.md"] = file_hash(directory / "SKILL.md")
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix.lower() in (".md", ".json", ".yml", ".yaml"):
                    path.resolve().relative_to(base)
                    files[path.relative_to(base).as_posix()] = file_hash(path)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"sha256": digest, "files": files}


def read_policy(root: Path) -> dict[str, Any]:
    """Read the documented two-level scalar YAML subset without adding a runtime dependency.

    Unsupported YAML is rejected explicitly; it is never partially interpreted.
    Unknown simple keys are preserved but do not weaken the effective policy.
    """
    from .gate import GateError
    path = root / "specs/zsspec.yml"
    values = {section: dict(items) for section, items in DEFAULTS.items()}
    section = None
    seen: set[tuple[str, str]] = set()
    if path.is_file():
        for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.split(" #", 1)[0].rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = re.fullmatch(r"([A-Za-z_][\w-]*):\s*", line)
            if match:
                section = match[1]
                if (section, "") in seen:
                    raise GateError(f"Duplicate zsspec configuration section: {section}")
                seen.add((section, ""))
                values.setdefault(section, {})
                continue
            match = re.fullmatch(r"  ([A-Za-z_][\w-]*):\s*(.+)", line)
            if not match or section is None:
                raise GateError(f"Unsupported zsspec.yml syntax on line {number}; managed v1 accepts two-level scalar mappings")
            key, value = match.groups()
            if (section, key) in seen:
                raise GateError(f"Duplicate zsspec configuration key: {section}.{key}")
            seen.add((section, key))
            if value[:1] in ("'", '"') and value[-1:] == value[:1]:
                value = value[1:-1]
            elif not re.fullmatch(r"[A-Za-z0-9_./\\:-]+", value):
                raise GateError(f"Unsupported zsspec.yml scalar on line {number}")
            values[section][key] = value
    if values["paths"]["skills_root"].replace("\\", "/").rstrip("/") not in ("auto", ".claude/skills", "./.claude/skills"):
        raise GateError("Managed v1 requires project-local .claude/skills; global/CodeBuddy/custom skill roots are not yet supported")
    if values["gate"]["mode"] != "strict":
        raise GateError("Managed v1 requires gate.mode=strict; quick cannot weaken the pinned acceptance contract")
    for key in ("e2e", "manual"):
        if values["test"][key] not in ("enabled", "disabled"):
            raise GateError(f"Invalid zsspec.yml test.{key}; use enabled or disabled")
    if values["parallel"]["mode"] not in ("auto", "off") or not values["parallel"]["max"].isdigit() or int(values["parallel"]["max"]) < 1:
        raise GateError("Invalid zsspec.yml parallel configuration")
    return {"schema": "aidevflow/method-policy-v1", "profile": PROFILE,
            "config_path": "specs/zsspec.yml", "config_sha256": file_hash(path), "configured": values,
            "effective": {"skills_root": ".claude/skills", "gate": "strict", "parallel": "off",
                          "review": "ai_coordinator", "tests": "design_frozen", **values["test"]}}


def artifact_path(contract: dict[str, Any], relative: str) -> bool:
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix in contract.get("evidence_paths", []))


def validation_errors(contract: dict[str, Any]) -> list[str]:
    """Validate profile-specific rules without changing contracts that lack a profile."""
    method = contract.get("method", {})
    if not isinstance(method, dict) or "profile" not in method:
        return []
    if method["profile"] != PROFILE:
        return [f"Unknown method profile; supported: {PROFILE}"]
    errors = []
    if method.get("name") != "zsspec":
        errors.append("zsspec-managed-v1 requires method.name=zsspec")
    stages = method.get("stages", {})
    minimum = {"design": DESIGN_SKILLS + ["zsspec-test"], "build": ["zsspec-apply"],
               "review": ["zsspec-test", "zsspec-done"]}
    if not isinstance(stages, dict) or set(stages) - set(minimum) or any(
            not isinstance(stages.get(phase), list) or any(name not in stages[phase] for name in names)
            for phase, names in minimum.items()):
        errors.append("Managed v1 requires req/des/task/chk/verify/test Design, apply Build, and test/done Review (additional skills are allowed)")
    if contract.get("work_plan_required") is not True or contract.get("verifier", {}).get("required", True) is not True:
        errors.append("Managed v1 requires a work plan and independent verification")
    prefixes = contract.get("evidence_paths")
    if not isinstance(prefixes, list) or not prefixes:
        return errors + ["Managed v1 requires evidence_paths (dedicated runtime artifact directories)"]
    for prefix in prefixes:
        if not isinstance(prefix, str):
            errors.append("evidence_paths must contain relative directory names")
            continue
        path = PurePosixPath(prefix)
        if (not prefix or "\\" in prefix or path.is_absolute() or PureWindowsPath(prefix).drive
                or ".." in path.parts or any(c in prefix for c in "*?[]")
                or prefix.rstrip("/") != prefix or path.as_posix() != prefix or not path.parts or ":" in prefix
                or any(part.startswith(".") for part in path.parts) or bool(path.suffix)
                or path.parts[0] in (".aidevflow", ".claude", ".git", "aidevflow", "scripts", "evals")
                or prefix.startswith("specs/aidevflow-intake")):
            errors.append(f"Invalid evidence directory: {prefix}")
    if errors:
        return errors
    if any(not isinstance(contract.get(key, []), list) for key in ("code_paths", "test_files", "test_support_files", "design_artifacts", "required_checks")):
        return errors + ["Managed paths and checks must be arrays"]
    from .gate import _glob_match
    for prefix in prefixes:
        if any(_glob_match(prefix, pattern) or _glob_match(prefix + "/artifact.json", pattern)
               or pattern.startswith(prefix + "/") for pattern in contract.get("code_paths", [])):
            errors.append(f"Evidence directory overlaps product paths: {prefix}")
        if any(artifact_path(contract, rel) for rel in contract.get("test_files", []) + contract.get("test_support_files", []) + contract.get("design_artifacts", [])):
            errors.append("Evidence directories must not contain frozen specifications or tests")
    checks = contract.get("required_checks", [])
    if not any(isinstance(check, dict) and check.get("kind") == "static" for check in checks):
        errors.append("Managed v1 requires a static review check, even when CHK has no static items")
    acceptance_ids = {item["id"] for item in contract.get("acceptance_items", [])
                      if isinstance(item, dict) and isinstance(item.get("id"), str)}
    covered = {ref for check in checks if isinstance(check, dict) and isinstance(check.get("acceptance_items"), list)
               for ref in check["acceptance_items"] if isinstance(ref, str)}
    if acceptance_ids - covered:
        errors.append("Managed required_checks must cover every acceptance item")
    return errors
