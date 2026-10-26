"""Optional method adapters for generated documents; never edit method Skills."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def replace_status(text: str, row_id: str, status: str) -> str:
    """Replace exactly one named table row's status cell, preserving other text."""
    output: list[str] = []
    status_index: int | None = None
    found = 0
    for line in text.splitlines(keepends=True):
        cells = re.split(r"(?<!\\)\|", line)
        if len(cells) < 4:
            status_index = None
            output.append(line)
            continue
        clean = [cell.strip().strip("`* ") for cell in cells]
        if "状态" in clean or "status" in [cell.lower() for cell in clean]:
            status_index = next(i for i, cell in enumerate(clean) if cell.lower() in ("状态", "status"))
        elif status_index is not None and clean[1] == row_id:
            if status_index >= len(cells) - 1:
                raise ValueError(f"Malformed status row: {row_id}")
            cell = cells[status_index]
            left = cell[:len(cell) - len(cell.lstrip())]
            right = cell[len(cell.rstrip()):]
            cells[status_index] = left + status + right
            line = "|".join(cells)
            found += 1
        output.append(line)
    if found != 1:
        raise ValueError(f"Expected one status-table row {row_id!r}, found {found}")
    return "".join(output)


def validate_projections(plan: dict[str, Any], contract: dict[str, Any], root: Path | None = None) -> list[str]:
    bindings = plan.get("projections", [])
    if not isinstance(bindings, list):
        return ["projections must be an array"]
    if not bindings:
        return []
    if contract.get("method", {}).get("name") != "zsspec":
        return ["document projections currently require the zsspec adapter"]
    known = {
        "work_item": {item["id"] for item in plan.get("items", []) if isinstance(item, dict) and "id" in item},
        "check": {item.get("id") for item in contract.get("required_checks", []) if isinstance(item, dict)},
        "acceptance": {item.get("id") for item in contract.get("acceptance_items", []) if isinstance(item, dict)},
    }
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "row_id", "source_kind", "source_id"}:
            errors.append("each projection needs exactly path, row_id, source_kind, source_id")
            continue
        if any(not isinstance(value, str) or not value.strip() for value in binding.values()):
            errors.append("projection fields must be non-empty strings")
            continue
        if binding["path"] not in contract.get("design_artifacts", []):
            errors.append("projection path must be a declared design_artifact")
        key = (binding["path"], binding["row_id"])
        if key in seen:
            errors.append("a document row can have only one projection")
        seen.add(key)
        if binding["source_id"] not in known.get(binding["source_kind"], set()):
            errors.append("projection references an unknown work item, check, or acceptance item")
        if root is not None and binding["path"] in contract.get("design_artifacts", []):
            try:
                path = (root / binding["path"]).resolve()
                path.relative_to(root.resolve())
                replace_status(path.read_bytes().decode("utf-8"), binding["row_id"], "待执行")
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(str(exc))
    return errors


def semantic_content(rel: str, content: bytes, plan: dict[str, Any] | None) -> bytes:
    """Ignore only explicitly mapped status cells when identifying a specification."""
    bindings = [item for item in (plan or {}).get("projections", []) if item["path"] == rel]
    if not bindings:
        return content
    text = content.decode("utf-8")
    for binding in bindings:
        text = replace_status(text, binding["row_id"], "<AIDevFlow-status>")
    return text.encode("utf-8")


def projected_documents(root: Path, plan: dict[str, Any], state: dict[str, Any], checks: dict[str, str]) -> dict[Path, str]:
    """Prepare status projections in memory; the Gate performs the writes."""
    documents: dict[Path, str] = {}
    work = {item["id"]: item["status"] for item in state.get("work_items", [])}
    statuses = {"not_started": "未开始", "in_progress": "进行中", "completed": "已完成"}
    for binding in plan.get("projections", []):
        path = (root / binding["path"]).resolve()
        path.relative_to(root.resolve())
        text = documents.get(path, path.read_bytes().decode("utf-8"))
        kind, source = binding["source_kind"], binding["source_id"]
        if kind == "work_item":
            value = statuses[work[source]]
        elif kind == "check":
            value = checks.get(source, "待执行")
        else:
            value = "已完成" if state.get("status") in ("accepted", "delivery_ready") or state.get("current_stage") == "delivery_ready" else "进行中"
        documents[path] = replace_status(text, binding["row_id"], value)
    return documents
