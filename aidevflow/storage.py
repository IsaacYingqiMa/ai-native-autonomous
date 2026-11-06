"""Shared durable files and hashes; independent of either workflow engine."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

INFRA_FAILURES = (
    "no tests ran", "error collecting", "moduleNotFoundError", "importerror",
    "syntaxerror", "command not found", "no such file or directory", "could not start test command",
)
ASSERTION_FAILURE_MARKERS = ("assertionerror", "assertion failure", "fail:", "failed", "failure", "tests failed", "test failed", "expected:", "actual:")

class GateError(Exception):
    """An expected workflow or input validation error."""

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
