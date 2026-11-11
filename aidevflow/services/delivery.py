"""Staged knowledge closure, bundle-bound acceptance and recoverable publication."""
from __future__ import annotations

from ..locking import writes

import re
from pathlib import Path


def _inputs(gate, contract):
    from ..gate import sha256_file, GateError
    from ..managed import artifact_path
    values = {}
    for p in (gate.root / "specs").rglob("*"):
        if p.is_symlink():
            raise GateError("Archive inputs must be physical project files")
        if p.is_file():
            rel = p.relative_to(gate.root).as_posix()
            if not rel.startswith("specs/aidevflow-intake/") and not artifact_path(contract, rel):
                values[rel] = sha256_file(p)
    return values


@writes
def prepare(gate):
    from ..gate import GateError, read_json, write_json_new, write_bytes_new, sha256_bytes, sha256_file
    from ..method_adapters.zsspec_docs import enabled
    state, run, contract = gate._load_active()
    if not enabled(contract) or state["current_stage"] != "verify":
        raise GateError("Prepare delivery archive in Verify after current zsspec work completes")
    gate._check_build_ready(state, contract)
    from .execution import sync_plan
    sync_plan(gate, state, run, contract)
    # Closure must contain Gate's actual completion projection, never an unchecked draft.
    from ..method_adapters.zsspec_docs import parse_plan
    plan = gate.root / f"specs/active/{contract['method']['slug']}/plan.md"
    if not all(t["completed"] and t["observation"].strip() not in ("", "（待执行）", "（回填）") for t in parse_plan(plan.read_text(encoding="utf-8-sig"))):
        raise GateError("Archive requires completed task cards with recorded observations")
    inputs = _inputs(gate, contract)
    candidate = gate._candidate(state, contract)["sha256"]
    path = run / "archive-input.json"
    if path.exists():
        value = read_json(path)
        if value["inputs"] != inputs or value["candidate"] != candidate:
            raise GateError("Archive preparation is stale; reset-delivery-archive before preparing current work")
        return value
    stage = run / "archive-staging"
    if stage.is_symlink() or not stage.resolve().is_relative_to(run.resolve()):
        raise GateError("Archive staging must stay inside its run")
    for existing in stage.rglob("*"):
        if existing.is_symlink() or (existing.is_file() and existing.relative_to(stage).as_posix() not in inputs):
            raise GateError("Interrupted archive staging conflicts; reset-delivery-archive before preparing again")
    for rel, digest in inputs.items():
        content = (gate.root / rel).read_bytes()
        if sha256_bytes(content) != digest:
            raise GateError("Archive inputs changed during preparation; reset-delivery-archive and retry")
        destination = stage / rel
        if destination.exists():
            if not destination.is_file() or sha256_file(destination) != digest:
                raise GateError("Interrupted archive staging conflicts; reset-delivery-archive before preparing again")
        else:
            write_bytes_new(destination, content)
    if _inputs(gate, contract) != inputs or gate._candidate(state, contract)["sha256"] != candidate:
        raise GateError("Archive inputs changed during preparation; reset-delivery-archive and retry")
    value = {"schema": "aidevflow/archive-input-v1", "inputs": inputs, "candidate": candidate,
             "workspace": (run / "archive-staging").relative_to(gate.root).as_posix(), "slug": contract["method"]["slug"]}
    write_json_new(path, value)
    return value


@writes
def seal(gate):
    from ..gate import GateError, read_json, write_json_atomic, sha256_file, sha256_bytes, write_bytes_new
    state, run, contract = gate._load_active()
    gate._require_stage(state, "verify")
    if not (run / "archive-input.json").exists():
        raise GateError("prepare-delivery-archive first")
    value = read_json(run / "archive-input.json")
    candidate = gate._candidate(state, contract)
    if _inputs(gate, contract) != value["inputs"] or candidate["sha256"] != value["candidate"]:
        raise GateError("Archive inputs/candidate changed; reset and regenerate closure")
    stage = gate.root / value["workspace"]
    after = {}
    for p in stage.rglob("*"):
        if p.is_symlink():
            raise GateError("Archive staging cannot contain links")
        if p.is_file():
            after[p.relative_to(stage).as_posix()] = p
    slug = value["slug"]
    destinations = {p.split("/")[2] for p in after if re.match(r"^specs/archive/" + re.escape(slug) + r"_\d{4}-\d{2}-\d{2}/", p)}
    if len(destinations) != 1:
        raise GateError("Archive must have exactly one dated destination for this slug")
    destination = "specs/archive/" + destinations.pop()
    relocations = {f"specs/active/{slug}/{n}": f"{destination}/{n}" for n in ("spec.md", "plan.md")}
    for old, new in relocations.items():
        if old in after or new not in after or sha256_file(after[new]) != value["inputs"].get(old):
            raise GateError("Archive must relocate the exact completed spec/plan without changing content")
        if new in value["inputs"]:
            raise GateError("Archive destination already exists")
    index = after.get("specs/archive/INDEX.md")
    if not index or len(re.findall(r"\|\s*" + re.escape(slug) + r"\s*\|", index.read_text(encoding="utf-8"))) != 1:
        raise GateError("Archive INDEX needs exactly one row for this slug")
    changes = {}
    for rel in sorted(set(value["inputs"]) | set(after)):
        new_hash = sha256_file(after[rel]) if rel in after else None
        old_hash = value["inputs"].get(rel)
        if new_hash == old_hash:
            continue
        allowed = rel in relocations or rel in relocations.values() or rel in ("specs/MAP.md", "specs/archive/INDEX.md") or re.fullmatch(r"specs/modules/[\w-]+\.md", rel)
        if not allowed or (new_hash is None and rel not in relocations):
            raise GateError("Archive changed an unauthorized file: " + rel)
        entry = {"before": old_hash, "after": new_hash}
        for key, digest, content in (("before_object", old_hash, (gate.root / rel).read_bytes() if old_hash else None),
                                     ("after_object", new_hash, after[rel].read_bytes() if new_hash else None)):
            if digest:
                obj = run / "archive-objects" / digest
                if not obj.exists():
                    write_bytes_new(obj, content)
                entry[key] = obj.relative_to(gate.root).as_posix()
        changes[rel] = entry
    manifest = {"schema": "aidevflow/delivery-archive-v1", "candidate": candidate["sha256"], "changes": changes,
                "relocations": relocations, "inputs": value["inputs"], "candidate_before": {rel: candidate["files"][rel] for rel in changes if rel in candidate["files"]}}
    path = run / "delivery-archive.json"
    if state.get("delivery_archive") and read_json(path) == manifest:
        return state["delivery_archive"]
    write_json_atomic(path, manifest)
    state["delivery_archive"] = {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path), "committed": False}
    state["verifier_bundle"] = None
    state.pop("verdict_ref", None)
    gate._save(state, run, "delivery_archive_sealed")
    return state["delivery_archive"]


def manifest(gate, state):
    from ..gate import GateError, read_json, sha256_file
    ref = state.get("delivery_archive")
    if not ref or sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
        raise GateError("Seal the delivery archive before independent acceptance")
    value = read_json(gate._safe_repo_path(ref["path"]))
    for entry in value["changes"].values():
        for k in ("before", "after"):
            if entry[k] and sha256_file(gate._safe_repo_path(entry[k + "_object"])) != entry[k]:
                raise GateError("Archive snapshot changed")
    return value


def commit(gate, recover=False):
    from ..gate import GateError, read_json, sha256_file, write_json_atomic, replace_file_atomic
    from ..locking import writer_lock
    import tempfile, os
    with writer_lock(gate.control):
        state, run, contract = gate._load_active(allow_method_transaction=recover)
        value = manifest(gate, state)
        journal = run / "archive-transaction.json"
        if not recover:
            gate._check_verifier_passed(state, run, contract)
            if state["delivery_archive"]["committed"]:
                return state["delivery_archive"]
            write_json_atomic(journal, {"archive_sha256": state["delivery_archive"]["sha256"]})
        if not journal.exists() or read_json(journal)["archive_sha256"] != state["delivery_archive"]["sha256"]:
            raise GateError("No matching archive transaction to finish")
        for rel, expected in value["inputs"].items():
            if rel not in value["changes"] and (not gate._safe_repo_path(rel).is_file() or sha256_file(gate._safe_repo_path(rel)) != expected):
                raise GateError("Unchanged archive input was edited during acceptance: " + rel)
        for rel, entry in value["changes"].items():
            p = gate._safe_repo_path(rel)
            current = sha256_file(p) if p.is_file() else None
            if current not in (entry["before"], entry["after"]):
                raise GateError("Concurrent edit during archive; preserved for diagnosis: " + rel)
        for rel, entry in value["changes"].items():
            p = gate._safe_repo_path(rel)
            if (sha256_file(p) if p.is_file() else None) == entry["after"]:
                continue
            if entry["after"] is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=p.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(gate._safe_repo_path(entry["after_object"]).read_bytes())
                    replace_file_atomic(tmp, p)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
        state["delivery_archive"]["committed"] = True
        state["artifact_relocations"] = value["relocations"]
        gate._save(state, run, "delivery_archive_committed")
        journal.unlink()
        return state["delivery_archive"]


def normalize_candidate(gate, state, files):
    from ..gate import GateError, sha256_file
    if not state.get("delivery_archive", {}).get("committed"):
        return
    value = manifest(gate, state)
    for rel, entry in value["changes"].items():
        p = gate._safe_repo_path(rel)
        if (sha256_file(p) if p.is_file() else None) != entry["after"]:
            raise GateError("Committed delivery archive changed: " + rel)
        if rel in value["candidate_before"]:
            files[rel] = value["candidate_before"][rel]
        else:
            files.pop(rel, None)


@writes
def reset(gate):
    from ..gate import GateError
    import shutil
    state, run, _ = gate._load_active()
    if state.get("delivery_archive", {}).get("committed"):
        raise GateError("Committed closure cannot be discarded")
    # Explicit bounded targets, always inside this run.
    for name in ("archive-staging", "archive-objects"):
        path = (run / name).resolve()
        if not path.is_relative_to(run.resolve()):
            raise GateError("Archive staging escaped its run")
        if path.exists():
            shutil.rmtree(path)
    for name in ("archive-input.json", "delivery-archive.json"):
        (run / name).unlink(missing_ok=True)
    state.pop("delivery_archive", None)
    state["verifier_bundle"] = None
    state.pop("verdict_ref", None)
    gate._save(state, run, "delivery_archive_reset")
    return {"reset": True}
