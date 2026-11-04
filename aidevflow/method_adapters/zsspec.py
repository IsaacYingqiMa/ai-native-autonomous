from __future__ import annotations

import ast
import re
from pathlib import Path

from ..method_registry import V2


class ZsspecAdapter:
    name = "zsspec"
    profile = V2
    api_version = 1
    stages = {"design": ["zsspec-req", "zsspec-plan", "zsspec-test"],
              "build": ["zsspec-apply"], "review": ["zsspec-test", "zsspec-done"]}
    support = ["zsspec-standard", "zsspec-change"]
    script = "zsspec-standard/scripts/zsspec_check.py"

    def describe(self, source: Path) -> dict:
        from ..gate import GateError
        names = sorted({n for group in self.stages.values() for n in group} | set(self.support))
        missing = [n for n in names if not (source / n / "SKILL.md").is_file()]
        if missing:
            raise GateError("Current zsspec adapter is missing: " + ", ".join(missing))
        required = [self.script, "zsspec-plan/stage/des.md", "zsspec-plan/stage/task.md", "zsspec-plan/stage/chk.md",
                    "zsspec-standard/spec-rules.md"]
        if any(not (source / p).is_file() for p in required):
            raise GateError("Incomplete zsspec plan/script distribution")
        tree = ast.parse((source / self.script).read_text(encoding="utf-8-sig"))
        funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        if not {"cmd_status", "cmd_verify", "cmd_gate", "cmd_baseline", "cmd_set", "cmd_report", "cmd_close"} <= funcs:
            raise GateError("Unsupported zsspec script protocol; adapter review required")
        plan = (source / "zsspec-plan/SKILL.md").read_text(encoding="utf-8-sig")
        if any(flag not in plan for flag in ("--des", "--task", "--chk")):
            raise GateError("Unknown zsspec plan routing; adapter review required")
        return {"schema": "aidevflow/resolved-method-v1", "adapter": self.name, "profile": self.profile,
                "adapter_api_version": self.api_version, "resolver_version": 1, "stages": self.stages,
                "script": self.script, "source_files": required + [n + "/SKILL.md" for n in names],
                "operations": {"status": "read", "verify": "staged_write", "gate": "read",
                               "baseline": "design_write", "set": "gate_projection", "report": "staged_write",
                               "close": "user_acceptance"},
                "review_policy": {"ordinary_design": "ai_coordinator", "test_code": "ai_coordinator",
                                  "scope_change": "human", "business_acceptance": "human", "release": "human"},
                "auto_activation": False}

    def validate(self, contract: dict) -> list[str]:
        method = contract.get("method") or {}
        errors = []
        if method.get("name") != self.name:
            errors.append("zsspec-managed-v2 requires method.name=zsspec")
        stages = method.get("stages")
        if not isinstance(stages, dict) or set(stages) != set(self.stages) or any(
                not isinstance(stages.get(phase), list) or any(not isinstance(n, str) for n in stages[phase])
                or stages[phase] != names
                for phase, names in self.stages.items()):
            errors.append("Managed v2 requires Design req/plan/test, Build apply, Review test/done")
        modules = method.get("modules")
        if (not isinstance(modules, list) or not modules
                or any(not isinstance(m, str) or not re.fullmatch(r"[\w\u4e00-\u9fff-]+", m) for m in modules)
                or len(set(modules)) != len(modules)):
            errors.append("Managed v2 requires unique method.modules (module names, not paths)")
        mapping = method.get("acceptance_map")
        canonical = {a["id"] for a in contract.get("acceptance_items", []) if isinstance(a, dict) and isinstance(a.get("id"), str)}
        if (not isinstance(mapping, dict) or set(mapping) != canonical or any(
                not isinstance(v, list) or not v or any(not isinstance(x, str) or not x.startswith("AC-") for x in v)
                or len(set(v)) != len(v) for v in mapping.values())):
            errors.append("Managed v2 requires method.acceptance_map covering exact canonical IDs with native AC ID arrays")
        return errors

    def semantic_content(self, relative: str, content: bytes, plan: dict | None) -> bytes:
        from ..adapters import table_semantic_content
        # Only declared row status cells are mutable after freezing. Header baseline
        # changes remain semantic and must go through a baseline revision.
        return table_semantic_content(relative, content, plan).replace(b"\r\n", b"\n")
