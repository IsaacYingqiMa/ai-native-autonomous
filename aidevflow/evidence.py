"""Short physical paths for immutable bundles; logical paths live in the manifest."""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Any


class BundleWriter:
    def __init__(self, control: Path):
        identity = secrets.token_hex(8)
        self.directory = control / "bundles" / (".pending-" + identity)
        self.destination = control / "bundles" / identity
        self.directory.mkdir(parents=True)

    def copy(self, source: Path, logical_path: str) -> dict[str, Any]:
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        relative = "objects/" + digest
        target = self.directory / relative
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        elif target.read_bytes() != content:
            raise OSError("Bundle object content does not match its digest")
        return {"path": relative, "source_path": logical_path, "sha256": digest}

    def publish(self) -> Path:
        # Nothing points at the staging directory. Interrupted writes remain
        # unreferenced for diagnosis; only a complete manifest is published.
        if not (self.directory / "BUNDLE.json").is_file():
            raise OSError("Cannot publish a bundle without a manifest")
        os.replace(self.directory, self.destination)
        return self.destination
