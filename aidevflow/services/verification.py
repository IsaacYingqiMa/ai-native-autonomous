"""Machine-checked candidate identity and short, bundle-local evidence citations."""
from __future__ import annotations

import json


def index_bundle(gate, state, contract, directory, manifest, candidate):
    from ..gate import GateError, semantic_content, sha256_bytes, sha256_file, write_json_atomic
    bindings = []
    plan = gate._plan(state)
    for entry in manifest:
        rel = entry.get("source_path")
        if rel not in candidate["files"]:
            continue
        raw = (directory / entry["path"]).read_bytes()
        semantic = sha256_bytes(semantic_content(rel, raw, plan))
        if semantic != candidate["files"][rel]:
            raise GateError("Frozen object does not match the tested candidate: " + rel)
        bindings.append({"source_path": rel, "object_path": entry["path"], "raw_sha256": entry["sha256"],
                         "candidate_sha256": semantic, "normalization": "zsspec-plan-v1" if rel.endswith("/plan.md") else "lf-v1"})
    value = {"schema": "aidevflow/candidate-binding-v1", "candidate_sha256": candidate["sha256"],
             "explanation": "Manifest sha256 hashes physical bytes. candidate.files hashes LF-normalized content; zsspec plans also exclude Gate-owned progress slots. Compare using these verified bindings, never raw hash equality.",
             "bindings": bindings}
    path = directory / "candidate-binding.json"
    write_json_atomic(path, value)
    manifest.append({"path": path.name, "sha256": sha256_file(path)})
    summary = directory / "verification-summary.json"
    write_json_atomic(summary, {"schema": "aidevflow/verification-summary-v1", "candidate_sha256": candidate["sha256"],
        "commands": [{k: r.get(k) for k in ("id", "suite", "target", "exit_code", "passed", "test_counts", "candidate_sha256", "red_subject")}
                     for r in state["test_runs"]],
        "runtime_checks": [{"id": c["id"], "result": c["result"], "candidate_sha256": c["candidate_sha256"],
                            "observations": c.get("observations", []), "source": c["source"]} for c in state.get("check_results", [])]})
    manifest.append({"path": summary.name, "sha256": sha256_file(summary)})
    # Multiple logical sources can share an object. Give that physical object one ID.
    ids = {}
    for entry in manifest:
        ids.setdefault(entry["path"], f"E{len(ids)+1:04d}")
        entry["id"] = ids[entry["path"]]
    return ids


def citation_paths(meta):
    return {**{e["path"]: e["path"] for e in meta["files"]},
            **{e["id"]: e["path"] for e in meta["files"] if e.get("id")}}


def correction(gate, state, run, verdict, path, errors):
    """Preserve rejected native output; a fresh isolated reviewer fixes format only."""
    from ..gate import sha256_file
    ref = {"path": path, "sha256": sha256_file(gate._safe_repo_path(path)), "bundle_sha256": state["verifier_bundle"]["sha256"], "errors": errors}
    state["verifier_output_correction"] = ref
    gate._save(state, run, "verifier_output_rejected", errors=errors)


def correction_prompt(gate, state):
    from ..gate import GateError, read_json, sha256_file
    ref = state.get("verifier_output_correction")
    if not ref or ref["bundle_sha256"] != state["verifier_bundle"]["sha256"]:
        return ""
    if sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
        raise GateError("Rejected independent output changed")
    value = read_json(gate._safe_repo_path(ref["path"]))
    return (" This is a citation/format correction of a completed independent evaluation, not a new full audit. "
            "The following JSON is the immutable previous independent role output, not writer commentary. "
            "Preserve its item IDs, verdicts, reasons and risks; repair only evidence citations using exact short E IDs from BUNDLE.json. "
            "If an existing conclusion cannot be supported, return a full fresh evaluation instead of inventing evidence. "
            "Validation errors: " + json.dumps(ref["errors"]) + ". Previous independent output: " + json.dumps(value, ensure_ascii=False))
