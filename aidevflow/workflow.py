"""Native human interaction routing. No model-authored approval or extra agent loop."""
from __future__ import annotations

import secrets
from pathlib import Path

from .discovery import Discovery, reviewed, verify_reference
from .gate import Gate, GateError, read_json, safe_id, sha256_file, utc_now, write_json_atomic, write_json_new
from .locking import writes


class Workflow:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.control = self.root / ".aidevflow"
        self.discovery = Discovery(self.root)

    def _result(self, state):
        if state["status"] == "review_ready":
            return {"action": "review", "discovery_id": state["task_id"]}
        if state["status"] == "paused":
            return {"action": "paused"}
        return {"action": "discuss", "state": state}

    @writes
    def enter(self, text: str = "", discussion_only: bool = False):
        """Called only by an explicit native user command, never by session restart."""
        if not reviewed(self.root):
            return {"action": "legacy"}
        if self.discovery.transaction.exists():
            if discussion_only:
                return {"action": "delivery_active", "status": "handoff_pending"}
            return {"action": "deliver", **self.discovery.start_delivery(), "user_text": text}
        gate = Gate(self.root)
        if gate.active_path.exists():
            state, _, _ = gate._load_active()
            if discussion_only:
                return {"action": "delivery_active", "task_id": state["task_id"], "status": state["status"]}
            return {"action": "deliver", "task_id": state["task_id"], "status": state["status"], "user_text": text}
        if not self.discovery.active.exists():
            if not text.strip():
                return {"action": "request_needed"}
            try:
                candidate = gate._safe_repo_path(text.strip()) if len(text) < 250 and "\n" not in text else None
            except (GateError, OSError, ValueError):
                candidate = None
            if candidate is not None and candidate.is_file():
                state = self.discovery.create(text.strip())
            else:
                state = self.discovery.create("request", request_text=text)
            state, directory, _ = self.discovery.load()
            state["discussion_only"] = discussion_only
            self.discovery._save(state, directory, "entry_selected")
            return self._result(state)
        state, directory, _ = self.discovery.load()
        # Pausing preserves the pending checkpoint. Only a new human entry resumes it.
        if state["status"] == "paused":
            state["status"] = state.pop("resume_status", "collecting")
            self.discovery._save(state, directory, "human_resumed")
        if text.strip() and text.strip().lower() not in ("继续", "resume", "continue", "开始", "开始开发", "继续执行", "确认开始"):
            self._feedback(state, directory, text)
        if state["status"] == "approved":
            if discussion_only:
                return {"action": "approved_saved"}
            if state.get("delivery_authorized") is False:
                state["delivery_authorized"] = True
                self.discovery._save(state, directory, "human_authorized_delivery")
            return {"action": "deliver", **self.discovery.start_delivery()}
        # /flow is the explicit full-workflow entry; /discover remains discussion-only.
        if state.get("discussion_only", False) != discussion_only:
            state["discussion_only"] = discussion_only
            self.discovery._save(state, directory, "entry_selected")
        return self._result(state)

    def _feedback(self, state, directory, text, *, interaction=None):
        path = directory / "answers" / f"feedback-{secrets.token_hex(12)}.json"
        write_json_new(path, {"answer": text, "source": "native_user_input", "recorded_at": utc_now()})
        state.setdefault("answers", []).append({"path": path.relative_to(self.root).as_posix(), "sha256": sha256_file(path)})
        state.update(status="collecting", review=None, requirements_ref=None)
        if interaction is not None:
            state["last_review_interaction"] = interaction
        self.discovery._save(state, directory, "human_feedback")

    @writes
    def prepare_review(self, session_id: str):
        if not session_id.strip():
            raise GateError("Review requires a native session identity")
        state, directory, _ = self.discovery.load()
        if state["status"] != "review_ready":
            return {"action": "wait", "status": state["status"]}
        manifest_path = Gate(self.root)._safe_repo_path(state["manifest_path"])
        if sha256_file(manifest_path) != state["manifest_sha256"]:
            raise GateError("Review manifest changed")
        manifest = read_json(manifest_path)
        for rel, expected in manifest["files"].items():
            if sha256_file(Gate(self.root)._safe_repo_path(rel)) != expected:
                raise GateError("Review content changed: " + rel)
        requirements = read_json(Gate(self.root)._safe_repo_path(manifest["requirements_path"]))
        prd = manifest_path.parent / "prd.md"
        intent = Gate(self.root)._safe_repo_path(manifest["intent_path"])
        token = "REVIEW-" + secrets.token_hex(16)
        record = {"discovery_id": state["task_id"], "state_revision": state["revision"],
                  "requirements_revision": state["requirements_revision"], "manifest_sha256": state["manifest_sha256"],
                  "session_id": session_id, "discussion_only": state.get("discussion_only", False),
                  "status": "pending", "created_at": utc_now()}
        write_json_new(directory / "review-interactions" / f"{token}.json", record)
        def preview(path):
            content = path.read_text(encoding="utf-8")
            return content if len(content) <= 24000 else content[:24000] + "\n[内容较长，请打开完整文件审查]"
        return {"action": "review", "token": token, "discovery_id": state["task_id"],
                "revision": state["requirements_revision"], "discussion_only": record["discussion_only"],
                "manifest_sha256": state["manifest_sha256"],
                "intent": preview(intent), "prd": preview(prd), "scenarios": requirements["scenarios"],
                "paths": {"intent": intent.relative_to(self.root).as_posix(), "prd": prd.relative_to(self.root).as_posix(),
                          "requirements": manifest["requirements_path"]}}

    @writes
    def respond(self, discovery_id: str, token: str, session_id: str, choice: str, feedback: str = ""):
        directory = self.control / "discoveries" / safe_id(discovery_id)
        path = directory / "review-interactions" / f"{safe_id(token)}.json"
        record = read_json(path)
        if record["session_id"] != session_id or record["discovery_id"] != discovery_id:
            raise GateError("Review response belongs to another session or discovery")
        if choice not in ("pause", "revise", "approve_start", "approve_save"):
            raise GateError("Unknown human review choice")
        if record["discussion_only"] and choice == "approve_start":
            raise GateError("Discussion-only review did not authorize starting delivery")
        if choice == "revise" and not feedback.strip():
            raise GateError("Revision feedback is required")
        if record.get("choice") and (record["choice"] != choice or record.get("feedback", "") != feedback):
            raise GateError("A review response cannot be changed or replayed as another choice")
        if record.get("status") == "completed":
            return {**record["result"], "replayed": True}
        if record.get("choice") == "approve_start" and self.discovery.transaction.exists():
            tx = read_json(self.discovery.transaction)
            if tx["discovery_id"] != discovery_id or tx["requirements_ref"]["sha256"] != record["manifest_sha256"]:
                raise GateError("Review recovery conflicts with the pending handoff")
            result = {"action": "deliver", **self.discovery.start_delivery()}
            record.update(status="completed", result=result)
            write_json_atomic(path, record)
            return {**result, "replayed": False}
        if record.get("choice") == "approve_start" and not self.discovery.active.exists() and Gate(self.root).active_path.exists():
            state, _, _ = Gate(self.root)._load_active()
            manifest = verify_reference(self.root, state.get("requirements_ref", {}))
            if manifest["discovery_id"] != discovery_id or state["requirements_ref"]["sha256"] != record["manifest_sha256"]:
                raise GateError("Review recovery conflicts with the active delivery")
            result = {"action": "deliver", "task_id": state["task_id"], "status": state["status"]}
            record.update(status="completed", result=result)
            write_json_atomic(path, record)
            return {**result, "replayed": False}
        state, current, _ = self.discovery.load()
        if state["task_id"] != discovery_id or state["requirements_revision"] != record["requirements_revision"] or state.get("manifest_sha256") != record["manifest_sha256"]:
            raise GateError("Review is stale; review the current requirements revision")
        interaction = {"token": token, "choice": choice}
        if (record.get("status") == "applying" and choice in ("pause", "revise")
                and state["revision"] == record["state_revision"] + 1
                and state.get("last_review_interaction") == interaction
                and state["status"] == ("paused" if choice == "pause" else "collecting")):
            # The event committed but the interaction reply did not. Do not append feedback twice.
            result = {"action": "paused" if choice == "pause" else "discuss"}
            record.update(status="completed", result=result)
            write_json_atomic(path, record)
            return {**result, "replayed": False}
        resuming_approval = record.get("choice") in ("approve_start", "approve_save") and state["status"] == "approved"
        if not resuming_approval and (state["revision"] != record["state_revision"] or state["status"] != "review_ready"):
            raise GateError("Review state changed while waiting for a human response")
        # Persist the actual selected action before approval or handoff can be interrupted.
        record.update(choice=choice, feedback=feedback, status="applying")
        write_json_atomic(path, record)
        if choice == "pause":
            state.update(status="paused", resume_status="review_ready", last_review_interaction=interaction)
            self.discovery._save(state, current, "human_paused")
            result = {"action": "paused"}
        elif choice == "revise":
            self._feedback(state, current, feedback, interaction=interaction)
            result = {"action": "discuss"}
        else:
            approved = self.discovery.approve(discovery_id, record["requirements_revision"], record["manifest_sha256"],
                                              "omp user", "确认并开始自主开发" if choice == "approve_start" else "确认，仅保存需求",
                                              "native_review_choice", delivery_authorized=choice == "approve_start")
            result = ({"action": "deliver", **self.discovery.start_delivery()} if choice == "approve_start"
                      else {"action": "approved_saved", "requirements_ref": approved["requirements_ref"]})
        record.update(status="completed", result=result)
        write_json_atomic(path, record)
        return {**result, "replayed": False}
