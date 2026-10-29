"""Execution-result validation independent of a particular hook transport.

Legacy Claude receipts remain readable. OMP v3 receipts use the same business
input and output checks without impersonating Claude hook events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROLE_ALIASES = {
    "aidevflow-intent-analyst": "intent_analyst",
    "aidevflow-contract-planner": "contract_planner",
    "aidevflow-independent-verifier": "verifier",
}


def execution_context(state: dict[str, Any]) -> dict[str, Any]:
    """Business inputs fixed when a role is dispatched, independent of Claude."""
    bundle = state.get("verifier_bundle") or {}
    return {"stage": state.get("current_stage"), "evidence_epoch": state.get("evidence_epoch", 0),
            "request_sha256": state.get("request_sha256"), "intent_sha256": state.get("intent_sha256"),
            "contract_sha256": state.get("contract_sha256"),
            "bundle_sha256": bundle.get("sha256"), "candidate_sha256": bundle.get("candidate_sha256")}


def completed_results(entries: list[dict[str, Any]], role: str, digest: str,
                      epoch: int, bundle_sha256: str | None,
                      context: dict[str, Any] | None = None,
                      runtime: str | None = None) -> list[dict[str, Any]]:
    return [entry for entry in entries
            if entry.get("role", ROLE_ALIASES.get(entry.get("agent_type"))) == role
            and (runtime is None or entry.get("runtime", "claude-code") == runtime)
            and entry.get("receipt_schema") in (None, "aidevflow/invocation-v1", "aidevflow/invocation-v2", "aidevflow/invocation-v3")
            and (runtime != "omp" or entry.get("receipt_schema") == "aidevflow/invocation-v3")
            and entry.get("status") == "completed"
            and entry.get("result_sha256") == digest
            and entry.get("evidence_epoch", 0) == epoch
            and (bundle_sha256 is None or entry.get("bundle_sha256") == bundle_sha256)
            and (entry.get("receipt_schema") not in ("aidevflow/invocation-v2", "aidevflow/invocation-v3") or (
                entry.get("execution_finished") is True
                and (entry.get("dispatch_observed") is True or entry.get("start_observed") is True)
                and (context is None or entry.get("context") == context)))]


def read_invocations(run_dir: Path) -> list[dict[str, Any]]:
    from .gate import read_json
    return [read_json(path) for path in sorted((run_dir / "agent-invocations").glob("*.json"))]
