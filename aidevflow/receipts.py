"""Execution-result validation independent of a particular hook transport.

Legacy Claude receipts remain readable. This is a protocol seam, not a claim
that a second runtime has already been implemented or independently verified.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROLE_ALIASES = {
    "aidevflow-intent-analyst": "intent_analyst",
    "aidevflow-contract-planner": "contract_planner",
    "aidevflow-independent-verifier": "verifier",
}


def completed_results(entries: list[dict[str, Any]], role: str, digest: str,
                      epoch: int, bundle_sha256: str | None) -> list[dict[str, Any]]:
    return [entry for entry in entries
            if entry.get("role", ROLE_ALIASES.get(entry.get("agent_type"))) == role
            and entry.get("status") == "completed"
            and entry.get("result_sha256") == digest
            and entry.get("evidence_epoch", 0) == epoch
            and (bundle_sha256 is None or entry.get("bundle_sha256") == bundle_sha256)]


def read_invocations(run_dir: Path) -> list[dict[str, Any]]:
    from .gate import read_json
    return [read_json(path) for path in sorted((run_dir / "agent-invocations").glob("*.json"))]
