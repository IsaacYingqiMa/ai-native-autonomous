from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .adapters import projected_documents, semantic_content, validate_projections
from .runtimes import binding as runtime_binding, selected as selected_runtime
from .locking import writes


SCHEMA = "aidevflow/run-state-v1"
INTENT_SCHEMA = "aidevflow/intent-draft-v1"
VERDICT_SCHEMA = "aidevflow/verifier-verdict-v1"
STAGES = ("intent", "design", "build", "verify", "delivery_ready")
INFRA_FAILURES = (
    "no tests ran", "error collecting", "moduleNotFoundError", "importerror",
    "syntaxerror", "command not found", "no such file or directory", "could not start test command",
)
ASSERTION_FAILURE_MARKERS = ("assertionerror", "assertion failure", "fail:", "failed", "failure", "tests failed", "test failed", "expected:", "actual:")


class GateError(Exception):
    """An expected workflow or input validation error."""


def check_acceptable(item: dict, contract: dict) -> bool:
    return item["status"] == "passed" or (item["status"] == "passed_with_waivers" and contract.get("test_policy", {}).get("allow_approved_waivers") is True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def valid_red_result(exit_code: int, output: str) -> bool:
    diagnostic = output.lower()
    return (
        exit_code != 0
        and not any(term.lower() in diagnostic for term in INFRA_FAILURES)
        and any(term in diagnostic for term in ASSERTION_FAILURE_MARKERS)
    )


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"Cannot read valid JSON from {path}: {exc}") from exc


def replace_file_atomic(source: str | Path, destination: Path) -> None:
    """Tolerate short Windows sharing/access races without hiding permanent errors."""
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(0.025 * (2 ** attempt))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        replace_file_atomic(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_json_new(path: Path, value: Any) -> None:
    """An immutable record: even an identity collision cannot replace evidence."""
    write_bytes_new(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def write_bytes_new(path: Path, content: bytes) -> None:
    """Publish complete bytes atomically without replacing an existing object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            # Windows rename is atomic and refuses an existing destination.
            # It also works in sandboxes/filesystems which prohibit hard links.
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise GateError("task id must use 1-80 letters, digits, dots, underscores, or hyphens")
    return value


class Gate:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.control = self.root / ".aidevflow"
        self.runs = self.control / "runs"
        self.active_path = self.control / "active-run.json"
        self.events_path = self.control / "events.jsonl"

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args], cwd=self.root, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False,
        )
        if check and result.returncode:
            raise GateError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result

    def _assert_git_root(self) -> str:
        result = self._git("rev-parse", "--show-toplevel", check=False)
        if result.returncode:
            raise GateError("AIDevFlow requires the target project to be inside a Git repository")
        git_root = Path(result.stdout.strip()).resolve()
        if git_root != self.root:
            raise GateError(f"Run from the target repository root ({git_root}), not a subdirectory")
        commit = self._git("rev-parse", "--verify", "HEAD", check=False)
        if commit.returncode:
            raise GateError("Target Git repository needs an initial commit before a run can start")
        return commit.stdout.strip()

    def _load_active(self) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
        if not self.active_path.exists():
            raise GateError("No active AIDevFlow run. Start one with `aidevflow start-intake --request <path>`.")
        active = read_json(self.active_path)
        task_id = safe_id(active.get("task_id", ""))
        run_dir = self.runs / task_id
        if (run_dir / "baseline-transaction.json").is_file():
            raise GateError("Interrupted baseline transaction; use recover-baseline-transaction --mode finish or rollback before continuing")
        state_path = run_dir / "run-state.json"
        state = read_json(state_path)
        if not isinstance(state, dict) or state.get("schema") != SCHEMA or state.get("task_id") != task_id:
            raise GateError("Active run-state schema or task id is invalid; use `rebuild --task-id` if the event log is intact")
        # Older v1 run snapshots remain readable; task-level tracking is opt-in.
        state.setdefault("work_plan", None)
        state.setdefault("work_items", [])
        state.setdefault("revision", 0)
        binding_ref = state.get("execution_binding_ref")
        if "execution_binding" in state or binding_ref is not None:
            if (not isinstance(state.get("execution_binding"), dict) or not isinstance(binding_ref, dict)
                    or not isinstance(binding_ref.get("path"), str) or not isinstance(binding_ref.get("sha256"), str)):
                raise GateError("Execution binding or its integrity reference is invalid")
            pinned = self._safe_repo_path(binding_ref["path"])
            pinned.relative_to(run_dir)
            if sha256_file(pinned) != binding_ref["sha256"] or read_json(pinned) != state.get("execution_binding"):
                raise GateError("Execution binding changed; cross-runtime handoff is not supported")
        if state.get("contract_path") is None:
            if state.get("current_stage") != "intent":
                raise GateError("An execution contract is required after the Intent stage")
            return state, run_dir, None
        if not isinstance(state.get("contract_path"), str):
            raise GateError("Active run-state contract_path is invalid")
        contract_path = self._safe_repo_path(state["contract_path"])
        contract = read_json(contract_path)
        if state.get("contract_sha256") != sha256_file(contract_path):
            raise GateError("AIDevFlow contract changed during this run; stop and escalate for human review")
        from .managed import enabled
        if enabled(contract):
            reference = state.get("method_policy")
            if not isinstance(reference, dict):
                raise GateError("Managed run has no pinned method policy")
            policy_path = self._safe_repo_path(reference["path"])
            policy_path.relative_to(run_dir)
            if not policy_path.is_file() or sha256_file(policy_path) != reference.get("sha256"):
                raise GateError("Pinned method policy changed; stop and escalate for human review")
        work_plan = state.get("work_plan")
        if work_plan:
            if not isinstance(work_plan, dict) or not isinstance(work_plan.get("path"), str):
                raise GateError("AIDevFlow work plan reference is invalid")
            pinned_plan = self._safe_repo_path(work_plan["path"])
            if not pinned_plan.is_file() or sha256_file(pinned_plan) != work_plan.get("sha256"):
                raise GateError("AIDevFlow work plan changed during this run; stop and escalate for human review")
            plan_value = read_json(pinned_plan)
            if validate_work_plan(plan_value, task_id, contract):
                raise GateError("Pinned AIDevFlow work plan is invalid; stop and escalate for human review")
            planned_ids = {item["id"] for item in plan_value["items"]}
            tracked_items = state.get("work_items", [])
            if not isinstance(tracked_items, list) or any(not isinstance(item, dict) for item in tracked_items):
                raise GateError("AIDevFlow work item state is malformed")
            tracked_ids = {item.get("id") for item in tracked_items}
            if planned_ids != tracked_ids:
                raise GateError("AIDevFlow work item state does not match its pinned work plan")
            plan_items = {item["id"]: item for item in plan_value["items"]}
            mutable_evidence = {
                (run_dir / "run-state.json").relative_to(self.root).as_posix(),
                self.active_path.relative_to(self.root).as_posix(),
                self.events_path.relative_to(self.root).as_posix(),
                work_plan["path"],
                state["contract_path"],
            }
            for item in tracked_items:
                source = plan_items[item["id"]]
                if item.get("title") != source["title"] or item.get("depends_on", []) != source.get("depends_on", []) or item.get("acceptance_items", []) != source.get("acceptance_items", []):
                    raise GateError(f"AIDevFlow work item definition changed: {item['id']}")
                if item.get("status") not in ("not_started", "in_progress", "completed"):
                    raise GateError(f"AIDevFlow work item has an invalid status: {item['id']}")
                evidence = item.get("evidence", [])
                if not isinstance(evidence, list) or (item["status"] == "completed" and not evidence) or (item["status"] != "completed" and evidence):
                    raise GateError(f"AIDevFlow work item evidence is inconsistent with its status: {item['id']}")
                for entry in evidence:
                    if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("path", "sha256", "source_path", "source_sha256")):
                        raise GateError(f"AIDevFlow work item evidence is malformed: {item['id']}")
                    evidence_path = self._safe_repo_path(entry["path"])
                    try:
                        evidence_path.relative_to(run_dir / "work-evidence")
                    except ValueError as exc:
                        raise GateError(f"Work item evidence snapshot is outside its run directory: {entry['path']}") from exc
                    if entry["source_path"] in mutable_evidence or not evidence_path.is_file() or sha256_file(evidence_path) != entry["sha256"]:
                        raise GateError(f"AIDevFlow work item evidence snapshot changed or cites mutable Gate state: {entry['path']}")
        return state, run_dir, contract

    @writes
    def start_intake(self, request_path: str) -> dict[str, Any]:
        """Start a durable, contract-free intake from a natural-language request file."""
        base_commit = self._assert_git_root()
        if self.active_path.exists():
            active = read_json(self.active_path)
            raise GateError(f"An active run already exists ({active.get('task_id')}); finish or archive it first")
        request_abs = self._safe_repo_path(request_path)
        if not request_abs.is_file():
            raise GateError(f"Request document does not exist: {request_path}")
        request_bytes = request_abs.read_bytes()
        try:
            request_text = request_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError("Request document must be UTF-8 text") from exc
        if not request_text.strip():
            raise GateError("Request document is empty")
        task_id = f"ADF-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
        run_dir = self.runs / task_id
        if run_dir.exists():
            raise GateError(f"Generated task id already exists: {task_id}; retry intake")
        run_dir.mkdir(parents=True)
        source_copy = run_dir / "source-request.md"
        source_copy.write_bytes(request_bytes)
        title = request_abs.stem[:120] or task_id
        state = {
            "schema": SCHEMA,
            "task_id": task_id,
            "title": title,
            "contract_path": None,
            "contract_sha256": None,
            "current_stage": "intent",
            "status": "intake_started",
            "autonomy_policy": "clarify_then_auto",
            "reliability_version": 3,
            "agent_receipts_required": True,
            "execution_binding": runtime_binding(self.root),
            "method_observations_required": False,
            "execution_policy": {"schema": "aidevflow/execution-policy-v1",
                                 "method_evidence": "artifact_gated",
                                 "continuation_owner": "bounded_stop"},
            "request_path": request_abs.relative_to(self.root).as_posix(),
            "request_sha256": sha256_bytes(request_bytes),
            "request_snapshot_path": source_copy.relative_to(self.root).as_posix(),
            "base_commit": base_commit,
            "intent_draft_path": None,
            "intent_path": None,
            "intent_sha256": None,
            "approvals": [],
            "frozen_tests": {},
            "test_runs": [],
            "work_plan": None,
            "work_items": [],
            "repair_attempts": {},
            "verifier_bundle": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        binding_path = run_dir / "execution-binding.json"
        write_json_atomic(binding_path, state["execution_binding"])
        state["execution_binding_ref"] = {"path": binding_path.relative_to(self.root).as_posix(), "sha256": sha256_file(binding_path)}
        self._event(task_id, "intake_started", stage="intent", request_path=state["request_path"], request_sha256=state["request_sha256"], state_snapshot=state)
        write_json_atomic(run_dir / "run-state.json", state)
        write_json_atomic(self.active_path, {"task_id": task_id, "state_path": (run_dir / "run-state.json").relative_to(self.root).as_posix()})
        return state

    @writes
    def register_contract(self, draft_path: str) -> dict[str, Any]:
        """Validate and pin the generated task contract after Intent is resolved."""
        state, run_dir, current_contract = self._load_active()
        self._require_stage(state, "intent")
        if current_contract is not None:
            raise GateError("The execution contract is already registered and immutable")
        if state.get("status") != "intent_approved":
            raise GateError("Resolve blocking questions and finalize Intent before generating the Contract")
        self._check_intent_approved(state)
        draft_abs = self._safe_repo_path(draft_path)
        contract = read_json(draft_abs)
        errors = validate_contract(contract)
        errors.extend(self._validate_available_method_skills(contract))
        if errors:
            raise GateError("Invalid contract: " + "; ".join(errors))
        if state.get("reliability_version", 1) >= 2:
            if contract.get("work_plan_required") is not True:
                errors.append("new autonomous runs require work_plan_required=true")
            if not contract.get("required_checks"):
                errors.append("new autonomous runs require explicit required_checks with acceptance coverage")
            covered = {ref for check in contract.get("required_checks", []) if isinstance(check, dict) for ref in check.get("acceptance_items", [])}
            missing = {item["id"] for item in contract.get("acceptance_items", [])} - covered
            if missing:
                errors.append("required_checks do not cover acceptance items: " + ", ".join(sorted(missing)))
            if contract.get("verifier", {}).get("required", True) is not True:
                errors.append("autonomous runs require independent verification")
        if errors:
            raise GateError("Invalid contract: " + "; ".join(errors))
        if contract.get("task_id") != state["task_id"]:
            raise GateError("Generated contract.task_id must match the task id assigned at intake")
        self._require_agent_receipt(state, run_dir, "contract_planner", contract)
        self._pin_method_policy(state, run_dir, contract)
        target = run_dir / "contract.json"
        write_json_atomic(target, contract)
        state["contract_path"] = target.relative_to(self.root).as_posix()
        state["contract_sha256"] = sha256_file(target)
        state["title"] = contract.get("title", state["title"])
        self._save(state, run_dir, "contract_registered", contract_path=state["contract_path"], contract_sha256=state["contract_sha256"], draft_path=draft_abs.relative_to(self.root).as_posix())
        return {"task_id": state["task_id"], "contract_path": state["contract_path"], "contract_sha256": state["contract_sha256"], "status": state["status"]}

    def _event(self, task_id: str, event: str, **fields: Any) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": utc_now(), "task_id": task_id, "event": event, **fields}
        from .locking import observer_lock
        with observer_lock(self.control, name="event-log.lock"):
            with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def _pin_method_policy(self, state: dict[str, Any], run_dir: Path, contract: dict[str, Any]) -> None:
        from .managed import enabled, package_manifest, read_policy
        if enabled(contract):
            policy = read_policy(self.root)
            policy["package"] = package_manifest(self.root, contract)
            if policy["effective"]["manual"] == "disabled" and any(check.get("kind") == "manual" for check in contract.get("required_checks", [])):
                raise GateError("Contract requires manual evidence but zsspec.yml disables it")
            if policy["effective"]["e2e"] == "disabled" and any(check.get("mode") == "auto" for check in contract.get("required_checks", [])):
                raise GateError("Contract requires auto evidence but zsspec.yml disables e2e")
            path = run_dir / "method-policy.json"
            write_json_atomic(path, policy)
            state["method_policy"] = {"path": path.relative_to(self.root).as_posix(), "sha256": sha256_file(path)}
            # Existing runs retain their original strict observation policy.
            # New autonomous runs gate artifacts/tests, not a provider's Skill hook.
            state["method_observations_required"] = state.get("execution_policy", {}).get("method_evidence") != "artifact_gated"

    def _save(self, state: dict[str, Any], run_dir: Path, event: str, **fields: Any) -> None:
        state["revision"] = state.get("revision", 0) + 1
        state["updated_at"] = utc_now()
        self._event(state["task_id"], event, stage=state["current_stage"], status=state["status"], state_snapshot=state, **fields)
        write_json_atomic(run_dir / "run-state.json", state)
        self._sync_document_projections(state, run_dir)

    @writes
    def init(self, task_id: str, contract_path: str) -> dict[str, Any]:
        task_id = safe_id(task_id)
        base_commit = self._assert_git_root()
        contract_abs = (self.root / contract_path).resolve()
        try:
            contract_rel = contract_abs.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise GateError("contract must be inside the target repository") from exc
        contract = read_json(contract_abs)
        contract_errors = validate_contract(contract)
        contract_errors.extend(self._validate_available_method_skills(contract))
        if contract_errors:
            raise GateError("Invalid contract: " + "; ".join(contract_errors))
        if contract.get("schema") != "aidevflow/contract-v1":
            raise GateError("contract schema must be aidevflow/contract-v1")
        if contract.get("task_id") != task_id:
            raise GateError("--task-id must match contract.task_id")
        if self.active_path.exists():
            active = read_json(self.active_path)
            raise GateError(f"An active run already exists ({active.get('task_id')}); finish or archive it first")
        run_dir = self.runs / task_id
        if run_dir.exists():
            raise GateError(f"Run directory already exists: {run_dir}; task ids are not reusable")
        state = {
            "schema": SCHEMA,
            "task_id": task_id,
            "title": contract.get("title", task_id),
            "contract_path": contract_rel,
            "contract_sha256": sha256_file(contract_abs),
            "current_stage": "intent",
            "status": "active",
            "execution_binding": runtime_binding(self.root),
            "base_commit": base_commit,
            "intent_draft_path": None,
            "intent_path": None,
            "intent_sha256": None,
            "approvals": [],
            "frozen_tests": {},
            "test_runs": [],
            "work_plan": None,
            "work_items": [],
            "repair_attempts": {},
            "verifier_bundle": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        run_dir.mkdir(parents=True)
        binding_path = run_dir / "execution-binding.json"
        write_json_atomic(binding_path, state["execution_binding"])
        state["execution_binding_ref"] = {"path": binding_path.relative_to(self.root).as_posix(), "sha256": sha256_file(binding_path)}
        self._pin_method_policy(state, run_dir, contract)
        self._event(task_id, "run_started", stage="intent", contract_path=contract_rel, state_snapshot=state)
        write_json_atomic(run_dir / "run-state.json", state)
        write_json_atomic(self.active_path, {"task_id": task_id, "state_path": (run_dir / "run-state.json").relative_to(self.root).as_posix()})
        return state

    def status(self, *, summary: bool = False) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        if summary:
            return {**{key: state.get(key) for key in ("task_id", "current_stage", "status", "revision", "evidence_epoch", "updated_at", "baseline_ref")},
                    "runtime": selected_runtime(self.root, state),
                    "next_actions": self._next_actions(state, contract), "candidate_freshness": "not_checked"}
        result = dict(state)
        from .recovery import diagnose
        result["evidence_health"] = diagnose(self, history=False)
        result["next_actions"] = self._next_actions(state, contract)
        result["agent_invocations"] = self._agent_invocations(run_dir)
        continuation = run_dir / "continuation.json"
        if continuation.is_file():
            result["continuation"] = read_json(continuation)
        observer_status = run_dir / "agent-observer-status.json"
        if observer_status.is_file():
            result["agent_observer"] = read_json(observer_status)
        if contract is not None:
            from .methods import method_status
            result["method_steps"] = method_status(self.root, run_dir, state, contract)
            result["method_warnings"] = ["Method invocation is not observed: " + item["skill"]
                                         for item in result["method_steps"] if item["status"] != "observed"]
            if state.get("method_policy"):
                result["method_policy_details"] = read_json(self._safe_repo_path(state["method_policy"]["path"]))
            result["candidate_sha256"] = self._candidate(state, contract)["sha256"]
            result["checks"] = self._check_statuses(state, contract, candidate=result["candidate_sha256"])
            pending = {}
            attestations = []
            for check in result["checks"]:
                if not check_acceptable(check, contract):
                    if check.get("kind", "command") != "command":
                        attestations.append({"id": check["id"], "kind": check["kind"], "status": check["status"],
                                             "action": "Inspect current candidate, then record-check with fresh report/screenshot evidence"})
                        continue
                    argv = ["python", "scripts/aidevflow.py", "run-tests", "--suite", check["suite"]]
                    if check.get("target"):
                        argv.extend(["--target", check["target"]])
                    pending[(check["suite"], check.get("target"))] = {"suite": check["suite"], "target": check.get("target"), "argv": argv}
            result["pending_checks"] = list(pending.values())
            result["pending_attestations"] = attestations
            if state["current_stage"] == "verify" and state["status"] == "active" and pending:
                result["next_actions"] = ["Run status.pending_checks once per suite/target in the current environment; Verify permits check reruns without reopening completed work items", "prepare-verifier; dispatch independent Verifier; record-verdict; advance --to delivery_ready"]
            result["verdict_current"] = bool(state.get("verdict_ref") and state.get("verifier_bundle") and state["verifier_bundle"].get("candidate_sha256") == result["candidate_sha256"] and all(check_acceptable(item, contract) for item in result["checks"]))
            if not result["evidence_health"]["ok"] or result["evidence_health"]["pending_replacements"]:
                result["verdict_current"] = False
                result["next_actions"] = ["diagnose-evidence; plan-evidence-repair --reason <cause>; review/apply the plan; run real replacement checks"]
            if result["verdict_current"]:
                report = run_dir / "verifier-report.json"
                try:
                    self._check_bundle_integrity(run_dir, state["verifier_bundle"])
                    result["verdict_current"] = report.is_file() and sha256_file(report) == state["verdict_ref"]["sha256"]
                except GateError:
                    result["verdict_current"] = False
            if state.get("status") == "accepted" and not result["verdict_current"]:
                result["next_actions"] = ["Acceptance evidence is stale/changed; route review_finding to Build and capture fresh checks and verification"]
        return result

    @writes
    def archive(self) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        if state["current_stage"] != "delivery_ready" and state["status"] != "needs_human":
            raise GateError("Only a delivery-ready or needs-human run can be archived")
        state["status"] = "archived"
        state["archived_at"] = utc_now()
        self._save(state, run_dir, "run_archived")
        archived = self.control / "archive" / f"{state['task_id']}.json"
        write_json_atomic(archived, {"task_id": state["task_id"], "state_path": (run_dir / "run-state.json").relative_to(self.root).as_posix(), "archived_at": state["archived_at"]})
        self.active_path.unlink(missing_ok=True)
        return {"task_id": state["task_id"], "status": state["status"], "archive_record": archived.relative_to(self.root).as_posix()}

    @writes
    def rebuild(self, task_id: str) -> dict[str, Any]:
        task_id = safe_id(task_id)
        if (self.runs / task_id / "baseline-transaction.json").is_file():
            raise GateError("Interrupted baseline transaction; use recover-baseline-transaction before rebuild")
        if self.active_path.is_file() and read_json(self.active_path).get("task_id") != task_id:
            raise GateError("Cannot replace a different active run during rebuild; archive it explicitly first")
        if not self.events_path.is_file():
            raise GateError("Cannot rebuild without the AIDevFlow event log")
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        recovered = None
        for line_number, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if line_number == len(lines):
                    break  # tolerate only a truncated final record
                raise GateError(f"Event log is corrupt at line {line_number}")
            if event.get("task_id") == task_id and isinstance(event.get("state_snapshot"), dict):
                recovered = event["state_snapshot"]
        if recovered is None or recovered.get("task_id") != task_id:
            raise GateError(f"No recoverable state snapshot for task {task_id}")
        run_dir = self.runs / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(run_dir / "run-state.json", recovered)
        write_json_atomic(self.active_path, {"task_id": task_id, "state_path": (run_dir / "run-state.json").relative_to(self.root).as_posix()})
        self._event(task_id, "state_rebuilt", stage=recovered["current_stage"], status=recovered["status"], state_snapshot=recovered)
        return recovered

    @writes
    def record_intent(self, draft_path: str) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "intent")
        draft_abs = self._safe_repo_path(draft_path)
        if not draft_abs.is_file():
            raise GateError(f"Intent draft does not exist: {draft_path}")
        draft = read_json(draft_abs)
        initial_errors = validate_intent(draft)
        if initial_errors:
            raise GateError("Invalid IntentDraft: " + "; ".join(initial_errors))
        self._require_agent_receipt(state, run_dir, "intent_analyst", draft)
        # Keep Gate-recorded user choices when the read-only analyst revises the
        # draft. Retain a choice only while its question and option still exist.
        prior_decisions: list[dict[str, Any]] = []
        if state.get("intent_draft_path"):
            prior_draft = read_json(self._safe_repo_path(state["intent_draft_path"]))
            prior_decisions = prior_draft.get("decisions", [])
        if isinstance(draft, dict):
            decisions = draft.setdefault("decisions", [])
            if isinstance(decisions, list):
                if any(decision not in prior_decisions for decision in decisions):
                    raise GateError("Intent Analyst cannot supply unrecorded user decisions; record actual user choices through answer")
                decided_questions = {item.get("question_id") for item in decisions if isinstance(item, dict)}
                new_questions = {item.get("id"): item for item in draft.get("open_questions", []) if isinstance(item, dict)}
                for decision in prior_decisions:
                    if not isinstance(decision, dict) or decision.get("question_id") in decided_questions:
                        continue
                    question = new_questions.get(decision.get("question_id"))
                    option_ids = {option.get("id") for option in question.get("options", []) if isinstance(option, dict)} if question else set()
                    if question and (decision.get("option_id") is None or decision.get("option_id") in option_ids):
                        decisions.append(decision)
                        decided_questions.add(decision["question_id"])
        errors = validate_intent(draft)
        if errors:
            raise GateError("Invalid IntentDraft: " + "; ".join(errors))
        unresolved = [q["id"] for q in draft["open_questions"] if q["blocking"] and q["id"] not in {d["question_id"] for d in draft.get("decisions", [])}]
        target = run_dir / "intent-draft.json"
        write_json_atomic(target, draft)
        state["intent_draft_path"] = target.relative_to(self.root).as_posix()
        state["status"] = "awaiting_user_input" if unresolved else "intent_review"
        self._save(state, run_dir, "intent_draft_recorded", unresolved_blockers=unresolved, sha256=sha256_file(target))
        return {"status": state["status"], "unresolved_blockers": unresolved, "draft_path": state["intent_draft_path"]}

    @writes
    def answer(self, question_id: str, option_id: str | None, answer_text: str) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "intent")
        if state.get("status") != "awaiting_user_input" or not state.get("intent_draft_path"):
            raise GateError("No blocking Intent question is currently awaiting an answer")
        draft_path = self.root / state["intent_draft_path"]
        draft = read_json(draft_path)
        question = next((item for item in draft["open_questions"] if item["id"] == question_id and item["blocking"]), None)
        if question is None:
            raise GateError(f"Unknown blocking question: {question_id}")
        if not answer_text.strip():
            raise GateError("Answer text cannot be empty")
        if option_id is not None and option_id not in {item["id"] for item in question["options"]}:
            raise GateError(f"Unknown option {option_id} for question {question_id}")
        decisions = draft.setdefault("decisions", [])
        decisions = [entry for entry in decisions if entry["question_id"] != question_id]
        decisions.append({"question_id": question_id, "option_id": option_id, "answer": answer_text.strip(), "recorded_at": utc_now()})
        draft["decisions"] = decisions
        write_json_atomic(draft_path, draft)
        unresolved = [q["id"] for q in draft["open_questions"] if q["blocking"] and q["id"] not in {d["question_id"] for d in decisions}]
        state["status"] = "awaiting_user_input" if unresolved else "intent_review"
        self._save(state, run_dir, "intent_question_answered", question_id=question_id, option_id=option_id, unresolved_blockers=unresolved)
        return {"status": state["status"], "unresolved_blockers": unresolved}

    @writes
    def finalize_intent(self) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "intent")
        if not state.get("intent_draft_path"):
            raise GateError("Record a valid IntentDraft before finalizing intent.md")
        draft = read_json(self.root / state["intent_draft_path"])
        decisions = {item["question_id"] for item in draft.get("decisions", [])}
        unresolved = [q["id"] for q in draft["open_questions"] if q["blocking"] and q["id"] not in decisions]
        if unresolved:
            raise GateError(f"Cannot finalize Intent while blocking questions remain: {', '.join(unresolved)}")
        content = render_intent(state["task_id"], state["title"], draft)
        artifact = run_dir / "intent.md"
        artifact.write_text(content, encoding="utf-8", newline="\n")
        state["intent_path"] = artifact.relative_to(self.root).as_posix()
        state["intent_sha256"] = sha256_file(artifact)
        if state.get("autonomy_policy") == "clarify_then_auto":
            approval = {
                "artifact": state["intent_path"],
                "sha256": state["intent_sha256"],
                "by": "AIDevFlow autonomy policy",
                "utterance": "User selected clarify-then-auto: proceed after all blocking questions are resolved and Gate validates the generated contract.",
                "recorded_at": utc_now(),
                "approval_mode": "autonomous_after_blockers",
            }
            state.setdefault("approvals", []).append(approval)
            state["status"] = "intent_approved"
        else:
            state["status"] = "intent_review"
            approval = None
        self._save(state, run_dir, "intent_finalized", path=state["intent_path"], sha256=state["intent_sha256"], approval=approval)
        return {"path": state["intent_path"], "sha256": state["intent_sha256"], "status": state["status"], "approval_mode": "autonomous_after_blockers" if approval else "user_review_required"}

    @writes
    def approve_intent(self, by: str, utterance: str) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "intent")
        if state.get("status") != "intent_review" or not state.get("intent_path"):
            raise GateError("Intent is not ready for approval")
        artifact = self.root / state["intent_path"]
        digest = sha256_file(artifact)
        if digest != state.get("intent_sha256"):
            raise GateError("Intent changed after finalization; finalize and review the updated artifact")
        if not by.strip() or not utterance.strip():
            raise GateError("Approval requires an approver name and explicit approval utterance")
        approval = {"artifact": state["intent_path"], "sha256": digest, "by": by.strip(), "utterance": utterance.strip(), "recorded_at": utc_now()}
        state["approvals"].append(approval)
        state["status"] = "intent_approved"
        self._save(state, run_dir, "intent_approved", approval=approval)
        return {"status": state["status"], "approval": approval}

    @writes
    def advance(self, to_stage: str) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_executable(state)
        if to_stage not in STAGES:
            raise GateError(f"Unknown stage {to_stage}; expected one of {', '.join(STAGES)}")
        current_index = STAGES.index(state["current_stage"])
        next_stage = STAGES.index(to_stage) == current_index + 1
        # Once an Intent exists, verify its approval digest before every transition.
        # A same-stage request after an edit must expose the integrity failure, while
        # a skipped stage on an empty run should still report the invalid transition.
        if state.get("intent_path"):
            self._check_intent_approved(state)
        if not next_stage:
            raise GateError(f"Invalid transition {state['current_stage']} -> {to_stage}")
        if state["current_stage"] == "intent":
            self._check_intent_approved(state)
            if contract is None:
                raise GateError("Register a Gate-validated Contract before entering Design")
        if state["current_stage"] == "design":
            self._check_design_ready(state, run_dir, contract)
            state["design_manifest"] = self._design_manifest(state, contract)
        elif state["current_stage"] == "build":
            self._check_build_ready(state, contract)
        elif state["current_stage"] == "verify":
            self._check_verifier_passed(state, run_dir, contract)
        state["current_stage"] = to_stage
        state["status"] = ("delivery_ready_with_exceptions" if state.get("accepted_with_exceptions") else "delivery_ready") if to_stage == "delivery_ready" else "active"
        self._save(state, run_dir, "stage_advanced", to_stage=to_stage)
        return state

    @writes
    def freeze_tests(self, paths: list[str]) -> dict[str, str]:
        state, run_dir, contract = self._load_active()
        required = contract.get("test_files", []) + contract.get("test_support_files", [])
        requested = paths or required
        if required and set(requested) != set(required):
            raise GateError("Freeze exactly the test_files and test_support_files declared in the contract")
        if not requested:
            raise GateError("No test files declared; set contract.test_files before freezing")
        if state.get("frozen_tests"):
            self.integrity()
            if set(requested) == set(state["frozen_tests"]):
                return state["frozen_tests"]
            raise GateError("Use plan-baseline-revision to change the frozen file set")
        self._require_stage(state, "design")
        from .checks import require_design_review
        require_design_review(self, state, run_dir, contract)
        from .baselines import require_support, snapshot
        require_support(self, contract)
        hashes: dict[str, str] = {}
        for rel in requested:
            path = self._safe_repo_path(rel)
            if not path.is_file():
                raise GateError(f"Test file does not exist: {rel}")
            hashes[rel] = sha256_file(path)
        state["frozen_tests"] = hashes
        state["baseline_ref"] = snapshot(self, state, contract)
        write_json_atomic(run_dir / "test-manifest.json", {"schema": "aidevflow/test-manifest-v1", "files": hashes, "frozen_at": utc_now()})
        self._save(state, run_dir, "tests_frozen", files=hashes)
        return hashes

    @writes
    def register_work_plan(self, draft_path: str) -> dict[str, Any]:
        """Pin a method-neutral work plan for task-level lifecycle tracking."""
        state, run_dir, contract = self._load_active()
        self._require_stage(state, "design")
        if state.get("work_plan"):
            raise GateError("A work plan is already registered and immutable")
        draft_abs = self._safe_repo_path(draft_path)
        expected = f"specs/aidevflow-intake/{state['task_id']}/work-plan.json"
        if draft_abs.relative_to(self.root).as_posix() != expected:
            raise GateError(f"Work plan draft must be saved at {expected}")
        draft = read_json(draft_abs)
        errors = validate_work_plan(draft, state["task_id"], contract)
        if errors:
            raise GateError("Invalid work plan: " + "; ".join(errors))
        errors = validate_projections(draft, contract, self.root)
        from .managed import enabled
        if (state.get("reliability_version", 1) >= 2 or enabled(contract)) and contract.get("method", {}).get("name") == "zsspec":
            expected = {
                "work_item": {item["id"] for item in draft["items"]} if any(Path(path).name.startswith("TASK-") for path in contract["design_artifacts"]) else set(),
                "check": {item["id"] for item in contract.get("required_checks", [])} if any(Path(path).name.startswith("CHK-") for path in contract["design_artifacts"]) else set(),
                "acceptance": {item["id"] for item in contract["acceptance_items"]} if any(Path(path).name.startswith("REQ-") for path in contract["design_artifacts"]) else set(),
            }
            for kind, ids in expected.items():
                mapped = {item["source_id"] for item in draft.get("projections", []) if isinstance(item, dict) and item.get("source_kind") == kind and isinstance(item.get("source_id"), str)}
                if ids - mapped:
                    errors.append(f"zsspec projections missing {kind} sources: {', '.join(sorted(ids - mapped))}")
        if errors:
            raise GateError("Invalid work plan: " + "; ".join(errors))
        target = run_dir / "work-plan.json"
        write_json_atomic(target, draft)
        digest = sha256_file(target)
        state["work_plan"] = {"path": target.relative_to(self.root).as_posix(), "sha256": digest}
        state["work_items"] = [
            {
                "id": item["id"],
                "title": item["title"],
                "depends_on": item.get("depends_on", []),
                "acceptance_items": item.get("acceptance_items", []),
                "status": "not_started",
                "evidence": [],
                "updated_at": None,
            }
            for item in draft["items"]
        ]
        self._save(state, run_dir, "work_plan_registered", path=state["work_plan"]["path"], sha256=digest, item_ids=[item["id"] for item in state["work_items"]])
        return {"path": state["work_plan"]["path"], "sha256": digest, "work_items": state["work_items"]}

    @writes
    def start_work_item(self, work_item_id: str) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_stage(state, "build")
        self._check_work_plan_integrity(state, run_dir)
        item = self._work_item(state, work_item_id)
        if item["status"] == "completed":
            raise GateError(f"Work item {work_item_id} is completed; reopen it before starting again")
        if item["status"] == "in_progress":
            return item
        from .managed import enabled
        if enabled(contract) and any(entry["id"] != work_item_id and entry["status"] == "in_progress" for entry in state["work_items"]):
            raise GateError("Managed v1 is serial; finish the active work item before starting another")
        incomplete = [dependency for dependency in item["depends_on"] if self._work_item(state, dependency)["status"] != "completed"]
        if incomplete:
            raise GateError(f"Work item {work_item_id} has incomplete dependencies: {', '.join(incomplete)}")
        item["status"] = "in_progress"
        item["updated_at"] = utc_now()
        self._save(state, run_dir, "work_item_started", work_item_id=work_item_id)
        return item

    @writes
    def complete_work_item(self, work_item_id: str, evidence_paths: list[str]) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "build")
        self._check_work_plan_integrity(state, run_dir)
        item = self._work_item(state, work_item_id)
        if not evidence_paths:
            raise GateError("Completing a work item requires at least one evidence path")
        source_evidence = []
        mutable_paths = {
            (run_dir / "run-state.json").relative_to(self.root).as_posix(),
            self.active_path.relative_to(self.root).as_posix(),
            self.events_path.relative_to(self.root).as_posix(),
            state["contract_path"],
            state["work_plan"]["path"],
        }
        for rel in dict.fromkeys(evidence_paths):
            path = self._safe_repo_path(rel)
            if not path.is_file():
                raise GateError(f"Work item evidence does not exist: {rel}")
            relative = path.relative_to(self.root).as_posix()
            if relative in mutable_paths:
                raise GateError(f"Mutable Gate state cannot be used as work item evidence: {rel}")
            source_evidence.append({"source_path": relative, "source_sha256": sha256_file(path), "source_file": path})
        if item["status"] == "completed":
            current_sources = [{"source_path": entry["source_path"], "source_sha256": entry["source_sha256"]} for entry in item.get("evidence", [])]
            requested_sources = [{"source_path": entry["source_path"], "source_sha256": entry["source_sha256"]} for entry in source_evidence]
            if current_sources == requested_sources:
                return item
            raise GateError(f"Work item {work_item_id} is already completed with different evidence; reopen it first")
        if item["status"] != "in_progress":
            raise GateError(f"Work item {work_item_id} must be started before it can be completed")
        incomplete = [dependency for dependency in item["depends_on"] if self._work_item(state, dependency)["status"] != "completed"]
        if incomplete:
            raise GateError(f"Work item {work_item_id} has incomplete dependencies: {', '.join(incomplete)}")
        evidence = []
        evidence_dir = run_dir / "work-evidence" / work_item_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(source_evidence, start=1):
            source_name = Path(source["source_path"]).name
            snapshot = evidence_dir / f"{index:03d}-{source['source_sha256'][:12]}-{source_name}"
            shutil.copyfile(source["source_file"], snapshot)
            if sha256_file(snapshot) != source["source_sha256"]:
                snapshot.unlink(missing_ok=True)
                raise GateError(f"Work item evidence changed while it was being captured: {source['source_path']}")
            evidence.append({
                "path": snapshot.relative_to(self.root).as_posix(),
                "sha256": sha256_file(snapshot),
                "source_path": source["source_path"],
                "source_sha256": source["source_sha256"],
            })
        item["status"] = "completed"
        item["evidence"] = evidence
        item["updated_at"] = utc_now()
        self._save(state, run_dir, "work_item_completed", work_item_id=work_item_id, evidence=evidence)
        return item

    @writes
    def reopen_work_item(self, work_item_id: str, reason: str) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_stage(state, "build")
        self._check_work_plan_integrity(state, run_dir)
        item = self._work_item(state, work_item_id)
        if item["status"] != "completed":
            raise GateError(f"Only a completed work item can be reopened: {work_item_id}")
        if not reason.strip():
            raise GateError("Reopening a work item requires a reason")
        reopened = {work_item_id}
        changed = True
        while changed:
            changed = False
            for candidate in state.get("work_items", []):
                if candidate.get("status") in ("completed", "in_progress") and set(candidate.get("depends_on", [])) & reopened and candidate["id"] not in reopened:
                    reopened.add(candidate["id"])
                    changed = True
        for candidate in state.get("work_items", []):
            if candidate["id"] in reopened:
                candidate["status"] = "in_progress"
                candidate["evidence"] = []
                candidate["updated_at"] = utc_now()
        self._save(state, run_dir, "work_item_reopened", work_item_id=work_item_id, reopened_work_items=sorted(reopened), reason=reason.strip())
        return item

    @staticmethod
    def _work_item(state: dict[str, Any], work_item_id: str) -> dict[str, Any]:
        if not state.get("work_plan"):
            raise GateError("No work plan is registered for this run")
        item = next((entry for entry in state.get("work_items", []) if entry.get("id") == work_item_id), None)
        if item is None:
            raise GateError(f"Unknown work item: {work_item_id}")
        return item

    def _check_work_plan_integrity(self, state: dict[str, Any], run_dir: Path) -> None:
        reference = state.get("work_plan")
        if not reference:
            raise GateError("No work plan is registered for this run")
        path = self._safe_repo_path(reference["path"])
        if not path.is_file() or sha256_file(path) != reference.get("sha256"):
            raise GateError("AIDevFlow work plan integrity mismatch; stop and escalate for human review")
        plan = read_json(path)
        plan_items = {item["id"]: item for item in plan["items"]}
        tracked = state.get("work_items", [])
        if not isinstance(tracked, list) or any(not isinstance(item, dict) for item in tracked):
            raise GateError("AIDevFlow work item state is malformed")
        if {item.get("id") for item in tracked} != set(plan_items):
            raise GateError("AIDevFlow work item state does not match its pinned work plan")
        for item in tracked:
            source = plan_items[item["id"]]
            if item.get("title") != source["title"] or item.get("depends_on", []) != source.get("depends_on", []) or item.get("acceptance_items", []) != source.get("acceptance_items", []):
                raise GateError(f"AIDevFlow work item definition changed: {item['id']}")
            if item.get("status") not in ("not_started", "in_progress", "completed"):
                raise GateError(f"AIDevFlow work item has an invalid status: {item['id']}")
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list) or any(not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str) for entry in evidence):
                raise GateError(f"AIDevFlow work item evidence is malformed: {item['id']}")

    @writes
    def integrity(self) -> list[str]:
        state, run_dir, contract = self._load_active()
        mismatches = []
        if state.get("baseline_ref"):
            from .recovery import owned_json
            baseline = owned_json(self, run_dir, state["baseline_ref"], "baselines")
            from .checks import intact
            if not intact(self, list(baseline["files"].values()), run_dir):
                raise GateError("Immutable baseline snapshots changed; restore the original objects from backup before continuing")
            for rel in contract.get("design_artifacts", []):
                entry = baseline["files"].get(rel)
                live = self._safe_repo_path(rel)
                if not entry or not live.is_file() or semantic_content(rel, live.read_bytes(), self._plan(state)) != semantic_content(rel, self._safe_repo_path(entry["path"]).read_bytes(), self._plan(state)):
                    mismatches.append(rel)
        if contract and state.get("baseline_ref"):
            from .baselines import require_support
            require_support(self, contract)
        manifest_path = self.control / "runtime-manifest.json"
        if selected_runtime(self.root, state) == "omp" and manifest_path.is_file():
            # Shared project defaults may change for future runs. Executable
            # adapters, roles and schemas must still match the installation.
            prefixes = (".aidevflow/runtime/", ".aidevflow/schemas/", ".omp/extensions/aidevflow/",
                        ".omp/agents/aidevflow-", ".omp/skills/aidevflow-flow/",
                        "scripts/aidevflow.py", "scripts/aidevflow_bridge.py", "scripts/aidevflow_browser.py")
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
                raise GateError("Runtime installation manifest is invalid")
            for rel, expected in manifest["files"].items():
                if not isinstance(rel, str) or not isinstance(expected, str):
                    raise GateError("Runtime installation manifest entries are invalid")
                if rel.startswith(prefixes):
                    file = self._safe_repo_path(rel)
                    if not file.is_file() or sha256_file(file) != expected:
                        mismatches.append(rel)
        from .managed import enabled, file_hash, package_manifest
        if enabled(contract):
            policy = read_json(self._safe_repo_path(state["method_policy"]["path"]))
            if file_hash(self.root / policy["config_path"]) != policy["config_sha256"]:
                mismatches.append(policy["config_path"])
            if package_manifest(self.root, contract, version=policy["package"].get("manifest_version", 1)) != policy["package"]:
                from .runtimes import skills_root
                mismatches.append(f"{skills_root(self.root).relative_to(self.root).as_posix()} (pinned method package)")
        for rel, expected in state.get("frozen_tests", {}).items():
            path = self._safe_repo_path(rel)
            if not path.is_file() or sha256_file(path) != expected:
                mismatches.append(rel)
        if state.get("request_snapshot_path"):
            source = self._safe_repo_path(state["request_snapshot_path"])
            if not source.is_file() or sha256_file(source) != state.get("request_sha256"):
                mismatches.append(state["request_snapshot_path"])
        if state.get("design_manifest") and contract is not None:
            current = self._design_manifest(state, contract)
            mismatches.extend(rel for rel, expected in state["design_manifest"].items() if current.get(rel) != expected)
        if mismatches:
            state["status"] = "needs_human"
            state["blocker"] = {"kind": "integrity", "files": mismatches}
            self._save(state, run_dir, "integrity_mismatch", files=mismatches)
            raise GateError("Frozen test or specification integrity mismatch: " + ", ".join(mismatches))
        return mismatches

    @writes
    def run_tests(self, suite: str, target: str | None = None, evidence_paths: list[str] | None = None, *, against_base: bool = False) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_executable(state)
        if evidence_paths:
            from .managed import enabled
            if not enabled(contract):
                raise GateError("Command artifact capture requires zsspec-managed-v1")
        if target is not None:
            declared_targets = contract.get("test_files", []) + [check["target"] for check in contract.get("required_checks", []) if check.get("target")]
            target = next((path for path in declared_targets if path.replace("\\", "/") == target.replace("\\", "/")), target)
        if state["current_stage"] not in ("design", "build", "verify"):
            raise GateError("Tests may run only during design, build, or verify")
        self.integrity()
        command = contract.get("test_commands", {}).get(suite)
        if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
            raise GateError(f"test_commands.{suite} must be a non-empty argv array in the contract")
        if "{target}" in command and not target:
            raise GateError(f"test suite {suite} requires --target")
        if suite in ("red", "green") and target not in contract.get("test_files", []):
            raise GateError(f"{suite} target must match one of contract.test_files")
        if suite == "red" and state["current_stage"] != "design":
            raise GateError("Red evidence must be collected during Design, before implementation")
        if suite == "red":
            from .managed import enabled
            if enabled(contract) and not state.get("frozen_tests"):
                raise GateError("Managed Red evidence requires reviewed and frozen tests first")
        if suite in ("green", "regression") and state["current_stage"] == "design":
            raise GateError(f"{suite} suite cannot run until Build")
        command = [arg.replace("{target}", target or "") for arg in command]
        candidate = self._candidate(state, contract)
        from .test_execution import replay, prepare_report, summary, apply_waivers
        if against_base and suite != "red":
            raise GateError("--against-base is only for behavioral Red on the original implementation")
        cwd, executed_command = replay(self, state, contract, command) if against_base else (self.root, command)
        executed_command, test_report = prepare_report(self, state, suite, executed_command, cwd)
        prior_artifacts = {}
        for rel in evidence_paths or []:
            path = self._safe_repo_path(rel)
            prior_artifacts[rel] = (sha256_file(path), path.stat().st_mtime_ns) if path.is_file() else None
        started = utc_now()
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            if test_report:
                env["AIDEVFLOW_TEST_REPORT"] = test_report["path"]
                if any(arg == "pytest" or Path(arg).stem.lower() == "pytest" for arg in command[:4]) and (env.get("PYTEST_ADDOPTS") or env.get("PYTEST_PLUGINS")):
                    raise GateError("Declare pytest selection/plugins in frozen argv/config, not PYTEST_ADDOPTS/PYTEST_PLUGINS")
            result = subprocess.run(executed_command, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, env=env, timeout=contract.get("test_timeout_seconds", 300))
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            result = subprocess.CompletedProcess(command, 124, stdout, "Test command timed out")
        except OSError as exc:
            result = subprocess.CompletedProcess(command, 127, "", f"Could not start test command: {exc}")
        safe_suite = re.sub(r"[^A-Za-z0-9._-]", "_", suite)
        evidence_dir = run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        execution_id = secrets.token_hex(16)
        evidence = evidence_dir / f"{execution_id}-{safe_suite}.json"
        record = {
            "id": execution_id, "suite": suite, "command": executed_command, "exit_code": result.returncode,
            "target": target,
            "started_at": started, "finished_at": utc_now(),
            "stdout": result.stdout, "stderr": result.stderr,
            "evidence_path": evidence.relative_to(self.root).as_posix(),
            "candidate_sha256": candidate["sha256"],
            "evidence_epoch": state.get("evidence_epoch", 0),
            "candidate_changed": self._candidate(state, contract)["sha256"] != candidate["sha256"],
        }
        from .recovery import baseline_id, replaced
        record["baseline_id"] = baseline_id(state)
        if against_base:
            record["replay_base_commit"] = state["base_commit"]
        record["passed"] = result.returncode == 0 and not record["candidate_changed"]
        record["valid_red"] = suite != "red" or (not record["candidate_changed"] and valid_red_result(result.returncode, result.stdout + result.stderr))
        if suite != "red":
            coverage = apply_waivers(self, state, contract, suite, target, summary(test_report, result.stdout + result.stderr))
            if coverage is not None:
                record["coverage"] = coverage
                record["accepted_with_exceptions"] = bool(record["passed"] and coverage.get("accepted_with_exceptions"))
                record["passed"] = bool(record["passed"] and coverage["ok"])
            if test_report and Path(test_report["path"]).is_file():
                from .recovery import put_blob
                blob = put_blob(run_dir / "test-report-objects", Path(test_report["path"]).read_bytes())
                blob["path"] = Path(blob["path"]).relative_to(self.root).as_posix()
                record.setdefault("artifacts", []).append(blob)
        if evidence_paths:
            from .checks import snapshot_files
            try:
                for rel in evidence_paths:
                    path = self._safe_repo_path(rel)
                    if path.is_file() and prior_artifacts[rel] == (sha256_file(path), path.stat().st_mtime_ns):
                        raise GateError(f"Evidence was not refreshed by this command: {rel}")
                record.setdefault("artifacts", []).extend(snapshot_files(self, state, contract, evidence_paths, "test-artifacts"))
            except GateError as exc:
                record["artifact_error"] = str(exc)
                record["passed"] = False
                record["valid_red"] = False
                record["accepted_with_exceptions"] = False
        replaced(state, record)
        write_json_new(evidence, record)
        record["evidence_sha256"] = sha256_file(evidence)
        state["test_runs"].append({key: record[key] for key in ("id", "suite", "target", "exit_code", "passed", "valid_red", "evidence_path", "evidence_sha256", "finished_at", "candidate_sha256", "evidence_epoch", "candidate_changed", "artifacts", "artifact_error", "baseline_id", "coverage", "replay_base_commit", "accepted_with_exceptions", "replaces_incidents") if key in record})
        state["verifier_bundle"] = None
        state.pop("verdict_ref", None)
        state.pop("accepted_with_exceptions", None)
        state.pop("waived_acceptance_items", None)
        if state.get("status") in ("accepted", "retrying"):
            state["status"] = "active"
        self._save(state, run_dir, "test_executed", suite=suite, target=target, exit_code=result.returncode, passed=record["passed"], valid_red=record["valid_red"], evidence_path=record["evidence_path"], evidence_sha256=record["evidence_sha256"])
        return record

    @writes
    def prepare_verifier(self) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_stage(state, "verify")
        from .recovery import require_healthy
        require_healthy(self, state, run_dir)
        self._check_build_ready(state, contract)
        if state.get("verifier_bundle"):
            self._check_candidate_bundle(state, contract)
            self._check_bundle_integrity(run_dir, state["verifier_bundle"])
            return state["verifier_bundle"]
        from .evidence import BundleWriter
        try:
            writer = BundleWriter(self.control)
            result = self._prepare_verifier(state, run_dir, contract, writer)
        except OSError as exc:
            attempts = state.setdefault("infrastructure_attempts", {})
            attempts["prepare-verifier"] = attempts.get("prepare-verifier", 0) + 1
            state["blocker"] = {"kind": "infrastructure", "operation": "prepare-verifier",
                                "reason": str(exc), "attempt": attempts["prepare-verifier"]}
            if attempts["prepare-verifier"] >= 3:
                state["status"] = "needs_human"
            self._save(state, run_dir, "infrastructure_failed", **state["blocker"])
            action = "retry prepare-verifier" if state["status"] != "needs_human" else "explain the infrastructure blocker and await explicit resume-run --mode retry"
            raise GateError(f"Verifier packaging failed (infrastructure; {attempts['prepare-verifier']}/3): {exc}. {action}. Stage, checks and code-repair budget were preserved.") from exc
        return result

    def _prepare_verifier(self, state: dict[str, Any], run_dir: Path, contract: dict[str, Any], writer: Any) -> dict[str, Any]:
        candidate = self._candidate(state, contract)
        bundle_dir = writer.directory
        files: list[Path] = []
        files.append(self._safe_repo_path(state["contract_path"]))
        from .checks import intact
        for reference in (state.get("method_policy"), state.get("design_review")):
            if reference:
                path = self._safe_repo_path(reference["path"])
                if not path.is_file() or sha256_file(path) != reference["sha256"]:
                    raise GateError("Managed policy/review evidence changed")
                files.append(path)
                for entry in read_json(path).get("evidence", []):
                    if not intact(self, [entry], run_dir):
                        raise GateError("Design review evidence changed")
                    files.append(self._safe_repo_path(entry["path"]))
        for record in state.get("check_results", []):
            path = self._safe_repo_path(record["path"])
            if not path.is_file() or sha256_file(path) != record["sha256"] or not intact(self, record["evidence"], run_dir):
                raise GateError("Recorded non-command check evidence changed")
            files.append(path)
            files.extend(self._safe_repo_path(entry["path"]) for entry in record["evidence"])
        files.extend(self._safe_repo_path(rel) for rel in candidate["files"] if any(_glob_match(rel, pattern) for pattern in contract.get("code_paths", [])) and self._safe_repo_path(rel).is_file())
        if state.get("request_snapshot_path"):
            source = self._safe_repo_path(state["request_snapshot_path"])
            if sha256_file(source) != state.get("request_sha256"):
                raise GateError("Original request snapshot changed")
            files.append(source)
        files.extend(self._safe_repo_path(rel) for rel in contract.get("test_files", []) + contract.get("test_support_files", []))
        if state.get("work_plan"):
            files.append(self._safe_repo_path(state["work_plan"]["path"]))
            for item in state.get("work_items", []):
                for evidence in item.get("evidence", []):
                    files.append(self._safe_repo_path(evidence["path"]))
        if state.get("intent_path"):
            files.append(self.root / state["intent_path"])
        files.extend(self._safe_repo_path(rel) for rel in contract.get("design_artifacts", []) if self._safe_repo_path(rel).is_file())
        acceptance_path = bundle_dir / "acceptance-items.json"
        write_json_atomic(acceptance_path, {"acceptance_items": contract.get("acceptance_items", [])})
        work_items_path = None
        if state.get("work_plan"):
            work_items_path = bundle_dir / "work-items.json"
            write_json_atomic(work_items_path, {
                "task_id": state["task_id"],
                "work_plan_sha256": state["work_plan"]["sha256"],
                "items": state.get("work_items", []),
            })
        from .recovery import effective_records, owned_json
        def include_reference(ref, bucket):
            value = owned_json(self, run_dir, ref, bucket)
            files.append(self._safe_repo_path(ref["path"]))
            return value

        baseline_ref = state.get("baseline_ref")
        while baseline_ref:
            baseline = include_reference(baseline_ref, "baselines")
            for entry in baseline.get("files", {}).values():
                if not intact(self, [entry], run_dir):
                    raise GateError("Baseline snapshot object changed")
                files.append(self._safe_repo_path(entry["path"]))
            if baseline.get("proposal"):
                include_reference(baseline["proposal"], "baseline-proposals")
            for key in ("contract", "work_plan"):
                if baseline.get(key):
                    ref = baseline[key]
                    path = self._safe_repo_path(ref["path"])
                    if not path.is_file() or sha256_file(path) != ref["sha256"]:
                        raise GateError("Historical baseline metadata changed")
                    files.append(path)
            baseline_ref = baseline.get("parent")
        for ref in state.get("test_waivers", []):
            include_reference(ref, "test-waivers")
        for ref in state.get("evidence_repairs", []):
            repair = owned_json(self, run_dir, ref, "evidence-repairs")
            files.append(self._safe_repo_path(ref["path"]))
            for incident in repair["incidents"]:
                files.extend(self._safe_repo_path(blob["path"]) for blob in incident.get("snapshots", []))
        ledger = run_dir / "execution-ledger.json"
        write_json_atomic(ledger, {"test_runs": state["test_runs"], "repairs": state.get("evidence_repairs", [])})
        files.append(ledger)
        for item in effective_records(self, state, run_dir):
            evidence = self._safe_repo_path(item["evidence_path"])
            if not evidence.is_file() or sha256_file(evidence) != item.get("evidence_sha256"):
                raise GateError(f"Recorded test evidence changed: {item['evidence_path']}")
            files.append(evidence)
            if not intact(self, item.get("artifacts", []), run_dir):
                raise GateError("Recorded command artifacts changed")
            files.extend(self._safe_repo_path(entry["path"]) for entry in item.get("artifacts", []))
        code_paths = contract.get("code_paths", []) or ["."]
        diff_result = self._git("diff", "--binary", state["base_commit"], "--", ".", ":(exclude).aidevflow/**", ":(exclude).claude/**", ":(exclude)specs/aidevflow-intake/**", check=False)
        if diff_result.returncode:
            raise GateError("Could not produce Git diff for Verifier bundle")
        import difflib
        patch_parts = [diff_result.stdout]
        untracked_result = self._git("ls-files", "--others", "--exclude-standard", check=False)
        if untracked_result.returncode:
            raise GateError("Could not enumerate untracked files for Verifier bundle")
        for rel in untracked_result.stdout.splitlines():
            if not any(_glob_match(rel, pattern) for pattern in code_paths):
                continue
            source = self._safe_repo_path(rel)
            if not source.is_file():
                continue
            try:
                new_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
            except UnicodeDecodeError:
                continue
            patch_parts.extend(difflib.unified_diff([], new_lines, fromfile="/dev/null", tofile=f"b/{rel}"))
        (bundle_dir / "diff.patch").write_text("".join(patch_parts), encoding="utf-8", newline="\n")
        manifest: list[dict[str, str]] = []
        copied_sources: set[Path] = set()
        for source in files:
            if source in copied_sources:
                continue
            if not source.is_file():
                continue
            rel = source.relative_to(self.root).as_posix()
            manifest.append(writer.copy(source, rel))
            copied_sources.add(source)
        manifest.append({"path": "diff.patch", "sha256": sha256_file(bundle_dir / "diff.patch")})
        manifest.append({"path": "acceptance-items.json", "sha256": sha256_file(acceptance_path)})
        write_json_atomic(bundle_dir / "candidate.json", candidate)
        manifest.append({"path": "candidate.json", "sha256": sha256_file(bundle_dir / "candidate.json")})
        write_json_atomic(bundle_dir / "checks.json", {"required": self._required_checks(contract), "results": self._check_statuses(state, contract)})
        manifest.append({"path": "checks.json", "sha256": sha256_file(bundle_dir / "checks.json")})
        schema_source = self.control / "schemas" / "verifier-verdict.schema.json"
        if not schema_source.is_file():
            schema_source = Path(__file__).resolve().parents[1] / ".aidevflow" / "schemas" / "verifier-verdict.schema.json"
        if not schema_source.is_file():
            raise GateError("Verifier schema is missing; install the framework assets first")
        shutil.copyfile(schema_source, bundle_dir / "verdict-schema.json")
        manifest.append({"path": "verdict-schema.json", "sha256": sha256_file(bundle_dir / "verdict-schema.json")})
        if work_items_path:
            manifest.append({"path": "work-items.json", "sha256": sha256_file(work_items_path)})
        spec_files = [{"path": rel, "sha256": sha256_file(self._safe_repo_path(rel))} for rel in contract.get("design_artifacts", []) if self._safe_repo_path(rel).is_file()]
        if state.get("intent_path"):
            spec_files.append({"path": state["intent_path"], "sha256": sha256_file(self.root / state["intent_path"])})
        spec_version = sha256_bytes(json.dumps(spec_files, sort_keys=True).encode("utf-8"))
        bundle_digest = sha256_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))
        write_json_atomic(bundle_dir / "BUNDLE.json", {"schema": "aidevflow/verifier-bundle-v2", "task_id": state["task_id"], "spec_version": spec_version, "files": manifest, "bundle_sha256": bundle_digest})
        if self._candidate(state, contract)["sha256"] != candidate["sha256"]:
            raise GateError("Candidate changed while preparing verifier bundle; rerun required checks")
        bundle_dir = writer.publish()
        state["verifier_bundle"] = {"path": bundle_dir.relative_to(self.root).as_posix(), "sha256": bundle_digest, "spec_version": spec_version, "candidate_sha256": candidate["sha256"], "evidence_epoch": state.get("evidence_epoch", 0)}
        state.pop("verdict_ref", None)
        state.pop("accepted_with_exceptions", None)
        state.pop("waived_acceptance_items", None)
        state.pop("blocker", None)
        state.setdefault("infrastructure_attempts", {}).pop("prepare-verifier", None)
        state["status"] = "active"
        self._save(state, run_dir, "verifier_bundle_prepared", bundle_sha256=bundle_digest, file_count=len(manifest))
        return state["verifier_bundle"]

    @writes
    def record_verdict(self, verdict_path: str) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_stage(state, "verify")
        if not state.get("verifier_bundle"):
            raise GateError("Prepare a verifier bundle before recording a verdict")
        self._check_bundle_integrity(run_dir, state["verifier_bundle"])
        self._check_candidate_bundle(state, contract)
        self._check_build_ready(state, contract)
        verdict = read_json(self._safe_repo_path(verdict_path))
        errors = validate_verdict(verdict, state, run_dir, contract)
        if errors:
            raise GateError("Invalid verifier verdict: " + "; ".join(errors))
        self._require_agent_receipt(state, run_dir, "verifier", verdict, state["verifier_bundle"]["sha256"])
        destination = run_dir / "verifier-report.json"
        write_json_atomic(destination, verdict)
        state["verdict_ref"] = {"sha256": sha256_file(destination), "bundle_sha256": state["verifier_bundle"]["sha256"]}
        if all(item["verdict"] in ("pass", "waived") for item in verdict["items"]):
            state["status"] = "accepted"
            state["accepted_with_exceptions"] = any(item["verdict"] == "waived" for item in verdict["items"])
            state["waived_acceptance_items"] = [item["id"] for item in verdict["items"] if item["verdict"] == "waived"]
        else:
            state.pop("accepted_with_exceptions", None)
            state.pop("waived_acceptance_items", None)
            state["status"] = "needs_human" if any(item["verdict"] == "blocked" for item in verdict["items"]) else "retrying"
        self._save(state, run_dir, "verifier_verdict_recorded", passed=state["status"] == "accepted", verdict_path=destination.relative_to(self.root).as_posix())
        return {"status": state["status"], "path": destination.relative_to(self.root).as_posix()}

    @writes
    def route(self, failure_type: str, summary: str) -> dict[str, Any]:
        state, run_dir, contract = self._load_active()
        self._require_executable(state)
        if contract is None:
            raise GateError("No execution contract is registered; resolve Intent blockers first")
        route = contract.get("routing", {}).get(failure_type)
        if not isinstance(route, dict) or route.get("to") not in STAGES and route.get("to") != "needs_human":
            raise GateError(f"No valid routing rule for failure type {failure_type}")
        attempts = state.setdefault("repair_attempts", {})
        attempts[failure_type] = attempts.get(failure_type, 0) + 1
        target = route["to"]
        if sum(attempts.values()) > contract.get("max_repair_attempts", 3) + state.get("repair_budget_extension", 0):
            target = "needs_human"
        if target in ("intent", "design"):
            # Changing a pinned specification/test baseline needs an explicit new
            # version. Preserve it and request plan-baseline-revision.
            target = "needs_human"
        state["evidence_epoch"] = state.get("evidence_epoch", 0) + 1
        state["verifier_bundle"] = None
        state.pop("verdict_ref", None)
        state.pop("accepted_with_exceptions", None)
        state.pop("waived_acceptance_items", None)
        state["current_stage"] = target if target in STAGES else state["current_stage"]
        state["status"] = "needs_human" if target == "needs_human" else "active"
        state["blocker"] = {"kind": "product", "failure_type": failure_type, "reason": summary}
        self._save(state, run_dir, "failure_routed", failure_type=failure_type, to=target, attempt=attempts[failure_type], summary=summary)
        return {"to": target, "attempt": attempts[failure_type], "status": state["status"]}

    @writes
    def resume_run(self, by: str, utterance: str, additional_attempts: int = 1, mode: str = "repair") -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        if state.get("status") != "needs_human":
            raise GateError("Only a needs_human run can be explicitly resumed")
        if not by.strip() or not utterance.strip() or additional_attempts < 1:
            raise GateError("Resume requires an approver, their explicit instruction, and a positive repair allowance")
        if mode not in ("repair", "retry"):
            raise GateError("Resume mode must be repair or retry")
        if mode == "retry" and not self._can_retry_infrastructure(state):
            raise GateError("retry is only for a recorded infrastructure failure or legacy Stop escalation; use repair for acceptance failures")
        self.integrity()
        if mode == "retry":
            state["status"] = "active"
            state.pop("blocker", None)
            state["infrastructure_attempts"] = {}
            self._save(state, run_dir, "infrastructure_resumed", by=by.strip(), utterance=utterance.strip(), mode=mode)
            return state
        state["repair_budget_extension"] = state.get("repair_budget_extension", 0) + additional_attempts
        state["evidence_epoch"] = state.get("evidence_epoch", 0) + 1
        state["verifier_bundle"] = None
        state.pop("verdict_ref", None)
        state.pop("accepted_with_exceptions", None)
        state.pop("waived_acceptance_items", None)
        # A failed acceptance resumes implementation, with fresh checks required.
        if state["current_stage"] == "verify":
            state["current_stage"] = "build"
        state["status"] = "active"
        state.pop("blocker", None)
        self._save(state, run_dir, "run_resumed", by=by.strip(), utterance=utterance.strip(), additional_attempts=additional_attempts)
        return state

    def _can_retry_infrastructure(self, state: dict[str, Any]) -> bool:
        if state.get("blocker", {}).get("kind") == "infrastructure":
            return True
        # Compatibility for the observed v2 Stop-hook escalation. Do not infer
        # an infrastructure failure merely from an unfinished Verify stage.
        latest = None
        if self.events_path.is_file():
            with self.events_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("task_id") == state["task_id"] and "state_snapshot" in event:
                        latest = event
        return bool(latest and latest.get("event") == "stop_escalated"
                    and state.get("current_stage") == "verify" and not state.get("verdict_ref"))

    def _safe_repo_path(self, rel: str) -> Path:
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise GateError(f"Path escapes target repository: {rel}") from exc
        return candidate

    @staticmethod
    def _require_executable(state: dict[str, Any]) -> None:
        if state.get("status") in ("needs_human", "archived", "delivery_ready", "delivery_ready_with_exceptions"):
            raise GateError(f"Run is {state['status']}; execution is stopped. A needs_human run requires an explicit resume-run instruction.")

    def _plan(self, state: dict[str, Any]) -> dict[str, Any] | None:
        return read_json(self._safe_repo_path(state["work_plan"]["path"])) if state.get("work_plan") else None

    def _design_manifest(self, state: dict[str, Any], contract: dict[str, Any]) -> dict[str, str | None]:
        plan = self._plan(state)
        result: dict[str, str | None] = {}
        for rel in contract.get("design_artifacts", []):
            path = self._safe_repo_path(rel)
            try:
                result[rel] = sha256_bytes(semantic_content(rel, path.read_bytes(), plan)) if path.is_file() else None
            except (UnicodeError, ValueError) as exc:
                raise GateError(f"Cannot identify specification {rel}: {exc}") from exc
        return result

    def _candidate(self, state: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
        """Hash working-tree bytes, not just HEAD; include new declared product files."""
        tracked = self._git("ls-files", "-z").stdout.split("\0")
        others = self._git("ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
        ignored_product = self._git("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *contract.get("code_paths", [])).stdout.split("\0")
        explicit = set(contract.get("test_files", [])) | set(contract.get("test_support_files", [])) | set(contract.get("design_artifacts", []))
        # Tracked dependencies (including test runners and framework files) matter.
        # New files are scoped so a newly saved, untracked verdict/draft is not a
        # product change. All coordinator drafts belong under the intake directory.
        names = set(tracked) | explicit | {rel for rel in others + ignored_product if rel in explicit or any(_glob_match(rel, pattern) for pattern in contract.get("code_paths", []))}
        plan = self._plan(state)
        files: dict[str, str | None] = {}
        from .managed import enabled, artifact_path, file_hash, package_manifest
        for rel in sorted(names - {""}):
            parts = PurePosixPath(rel).parts
            if parts[0] == ".aidevflow" or rel.startswith("specs/aidevflow-intake/") or any(part in ("__pycache__", ".pytest_cache", ".test-tmp") for part in parts) or rel.endswith((".pyc", ".pyo")):
                continue
            if enabled(contract) and artifact_path(contract, rel):
                continue  # Runtime reports are separately snapshotted, never product inputs.
            path = self._safe_repo_path(rel)
            if path.is_dir():
                raise GateError(f"Nested Git repositories/submodules require an explicit snapshot adapter: {rel}")
            try:
                files[rel] = sha256_bytes(semantic_content(rel, path.read_bytes(), plan)) if path.is_file() else None
            except (UnicodeError, ValueError) as exc:
                raise GateError(f"Cannot snapshot candidate file {rel}: {exc}") from exc
        runtime = self.control / "runtime-manifest.json"
        value = {"files": files, "contract_sha256": state.get("contract_sha256"), "evidence_epoch": state.get("evidence_epoch", 0), "runtime_sha256": sha256_file(runtime) if runtime.is_file() else None}
        if state.get("baseline_ref"):
            value["baseline_id"] = state["baseline_ref"]["id"]
        if enabled(contract):
            from .managed import package_version
            value["method_package_sha256"] = package_manifest(self.root, contract, version=package_version(self.root, state))["sha256"]
            value["method_config_sha256"] = file_hash(self.root / "specs/zsspec.yml")
        return {**value, "sha256": sha256_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))}

    @staticmethod
    def _required_checks(contract: dict[str, Any]) -> list[dict[str, Any]]:
        checks = [{"id": f"green:{rel}", "suite": "green", "target": rel} for rel in contract.get("test_files", [])]
        if "regression" in contract.get("test_commands", {}):
            checks.append({"id": "regression", "suite": "regression", "target": None})
        checks.extend(contract.get("required_checks", []))
        return checks

    def _check_statuses(self, state: dict[str, Any], contract: dict[str, Any], *, candidate: str | None = None) -> list[dict[str, Any]]:
        candidate = candidate or self._candidate(state, contract)["sha256"]
        from .recovery import effective_records
        latest = {(item["suite"], item.get("target")): item for item in effective_records(self, state, self.runs / state["task_id"]) if item["suite"] != "red"}
        results = []
        for check in self._required_checks(contract):
            if check.get("kind", "command") != "command":
                from .checks import attestation_status
                results.append(attestation_status(self, state, check, candidate))
                continue
            item = latest.get((check["suite"], check.get("target")))
            fresh = bool(item and item.get("candidate_sha256") == candidate and not item.get("candidate_changed"))
            status = "missing" if item is None else "stale" if not fresh else "passed" if item["passed"] else "failed"
            if item and fresh and item.get("accepted_with_exceptions"):
                status = "passed_with_waivers"
            if any((entry["suite"], entry["target"]) == (check["suite"], check.get("target")) for entry in state.get("evidence_recovery_pending", [])):
                status = "invalid"
            if item:
                from .checks import intact
                evidence = self._safe_repo_path(item["evidence_path"])
                if not evidence.is_file() or sha256_file(evidence) != item.get("evidence_sha256") or not intact(self, item.get("artifacts", []), self.runs / state["task_id"]):
                    status = "invalid"
            results.append({"id": check["id"], "kind": "command", "suite": check["suite"], "target": check.get("target"), "status": status, "evidence_path": item.get("evidence_path") if item else None, "coverage": item.get("coverage") if item else None})
        return results

    def _check_candidate_bundle(self, state: dict[str, Any], contract: dict[str, Any]) -> None:
        reference = state.get("verifier_bundle")
        if not reference or reference.get("candidate_sha256") != self._candidate(state, contract)["sha256"]:
            raise GateError("Verifier bundle is stale for the current candidate; rerun checks and prepare a new bundle")

    def _sync_document_projections(self, state: dict[str, Any], run_dir: Path) -> None:
        plan = self._plan(state)
        if not plan or not plan.get("projections"):
            return
        contract = read_json(self._safe_repo_path(state["contract_path"]))
        statuses = {item["id"]: {"passed": "通过 (AI辅助)" if item.get("kind") == "manual" else "通过", "passed_with_waivers": "有批准例外", "failed": "失败", "blocked": "阻塞", "invalid": "阻塞", "stale": "待执行", "missing": "待执行"}[item["status"]] for item in self._check_statuses(state, contract)}
        try:
            documents = projected_documents(self.root, plan, state, statuses)
        except (ValueError, OSError, UnicodeError) as exc:
            raise GateError(f"Cannot project document status; run sync-projections after repairing the declared mapping: {exc}") from exc
        for path, content in documents.items():
            if path.read_bytes().decode("utf-8") != content:
                # Reuse the same atomic replacement used for state, while keeping
                # Markdown itself plain text rather than JSON encoded.
                fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(content)
                    replace_file_atomic(temp_name, path)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)

    @writes
    def sync_projections(self) -> dict[str, Any]:
        state, run_dir, _ = self._load_active()
        self._require_executable(state)
        self.integrity()
        self._sync_document_projections(state, run_dir)
        return {"ok": True, "task_id": state["task_id"]}

    @writes
    def record_design_review(self, reviewer: str, reason: str, evidence: list[str]) -> dict[str, Any]:
        from .checks import record_design_review
        return record_design_review(self, reviewer, reason, evidence)

    @writes
    def record_check(self, check_id: str, result: str, candidate: str, reviewer: str, reason: str, evidence: list[str]) -> dict[str, Any]:
        from .checks import record_check
        return record_check(self, check_id, result, candidate, reviewer, reason, evidence)

    def diagnose_evidence(self) -> dict[str, Any]:
        from .recovery import diagnose
        return diagnose(self)

    @writes
    def plan_evidence_repair(self, reason: str) -> dict[str, Any]:
        from .recovery import plan_repair
        return plan_repair(self, reason)

    @writes
    def apply_evidence_repair(self, plan: str, sha256: str, by: str, utterance: str) -> dict[str, Any]:
        from .recovery import apply_repair
        return apply_repair(self, plan, sha256, by, utterance)

    @writes
    def plan_baseline_revision(self, draft: str) -> dict[str, Any]:
        from .baselines import plan_revision
        return plan_revision(self, draft)

    @writes
    def apply_baseline_revision(self, proposal: str, sha256: str, by: str, utterance: str, reviewer: str, review: str) -> dict[str, Any]:
        from .baselines import apply_revision
        return apply_revision(self, proposal, sha256, by, utterance, reviewer, review)

    @writes
    def recover_baseline_transaction(self, mode: str, by: str, utterance: str, accept_conflicts: bool = False) -> dict[str, Any]:
        from .baselines import recover_transaction
        return recover_transaction(self, mode, by, utterance, accept_conflicts)

    @writes
    def record_test_waiver(self, suite: str, target: str | None, cases: list[str], acceptance: list[str], reason: str, by: str, utterance: str) -> dict[str, Any]:
        from .test_execution import waive
        return waive(self, suite, target, cases, acceptance, reason, by, utterance)

    @staticmethod
    def _agent_invocations(run_dir: Path) -> list[dict[str, Any]]:
        from .receipts import read_invocations
        return read_invocations(run_dir)

    def _require_agent_receipt(self, state: dict[str, Any], run_dir: Path, agent_type: str, result: Any, bundle_sha256: str | None = None) -> None:
        if not state.get("agent_receipts_required"):
            return
        digest = sha256_bytes(json.dumps(result, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        from .receipts import completed_results, execution_context
        from .locking import observer_lock
        with observer_lock(self.control):
            matching = completed_results(self._agent_invocations(run_dir), agent_type, digest, state.get("evidence_epoch", 0), bundle_sha256, execution_context(state), selected_runtime(self.root, state))
            for entry in matching:
                output = self._safe_repo_path(entry["result_path"])
                if output.is_file() and sha256_bytes(json.dumps(read_json(output), sort_keys=True, ensure_ascii=False).encode("utf-8")) == digest:
                    return
        # Recover native delivery cancelled after Handback, without asking the
        # model to reproduce an already completed analysis. No synthesized hooks.
        from .runtimes import recover
        try:
            recovered = recover(self.root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._event(state["task_id"], "runtime_recovery_error", error_type=type(exc).__name__, detail=str(exc))
        else:
            if recovered["recovered_events"]:
                with observer_lock(self.control):
                    matching = completed_results(self._agent_invocations(run_dir), agent_type, digest, state.get("evidence_epoch", 0), bundle_sha256, execution_context(state), selected_runtime(self.root, state))
                    for entry in matching:
                        output = self._safe_repo_path(entry["result_path"])
                        if output.is_file() and sha256_bytes(json.dumps(read_json(output), sort_keys=True, ensure_ascii=False).encode("utf-8")) == digest:
                            return
        raise GateError(f"Missing completed execution receipt for {agent_type}; run recover-runtime and inspect doctor --session-id <native-session-id> before redispatch. Inspect status.agent_invocations for pending completion, missing/invalid output, identity conflict, unsupported mode or stale inputs. Wait for active execution; confirm failed/unsupported execution has ended before a fresh ordinary role dispatch. Submit its unchanged JSON result.")

    def _next_actions(self, state: dict[str, Any], contract: dict[str, Any] | None) -> list[str]:
        if state.get("status") == "needs_human":
            kind = state.get("blocker", {}).get("kind")
            if kind == "evidence_integrity":
                return ["diagnose-evidence; plan-evidence-repair --reason ...; review the concrete plan; apply-evidence-repair only with actual authorization; rerun affected checks"]
            if kind == "integrity":
                return ["Restore the original frozen bytes, or prepare baseline-drafts and plan-baseline-revision; review/apply only with actual authorization; collect new Red before Build"]
            if self._can_retry_infrastructure(state):
                return ["Explain the infrastructure blocker; after explicit user instruction use resume-run --mode retry --by ... --utterance ...; preserve stage and checks, then rerun stale checks if the runtime changed"]
            return ["Explain the blocker; await an explicit user instruction before resume-run or archive"]
        if state.get("current_stage") == "delivery_ready":
            return ["Present the verified candidate and evidence, then archive"]
        if state.get("status") == "accepted":
            return ["advance --to delivery_ready; Gate rechecks candidate, checks, bundle and verdict"]
        if state.get("status") == "retrying":
            return ["route --type review_finding with the independent failure; repair within the remaining budget"]
        if contract is None:
            return ["Dispatch Intent Analyst and resolve blocking choices" if state.get("status") != "intent_approved" else "Dispatch Contract Planner and register-contract"]
        if state["current_stage"] == "design":
            from .managed import enabled
            if enabled(contract):
                return ["Run req/des/task/chk/local verify; prepare executable tests in Design (test --auto --gen-only when applicable)",
                        "Derive/register Work Plan from TASK; record-design-review as AI coordinator; freeze tests and support files; collect required Red; advance --to build"]
            return ["Complete method Design; register-work-plan; freeze-tests; collect required Red; advance --to build"]
        if state["current_stage"] == "build":
            return ["Use work-item commands for status; complete method Build/Review", "Resolve pending_checks with run-tests and pending_attestations with record-check, then advance --to verify"]
        if state["current_stage"] == "verify":
            return ["prepare-verifier; dispatch independent Verifier; record-verdict; advance --to delivery_ready"]
        return ["advance --to design after finalized Intent and registered Contract"]

    def _validate_available_method_skills(self, contract: dict[str, Any]) -> list[str]:
        from .methods import available_skill_errors
        errors = available_skill_errors(self.root, contract)
        from .managed import enabled, read_policy
        if enabled(contract):
            try:
                policy = read_policy(self.root)
                for prefix in contract.get("evidence_paths", []):
                    if self._safe_repo_path(prefix).is_file():
                        errors.append(f"Evidence directory is an existing file: {prefix}")
                for check in contract.get("required_checks", []):
                    if check.get("kind") == "manual" and policy["effective"]["manual"] == "disabled":
                        errors.append("Contract requires manual evidence but zsspec.yml disables it")
                    if check.get("mode") == "auto" and policy["effective"]["e2e"] == "disabled":
                        errors.append("Contract requires auto evidence but zsspec.yml disables e2e")
            except (GateError, OSError, UnicodeError) as exc:
                errors.append(str(exc))
        return errors

    def _check_method_steps(self, state: dict[str, Any], contract: dict[str, Any], phases: tuple[str, ...]) -> None:
        if not state.get("method_observations_required"):
            return  # Do not invent historical observations or retrofit old runs.
        from .methods import method_status
        missing = [item["skill"] for item in method_status(self.root, self.runs / state["task_id"], state, contract)
                   if item["phase"] in phases and item["status"] != "observed"]
        if missing:
            raise GateError("Method loading was not observed for: " + ", ".join(missing) + "; invoke the declared Skills in their stage and inspect hook diagnostics. Loading alone does not prove compliance.")

    @staticmethod
    def _require_stage(state: dict[str, Any], expected: str) -> None:
        Gate._require_executable(state)
        if state.get("current_stage") != expected:
            raise GateError(f"Operation requires stage {expected}; current stage is {state.get('current_stage')}")

    def _check_intent_approved(self, state: dict[str, Any]) -> None:
        approval = next((item for item in reversed(state.get("approvals", [])) if item.get("artifact") == state.get("intent_path")), None)
        artifact = (self.root / (state.get("intent_path") or "__missing__"))
        approval_required = state.get("current_stage") == "intent" and state.get("status") != "intent_approved"
        if approval_required or approval is None or not artifact.is_file() or sha256_file(artifact) != approval.get("sha256"):
            raise GateError("Design requires an approved, unchanged intent.md")

    def _check_design_ready(self, state: dict[str, Any], run_dir: Path, contract: dict[str, Any]) -> None:
        from .recovery import require_healthy
        require_healthy(self, state, run_dir)
        self.integrity()
        from .checks import require_design_review
        require_design_review(self, state, run_dir, contract)
        self._check_method_steps(state, contract, ("design",))
        missing = [rel for rel in contract.get("design_artifacts", []) if not self._safe_repo_path(rel).is_file()]
        if missing:
            raise GateError("Missing required design artifacts: " + ", ".join(missing))
        required_tests = contract.get("test_files", [])
        if required_tests:
            if set(state.get("frozen_tests", {})) != set(required_tests + contract.get("test_support_files", [])):
                raise GateError("Freeze all contract.test_files before entering build")
            from .recovery import effective_records, baseline_id
            red_targets = {item.get("target") for item in effective_records(self, state, run_dir)
                           if item["suite"] == "red" and item["valid_red"] and
                           (not state.get("baseline_ref") or item.get("baseline_id") == baseline_id(state))}
            missing_red = sorted(set(contract.get("red_test_files", required_tests)) - red_targets)
            if missing_red:
                raise GateError("A valid Red result is required for each frozen test: " + ", ".join(missing_red))
        if contract.get("work_plan_required") and not state.get("work_plan"):
            raise GateError("Register the required AIDevFlow work plan before entering Build")
        if state.get("work_plan"):
            self._check_work_plan_integrity(state, run_dir)

    def _check_build_ready(self, state: dict[str, Any], contract: dict[str, Any]) -> None:
        self.integrity()
        self._check_method_steps(state, contract, ("design", "build", "review"))
        if contract.get("work_plan_required") and not state.get("work_plan"):
            raise GateError("The Contract requires a registered AIDevFlow work plan")
        if state.get("work_plan"):
            incomplete = [item["id"] for item in state.get("work_items", []) if item.get("status") != "completed"]
            if incomplete:
                raise GateError("Complete all registered work items before Verify: " + ", ".join(incomplete))
            for item in state.get("work_items", []):
                for evidence in item.get("evidence", []):
                    path = self._safe_repo_path(evidence["path"])
                    if not path.is_file() or sha256_file(path) != evidence.get("sha256"):
                        raise GateError(f"Work item evidence changed after completion: {evidence['path']}")
        failures = [f"{item['suite']}:{item.get('target') or '*'} ({item['status']})" for item in self._check_statuses(state, contract) if not check_acceptable(item, contract)]
        if failures:
            raise GateError("Fresh passing results required for the current candidate: " + ", ".join(failures))

    def _check_verifier_passed(self, state: dict[str, Any], run_dir: Path, contract: dict[str, Any]) -> None:
        from .recovery import require_healthy
        require_healthy(self, state, run_dir)
        self._check_build_ready(state, contract)
        if not contract.get("verifier", {}).get("required", True) and any(item["status"] == "passed_with_waivers" for item in self._check_statuses(state, contract)):
            raise GateError("Exceptional delivery requires an independent Verifier; revise the Contract to require it")
        if contract.get("verifier", {}).get("required", True):
            verdict = run_dir / "verifier-report.json"
            if state.get("status") != "accepted" or not verdict.is_file():
                raise GateError("Delivery requires a recorded passing independent Verifier verdict")
            self._check_candidate_bundle(state, contract)
            self._check_bundle_integrity(run_dir, state["verifier_bundle"])
            ref = state.get("verdict_ref", {})
            if ref.get("sha256") != sha256_file(verdict) or ref.get("bundle_sha256") != state["verifier_bundle"]["sha256"]:
                raise GateError("Recorded verifier verdict changed or belongs to another bundle")
            errors = validate_verdict(read_json(verdict), state, run_dir, contract)
            if errors:
                raise GateError("Recorded verifier verdict is no longer valid: " + "; ".join(errors))

    @staticmethod
    def _check_bundle_integrity(run_dir: Path, bundle_ref: dict[str, Any]) -> None:
        bundle_rel = PurePosixPath(bundle_ref.get("path", ""))
        if bundle_rel.is_absolute() or ".." in bundle_rel.parts:
            raise GateError("Verifier bundle path escapes the target repository")
        bundle_dir = run_dir.parents[2] / Path(*bundle_rel.parts)
        manifest_path = bundle_dir / "BUNDLE.json"
        if not manifest_path.is_file():
            raise GateError("Verifier bundle manifest is missing")
        manifest = read_json(manifest_path)
        for entry in manifest.get("files", []):
            path = (bundle_dir / entry["path"]).resolve()
            try:
                path.relative_to(bundle_dir.resolve())
            except ValueError as exc:
                raise GateError("Verifier bundle manifest contains a path escape") from exc
            if not path.is_file() or sha256_file(path) != entry.get("sha256"):
                raise GateError(f"Verifier bundle content changed after preparation: {entry.get('path')}")
        actual = sha256_bytes(json.dumps(manifest.get("files", []), sort_keys=True).encode("utf-8"))
        if actual != bundle_ref.get("sha256") or actual != manifest.get("bundle_sha256"):
            raise GateError("Verifier bundle manifest hash does not match the active run")


def validate_contract(contract: Any) -> list[str]:
    if not isinstance(contract, dict):
        return ["top-level value must be an object"]
    errors = []
    for key in ("task_id", "title", "code_paths", "design_artifacts", "test_files", "test_commands", "acceptance_items"):
        if key not in contract:
            errors.append(f"missing {key}")
    if contract.get("schema") != "aidevflow/contract-v1":
        errors.append("schema must be aidevflow/contract-v1")
    policy = contract.get("test_policy", {})
    if not isinstance(policy, dict) or set(policy) - {"report_format", "required_cases", "allow_approved_waivers"}:
        errors.append("test_policy must be an object with report_format, required_cases and/or allow_approved_waivers")
    else:
        if "report_format" in policy and policy["report_format"] not in ("junit", "summary"):
            errors.append("test_policy.report_format must be junit or summary")
        if "allow_approved_waivers" in policy and not isinstance(policy["allow_approved_waivers"], bool):
            errors.append("test_policy.allow_approved_waivers must be boolean")
        cases = policy.get("required_cases", {})
        if not isinstance(cases, dict) or any(not isinstance(key, str) or not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip() for value in values) for key, values in cases.items()):
            errors.append("test_policy.required_cases must map suite[:target] to non-empty case ID arrays")
        elif cases and not policy.get("report_format"):
            errors.append("required_cases requires an explicit report_format")
        if isinstance(cases, dict) and isinstance(contract.get("test_commands"), dict):
            valid_keys = {name for name in contract["test_commands"] if name != "red"}
            paths = contract.get("test_files", [])
            valid_keys.update("green:" + path for path in (paths if isinstance(paths, list) else []) if isinstance(path, str))
            checks = contract.get("required_checks", [])
            for check in checks if isinstance(checks, list) else []:
                if isinstance(check, dict) and isinstance(check.get("suite"), str) and isinstance(check.get("target"), str):
                    valid_keys.add(check["suite"] + ":" + check["target"])
            if any(key not in valid_keys for key in cases):
                errors.append("required_cases keys must name a declared executable check suite[:target]")
    if "work_plan_required" in contract and not isinstance(contract["work_plan_required"], bool):
        errors.append("work_plan_required must be a boolean when provided")
    timeout = contract.get("test_timeout_seconds", 300)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 3600:
        errors.append("test_timeout_seconds must be an integer between 1 and 3600")
    method = contract.get("method")
    if method is not None:
        if not isinstance(method, dict) or not isinstance(method.get("name"), str) or not method.get("name", "").strip():
            errors.append("method.name must be a non-empty string when method is configured")
        stages = method.get("stages") if isinstance(method, dict) else None
        if not isinstance(stages, dict) or not stages:
            errors.append("method.stages must be a non-empty object when method is configured")
        else:
            for stage, skills in stages.items():
                if not isinstance(stage, str) or not stage or not isinstance(skills, list) or any(not isinstance(skill, str) or not skill.strip() for skill in skills):
                    errors.append(f"method.stages.{stage} must be an array of skill names")
                elif any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", skill) for skill in skills):
                    errors.append(f"method.stages.{stage} contains an invalid skill name")
    if not isinstance(contract.get("task_id"), str) or not contract.get("task_id"):
        errors.append("task_id must be a non-empty string")
    if not isinstance(contract.get("title"), str) or not contract.get("title"):
        errors.append("title must be a non-empty string")
    for key in ("code_paths", "design_artifacts", "test_files", "test_support_files"):
        if key == "test_support_files" and key not in contract:
            continue
        values = contract.get(key)
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            errors.append(f"{key} must be an array of non-empty relative paths")
    if not contract.get("code_paths"):
        errors.append("code_paths cannot be empty")
    if not contract.get("design_artifacts"):
        errors.append("design_artifacts must include the accepted design/specification artifact")
    if not contract.get("test_files"):
        errors.append("test_files must include at least one executable acceptance test")
    if not isinstance(contract.get("test_commands"), dict):
        errors.append("test_commands must be an object of argv arrays")
    else:
        for name, command in contract["test_commands"].items():
            if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
                errors.append(f"test_commands.{name} must be a non-empty argv array")
    if contract.get("test_files"):
        if "red" not in contract.get("test_commands", {}) or "green" not in contract.get("test_commands", {}):
            errors.append("test_files require red and green test_commands")
        if "{target}" not in contract.get("test_commands", {}).get("red", []):
            errors.append("red command must include {target} to bind Red evidence to each test file")
        if "{target}" not in contract.get("test_commands", {}).get("green", []):
            errors.append("green command must include {target} to bind Green evidence to each test file")
    items = contract.get("acceptance_items")
    if not isinstance(items, list) or not items:
        errors.append("acceptance_items must contain at least one item")
    else:
        ids = [item.get("id") for item in items if isinstance(item, dict)]
        if len(ids) != len(items) or any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
            errors.append("acceptance_items need unique non-empty IDs")
        if any(not isinstance(item, dict) or not item.get("criterion") for item in items):
            errors.append("each acceptance item requires a criterion")
    max_attempts = contract.get("max_repair_attempts", 3)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        errors.append("max_repair_attempts must be a positive integer")
    for key in ("code_paths", "design_artifacts", "test_files", "test_support_files", "evidence_paths"):
        paths = contract.get(key)
        if not isinstance(paths, list):
            continue
        for value in paths:
            if not isinstance(value, str):
                continue
            posix = PurePosixPath(value.replace("\\", "/"))
            windows = PureWindowsPath(value)
            if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
                errors.append(f"{key} must not contain absolute paths or parent traversal: {value}")
            if key in ("design_artifacts", "test_files", "test_support_files") and any(char in value for char in "*?[]"):
                errors.append(f"{key} requires concrete file paths, not globs: {value}; enumerate planned files before registering the Contract")
    red_files = contract.get("red_test_files", contract.get("test_files", []))
    if not isinstance(red_files, list) or any(not isinstance(path, str) or path not in contract.get("test_files", []) for path in red_files):
        errors.append("red_test_files must be a subset of test_files")
    checks = contract.get("required_checks", [])
    if not isinstance(checks, list):
        errors.append("required_checks must be an array")
    else:
        ids: set[str] = set()
        acceptance_ids = {item.get("id") for item in contract.get("acceptance_items", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
        for check in checks:
            if not isinstance(check, dict) or not {"id", "acceptance_items"}.issubset(check) or set(check) - {"id", "suite", "target", "acceptance_items", "kind", "mode"}:
                errors.append("each required check needs id, acceptance_items, and a command suite or static/manual kind")
                continue
            check_id = check.get("id")
            if not isinstance(check_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", check_id) or check_id in ids:
                errors.append("required check IDs must be unique safe identifiers")
            if isinstance(check_id, str):
                ids.add(check_id)
            suite = check.get("suite")
            kind = check.get("kind", "command")
            commands = contract.get("test_commands", {})
            command = commands.get(suite, []) if isinstance(commands, dict) and isinstance(suite, str) else []
            if kind not in ("command", "static", "manual"):
                errors.append("required check kind must be command, static or manual")
            if kind == "command" and (suite == "red" or not command):
                errors.append("required check suite must name a configured non-red test command")
            if kind != "command":
                from .managed import enabled
                if not enabled(contract):
                    errors.append("Non-command checks require zsspec-managed-v1")
                if any(key in check for key in ("suite", "target", "mode")):
                    errors.append("static/manual checks do not declare a command suite, target or mode")
            if "mode" in check and (kind != "command" or check["mode"] != "auto"):
                errors.append("command check mode may only be auto")
            target = check.get("target")
            if target is not None and (not isinstance(target, str) or not target):
                errors.append("required check target must be a non-empty string when present")
            if isinstance(command, list) and "{target}" in command and not target:
                errors.append("required check command needs a target")
            if suite == "green" and target not in contract.get("test_files", []):
                errors.append("required green check must target a declared test file")
            refs = check.get("acceptance_items")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in acceptance_ids for ref in refs):
                errors.append("required check must reference known acceptance items")
    from .managed import validation_errors
    errors.extend(validation_errors(contract))
    return errors


def validate_work_plan(plan: Any, task_id: str, contract: dict[str, Any]) -> list[str]:
    """Validate a method-neutral list of work items managed by AIDevFlow."""
    if not isinstance(plan, dict):
        return ["top-level value must be an object"]
    errors = []
    if not {"schema", "task_id", "items"}.issubset(plan) or set(plan) - {"schema", "task_id", "items", "projections"}:
        errors.append("work plan requires schema, task_id, items, and optional projections")
    if plan.get("schema") != "aidevflow/work-plan-v1":
        errors.append("schema must be aidevflow/work-plan-v1")
    if plan.get("task_id") != task_id:
        errors.append("task_id must match the active run")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        return errors + ["items must be a non-empty array"]
    required = {"id", "title", "done_criteria"}
    optional = {"depends_on", "acceptance_items"}
    ids: list[str] = []
    acceptance_ids = {item.get("id") for item in contract.get("acceptance_items", []) if isinstance(item, dict)}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be an object")
            continue
        if not required.issubset(item) or set(item) - required - optional:
            errors.append(f"items[{index}] requires id, title, done_criteria, and only optional depends_on/acceptance_items")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            errors.append(f"items[{index}].id must be a string")
            continue
        try:
            safe_id(item_id)
        except GateError:
            errors.append(f"items[{index}].id must be a safe identifier")
        ids.append(item_id)
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            errors.append(f"items[{index}].title must be a non-empty string")
        criteria = item.get("done_criteria")
        if not isinstance(criteria, list) or not criteria or any(not isinstance(value, str) or not value.strip() for value in criteria):
            errors.append(f"items[{index}].done_criteria must be a non-empty array of strings")
        for key in ("depends_on", "acceptance_items"):
            values = item.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
                errors.append(f"items[{index}].{key} must be an array of unique non-empty strings")
        if isinstance(item.get("acceptance_items", []), list) and any(not isinstance(value, str) or value not in acceptance_ids for value in item.get("acceptance_items", [])):
            errors.append(f"items[{index}].acceptance_items references an unknown acceptance item")
    if len(ids) != len(set(ids)):
        errors.append("work item ids must be unique")
    known_ids = set(ids)
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        dependencies = item.get("depends_on", [])
        if isinstance(dependencies, list) and any(value not in known_ids for value in dependencies):
            errors.append(f"work item {item['id']} depends on an unknown item")
        if isinstance(dependencies, list) and item["id"] in dependencies:
            errors.append(f"work item {item['id']} cannot depend on itself")
    if not errors:
        covered = {value for item in items for value in item.get("acceptance_items", [])}
        missing = acceptance_ids - covered
        if missing:
            errors.append("work plan does not cover acceptance items: " + ", ".join(sorted(missing)))
    if not errors:
        errors.extend(validate_projections(plan, contract))
    if not errors:
        dependencies = {item["id"]: set(item.get("depends_on", [])) for item in items}
        resolved: set[str] = set()
        while True:
            ready = {item_id for item_id, required_ids in dependencies.items() if item_id not in resolved and required_ids <= resolved}
            if not ready:
                break
            resolved.update(ready)
        if resolved != known_ids:
            errors.append("work item dependencies contain a cycle")
    return errors


def validate_intent(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["top-level value must be an object"]
    required = ("schema", "problem", "users", "desired_outcome", "scope", "non_goals", "constraints", "success_criteria", "assumptions", "open_questions")
    errors = [f"missing {key}" for key in required if key not in draft]
    if errors:
        return errors
    if draft["schema"] != INTENT_SCHEMA:
        errors.append(f"schema must be {INTENT_SCHEMA}")
    for key in ("problem", "desired_outcome"):
        if not isinstance(draft[key], str) or not draft[key].strip():
            errors.append(f"{key} must be a non-empty string")
    for key in ("users", "scope", "non_goals", "constraints", "success_criteria", "assumptions", "open_questions"):
        if not isinstance(draft[key], list):
            errors.append(f"{key} must be an array")
    for key in ("users", "scope", "non_goals", "constraints", "success_criteria"):
        if isinstance(draft[key], list) and any(not isinstance(value, str) or not value.strip() for value in draft[key]):
            errors.append(f"{key} entries must be non-empty strings")
    if isinstance(draft["assumptions"], list):
        for assumption in draft["assumptions"]:
            if not isinstance(assumption, dict) or set(assumption) != {"statement", "reason", "revisit_if"}:
                errors.append("each assumption requires exactly statement, reason, and revisit_if")
            elif any(not isinstance(assumption[field], str) or not assumption[field].strip() for field in ("statement", "reason", "revisit_if")):
                errors.append("assumption fields must be non-empty strings")
    if not isinstance(draft["success_criteria"], list) or not draft["success_criteria"]:
        errors.append("at least one success criterion is required")
    questions = draft.get("open_questions", [])
    seen: set[str] = set()
    decisions = draft.get("decisions", [])
    if not isinstance(decisions, list):
        errors.append("decisions must be an array")
        decisions = []
    decision_ids = [entry.get("question_id") for entry in decisions if isinstance(entry, dict)]
    if len(decision_ids) != len(set(decision_ids)):
        errors.append("each question can have at most one current decision")
    question_map: dict[str, dict[str, Any]] = {}
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            errors.append("each open question must be an object")
            continue
        qid = question.get("id")
        if not isinstance(qid, str) or not qid:
            errors.append("each open question needs a non-empty id")
            continue
        if qid in seen:
            errors.append(f"duplicate question id {qid}")
        seen.add(qid)
        question_map[qid] = question
        allowed_question_keys = {"id", "question", "blocking", "options", "recommended_option", "recommendation_reason"}
        if set(question) != allowed_question_keys:
            errors.append(f"question {qid} requires exactly {', '.join(sorted(allowed_question_keys))}")
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            errors.append(f"question {qid} needs non-empty question text")
        if not isinstance(question.get("blocking"), bool):
            errors.append(f"question {qid} needs boolean blocking")
        options = question.get("options")
        if not isinstance(options, list):
            errors.append(f"question {qid} options must be an array")
            options = []
        option_ids = [opt.get("id") for opt in options if isinstance(opt, dict)]
        if len(option_ids) != len(options) or any(not isinstance(value, str) or not value for value in option_ids) or len(set(option_ids)) != len(option_ids):
            errors.append(f"question {qid} options need unique non-empty ids and object entries")
        for option in options:
            if isinstance(option, dict):
                if set(option) != {"id", "label", "impact"}:
                    errors.append(f"question {qid} option requires exactly id, label, and impact")
                elif any(not isinstance(option.get(field), str) or not option[field].strip() for field in ("id", "label", "impact")):
                    errors.append(f"question {qid} options require non-empty id, label, and impact")
        if question.get("blocking") and not 2 <= len(options) <= 4:
            errors.append(f"blocking question {qid} must offer 2-4 options")
        recommended = question.get("recommended_option")
        if question.get("blocking") and recommended not in option_ids:
            errors.append(f"question {qid} must recommend one listed option")
        if not question.get("blocking") and recommended is not None and recommended not in option_ids:
            errors.append(f"question {qid} recommended_option must be null or a listed option")
        if not isinstance(question.get("recommendation_reason"), str):
            errors.append(f"question {qid} recommendation_reason must be a string")
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("question_id") not in seen:
            errors.append("decision references an unknown question")
            continue
        if set(decision) != {"question_id", "option_id", "answer", "recorded_at"}:
            errors.append(f"decision for {decision['question_id']} has unexpected or missing fields")
        question = question_map[decision["question_id"]]
        option_id = decision.get("option_id")
        option_ids = {option.get("id") for option in question.get("options", []) if isinstance(option, dict)}
        if option_id is not None and option_id not in option_ids:
            errors.append(f"decision for {decision['question_id']} references an unknown option")
        if not isinstance(decision.get("answer"), str) or not decision["answer"].strip():
            errors.append(f"decision for {decision['question_id']} needs a non-empty answer")
        if not isinstance(decision.get("recorded_at"), str) or not decision["recorded_at"].strip():
            errors.append(f"decision for {decision['question_id']} needs recorded_at")
    extra = set(draft) - set(required) - {"decisions"}
    if extra:
        errors.append("unexpected properties: " + ", ".join(sorted(extra)))
    return errors


def render_intent(task_id: str, title: str, draft: dict[str, Any]) -> str:
    def bullets(values: list[Any]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- 无"

    decisions = {item["question_id"]: item for item in draft.get("decisions", [])}
    questions = []
    for item in draft["open_questions"]:
        decision = decisions.get(item["id"])
        status = f"已决：{decision['answer']}" if decision else ("阻塞" if item["blocking"] else "非阻塞")
        questions.append(f"- **{item['id']}**：{item['question']}（{status}）")
    assumptions = [f"{a['statement']}；原因：{a['reason']}；复核条件：{a['revisit_if']}" for a in draft["assumptions"]]
    return (
        f"# Intent: {title}\n\n"
        f"- Task: `{task_id}`\n- Schema: `{draft['schema']}`\n\n"
        f"## 要解决的问题\n\n{draft['problem']}\n\n"
        f"## 受影响用户\n\n{bullets(draft['users'])}\n\n"
        f"## 期望结果\n\n{draft['desired_outcome']}\n\n"
        f"## 本次范围\n\n{bullets(draft['scope'])}\n\n"
        f"## 非目标\n\n{bullets(draft['non_goals'])}\n\n"
        f"## 约束\n\n{bullets(draft['constraints'])}\n\n"
        f"## 成功标准\n\n{bullets(draft['success_criteria'])}\n\n"
        f"## 假设\n\n{bullets(assumptions)}\n\n"
        f"## 未决问题与用户决策\n\n{bullets(questions)}\n"
    )


def validate_verdict(verdict: Any, state: dict[str, Any], run_dir: Path, contract: dict[str, Any]) -> list[str]:
    if not isinstance(verdict, dict):
        return ["top-level value must be an object"]
    required = ("schema", "task_id", "spec_version", "bundle_sha256", "items", "risks")
    errors = [f"missing {key}" for key in required if key not in verdict]
    if errors:
        return errors
    if set(verdict) != set(required):
        errors.append("verdict contains unexpected properties")
    if not isinstance(verdict.get("risks"), list) or any(not isinstance(risk, str) for risk in verdict.get("risks", [])):
        errors.append("risks must be an array of strings")
    bundle = state["verifier_bundle"]
    if verdict["schema"] != VERDICT_SCHEMA:
        errors.append(f"schema must be {VERDICT_SCHEMA}")
    if verdict["task_id"] != state["task_id"]:
        errors.append("task_id does not match active run")
    if verdict["spec_version"] != bundle["spec_version"]:
        errors.append("spec_version does not match prepared bundle")
    if verdict["bundle_sha256"] != bundle["sha256"]:
        errors.append("bundle_sha256 does not match prepared bundle")
    items = verdict.get("items")
    if not isinstance(items, list) or not items:
        return errors + ["items must be a non-empty array"]
    ids = [item.get("id") for item in items if isinstance(item, dict)]
    if len(ids) != len(items) or any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(set(ids)) != len(ids):
        errors.append("each verdict item needs a unique id")
    expected_items = contract.get("acceptance_items", [])
    expected = {item.get("id") for item in expected_items if isinstance(item, dict)}
    if expected and set(ids) != expected:
        errors.append(f"verdict item ids must exactly cover acceptance_items: {sorted(expected)}")
    bundle_rel = PurePosixPath(state["verifier_bundle"].get("path", ""))
    if bundle_rel.is_absolute() or ".." in bundle_rel.parts:
        return errors + ["verifier bundle path escapes the target repository"]
    bundle_dir = run_dir.parents[2] / Path(*bundle_rel.parts)
    bundle_meta = read_json(bundle_dir / "BUNDLE.json")
    bundled_files = {entry.get("path") for entry in bundle_meta.get("files", [])}
    from .test_execution import used_waivers
    waivers = used_waivers(Gate(run_dir.parents[2]), state)
    waived_ids = {identity for waiver in waivers.values() for identity in waiver["acceptance_items"]}
    for item in items:
        if not isinstance(item, dict):
            continue
        if set(item) != {"id", "verdict", "evidence", "reason"}:
            errors.append(f"verdict item {item.get('id')} contains unexpected or missing properties")
        if item.get("verdict") not in ("pass", "fail", "blocked", "waived"):
            errors.append(f"invalid verdict for {item.get('id')}")
        if item.get("verdict") == "waived" and (item.get("id") not in waived_ids or contract.get("test_policy", {}).get("allow_approved_waivers") is not True):
            errors.append(f"no current approved executed waiver for {item.get('id')}")
        if item.get("verdict") == "pass" and item.get("id") in waived_ids:
            errors.append(f"item {item.get('id')} has waived tests; report waived with risks instead of pass")
        if item.get("verdict") == "waived" and not verdict.get("risks"):
            errors.append("waived acceptance requires explicit residual risks")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            errors.append(f"reason for {item.get('id')} must be a non-empty string")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or any(not isinstance(path, str) for path in evidence):
            errors.append(f"evidence for {item.get('id')} must be an array of paths")
            continue
        elif item.get("verdict") in ("pass", "waived") and not evidence:
            errors.append(f"passing item {item.get('id')} must cite evidence")
        if item.get("verdict") == "waived":
            waiver_paths = {entry["path"] for entry in bundle_meta.get("files", []) if entry.get("source_path") in waivers
                            and item.get("id") in waivers[entry["source_path"]]["acceptance_items"]}
            if not waiver_paths.intersection(evidence):
                errors.append(f"waived item {item.get('id')} must cite its bundled waiver")
        for path in evidence or []:
            if path not in bundled_files:
                errors.append(f"evidence is not in the frozen bundle manifest: {path}")
                continue
            try:
                candidate = (bundle_dir / path).resolve()
                candidate.relative_to(bundle_dir.resolve())
            except (ValueError, TypeError):
                errors.append(f"evidence path escapes bundle for {item.get('id')}")
                continue
            if not candidate.is_file():
                errors.append(f"evidence file not found in bundle: {path}")
    return errors


def _glob_match(path: str, pattern: str) -> bool:
    import fnmatch
    normalized = pattern.replace("\\", "/")
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, normalized)
