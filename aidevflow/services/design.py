"""Approved requirements -> reviewed specification/plan -> immutable execution binding."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..locking import writes
from ..method_adapters.zsspec_docs import PROFILE, parse_spec, parse_plan


def _ref(gate, path):
    from ..gate import sha256_file
    return {"path": path.relative_to(gate.root).as_posix(), "sha256": sha256_file(path)}


def preparation(gate, state):
    from ..gate import GateError, read_json, sha256_file
    ref = state.get("method_preparation")
    if not ref:
        raise GateError("Run prepare-method --slug <change> after requirements approval")
    p = gate._safe_repo_path(ref["path"])
    if sha256_file(p) != ref["sha256"]:
        raise GateError("Method preparation changed")
    value = read_json(p)
    if state.get("engineering_revision"):
        value = dict(value)
        files = state["engineering_revision"]["files"]
        value["spec"], value["plan"] = files[value["spec"]], files[value["plan"]]
    return value


@writes
def prepare_method(gate, slug):
    from ..gate import GateError, write_json_new, sha256_file, read_json
    from ..method_resolution import discover
    state, run, contract = gate._load_active()
    gate._check_intent_approved(state)
    from ..runtimes import selected
    from ..runtimes.registry import get_runtime
    if not get_runtime(selected(gate.root, state)).resource_redirection:
        raise GateError("Current zsspec delivery requires a verified method-resource runtime adapter")
    if contract or state["current_stage"] != "intent":
        raise GateError("Method preparation belongs before execution Contract registration")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise GateError("Use a kebab-case change slug")
    if state.get("method_preparation"):
        value = preparation(gate, state)
        if value["slug"] != slug:
            raise GateError("The current change slug is fixed")
        return value
    if (gate.root / "specs/active" / slug).exists():
        raise GateError("Change slug already exists; do not adopt unrelated work")
    resolved = discover(gate.root)
    if resolved["profile"] != PROFILE:
        raise GateError("New delivery supports the current five-skill zsspec package only")
    # Pin all instructions before their first invocation, including initialization.
    source = gate.root / resolved["skills_root"]
    for rel, digest in resolved["package"]["files"].items():
        content = (source / rel).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise GateError("Method source changed during preparation")
        from ..gate import write_bytes_new
        destination = run / "method-source" / rel
        if destination.exists():
            if sha256_file(destination) != digest:
                raise GateError("Interrupted method snapshot conflicts with the selected package")
        else:
            write_bytes_new(destination, content)
    ready = run / "method-source-ready.json"
    if ready.exists():
        if read_json(ready) != resolved["package"]:
            raise GateError("Prepared package marker conflicts with selected source")
    else:
        write_json_new(ready, resolved["package"])
    value = {"schema": "aidevflow/method-preparation-v1", "profile": PROFILE, "slug": slug,
             "spec": f"specs/active/{slug}/spec.md", "plan": f"specs/active/{slug}/plan.md",
             "source_path": (run / "method-source").relative_to(gate.root).as_posix(),
             "package": resolved["package"], "requirements_ref": state.get("requirements_ref"),
             "intent_sha256": state["intent_sha256"], "automatic": True}
    from ..test_execution import snapshot_implementation
    state["implementation_baseline"] = snapshot_implementation(gate, state, run)
    path = run / "method-preparation.json"
    if path.exists():
        if read_json(path) != value:
            raise GateError("Interrupted method preparation conflicts with selected inputs")
    else:
        write_json_new(path, value)
    state["method_preparation"] = _ref(gate, path)
    gate._save(state, run, "method_prepared", slug=slug)
    return value


@writes
def record_initialization(gate):
    from ..gate import GateError, sha256_file
    state, run, _ = gate._load_active()
    preparation(gate, state)
    required = ["specs/MAP.md", "specs/archive/INDEX.md"]
    if any(not gate._safe_repo_path(p).is_file() or not gate._safe_repo_path(p).stat().st_size for p in required):
        raise GateError("Initialize the real MAP and archive INDEX before generating specifications")
    state["knowledge_initialized"] = {p: sha256_file(gate._safe_repo_path(p)) for p in required}
    gate._save(state, run, "knowledge_initialized")
    return state["knowledge_initialized"]


def subjects(gate, state, kind):
    prep = preparation(gate, state)
    from ..gate import sha256_bytes
    from ..method_adapters.zsspec_docs import ZsspecDocsAdapter
    if kind == "blind":
        return {state["intent_path"]: state["intent_sha256"], state["request_snapshot_path"]: state["request_sha256"]}
    rels = [prep["spec"]] + ([prep["plan"]] if kind == "plan" else [])
    result = {rel: sha256_bytes(ZsspecDocsAdapter().semantic_content(rel, gate._artifact_path(state, rel).read_bytes(), None)) for rel in rels}
    if state.get("engineering_revision", {}).get("contract_draft"):
        relative = state["engineering_revision"]["contract_draft"]
        result[relative] = sha256_bytes(gate._safe_repo_path(relative).read_bytes())
    elif state.get("engineering_contract_subject"):
        ref = state["engineering_contract_subject"]
        result[ref["path"]] = sha256_bytes(gate._safe_repo_path(ref["path"]).read_bytes())
    return result


def validate_artifacts(gate, state, kind):
    from ..gate import GateError
    prep = preparation(gate, state)
    spec = parse_spec(gate._safe_repo_path(prep["spec"]).read_text(encoding="utf-8-sig"))
    if kind == "plan":
        tasks = parse_plan(gate._safe_repo_path(prep["plan"]).read_text(encoding="utf-8-sig"))
        refs = {t["id"].split("-")[0] for t in tasks if t["id"].startswith("S")}
        if refs != set(spec):
            raise GateError("Every specification needs task coverage, without invented S IDs")
        if any(t["completed"] for t in tasks):
            raise GateError("A new execution plan cannot claim work already completed")
    return spec


@writes
def prepare_review(gate, kind):
    from ..gate import GateError, read_json, write_json_atomic
    if kind not in ("spec", "plan", "blind"):
        raise GateError("Review kind must be spec or plan")
    state, run, contract = gate._load_active()
    if contract and not state.get("engineering_revision"):
        raise GateError("Use begin-engineering-revision after execution binding")
    gate._check_intent_approved(state)
    if not state.get("knowledge_initialized"):
        raise GateError("record-initialization before artifact generation/review")
    if kind != "blind":
        parsed = validate_artifacts(gate, state, kind)
        if kind == "spec" and len(parsed) > 5:
            try:
                require_review(gate, state, "blind")
            except (GateError, OSError):
                kind = "blind"
    if kind == "plan":
        require_review(gate, state, "spec")
    prep = preparation(gate, state)
    value = {"schema": "aidevflow/artifact-review-input-v1", "task_id": state["task_id"], "kind": kind,
             "subjects": subjects(gate, state, kind), "intent_path": state["intent_path"],
             "intent_sha256": state["intent_sha256"], "requirements_ref": state.get("requirements_ref"),
             "request_path": state["request_snapshot_path"], "specification_ids": [] if kind == "blind" else list(validate_artifacts(gate, state, kind)),
             "previous_findings": state.get("artifact_findings", {}).get(kind, []),
             "acceptance_ids": approved_acceptance(gate, state), "slug": prep["slug"],
             "blind_probe": state.get("artifact_reviews", {}).get("blind") if kind != "blind" else None}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    path = run / "artifact-review-inputs" / (digest + ".json")
    if not path.exists():
        write_json_atomic(path, value)
    elif read_json(path) != value:
        raise GateError("Immutable artifact review request changed")
    ref = _ref(gate, path)
    if state.get("artifact_review_target") != ref:
        state["artifact_review_target"] = ref
        gate._save(state, run, "artifact_review_prepared", kind=kind)
    return {**ref, "kind": kind}


def approved_acceptance(gate, state):
    from ..gate import read_json
    if state.get("requirements_ref"):
        from ..discovery import verify_reference
        manifest = verify_reference(gate.root, state["requirements_ref"])
        req = read_json(gate._safe_repo_path(manifest["requirements_path"]))
        return sorted({i for r in req["requirements"] for i in r["acceptance_ids"]})
    return []


@writes
def record_review(gate, path):
    from ..gate import GateError, read_json, write_json_new, sha256_file
    state, run, contract = gate._load_active()
    if contract and not state.get("engineering_revision"):
        raise GateError("Execution binding is already fixed")
    target = state.get("artifact_review_target")
    if not target:
        raise GateError("prepare-artifact-review must precede dispatch")
    if sha256_file(gate._safe_repo_path(target["path"])) != target["sha256"]:
        raise GateError("Artifact review request changed after dispatch")
    request = read_json(gate._safe_repo_path(target["path"]))
    value = read_json(gate._safe_repo_path(path))
    if (not isinstance(value, dict) or set(value) != {"schema", "task_id", "kind", "input_sha256", "outcome", "coverage", "findings"}
            or value.get("schema") != "aidevflow/artifact-review-v1" or value.get("task_id") != state["task_id"]
            or value.get("kind") != request["kind"] or value.get("input_sha256") != target["sha256"]
            or value.get("outcome") not in ("pass", "changes_required", "requires_decision")
            or not isinstance(value.get("findings"), list) or any(not isinstance(f, str) or not f.strip() for f in value["findings"])):
        raise GateError("Invalid artifact review output or input binding")
    if subjects(gate, state, request["kind"]) != request["subjects"]:
        raise GateError("Artifact review is stale; review current files")
    if request["kind"] == "blind":
        if value["outcome"] != "pass" or value["coverage"] != {} or not 3 <= len(value["findings"]) <= 5:
            raise GateError("A blind probe returns 3-5 independent observable outcomes, pass and empty coverage")
    elif value["outcome"] == "pass":
        validate_artifacts(gate, state, request["kind"])
        mapping = value["coverage"]
        ids = set(request["acceptance_ids"])
        if (not isinstance(mapping, dict) or (ids and set(mapping) != ids) or not mapping
                or any(not isinstance(v, list) or not v or any(s not in request["specification_ids"] for s in v) for v in mapping.values())
                or {s for v in mapping.values() for s in v} != set(request["specification_ids"]) or value["findings"]):
            raise GateError("Passing review needs exact acceptance/spec coverage and no unresolved findings")
    elif not value["findings"]:
        raise GateError("An unsuccessful review needs actionable findings")
    gate._require_agent_receipt(state, run, "artifact_reviewer", value)
    history = state.setdefault("artifact_review_history", [])
    for ref in history:
        if read_json(gate._safe_repo_path(ref["path"])) == value:
            return value
    output = run / "artifact-reviews" / f"{len(history)+1:03d}.json"
    if output.exists():
        if read_json(output) != value:
            raise GateError("Interrupted review publication conflicts with its immutable result")
    else:
        write_json_new(output, value)
    from ..gate import write_bytes_new, sha256_bytes
    snapshots = []
    for relative, semantic_digest in request["subjects"].items():
        source = gate._safe_repo_path(relative)
        content = source.read_bytes()
        digest = sha256_bytes(content)
        snapshot = run / "artifact-review-subjects" / digest
        if not snapshot.exists():
            write_bytes_new(snapshot, content)
        if sha256_file(snapshot) != digest:
            raise GateError("Immutable reviewed subject changed")
        snapshots.append({"path": snapshot.relative_to(gate.root).as_posix(), "sha256": digest, "source_path": relative, "semantic_sha256": semantic_digest})
    ref = {**_ref(gate, output), "subjects": request["subjects"], "input": target, "subject_snapshots": snapshots}
    history.append(ref)
    state.setdefault("artifact_findings", {})[request["kind"]] = value["findings"]
    if value["outcome"] == "pass":
        state.setdefault("artifact_reviews", {})[request["kind"]] = ref
    else:
        state.setdefault("artifact_reviews", {}).pop(request["kind"], None)
        attempts = state.setdefault("design_repair_attempts", {})
        attempts[request["kind"]] = attempts.get(request["kind"], 0) + 1
        if value["outcome"] == "requires_decision":
            state.update(status="needs_human", blocker={"kind": "requirements_decision", "findings": value["findings"]})
        else:
            from .autonomy import attempt
            attempt(state, contract or {"method": {"profile": PROFILE, "name": "zsspec"}}, "artifact_" + request["kind"], request["subjects"])
    gate._save(state, run, "artifact_review_recorded", kind=request["kind"], outcome=value["outcome"])
    return value


def require_review(gate, state, kind):
    from ..gate import GateError, read_json, sha256_file
    ref = state.get("artifact_reviews", {}).get(kind)
    if not ref:
        raise GateError("A passing independent " + kind + " review is required")
    p = gate._safe_repo_path(ref["path"])
    if sha256_file(gate._safe_repo_path(ref["input"]["path"])) != ref["input"]["sha256"]:
        raise GateError("Immutable review input changed")
    if sha256_file(p) != ref["sha256"] or read_json(p)["outcome"] != "pass" or ref["subjects"] != subjects(gate, state, kind):
        raise GateError("The " + kind + " review changed or is stale")
    return read_json(p)


def check_binding(gate, state, contract):
    from ..gate import GateError
    prep = preparation(gate, state)
    spec = require_review(gate, state, "spec")
    tasks = {t["id"] for t in parse_plan(gate._safe_repo_path(prep["plan"]).read_text(encoding="utf-8-sig"))}
    checks = contract["method"].get("task_checks", {})
    known_checks = {c["id"] for c in contract["required_checks"]}
    if not isinstance(checks, dict) or any(k not in tasks or not isinstance(v, list) or not v or any(not isinstance(c, str) or c not in known_checks for c in v) for k, v in checks.items()):
        raise GateError("task_checks must reference real cards and declared checks")
    if len([t for t in tasks if not t.startswith("R")]) > 1 and any(t not in checks for t in tasks if not t.startswith("R")):
        raise GateError("Multiple non-regression cards (including setup) require explicit task_checks, avoiding whole-feature completion deadlocks")
    mapped = contract["method"].get("task_tests", {})
    if not isinstance(mapped, dict) or any(k not in tasks or not isinstance(v, list) or any(f not in contract["test_files"] for f in v) for k, v in mapped.items()) or {f for v in mapped.values() for f in v} != set(contract["test_files"]) or len([f for v in mapped.values() for f in v]) != len(set(contract["test_files"])):
        raise GateError("Every generated test file needs an exact task_tests owner")
    require_review(gate, state, "plan")
    if (contract["method"]["slug"] != prep["slug"] or contract["method"]["acceptance_map"] != spec["coverage"]
            or set(contract["design_artifacts"]) != {prep["spec"], prep["plan"]}):
        raise GateError("Execution binding must consume the exact reviewed specification, plan and acceptance mapping")


@writes
def compile_plan(gate):
    from ..gate import GateError, write_json_atomic
    state, run, contract = gate._load_active()
    if not contract or contract.get("method", {}).get("profile") != PROFILE:
        raise GateError("compile-work-plan requires zsspec-docs-v2")
    if state.get("work_plan"):
        return gate._plan(state)
    require_review(gate, state, "spec")
    require_review(gate, state, "plan")
    prep = preparation(gate, state)
    tasks = parse_plan(gate._safe_repo_path(prep["plan"]).read_text(encoding="utf-8-sig"))
    mapping = contract["method"]["acceptance_map"]
    items = []
    for task in tasks:
        sid = task["id"].split("-")[0]
        refs = [a for a, specs in mapping.items() if sid in specs] if sid.startswith("S") else list(mapping)
        # Safe serial default; explicit worker scheduling may relax independent dependencies later.
        items.append({"id": task["id"], "title": task["title"], "done_criteria": [task["expected"]],
                      "acceptance_items": refs, "depends_on": [items[-1]["id"]] if items else []})
    draft = {"schema": "aidevflow/work-plan-v1", "task_id": state["task_id"], "method_profile": PROFILE, "items": items, "projections": []}
    path = f"specs/aidevflow-intake/{state['task_id']}/work-plan.json"
    write_json_atomic(gate._safe_repo_path(path), draft)
    return gate.register_work_plan(path)


def next_actions(gate, state, contract):
    """Durable checkpoints, never inferred from the last model message."""
    from ..gate import GateError
    if not state.get("method_preparation"):
        from ..runtimes import skills_root
        if not contract and state.get("status") == "intent_approved" and (skills_root(gate.root) / "zsspec-spec/SKILL.md").is_file():
            return ["prepare-method; initialize missing knowledge skeleton with pinned zsspec-init; generate specification from approved requirements"]
        return None
    if state.get("status") == "needs_human":
        return None
    if state.get("status") == "automation_stalled":
        return ["Automatic repair made no progress. Preserve the checkpoint and report the technical cause; do not request routine repair authorization or claim delivery."]
    prep = preparation(gate, state)
    from ..gate import read_json
    pending = [read_json(p) for p in (gate.runs / state["task_id"] / "agent-invocations").glob("*.json")]
    if any(p.get("status") in ("dispatched", "started") for p in pending):
        return ["A native role execution remains unresolved; wait for completion or recover-runtime before another dispatch"]
    if not state.get("knowledge_initialized"):
        return ["Use pinned zsspec-init to initialize missing MAP, modules and INDEX; record-initialization. Preserve existing active changes."]
    if state.get("engineering_revision"):
        for kind in ("spec", "plan"):
            try:
                require_review(gate, state, kind)
            except (GateError, OSError):
                return [f"Repair only engineering draft files; prepare-artifact-review --kind {kind}; dispatch artifact_reviewer; record-artifact-review using its immutable result_path"]
        return ["commit-engineering-revision; all affected work and checks will reopen without changing the approved requirements"]
    if not contract:
        for kind in ("spec", "plan"):
            if not gate._safe_repo_path(prep[kind]).is_file():
                return [f"Generate {prep[kind]} from the approved requirements using zsspec-{kind}; then prepare-artifact-review --kind {kind}"]
            try:
                require_review(gate, state, kind)
            except (GateError, OSError):
                return [f"Repair any recorded {kind} findings without changing approved requirements; prepare-artifact-review --kind {kind}; role-context --role artifact_reviewer; dispatch one read-only role; record-artifact-review --path <immutable result_path>"]
        return ["role-context --role contract_planner; register-contract from the real reviewed spec/plan; advance --to design; compile-work-plan; advance --to build"]
    if state.get("current_stage") == "design":
        return ["compile-work-plan; advance --to build"]
    if state.get("status") == "accepted":
        return ["commit-delivery-archive; advance --to delivery_ready"]
    if state.get("status") == "retrying":
        return ["route --type review_finding --summary <actual independent findings>; reopen affected work; repair and capture new checks"]
    if state.get("current_stage") == "build":
        active = [w for w in state.get("work_items", []) if w["status"] == "in_progress"]
        todo = active or [w for w in state.get("work_items", []) if w["status"] == "not_started"]
        if todo:
            return [f"Continue work item {todo[0]['id']} through zsspec-apply; start-work-item if needed; testing cards require assertion review/freeze and Red before implementation; run its actual task_checks; complete-work-item with concrete observation and immutable evidence"]
        from .execution import missing_red
        missing = missing_red(gate, state, contract)
        if missing:
            return ["Replay current frozen assertions against the pinned implementation baseline: " + "; ".join("run-tests --suite red --target " + t + " --against-base" for t in missing)]
        return ["All cards are complete; rerun only missing/failed/stale checks and attestations; sync-projections; advance --to verify"]
    if state.get("current_stage") == "verify":
        if not (gate.runs / state["task_id"] / "archive-input.json").is_file():
            return ["prepare-delivery-archive; perform zsspec-archive only in its disposable specs workspace; seal-delivery-archive"]
        if not state.get("delivery_archive"):
            return ["Finish staged zsspec-archive and seal-delivery-archive; do not modify live specifications or knowledge"]
        return ["prepare-verifier; role-context --role verifier; dispatch one fresh independent native role; record-verdict from its immutable result_path"]
    return None
