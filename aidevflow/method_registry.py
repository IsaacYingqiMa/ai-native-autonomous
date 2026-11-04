"""Trusted method adapters. Discovery never installs executable code."""
from __future__ import annotations

from typing import Protocol, Any

V2 = "zsspec-managed-v2"


class MethodAdapter(Protocol):
    name: str
    profile: str
    api_version: int

    def describe(self, source: Any) -> dict: ...
    def validate(self, contract: dict) -> list[str]: ...
    def semantic_content(self, relative: str, content: bytes, plan: dict | None) -> bytes: ...


def adapter_for(contract: dict | None) -> MethodAdapter | None:
    method = contract.get("method") if isinstance(contract, dict) else None
    if not isinstance(method, dict):
        return None
    if method.get("profile") == V2:
        from .method_adapters.zsspec import ZsspecAdapter
        return ZsspecAdapter()
    return None


def scripted(contract: dict | None) -> bool:
    return adapter_for(contract) is not None
