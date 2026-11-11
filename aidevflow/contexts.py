"""Public, read-only role assignments assembled from Gate-owned inputs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .gate import Gate, GateError
from .receipts import ROLE_ALIASES, execution_context
from .runtimes import skills_root


def role_context(root: Path, role: str) -> dict[str, Any]:
    if role == "requirements_analyst":
        from .discovery import Discovery
        state, _, _ = Discovery(root).load()
        if state["status"] not in ("collecting", "awaiting_answers", "review_ready"):
            raise GateError("Requirements role requires editable Discovery")
        task = (f"Read {state['request_snapshot_path']} and .aidevflow/schemas/requirements.schema.json. "
                f"Read these Gate-recorded answers: {state.get('answers', [])}. "
                "Return one complete requirements-v1 object with Intent, PRD, stable requirement and acceptance IDs, "
                "sources and Given/When/Then scenarios. Do not invent user decisions; intent.decisions must be empty. "
                "Keep unresolved material questions blocking. Incorporate actual recorded answers, never ask them again. "
                'Do not implement, write files or claim approval. Native yield syntax: yield({"data": <complete object>}).')
        if state.get("manifest_path"):
            task += (f" Read the previous requirements manifest {state['manifest_path']} and its referenced requirements JSON. "
                     "Preserve stable IDs and previously resolved scope unless actual recorded human feedback changes them. "
                     "An earlier approval does not approve this revised output.")
        return {"role": role, "execution_context": execution_context(state), "task_call": {
            "context": "Read-only discovery; human approval is required before delivery.",
            "tasks": [{"agent": "aidevflow-requirements-analyst", "task": task, "solutionSpace": "source-backed requirements only"}]}}
    gate = Gate(root)
    state, _, contract = gate._load_active()
    names = {value: key for key, value in ROLE_ALIASES.items()}
    if role not in names:
        raise GateError("Unknown AIDevFlow role")
    if state.get("status") in ("automation_stalled", "needs_human", "archived", "delivery_ready", "delivery_ready_with_exceptions", "awaiting_user_input"):
        raise GateError("Resolve the Gate checkpoint before dispatching another role")
    if role == "intent_analyst":
        if state["current_stage"] != "intent" or contract is not None:
            raise GateError("Intent Analyst requires the Intent stage")
        task = (f"Read {state['request_snapshot_path']} and .aidevflow/schemas/intent-draft.schema.json. "
                "Return the complete IntentDraft JSON via native yield. Record only decision-driving ambiguities as blocking questions. "
                "Preserve array types. Do not write any files, author a Contract or implement code.")
    elif role == "artifact_reviewer":
        from .services.design import preparation, subjects
        from .gate import read_json, sha256_file
        target = state.get("artifact_review_target")
        if not target or (contract and not state.get("engineering_revision")):
            raise GateError("Prepare the current spec/plan review before dispatch")
        request = read_json(root / target["path"])
        if sha256_file(root / target["path"]) != target["sha256"] or subjects(gate, state, request["kind"]) != request["subjects"]:
            raise GateError("Review inputs are stale")
        task = (f"Read {target['path']} (SHA-256 {target['sha256']}) and .aidevflow/schemas/artifact-review.schema.json. "
                "Independently inspect the exact subjects against the original request, approved Intent/PRD and scenarios. "
                "Do not reopen resolved business questions, prune approved scope, invent human approval or modify files. "
                "For engineering corrections also inspect the execution Contract draft in subjects: bounded technical file/check refinements must follow the same approved behavior, not expand scope or weaken checks. "
                "Report artifact defects as changes_required with concrete repair instructions. Only a genuine new business "
                "decision uses requires_decision. For pass, findings must be empty and coverage must map every unchanged "
                "approved acceptance ID to S IDs, covering all specifications. Return one artifact-review-v1 object "
                f"with task_id {state['task_id']}, kind {request['kind']} and input_sha256 {target['sha256']}.")
    elif role == "contract_planner":
        if state.get("status") != "intent_approved" or contract is not None:
            raise GateError("Planner requires finalized Intent and no registered Contract")
        from .services.design import require_review
        if state.get("method_preparation"):
            require_review(gate, state, "spec")
            require_review(gate, state, "plan")
        task = (f"Read {state['request_snapshot_path']}, {state['intent_path']}, .aidevflow/contract.example.json "
                "and .aidevflow/schemas/contract.schema.json. Inspect relevant business project context only. "
                f"Use the assigned task_id {state['task_id']}. Available method assets live in {skills_root(root).relative_to(root).as_posix()}. "
                "Use method.profile=zsspec-docs-v2, method.slug from method-preparation.json and stages "
                "design=[zsspec-spec,zsspec-plan], build=[zsspec-apply], review=[]. "
                "Read the pinned method-preparation and both passing artifact reviews from status. Consume the real "
                "spec.md and plan.md. Copy the approved acceptance_map from the spec review exactly. "
                "Set design_artifacts to those two exact files and work_plan_required=true. Declare real argv checks "
                "covering all approved acceptance IDs, including static review. test_files may be empty when real "
                "build/runtime/metric checks suffice. For testing tasks declare method.task_tests mapping card IDs "
                "to their concrete test files; test code is generated/reviewed/frozen during that active Build task. "
                "Inspect existing package scripts and previous passing execution argv before choosing commands; do not invent a runner or repeat an already diagnosed invalid command. "
                "Include concrete runtime scenarios for UI/stateful criteria, suitable replay scripts and evidence paths. "
                "Preserve requirements_ref. Return exactly one Contract JSON; do not implement or write state.")

    else:
        bundle = state.get("verifier_bundle")
        if state["current_stage"] != "verify" or not bundle:
            raise GateError("Prepare the verifier bundle in Verify first")
        task = (f"Independently assess only the frozen bundle {bundle['path']} with SHA-256 {bundle['sha256']}. "
                f"Read {bundle['path']}/BUNDLE.json first, then its physical object paths and verdict-schema.json. "
                "Do not inspect writer conversation, project files outside the bundle, memory, tools or previous reports. "
                "Do not run commands, repair, write files or delegate. Return one complete verdict JSON through native yield. "
                "Read candidate-binding.json: raw object hashes and normalized candidate hashes have different meanings; the machine-checked bindings establish their identity. "
                "Use short BUNDLE.json files[].id values (E0001 etc.) in items[].evidence; do not transcribe SHA-256 paths. "
                "Exact files[].path values remain valid for older bundles. "
                "Do not prepend the bundle directory, append line numbers, descriptions or parentheses. "
                "Put explanations in reason; report absent evidence honestly. "
                "Inspect baseline revisions and evidence quarantine history. Skipped tests are not passes. "
                "For approved executed exceptions, report affected acceptance items as waived, cite their exact waiver object and state residual risks.")
        from .services.verification import correction_prompt
        task += correction_prompt(gate, state)
    from .runtimes import selected
    from .runtimes.registry import get_runtime
    if role == "artifact_reviewer" and request["kind"] == "blind":
        task = (f"Read only {target['path']} (SHA-256 {target['sha256']}) and its approved original request/Intent/PRD "
                "plus relevant specs/modules knowledge. Do not read spec.md, plan.md, candidate drafts or writer conversation. "
                "Independently derive 3-5 observable outcomes that must be verified for the approved request. "
                f"Return artifact-review-v1, task_id {state['task_id']}, kind blind, input_sha256 {target['sha256']}, "
                "outcome pass, coverage {}, and findings containing those 3-5 concrete input-to-expected observations.")
    task += " " + get_runtime(selected(root, state)).completion_hint()
    if state.get("method_preparation") and role == "contract_planner":
        task += f" Read preparation {state['method_preparation']['path']} and these passing reviews: {state.get('artifact_reviews', {})}."
    if state.get("requirements_ref") and role == "contract_planner":
        task += (f" Read the approved requirements manifest {state['requirements_ref']['path']} and its PRD/scenarios. "
                 f"Copy requirements_ref exactly into Contract: {state['requirements_ref']}. "
                 "Preserve all approved acceptance IDs exactly; do not restart product clarification or change scope.")
    return {"schema": "aidevflow/role-context-v1", "role": role, "execution_context": execution_context(state),
            "task_call": {"context": f"AIDevFlow task {state['task_id']}. Gate owns state and acceptance; this role is read-only.",
                          "tasks": [{"agent": names[role], "task": task, "solutionSpace": "bounded by the immutable request and current Gate checkpoint"}]}}
