"""Bounded native method operations with staged writes and recoverable commit.

No model/network waits run under the Gate writer lock. The vendor script is trusted
local code, not an OS sandbox; its working specs tree is a disposable copy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .gate import Gate, GateError, read_json, sha256_file, utc_now, write_json_atomic, write_bytes_new
from .locking import writer_lock, observer_lock
from .method_registry import adapter_for


def _inputs(gate):
    paths = set()
    for name in ("requirement", "design", "task", "checklist"):
        directory = gate.root / "specs" / name
        for p in directory.rglob("*.md"):
            if "reports" not in p.relative_to(directory).parts:
                paths.add(p)
    config = gate.root / "specs/zsspec.yml"
    if config.exists():
        paths.add(config)
    result = {}
    for p in sorted(paths):
        if p.is_symlink() or not p.resolve().is_relative_to(gate.root):
            raise GateError("Method input escapes the project")
        result[p.relative_to(gate.root).as_posix()] = sha256_file(p)
    return result


def _replace(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _run(script, stage, args):
    try:
        p = subprocess.run([sys.executable, "-B", str(script), *args, "--specs", str(stage / "specs")],
                           cwd=stage, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise GateError("Method script exceeded its 30s local-operation budget") from exc
    return {"argv": args, "exit_code": p.returncode, "stdout": p.stdout[-200000:], "stderr": p.stderr[-20000:]}


def _status(text, row_id):
    from .adapters import replace_status
    marked = replace_status(text, row_id, "__METHOD_STATUS__")
    before, after = text.splitlines(), marked.splitlines()
    for old, new in zip(before, after):
        if old != new:
            old_cells, new_cells = old.split("|"), new.split("|")
            index = next(i for i, cell in enumerate(new_cells) if "__METHOD_STATUS__" in cell)
            return old_cells[index].strip()
    raise GateError("Cannot read mapped native status: " + row_id)


def _sync_commands(stage, gate, state, contract):
    plan = gate._plan(state)
    work = {w["id"]: w["status"] for w in state.get("work_items", [])}
    checks = {c["id"]: c["status"] for c in gate._check_statuses(state, contract)}
    commands, deferred = [], []
    # REQ completion is owned by native close after actual user acceptance.
    for b in (plan or {}).get("projections", []):
        if b["source_kind"] == "acceptance":
            continue
        current = _status((stage / b["path"]).read_text(encoding="utf-8"), b["row_id"])
        base = current.split("(")[0].strip()
        if b["source_kind"] == "work_item":
            desired = {"not_started": "未开始", "in_progress": "进行中", "completed": "已完成"}[work[b["source_id"]]]
            if base == desired:
                continue
            if base == "已完成":
                # Native TASK is monotonic. Gate alone tracks reopened repair work;
                # never invent a vendor transition or relax Gate completion checks.
                deferred.append({**b, "native_status": current, "gate_status": work[b["source_id"]],
                                 "reason": "native_task_is_monotonic; repair remains active in Gate"})
                continue
            if base == "未开始" and desired == "已完成":
                commands.append(["set", b["row_id"], "进行中"])
            commands.append(["set", b["row_id"], desired])
        else:
            status = checks.get(b["source_id"], "missing")
            desired = {"passed": "通过", "passed_with_waivers": "跳过", "failed": "失败", "blocked": "阻塞",
                       "invalid": "阻塞", "stale": "阻塞", "missing": "待执行"}[status]
            if base == desired:
                continue
            if desired == "待执行":
                if base == "跳过":
                    commands.append(["set", b["row_id"], "待执行"])
                elif base != "待执行":
                    commands.append(["set", b["row_id"], "阻塞"])
                continue
            if base == "通过" and desired == "跳过":
                commands.append(["set", b["row_id"], "阻塞"])
            args = ["set", b["row_id"], desired]
            if desired == "通过":
                args += ["--note", "(AI辅助; Gate evidence captured)"]
            commands.append(args)
    return commands, deferred


def _header_semantic(data):
    lines = data.decode("utf-8").splitlines()
    import re
    for i, line in enumerate(lines[:15]):
        if re.fullmatch(r"\*\*文档状态\*\*:\s*(草稿|已定稿|待同步|已废弃)\s*", line.strip()):
            lines[i] = "**文档状态**: <native-status>"
    return "\n".join(lines).encode()


def _check_native_mappings(script, stage, gate, state, contract):
    """Use the pinned vendor parser in a child process; no guessed stdout success."""
    worker = '''import runpy,json,sys
from pathlib import Path
n=runpy.run_path(sys.argv[1],run_name="aidevflow_vendor_inspection")
out={}
for name in json.loads(sys.argv[3]):
 m=n['Module'](Path(sys.argv[2]),name).load()
 out[name]={'acs':list(m.req.acs) if m.req else [],
 'requirements':{rid:[a for a,r in m.req.ac_owner.items() if r==rid] for rid in m.req.items} if m.req else {},
 'tasks':{k:v.get('acs',[]) for k,v in ({**m.task.tasks,**m.task.eng_tasks} if m.task else {}).items()},
 'checks':{k:v['acs'] for k,v in (m.chk.checks if m.chk else {}).items()},
 'complete':bool(m.req and not m.req.legacy and m.req.has_overview and m.des and all(d.has_snap_col for d in m.des) and m.task and m.task.has_main and m.chk and m.chk.has_list)}
print(json.dumps(out,ensure_ascii=False))
'''
    try:
        p = subprocess.run([sys.executable, "-B", "-c", worker, str(script), str(stage / "specs"),
                            json.dumps(contract["method"]["modules"])], cwd=stage, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise GateError("Native method parser exceeded its local-operation budget") from exc
    if p.returncode:
        raise GateError("Native document parser rejected the method protocol: " + p.stderr[-2000:])
    try:
        native = json.loads(p.stdout)
        if not all(m["complete"] for m in native.values()):
            raise GateError("Incomplete/legacy native specs cannot satisfy the current adapter")
        actual = {a for m in native.values() for a in m["acs"]}
        mapping = contract["method"]["acceptance_map"]
        if actual != {a for ids in mapping.values() for a in ids}:
            raise GateError("Native AC IDs do not exactly cover the approved acceptance mapping")
        plan = gate._plan(state)
        if not plan:
            raise GateError("Register a work plan before native gate")
        rows = {k:v for m in native.values() for k,v in m['requirements'].items()}
        bindings = [b for b in plan.get('projections',[]) if b['source_kind'] == 'acceptance']
        if set(rows) != {b['row_id'] for b in bindings}:
            raise GateError("Every native REQ overview row needs one acceptance projection (canonical coverage uses acceptance_map)")
        for b in bindings:
            if not set(rows[b['row_id']]) & set(mapping[b['source_id']]):
                raise GateError("REQ overview row does not belong to its canonical acceptance binding")
        for kind, category, sources in (("work_item", "tasks", {w['id']:w['acceptance_items'] for w in plan['items']}),
                                        ("check", "checks", {c['id']:c['acceptance_items'] for c in contract['required_checks']})):
            rows = {k:v for m in native.values() for k,v in m[category].items()}
            bindings = [b for b in plan.get('projections',[]) if b['source_kind'] == kind]
            if set(rows) != {b['row_id'] for b in bindings}:
                raise GateError("Native " + category + " must all map to Gate's work/check facts")
            for b in bindings:
                covered = {a for canonical in sources[b['source_id']] for a in mapping[canonical]}
                if not set(rows[b['row_id']]) <= covered:
                    raise GateError("Native row acceptance exceeds its Gate binding: " + b['row_id'])
    except (ValueError, KeyError, TypeError) as exc:
        raise GateError("Unsupported native parser result; adapter review required") from exc


def _receipt(run, tx, status, **extra):
    result = {**tx, "status": status, **extra}
    path = run / "method-operations" / (tx["operation_id"] + ".json")
    if path.exists() and (old := read_json(path)).get("status") == "applied":
        return old
    write_json_atomic(path, result)
    return result


def execute(root: Path, operation: str, module: str | None = None, scope: str | None = None,
            evidence: list[str] | None = None, *, by: str = "", utterance: str = ""):
    # Recovery must not roll back staging while its original worker still runs.
    # This owner lease is separate from the short Gate writer lock.
    with observer_lock(root.resolve() / ".aidevflow", timeout=0, name="method-operation.lock"):
        return _execute(root, operation, module, scope, evidence, by=by, utterance=utterance)


def _execute(root, operation, module, scope, evidence, *, by, utterance):
    root = root.resolve()
    gate = Gate(root)
    if operation not in ("status", "verify", "gate", "baseline", "report", "sync", "close"):
        raise GateError("Unknown method operation; direct set/stale/migrate/discard are not autonomous adapters")
    with writer_lock(gate.control):
        state, run, contract = gate._load_active()
        adapter = adapter_for(contract)
        if adapter is None:
            raise GateError("This operation requires a scripted method adapter")
        if operation == "close":
            if state["current_stage"] != "delivery_ready" or not by.strip() or not utterance.strip():
                raise GateError("Method close requires delivery_ready and actual user business acceptance")
            if not gate.status().get("verdict_current"):
                raise GateError("Business acceptance requires current independent verification")
        else:
            gate._require_executable(state)
        gate.integrity()
        modules = contract["method"]["modules"]
        if operation != "sync" and module not in modules:
            raise GateError("Use one of the Contract's pinned method.modules")
        if operation == "baseline":
            if state["current_stage"] != "design" or state.get("frozen_tests"):
                raise GateError("Method baseline is allowed only before Design freeze")
            if scope not in ("req", "des", "task", "chk"):
                raise GateError("Baseline requires one explicit layer: req/des/task/chk")
            gate._check_intent_approved(state)
        reviews = []
        for rel in evidence or []:
            from .managed import artifact_path
            p = gate._safe_repo_path(rel)
            if not artifact_path(contract, rel) or not p.is_file() or not p.stat().st_size:
                raise GateError("Method review evidence must be nonempty and under contract.evidence_paths")
            reviews.append({"path": rel, "sha256": sha256_file(p)})
        if operation == "baseline" and not reviews:
            raise GateError("Baseline requires --evidence from the actual AI design/requirements review")
        inputs = _inputs(gate)
        from .method_resolution import source_for
        script = source_for(root, contract) / adapter.script
        tx = {"operation": operation, "module": module, "scope": scope, "task_id": state["task_id"],
              "revision": state["revision"], "inputs": inputs, "script_sha256": sha256_file(script),
              "evidence": reviews, "review_source": "operator_business_acceptance" if operation == "close" else "ai_coordinator",
              "by": by, "utterance": utterance, "created_at": utc_now()}
        tx["operation_id"] = hashlib.sha256(json.dumps({k: v for k, v in tx.items() if k != "created_at"}, sort_keys=True).encode()).hexdigest()[:32]
        journal = run / "method-transaction.json"
        previous = run / "method-operations" / (tx["operation_id"] + ".json")
        if previous.exists():
            old = read_json(previous)
            if old.get("status") == "applied":
                return {**old, "replayed": True}
            # Failed/rolled-back attempts may be retried without reusing their files.
            import uuid
            tx["operation_id"] = uuid.uuid4().hex
        write_json_atomic(journal, {**tx, "status": "executing"})
        # Copy all participating module inputs, including read-only dependencies.
        stage = run / "method-staging" / tx["operation_id"]
        for rel, expected in inputs.items():
            data = (root / rel).read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                journal.unlink()
                raise GateError("Method inputs changed while preparing")
            write_bytes_new(stage / rel, data)
    reports = []
    try:
        if operation == "sync":
            commands, deferred = _sync_commands(stage, gate, state, contract)
        else:
            deferred = []
            commands = [[operation, module]]
            if operation == "baseline":
                commands[0].append(scope)
            elif operation in ("verify", "report"):
                commands[0].append("--dry-run")
        for args in commands:
            report = _run(script, stage, args)
            reports.append(report)
            if report["exit_code"]:
                raise GateError("Native method rejected operation: " + report["stdout"][-6000:] + report["stderr"][-2000:])
            if operation in ("verify", "gate", "baseline", "close") and any(
                    marker in report["stdout"] for marker in ("旧格式", "未断言", "A2~A4 跳过")):
                raise GateError("Legacy/incomplete method assertions cannot satisfy strict adapter checks")
        if operation == "gate":
            _check_native_mappings(script, stage, gate, state, contract)
        if _inputs(gate) != inputs or sha256_file(script) != tx["script_sha256"]:
            raise GateError("Stale method operation: sources or specs changed")
        changes = {}
        after_files = {p.relative_to(stage).as_posix(): p for p in stage.rglob("*") if p.is_file()}
        generated = {}
        # Native close generates a shared index. Retain it as operation evidence;
        # it is not a frozen requirement or permission to overwrite the live index.
        if operation == "close" and "specs/index.md" in after_files:
            index = after_files.pop("specs/index.md")
            generated["native_index"] = {"path": index.relative_to(root).as_posix(), "sha256": sha256_file(index)}
        if set(after_files) != set(inputs):
            raise GateError("Native script created/deleted undeclared files")
        for rel, expected in inputs.items():
            data = after_files[rel].read_bytes()
            if hashlib.sha256(data).hexdigest() == expected:
                continue
            if rel not in contract["design_artifacts"]:
                raise GateError("Native script changed an undeclared artifact: " + rel)
            old = (root / rel).read_bytes()
            from .adapters import semantic_content
            before_sem = semantic_content(rel, old, gate._plan(state))
            after_sem = semantic_content(rel, data, gate._plan(state))
            if operation == "baseline":
                before_sem, after_sem = _header_semantic(old), _header_semantic(data)
            if before_sem != after_sem:
                raise GateError("Native operation changed semantic/frozen content: " + rel)
            changes[rel] = {"before_sha256": expected, "after_sha256": hashlib.sha256(data).hexdigest(),
                            "before": "before/" + rel, "after": "after/" + rel}
            write_bytes_new(stage / "before" / rel, old)
            write_bytes_new(stage / "after" / rel, data)
        review_snapshots = []
        for i, e in enumerate(reviews):
            data = (root / e["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != e["sha256"]:
                raise GateError("Method review changed while snapshotting")
            target = stage / "reviews" / (str(i) + ".md")
            write_bytes_new(target, data)
            review_snapshots.append({"path": target.relative_to(root).as_posix(), "sha256": e["sha256"]})
        with writer_lock(gate.control):
            current, _, _ = gate._load_active(allow_method_transaction=True)
            if current["revision"] != tx["revision"] or _inputs(gate) != inputs or sha256_file(script) != tx["script_sha256"]:
                raise GateError("Stale method operation: state, sources or specs changed")
            if any(sha256_file(root / e["path"]) != e["sha256"] for e in reviews):
                raise GateError("Method review evidence changed during operation")
            tx.update(status="validated", changes=changes, reports=reports, deferred=deferred,
                      generated=generated,
                      review_snapshots=review_snapshots,
                      staging_path=stage.relative_to(root).as_posix())
            write_json_atomic(journal, tx)
            return _commit(gate, run, tx, current)
    except BaseException as exc:
        with writer_lock(gate.control):
            # Once commit is validated, retain its recovery marker on any interruption.
            saved = read_json(journal) if journal.exists() else {}
            if saved.get("status") == "executing":
                _receipt(run, tx, "failed", error=str(exc), reports=reports)
                journal.unlink()
        raise


def _commit(gate, run, tx, state):
    if state.get("last_method_operation") != tx["operation_id"] and state["revision"] != tx["revision"]:
        raise GateError("Method transaction state revision changed")
    contract = read_json(gate.root / state["contract_path"])
    from .method_resolution import source_for
    script = source_for(gate.root, contract) / adapter_for(contract).script
    if sha256_file(script) != tx["script_sha256"]:
        raise GateError("Method script changed during recovery")
    for ref in [*tx.get("review_snapshots", []), *tx.get("generated", {}).values()]:
        if sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
            raise GateError("Method evidence snapshot changed")
    if set(_inputs(gate)) != set(tx["inputs"]):
        raise GateError("Method transaction input set changed")
    for rel, expected in tx["inputs"].items():
        actual = sha256_file(gate.root / rel)
        allowed = {expected}
        if rel in tx["changes"]:
            allowed.add(tx["changes"][rel]["after_sha256"])
        if actual not in allowed:
            raise GateError("Method recovery conflicts with a later file edit: " + rel)
    stage = gate._safe_repo_path(tx["staging_path"])
    validated = {}
    for rel, change in tx["changes"].items():
        data = (stage / change["after"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != change["after_sha256"]:
            raise GateError("Staged method transaction changed")
        validated[rel] = data
    for rel, data in validated.items():
        change = tx["changes"][rel]
        if sha256_file(gate.root / rel) != change["after_sha256"]:
            _replace(gate.root / rel, data)
    result = _receipt(run, tx, "applied", completed_at=utc_now())
    if state.get("last_method_operation") != tx["operation_id"]:
        if state["revision"] != tx["revision"]:
            raise GateError("Method transaction state revision changed")
        state["last_method_operation"] = tx["operation_id"]
        receipt_path = run / "method-operations" / (tx["operation_id"] + ".json")
        state.setdefault("method_operations", []).append({"path": receipt_path.relative_to(gate.root).as_posix(),
                                                         "sha256": sha256_file(receipt_path)})
        state["method_sync_pending"] = False if tx["operation"] == "sync" else state.get("method_sync_pending", False)
        state["method_deferred_transitions"] = tx.get("deferred", [])
        if tx["operation"] == "close":
            closed = state.setdefault("method_closed_modules", [])
            if tx["module"] not in closed:
                closed.append(tx["module"])
        gate._save(state, run, "method_operation_applied", operation_id=tx["operation_id"], operation=tx["operation"])
    (run / "method-transaction.json").unlink(missing_ok=True)
    return result


def recover(root: Path, mode: str):
    with observer_lock(root.resolve() / ".aidevflow", timeout=0, name="method-operation.lock"):
        return _recover(root, mode)


def _recover(root: Path, mode: str):
    gate = Gate(root)
    with writer_lock(gate.control):
        state, run, _ = gate._load_active(allow_method_transaction=True)
        path = run / "method-transaction.json"
        if not path.exists():
            return {"recovered": False}
        tx = read_json(path)
        if tx.get("status") == "executing":
            if mode != "rollback":
                raise GateError("Unfinished staging is not a validated result; rollback before retrying")
            _receipt(run, tx, "rolled_back")
            path.unlink()
            return {"recovered": True, "status": "rolled_back"}
        if mode == "finish":
            return _commit(gate, run, tx, state)
        if mode != "rollback" or state.get("last_method_operation") == tx["operation_id"]:
            raise GateError("Applied state must be finished, not silently rolled back")
        if state["revision"] != tx["revision"]:
            raise GateError("Rollback state revision changed")
        stage = gate._safe_repo_path(tx["staging_path"])
        for rel, change in tx["changes"].items():
            actual = sha256_file(gate.root / rel)
            if actual not in (change["before_sha256"], change["after_sha256"]):
                raise GateError("Rollback conflicts with a later edit: " + rel)
            data = (stage / change["before"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != change["before_sha256"]:
                raise GateError("Rollback snapshot changed")
        for rel, change in tx["changes"].items():
            _replace(gate.root / rel, (stage / change["before"]).read_bytes())
        _receipt(run, tx, "rolled_back")
        path.unlink()
        return {"recovered": True, "status": "rolled_back"}


def require_ready(gate, state, run, contract):
    """A real native gate pass supplements the Gate's own acceptance checks."""
    if not adapter_for(contract):
        return
    inputs = _inputs(gate)
    for module in contract["method"]["modules"]:
        refs = state.get("method_operations", [])
        for ref in refs:
            if sha256_file(gate._safe_repo_path(ref["path"])) != ref["sha256"]:
                raise GateError("Native method receipt changed")
        found = any((r := read_json(gate._safe_repo_path(ref["path"]))).get("status") == "applied" and r.get("operation") == "gate"
                    and r.get("module") == module and r.get("inputs") == inputs
                    for ref in refs)
        if not found:
            raise GateError("Run method-run gate --module " + module + " against the current finalized specs before Build")


def validate_revision(gate, state, contract, plan, files):
    """Reject an authorized revision that would freeze an unusable native chain."""
    from types import SimpleNamespace
    from .method_resolution import source_for
    adapter = adapter_for(contract)
    script = source_for(gate.root, contract) / adapter.script
    run = gate.runs / state["task_id"]
    with tempfile.TemporaryDirectory(dir=run, prefix="method-revision-") as directory:
        stage = Path(directory)
        for rel in _inputs(gate):
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((gate.root / rel).read_bytes())
        for rel in contract["design_artifacts"]:
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gate._safe_repo_path(files[rel]["path"]).read_bytes())
        for module in contract["method"]["modules"]:
            result = _run(script, stage, ["gate", module])
            if result["exit_code"]:
                raise GateError("Revised native specs must already be finalized and pass gate before freezing: " + result["stdout"][-3000:])
        _check_native_mappings(script, stage, SimpleNamespace(_plan=lambda _: plan), state, contract)
