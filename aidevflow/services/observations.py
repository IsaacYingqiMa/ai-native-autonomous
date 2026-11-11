"""Explicit approved-scenario observations for AI-assisted runtime checks."""
from __future__ import annotations


def expected(gate, state, contract, check):
    from ..gate import read_json
    ids = set(check["acceptance_items"])
    if state.get("requirements_ref"):
        from ..discovery import verify_reference
        manifest = verify_reference(gate.root, state["requirements_ref"])
        requirements = read_json(gate._safe_repo_path(manifest["requirements_path"]))
        return [{"id": s["id"], "acceptance_ids": sorted(ids & set(s["acceptance_ids"])),
                 "given": s["given"], "when": s["when"], "expected": s["then"]}
                for s in requirements["scenarios"] if ids & set(s["acceptance_ids"])]
    return [{"id": a["id"], "acceptance_ids": [a["id"]], "expected": a["criterion"]}
            for a in contract["acceptance_items"] if a["id"] in ids]


def context(gate, check_id):
    from ..gate import GateError
    state, _, contract = gate._load_active()
    if not contract:
        raise GateError("Prepare the execution Contract before requesting runtime scenarios")
    check = next((c for c in contract["required_checks"] if c["id"] == check_id), None)
    if not check or check.get("kind") != "manual":
        raise GateError("check-context requires a declared AI-assisted runtime check")
    return {"schema": "aidevflow/check-observations-v1", "check_id": check_id,
            "candidate_sha256": gate._candidate(state, contract)["sha256"],
            "observations": [{**s, "result": "unverified", "observed": "", "evidence": []}
                             for s in expected(gate, state, contract, check)]}


def validate(gate, state, contract, check, path, candidate, evidence):
    from ..gate import GateError, read_json
    from ..managed import artifact_path
    if not path or not artifact_path(contract, path):
        raise GateError("Passed runtime checks require --observations under evidence_paths; use check-context --id to obtain approved scenarios")
    value = read_json(gate._safe_repo_path(path))
    if not isinstance(value, dict) or value.get("schema") != "aidevflow/check-observations-v1" or value.get("check_id") != check["id"] or value.get("candidate_sha256") != candidate:
        raise GateError("Runtime observations do not identify this check/current candidate")
    required = {s["id"]: s for s in expected(gate, state, contract, check)}
    rows = value.get("observations")
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows):
        raise GateError("Runtime observations need approved scenario IDs")
    if len(rows) != len(required) or {r["id"] for r in rows} != set(required):
        raise GateError("Runtime observations must cover every approved scenario for this check")
    normalized = []
    for row in rows:
        if row.get("result") != "passed" or not isinstance(row.get("observed"), str) or len(row["observed"].strip()) < 8:
            raise GateError("Unverified/failed scenarios cannot become a passed check; record actual observed results")
        refs = row.get("evidence")
        if not isinstance(refs, list) or not refs or any(not isinstance(p, str) or p not in evidence for p in refs):
            raise GateError("Each scenario must cite captured evidence supplied to record-check")
        source = required[row["id"]]
        if "expected" in row and row["expected"] != source["expected"]:
            raise GateError("Runtime checks cannot rewrite the approved expected behavior")
        normalized.append({**source, "result": row["result"], "observed": row["observed"], "evidence": refs})
    return normalized
