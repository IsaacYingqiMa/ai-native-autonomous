"""Executed test summaries and isolated Red replay. No model-authored pass records."""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
import tarfile
from pathlib import Path
from xml.etree import ElementTree

from .gate import GateError, read_json, sha256_file, write_json_new
from .recovery import baseline_id, owned_json


def snapshot_implementation(gate, state, run):
    """Pin the actual starting worktree, including a previous uncommitted delivery."""
    from .gate import write_bytes_new
    manifest = run / "implementation-baseline.json"
    if manifest.exists():
        value = read_json(manifest)
        for ref in value["files"].values():
            if sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
                raise GateError("Implementation baseline object changed")
        return {"path": manifest.relative_to(gate.root).as_posix(), "sha256": sha256_file(manifest)}
    names = set(gate._git("ls-files", "-z").stdout.split("\0")) | set(gate._git("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0"))
    installed = read_json(gate.control / "runtime-manifest.json").get("files", {}) if (gate.control / "runtime-manifest.json").is_file() else {}
    runtime_homes = []
    for key in ("HOME", "USERPROFILE", "PI_CODING_AGENT_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        if os.environ.get(key):
            home = Path(os.environ[key]).resolve()
            if home != gate.root and home.is_relative_to(gate.root):
                runtime_homes.append(home)
    files = {}
    for rel in sorted(names - {""}):
        parts = Path(rel).parts
        if parts[0] in (".aidevflow", ".claude", ".omp", ".zscode", "specs", "verification", "test-results") or any(x in ("__pycache__", ".pytest_cache", ".test-tmp", "node_modules", ".venv") for x in parts) or rel in installed:
            continue
        p = gate._safe_repo_path(rel)
        if any(p.is_relative_to(home) for home in runtime_homes):
            continue  # Native sessions/configuration are never implementation inputs.
        if not p.is_file():
            continue
        if (gate.root / rel).is_symlink():
            raise GateError("Implementation baseline cannot contain symlinks")
        content = p.read_bytes()
        from .gate import sha256_bytes
        digest = sha256_bytes(content)
        obj = run / "implementation-objects" / digest
        if not obj.exists():
            write_bytes_new(obj, content)
        files[rel] = {"path": obj.relative_to(gate.root).as_posix(), "sha256": digest}
    write_json_new(manifest, {"schema": "aidevflow/implementation-baseline-v1", "base_commit": state["base_commit"], "files": files})
    return {"path": manifest.relative_to(gate.root).as_posix(), "sha256": sha256_file(manifest)}


def replay(gate, state: dict, contract: dict, argv: list[str]) -> tuple[Path, list[str]]:
    """Materialize the original Git implementation, overlay the frozen test/spec baseline."""
    from .method_adapters.zsspec_docs import enabled as current_documents
    task_baseline = current_documents(contract) and bool(state.get("frozen_tests"))
    if not state.get("baseline_ref") and not task_baseline:
        raise GateError("Isolated Red replay requires a versioned baseline; create a reviewed baseline revision first")
    run = gate.runs / state["task_id"]
    if task_baseline:
        baseline = {"files": {rel: {"path": (run / "task-test-baselines" / digest).relative_to(gate.root).as_posix(), "sha256": digest}
                              for rel, digest in state["frozen_tests"].items()}}
    else:
        baseline = owned_json(gate, run, state["baseline_ref"], "baselines")
    destination = gate.control / "maintenance" / "red-replays" / secrets.token_hex(12)
    destination.mkdir(parents=True)
    if state.get("implementation_baseline"):
        ref = state["implementation_baseline"]
        if sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
            raise GateError("Implementation baseline manifest changed")
        original = read_json(gate._safe_repo_path(ref["path"]))
        for rel, ref in original["files"].items():
            source = gate._safe_repo_path(ref["path"])
            target = (destination / rel).resolve()
            if not source.is_relative_to(run / "implementation-objects") or sha256_file(source) != ref["sha256"] or not target.is_relative_to(destination):
                raise GateError("Implementation replay snapshot is damaged or unsafe")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    else:
        # Git emits a global PAX header even for an empty tree; Python's tar reader
        # cannot consume that header without a following member. Empty is valid.
        tree = gate._git("ls-tree", "-r", "--name-only", state["base_commit"])
        if tree.stdout.strip():
            result = subprocess.run(["git", "archive", "--format=tar", state["base_commit"]], cwd=gate.root, capture_output=True, check=False)
            if result.returncode:
                raise GateError("Cannot materialize the original implementation commit for Red replay")
            try:
                with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                    members = archive.getmembers()
                    for member in members:
                        path = (destination / member.name).resolve()
                        if not path.is_relative_to(destination) or not (member.isfile() or member.isdir()):
                            raise GateError("Red archive contains an unsafe path/link; use an explicit reviewed test setup")
                    archive.extractall(destination, members=members, filter="data")
            except tarfile.ReadError as exc:
                raise GateError("Git baseline archive cannot be decoded; preserve the baseline and repair replay setup") from exc
    for rel, ref in baseline["files"].items():
        source = gate._safe_repo_path(ref["path"])
        if sha256_file(source) != ref["sha256"]:
            raise GateError("Frozen replay object changed")
        target = (destination / rel).resolve()
        if not target.is_relative_to(destination):
            raise GateError("Frozen replay path escapes its workspace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    translated = []
    for arg in argv:
        if str(gate.root) in arg:
            if not arg.startswith(str(gate.root)):
                raise GateError("Embedded live-project paths are unsupported in isolated Red; declare a portable argv")
            arg = str(destination) + arg[len(str(gate.root)):]
        translated.append(arg)
    return destination, translated


def prepare_report(gate, state: dict, suite: str, argv: list[str], cwd: Path) -> tuple[list[str], dict | None]:
    if suite == "red":
        return argv, None
    # Explicit report mode is configured by Contract; ordinary pytest commands are captured automatically.
    contract = read_json(gate._safe_repo_path(state["contract_path"]))
    policy = contract.get("test_policy", {})
    pytest = any(arg == "pytest" or Path(arg).stem.lower() == "pytest" for arg in argv[:4])
    mode = policy.get("report_format")
    if mode not in (None, "junit", "summary"):
        raise GateError("test_policy.report_format must be junit or summary")
    if not pytest and mode is None:
        return argv, None
    run = gate.runs / state["task_id"]
    report = run / "test-reports" / (secrets.token_hex(16) + (".xml" if pytest or mode == "junit" else ".json"))
    report.parent.mkdir(parents=True, exist_ok=True)
    if pytest:
        if any(arg.startswith(("--junitxml", "--junit-xml")) for arg in argv):
            raise GateError("Gate owns pytest JUnit output; remove --junitxml from the argv")
        argv = [*argv, "--junitxml=" + str(report)]
    return argv, {"path": str(report), "format": "junit" if pytest or mode == "junit" else "summary"}


def parse_test_counts(output: str) -> dict | None:
    counts = {m[1]: int(m[2]) for m in re.finditer(r"^[#ℹ]\s*(tests|pass|fail|cancelled|skipped|todo)\s+(\d+)\s*$", output, re.M)}
    return counts if {"tests", "pass", "fail", "skipped"} <= set(counts) else None


def summary(report: dict | None, output: str) -> dict | None:
    if report is None:
        counts = parse_test_counts(output)
        if counts is not None:
            return {"ok": counts["tests"] > 0 and counts["pass"] == counts["tests"] and
                    not any(counts.get(k, 0) for k in ("fail", "cancelled", "skipped", "todo")), "cases": [], "counts": counts}
        # Legacy/custom runners: detect visible coverage gaps, never treat their skip summary as a clean pass.
        if re.search(r"\b[1-9]\d*\s+(?:skipped|xfailed|deselected)\b", output, re.I):
            return {"ok": False, "reason": "unreported_skips", "cases": []}
        return None
    path = Path(report["path"])
    if not path.is_file() or not path.stat().st_size or path.stat().st_size > 16 * 1024 * 1024:
        return {"ok": False, "reason": "missing_or_oversized_test_report", "cases": []}
    try:
        if report["format"] == "junit":
            raw = path.read_bytes()
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise ValueError("DTD is not accepted")
            tree = ElementTree.fromstring(raw)
            cases = []
            for case in tree.iter("testcase"):
                identity = case.get("file") or case.get("classname", "")
                name = case.get("name", "")
                status = "skipped" if case.find("skipped") is not None else "failed" if case.find("failure") is not None or case.find("error") is not None else "passed"
                cases.append({"id": identity + "::" + name, "name": name, "status": status})
        else:
            data = read_json(path)
            if not isinstance(data, dict) or data.get("schema") != "aidevflow/test-summary-v1":
                raise ValueError("Unknown report schema")
            cases = data["cases"]
        if (not isinstance(cases, list) or not cases or any(not isinstance(case, dict) or
                not isinstance(case.get("id"), str) or not case["id"].strip() or case.get("status") not in ("passed", "failed", "skipped", "xfail", "deselected") for case in cases)
                or len({case["id"] for case in cases}) != len(cases)):
            raise ValueError("Invalid or empty case list")
        deselected = bool(re.search(r"\b[1-9]\d*\s+deselected\b", output, re.I))
        return {"ok": all(case["status"] == "passed" for case in cases) and not deselected, "cases": cases,
                "unidentified_deselections": deselected,
                "reason": None if all(case["status"] == "passed" for case in cases) else "required_cases_not_executed_or_failed"}
    except (ValueError, KeyError, TypeError, UnicodeError, ElementTree.ParseError, GateError):
        return {"ok": False, "reason": "invalid_test_report", "cases": []}


def waive(gate, suite: str, target: str | None, cases: list[str], acceptance: list[str], reason: str, by: str, utterance: str) -> dict:
    state, run, contract = gate._load_active()
    if not contract or state["current_stage"] not in ("design", "build", "verify") or not all(value.strip() for value in (reason, by, utterance)):
        raise GateError("Waiver requires an open task, specific cases, reason and actual authorization")
    gate._require_executable(state)
    if target is not None and target not in contract.get("test_files", []) + [check.get("target") for check in contract.get("required_checks", [])]:
        raise GateError("Waiver target must belong to the Contract")
    if not all(isinstance(case, str) and case.strip() for case in cases):
        raise GateError("Waiver case IDs must be non-empty strings")
    if not cases or not acceptance or set(acceptance) - {item["id"] for item in contract["acceptance_items"]} or suite == "red" or suite not in contract["test_commands"]:
        raise GateError("Waiver cases and acceptance IDs must belong to the Contract")
    value = {"schema": "aidevflow/test-waiver-v1", "id": secrets.token_hex(16), "suite": suite, "target": target,
             "cases": sorted(set(cases)), "acceptance_items": sorted(set(acceptance)), "reason": reason,
             "baseline_id": baseline_id(state), "contract_sha256": state["contract_sha256"],
             "authorization": {"by": by, "utterance": utterance, "source": "operator_assertion"}}
    path = run / "test-waivers" / f"{value['id']}.json"
    write_json_new(path, value)
    state.setdefault("test_waivers", []).append({"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)})
    state["verifier_bundle"] = None
    state.pop("verdict_ref", None)
    state.pop("accepted_with_exceptions", None)
    state.pop("waived_acceptance_items", None)
    if state.get("status") == "accepted":
        state["status"] = "active"
    gate._save(state, run, "test_waiver_recorded", waiver_id=value["id"], authorization=value["authorization"])
    return value


def apply_waivers(gate, state: dict, contract: dict, suite: str, target: str | None, coverage: dict | None) -> dict | None:
    if coverage is None:
        return None
    coverage = dict(coverage)
    run = gate.runs / state["task_id"]
    accepted = {}
    for ref in state.get("test_waivers", []):
        value = owned_json(gate, run, ref, "test-waivers")
        if value["baseline_id"] == baseline_id(state) and value["contract_sha256"] == state["contract_sha256"] and (value["suite"], value["target"]) == (suite, target):
            for name in value["cases"]:
                accepted[name] = value["id"]
    declared_cases = contract.get("test_policy", {}).get("required_cases", {})
    required = declared_cases.get(suite + (":" + target if target else ""), declared_cases.get(suite, []))
    observed = {case["id"] for case in coverage["cases"]}
    coverage["missing_cases"] = sorted(set(required) - observed)
    omitted = [case for case in coverage["cases"] if case["status"] in ("skipped", "xfail", "deselected")]
    coverage["waiver_ids"] = sorted({accepted[case["id"]] for case in omitted if case["id"] in accepted})
    allowed = contract.get("test_policy", {}).get("allow_approved_waivers") is True
    coverage["accepted_with_exceptions"] = bool(allowed and omitted and not coverage.get("unidentified_deselections") and not coverage["missing_cases"] and
        all(case["status"] == "passed" or case["id"] in accepted for case in coverage["cases"]) and
        all(case["status"] != "failed" for case in coverage["cases"]))
    if coverage["missing_cases"]:
        coverage["ok"] = False
    return coverage


def used_waivers(gate, state: dict) -> dict[str, dict]:
    run = gate.runs / state["task_id"]
    from .recovery import effective_records
    latest = {(item["suite"], item.get("target")): item for item in effective_records(gate, state, run)}
    used = {identity for item in latest.values() if item.get("accepted_with_exceptions")
            and item.get("candidate_sha256") == (state.get("verifier_bundle") or {}).get("candidate_sha256")
            for identity in item.get("coverage", {}).get("waiver_ids", [])}
    return {ref["path"]: waiver for ref in state.get("test_waivers", [])
            for waiver in [owned_json(gate, run, ref, "test-waivers")] if waiver["id"] in used}


def waived_acceptance(gate, state: dict) -> set[str]:
    return {acceptance for waiver in used_waivers(gate, state).values() for acceptance in waiver["acceptance_items"]}
