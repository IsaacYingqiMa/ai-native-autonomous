"""Public, read-only role assignments assembled from Gate-owned inputs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .gate import Gate, GateError
from .receipts import ROLE_ALIASES, execution_context
from .runtimes import skills_root


def role_context(root: Path, role: str) -> dict[str, Any]:
    gate = Gate(root)
    state, _, contract = gate._load_active()
    names = {value: key for key, value in ROLE_ALIASES.items()}
    if role not in names:
        raise GateError("Unknown AIDevFlow role")
    if state.get("status") in ("needs_human", "archived", "delivery_ready", "delivery_ready_with_exceptions", "awaiting_user_input"):
        raise GateError("Resolve the Gate checkpoint before dispatching another role")
    if role == "intent_analyst":
        if state["current_stage"] != "intent" or contract is not None:
            raise GateError("Intent Analyst requires the Intent stage")
        task = (f"Read {state['request_snapshot_path']} and .aidevflow/schemas/intent-draft.schema.json. "
                "Return the complete IntentDraft JSON via native yield. Record only decision-driving ambiguities as blocking questions. "
                "Preserve array types. Do not write any files, author a Contract or implement code.")
    elif role == "contract_planner":
        if state.get("status") != "intent_approved" or contract is not None:
            raise GateError("Planner requires finalized Intent and no registered Contract")
        task = (f"Read {state['request_snapshot_path']}, {state['intent_path']}, .aidevflow/contract.example.json "
                "and .aidevflow/schemas/contract.schema.json. Inspect relevant business project context only. "
                f"Use the assigned task_id {state['task_id']}. Available method assets live in {skills_root(root).relative_to(root).as_posix()}. "
                "For zsspec use zsspec-managed-v1 with its complete Design/Build/Review stages, one cohesive work item for a small feature, "
                "work_plan_required=true, concrete test argv and required_checks covering all acceptance IDs. "
                "Do not use an unavailable test runner. Return one complete Contract JSON via native yield; do not implement or write state. "
                "specs/zsspec.yml and method assets are project setup inputs, never generated design artifacts. "
                "If zsspec.yml is absent use its documented defaults; its absence is pinned at Contract registration. "
                "Use public schemas and examples; do not read framework implementation code or private observation logs.")
    else:
        bundle = state.get("verifier_bundle")
        if state["current_stage"] != "verify" or not bundle:
            raise GateError("Prepare the verifier bundle in Verify first")
        task = (f"Independently assess only the frozen bundle {bundle['path']} with SHA-256 {bundle['sha256']}. "
                f"Read {bundle['path']}/BUNDLE.json first, then its physical object paths and verdict-schema.json. "
                "Do not inspect writer conversation, project files outside the bundle, memory, tools or previous reports. "
                "Do not run commands, repair, write files or delegate. Return one complete verdict JSON through native yield. "
                "Each items[].evidence string must exactly equal one BUNDLE.json files[].path value, "
                "for example objects/<sha256> or diff.patch, relative to the bundle. "
                "Do not prepend the bundle directory, append line numbers, descriptions or parentheses. "
                "Put explanations in reason; report absent evidence honestly. "
                "Inspect baseline revisions and evidence quarantine history. Skipped tests are not passes. "
                "For approved executed exceptions, report affected acceptance items as waived, cite their exact waiver object and state residual risks.")
    task += (' Native completion syntax is yield({"data": <the complete output object>}). '
             'Do not put output fields directly at the top level of yield arguments; never submit {}. '
             'The runtime removes the data envelope before Gate registration.')
    return {"schema": "aidevflow/role-context-v1", "role": role, "execution_context": execution_context(state),
            "task_call": {"context": f"AIDevFlow task {state['task_id']}. Gate owns state and acceptance; this role is read-only.",
                          "tasks": [{"agent": names[role], "task": task, "solutionSpace": "bounded by the immutable request and current Gate checkpoint"}]}}
