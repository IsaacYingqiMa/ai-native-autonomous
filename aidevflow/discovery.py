"""Versioned, human-reviewed requirements before autonomous delivery (OMP first)."""
from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .gate import (Gate, GateError, read_json, safe_id, sha256_file, utc_now,
                   validate_intent, render_intent, write_json_atomic, write_json_new,
                   write_bytes_new)
from .locking import writes
from .runtimes import binding, digest, project_config
from .requirements import validate_requirements

PROFILE = "reviewed-discovery-v1"


def reviewed(root: Path) -> bool:
    return project_config(root).get("workflow_profile") == PROFILE


def subject(root: Path):
    if (root / ".aidevflow/active-discovery.json").is_file():
        return Discovery(root).load()
    return Gate(root)._load_active()


def verify_reference(root: Path, ref: dict) -> dict:
    gate = Gate(root)
    path = gate._safe_repo_path(ref["path"])
    if not path.is_file() or sha256_file(path) != ref["sha256"]:
        raise GateError("Approved requirements manifest changed")
    manifest = read_json(path)
    if manifest.get("schema") != "aidevflow/requirements-manifest-v1" or not manifest.get("files"):
        raise GateError("Invalid approved requirements manifest")
    directory = root / ".aidevflow/discoveries" / safe_id(manifest["discovery_id"])
    if not path.is_relative_to(directory / "revisions"):
        raise GateError("Requirements manifest is outside its discovery")
    for rel, sha in manifest["files"].items():
        target = gate._safe_repo_path(rel)
        if not target.is_file() or sha256_file(target) != sha:
            raise GateError("Approved requirements content changed: " + rel)
    approval = gate._safe_repo_path(ref["approval_path"])
    if not approval.is_file() or sha256_file(approval) != ref["approval_sha256"]:
        raise GateError("Requirements approval changed")
    record = read_json(approval)
    if not approval.is_relative_to(directory / "approvals") or record.get("discovery_id") != manifest["discovery_id"] or record.get("manifest_sha256") != ref["sha256"] or record.get("revision") != manifest["revision"]:
        raise GateError("Requirements approval does not match the manifest")
    return manifest


def check_contract(root: Path, ref: dict, contract: dict):
    manifest = verify_reference(root, ref)
    if contract.get("requirements_ref") != ref:
        raise GateError("Contract must bind the exact approved requirements_ref")
    if {a.get("id") for a in contract.get("acceptance_items", []) if isinstance(a, dict)} != set(manifest["acceptance_ids"]):
        raise GateError("Contract acceptance IDs must preserve the approved requirements baseline")


class Discovery:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.control = self.root / ".aidevflow"
        self.active = self.control / "active-discovery.json"
        self.transaction = self.control / "discovery-handoff.json"

    def load(self):
        if not self.active.is_file():
            raise GateError("No active requirements discovery")
        identity = safe_id(read_json(self.active)["discovery_id"])
        directory = self.control / "discoveries" / identity
        state = read_json(directory / "state.json")
        events = sorted((directory / "events").glob("*.json"))
        if events:
            latest = read_json(events[-1])["state"]
            if latest.get("revision", -1) > state.get("revision", -1):
                state = latest  # Committed event is authoritative after interrupted snapshot replacement.
        if state.get("schema") != "aidevflow/discovery-v1" or state.get("task_id") != identity:
            raise GateError("Invalid discovery state")
        for ref in [{"path": state["request_snapshot_path"], "sha256": state["request_sha256"]}, *state.get("answers", [])]:
            target = Gate(self.root)._safe_repo_path(ref["path"])
            if not target.is_file() or sha256_file(target) != ref["sha256"]:
                raise GateError("Discovery source or recorded answer changed")
        if Gate(self.root).active_path.exists() and not self.transaction.exists():
            raise GateError("Conflicting active discovery and delivery pointers")
        return state, directory, None

    def _save(self, state, directory, event):
        state["revision"] += 1
        state["updated_at"] = utc_now()
        write_json_new(directory / "events" / f"{state['revision']:08d}.json", {"event": event, "state": state})
        write_json_atomic(directory / "state.json", state)

    @writes
    def answer(self, question_id: str, answer: str):
        state, directory, _ = self.load()
        if (state["status"] not in ("collecting", "awaiting_answers", "review_ready")
                or not state.get("manifest_path") or not answer.strip()):
            raise GateError("An open requirements question and a non-empty actual answer are required")
        manifest = read_json(Gate(self.root)._safe_repo_path(state["manifest_path"]))
        draft = read_json(Gate(self.root)._safe_repo_path(manifest["requirements_path"]))
        if question_id not in {q["id"] for q in draft["intent"]["open_questions"]}:
            raise GateError("Unknown requirements question")
        path = directory / "answers" / f"{state['revision'] + 1}.json"
        write_json_new(path, {"question_id": question_id, "answer": answer, "source": "operator_assertion", "recorded_at": utc_now()})
        state.setdefault("answers", []).append({"path": path.relative_to(self.root).as_posix(), "sha256": sha256_file(path)})
        state["status"] = "collecting"
        self._save(state, directory, "answer_recorded")
        return state

    @writes
    def create(self, request: str, *, request_text: str | None = None):
        gate = Gate(self.root)
        gate._assert_git_root()
        if not reviewed(self.root) or project_config(self.root).get("runtime") not in ("omp", "zscode"):
            raise GateError("Discovery requires omp or zscode with workflow_profile=reviewed-discovery-v1")
        if self.active.exists() or gate.active_path.exists() or self.transaction.exists():
            raise GateError("An active context or handoff already exists; resume it first")
        source = gate._safe_repo_path(request) if request_text is None else None
        content = source.read_bytes() if source is not None else request_text.encode("utf-8")
        if not content.decode("utf-8").strip():
            raise GateError("Request must be non-empty UTF-8")
        identity = "DISC-" + secrets.token_hex(8)
        directory = self.control / "discoveries" / identity
        write_bytes_new(directory / "source-request.md", content)
        state = {"schema": "aidevflow/discovery-v1", "task_id": identity, "current_stage": "discovery",
                 "status": "collecting", "revision": 0, "requirements_revision": 0,
                 "request_path": source.relative_to(self.root).as_posix() if source is not None else "request",
                 "request_snapshot_path": (directory / "source-request.md").relative_to(self.root).as_posix(),
                 "request_sha256": sha256_file(directory / "source-request.md"),
                 "execution_binding": binding(self.root), "evidence_epoch": 0, "requirements_ref": None}
        self._save(state, directory, "created")
        write_json_atomic(self.active, {"discovery_id": identity})
        return state

    @writes
    def record(self, draft: str):
        state, directory, _ = self.load()
        if self.transaction.exists() or state["status"] in ("approved", "delivery_started", "paused"):
            raise GateError("Revise approved discovery before recording a new draft")
        value = read_json(Gate(self.root)._safe_repo_path(draft))
        errors = validate_requirements(value)
        if errors:
            raise GateError("Invalid requirements: " + "; ".join(errors))
        sources = {"request", state["request_path"], state["request_snapshot_path"], *(ref["path"] for ref in state.get("answers", []))}
        if any(req["source"] not in sources for req in value["requirements"]):
            raise GateError("Requirement source must reference the snapshotted request or a Gate-recorded answer")
        from .receipts import completed_results, execution_context, read_invocations
        receipts = completed_results(read_invocations(directory), "requirements_analyst", digest(value), 0, None,
                                     execution_context(state), state["execution_binding"]["runtime"])
        if not receipts:
            raise GateError("Requirements require a completed current native analyst receipt")
        prior = [int(p.name) for p in (directory / "revisions").glob("*") if p.is_dir() and p.name.isdecimal()]
        revision = max([state["requirements_revision"], *prior]) + 1
        revision_dir = directory / "revisions" / str(revision)
        intent = value["intent"]
        answered = {item["question_id"] for item in intent.get("decisions", [])}
        if answered:
            raise GateError("Analyst cannot fabricate user decisions; resolve answers in the source discussion")
        blockers = [q["id"] for q in intent["open_questions"] if q["blocking"]]
        outputs = {"requirements.json": (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(),
                   "intent.md": render_intent(state["task_id"], "Requirements", intent).encode(),
                   "prd.md": value["prd"].encode()}
        files = {state["request_snapshot_path"]: state["request_sha256"]}
        files.update({ref["path"]: ref["sha256"] for ref in state.get("answers", [])})
        for name, content in outputs.items():
            path = revision_dir / name
            write_bytes_new(path, content)
            files[path.relative_to(self.root).as_posix()] = sha256_file(path)
        manifest = {"schema": "aidevflow/requirements-manifest-v1", "discovery_id": state["task_id"],
                    "revision": revision, "files": files, "acceptance_ids": sorted({a for r in value['requirements'] for a in r['acceptance_ids']}),
                    "requirements_path": (revision_dir / "requirements.json").relative_to(self.root).as_posix(),
                    "intent_path": (revision_dir / "intent.md").relative_to(self.root).as_posix()}
        path = revision_dir / "manifest.json"
        write_json_new(path, manifest)
        state.update(requirements_revision=revision, manifest_path=path.relative_to(self.root).as_posix(),
                     manifest_sha256=sha256_file(path), status="awaiting_answers" if blockers else "review_ready", blockers=blockers)
        state["review"] = {"intent_path": manifest["intent_path"],
                           "prd_path": (revision_dir / "prd.md").relative_to(self.root).as_posix(),
                           "requirements_path": manifest["requirements_path"],
                           "approval_command": None if blockers else f"/aidevflow-approve {state['task_id']} {revision} {state['manifest_sha256']}"}
        self._save(state, directory, "requirements_recorded")
        return state

    @writes
    def revise(self, reason: str):
        state, directory, _ = self.load()
        if not reason.strip() or self.transaction.exists():
            raise GateError("Revision needs a reason and no pending handoff")
        if state["status"] == "paused":
            raise GateError("Resume paused discovery through the native /aidevflow-flow entry first")
        state.update(status="collecting", requirements_ref=None, revision_reason=reason, review=None)
        self._save(state, directory, "revision_requested")
        return state

    @writes
    def cancel(self, reason: str):
        state, directory, _ = self.load()
        if not reason.strip() or self.transaction.exists():
            raise GateError("Cancellation needs a reason and no pending handoff")
        from .receipts import read_invocations
        if any(record.get("status") in ("dispatched", "started") for record in read_invocations(directory)):
            raise GateError("Confirm the pending native analyst has ended before cancelling discovery")
        state.update(status="cancelled", cancellation_reason=reason)
        self._save(state, directory, "cancelled")
        self.active.unlink()
        return state

    @writes
    def approve(self, identity: str, revision: int, sha256: str, by: str, utterance: str, source="operator_assertion", *, delivery_authorized: bool | None = None):
        state, directory, _ = self.load()
        if state["status"] == "approved" and identity == state["task_id"] and revision == state["requirements_revision"] and sha256 == state["manifest_sha256"]:
            verify_reference(self.root, state["requirements_ref"])
            return state
        if state["status"] != "review_ready" or identity != state["task_id"] or revision != state["requirements_revision"] or sha256 != state.get("manifest_sha256"):
            raise GateError("Approval must name the current review-ready discovery, revision and manifest SHA-256")
        if not by.strip() or not utterance.strip():
            raise GateError("Approval requires the actual operator and explicit approval statement")
        manifest = Gate(self.root)._safe_repo_path(state["manifest_path"])
        if sha256_file(manifest) != sha256:
            raise GateError("Requirements manifest changed before approval")
        for rel, sha in read_json(manifest)["files"].items():
            if sha256_file(Gate(self.root)._safe_repo_path(rel)) != sha:
                raise GateError("Requirements content changed before approval")
        path = directory / "approvals" / f"{revision}.json"
        record = {"discovery_id": identity, "revision": revision, "manifest_sha256": sha256,
                  "by": by, "utterance": utterance, "source": source, "recorded_at": utc_now()}
        if delivery_authorized is not None:
            record["delivery_authorized"] = delivery_authorized
        if path.exists():
            old = read_json(path)
            if any(old.get(k) != v for k, v in record.items() if k != "recorded_at"):
                raise GateError("Conflicting approval record already exists")
        else:
            write_json_new(path, record)
        state.update(status="approved", requirements_ref={"path": state["manifest_path"], "sha256": sha256,
                     "approval_path": path.relative_to(self.root).as_posix(), "approval_sha256": sha256_file(path)})
        if delivery_authorized is not None:
            state["delivery_authorized"] = delivery_authorized
        self._save(state, directory, "approved")
        return state

    @writes
    def start_delivery(self):
        gate = Gate(self.root)
        if not self.transaction.exists() and not self.active.exists() and gate.active_path.exists():
            state, _, _ = gate._load_active()
            if state.get("workflow_profile") == PROFILE:
                return {"task_id": state["task_id"], "status": state["status"], "requirements_ref": state["requirements_ref"]}
            raise GateError("Another delivery is already active")
        if self.transaction.exists():
            tx = read_json(self.transaction)
        else:
            state, directory, _ = self.load()
            if state["status"] != "approved":
                raise GateError("Explicit requirements approval is required before delivery")
            if state.get("delivery_authorized") is False:
                raise GateError("Requirements were approved for saving only; use the native /aidevflow-flow entry to authorize delivery")
            verify_reference(self.root, state["requirements_ref"])
            tx = {"discovery_id": state["task_id"], "task_id": "ADF-" + secrets.token_hex(12),
                  "requirements_ref": state["requirements_ref"], "request_path": state["request_snapshot_path"]}
            write_json_new(self.transaction, tx)
        manifest = verify_reference(self.root, tx["requirements_ref"])
        directory = self.control / "discoveries" / safe_id(tx["discovery_id"])
        run = gate.runs / safe_id(tx["task_id"])
        if gate.active_path.exists() and read_json(gate.active_path)["task_id"] != tx["task_id"]:
            raise GateError("Handoff conflicts with another active delivery")
        if not (run / "run-state.json").exists():
            # The factory writes the reviewed binding in its very first event.
            gate._create_intake(tx["request_path"], tx["task_id"], tx["requirements_ref"])
        state = read_json(run / "run-state.json")
        if state.get("requirements_ref") != tx["requirements_ref"]:
            raise GateError("Handoff run does not match approved requirements")
        write_json_atomic(gate.active_path, {"task_id": tx["task_id"], "state_path": (run / "run-state.json").relative_to(self.root).as_posix()})
        discovery_state = read_json(directory / "state.json")
        if discovery_state["status"] != "delivery_started":
            discovery_state.update(status="delivery_started", delivery_task_id=tx["task_id"])
            self._save(discovery_state, directory, "delivery_started")
        self.active.unlink(missing_ok=True)
        # Retain an immutable completed handoff before clearing the recovery marker.
        completed = directory / "handoff.json"
        if not completed.exists():
            write_json_new(completed, tx)
        self.transaction.unlink()
        return {"task_id": tx["task_id"], "status": state["status"], "requirements_ref": tx["requirements_ref"]}
