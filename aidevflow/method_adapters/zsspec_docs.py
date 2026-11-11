"""Adapter for the five-skill zsspec 2 document protocol (no vendor scripts)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PROFILE = "zsspec-docs-v2"
STAGES = {"design": ["zsspec-spec", "zsspec-plan"], "build": ["zsspec-apply"], "review": []}
NAMES = {"zsspec-init", "zsspec-spec", "zsspec-plan", "zsspec-apply", "zsspec-archive"}


def enabled(contract):
    return isinstance(contract, dict) and contract.get("method", {}).get("profile") == PROFILE


def clean(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).replace("\r\n", "\n")


def parse_spec(text):
    from ..gate import GateError
    text = clean(text)
    for heading in ("需求", "边界", "规格", "设计", "记录"):
        if not re.search(r"^## " + heading + r"\s*$", text, re.M):
            raise GateError("spec.md is missing section: " + heading)
    for heading in ("方案", "模块与接口", "数据与状态", "取舍", "冲突声明"):
        if not re.search(r"^### " + heading + r"\s*$", text, re.M):
            raise GateError("spec.md is missing section: " + heading)
    result = {}
    for match in re.finditer(r"^### (S\d+)\s+(.+?)\n(.*?)(?=^### |^## |\Z)", text, re.M | re.S):
        ident, title, body = match.groups()
        if ident in result:
            raise GateError("Duplicate specification: " + ident)
        validation = re.search(r"^- 验证[：:]\s*(.+)$", body, re.M)
        if not validation or not re.search(r"^- 做什么[：:]\s*\S", body, re.M):
            raise GateError("Every specification needs behavior and executable validation: " + ident)
        result[ident] = {"id": ident, "title": title.strip(), "validation": validation[1].strip(), "body": body.strip()}
    if not result:
        raise GateError("spec.md needs stable S1/S2 specification sections")
    conflict = re.search(r"^### 冲突声明\s*\n(.*?)(?=^##|\Z)", text, re.M | re.S)
    if not conflict or conflict[1].strip() != "无":
        raise GateError("Unresolved specification conflict; classify it against the approved requirements")
    if re.search(r"<(?:标题|slug|YYYY-MM-DD|规格名|触发|结果|一句话[^>]*|判定|n)>|^[-*]\s*待补充\s*$", text, re.M):
        raise GateError("Specification contains unresolved placeholders")
    return result


def parse_plan(text):
    from ..gate import GateError
    text = clean(text)
    for heading in ("任务", "阻塞", "完成"):
        if not re.search(r"^## " + heading + r"\s*$", text, re.M):
            raise GateError("plan.md is missing section: " + heading)
    tasks, group = [], None
    for line in text.splitlines():
        heading = re.match(r"^###\s+(.+?)(?:\s*·\s*(未开始|进行中|已完成))?\s*$", line)
        if heading:
            group = heading[1]
        card = re.match(r"^- \[([ xX])\] ((?:S\d+-\d+|[PTR]\d+))\s+(.+)$", line)
        if card:
            tasks.append({"id": card[2], "title": card[3], "group": group, "completed": card[1].lower() == "x", "lines": []})
        elif re.match(r"^\s*- \[[^\]]*\]", line):
            raise GateError("Unrecognized task card; use top-level Sx-n, Pn, Tn or Rn: " + line.strip())
        elif tasks and (line.startswith("  ") or not line.strip()):
            tasks[-1]["lines"].append(line)
    if not tasks or len({t["id"] for t in tasks}) != len(tasks):
        raise GateError("plan.md needs uniquely identified task cards")
    for task in tasks:
        body = "\n".join(task.pop("lines"))
        expected = re.search(r"^  - 预期[：:]\s*(.+)$", body, re.M)
        observation = re.search(r"^  - 验收[：:]\s*(.*)$", body, re.M)
        if not expected or not observation:
            raise GateError("Task needs expected result and observation slot: " + task["id"])
        task.update(expected=expected[1], observation=observation[1], testing="转写用例" in body)
    if not any(t["id"].startswith("R") for t in tasks):
        raise GateError("Plan needs a full validation replay task (R1)")
    return tasks


def plan_semantics(text):
    """Only well-defined execution slots are mutable; task/expectation prose stays frozen."""
    text = clean(text)
    text = re.sub(r"^(- )\[[ xX]\]( ((?:S\d+-\d+|[PTR]\d+))\s)", r"\1[ ]\2", text, flags=re.M)
    text = re.sub(r"^(### .+?)\s*·\s*(未开始|进行中|已完成)\s*$", r"\1 · <status>", text, flags=re.M)
    text = re.sub(r"^  - 验收[：:].*(?:\n    .*)*", "  - 验收：<observation>", text, flags=re.M)
    text = re.sub(r"(^## (?:阻塞|完成)[ \t]*\n).*?(?=^## |\Z)", r"\1<execution>\n", text, flags=re.M | re.S)
    return text.strip().encode("utf-8")


def project_plan(text, items):
    from ..gate import GateError
    states = {item["id"]: item for item in items}
    parse_plan(text)
    for ident, item in states.items():
        marker = "x" if item["status"] == "completed" else " "
        text, count = re.subn(r"^(- )\[[ xX]\]( " + re.escape(ident) + r"\s)", rf"\g<1>[{marker}]\2", text, flags=re.M)
        if count != 1:
            raise GateError("Mapped task card is missing: " + ident)
    # Line-wise update avoids a multiline expression swallowing subsequent cards.
    output, current = [], None
    for line in text.splitlines():
        m = re.match(r"^- \[[ xX]\] (\S+)\s", line)
        if m:
            current = states.get(m[1])
        if current and re.match(r"^  - 验收[：:]", line):
            line = "  - 验收：" + (current.get("observation", "") if current["status"] == "completed" else "（待执行）")
        output.append(line)
    text = "\n".join(output) + "\n"
    def header(m):
        body = m[2]
        ids = re.findall(r"^- \[[ xX]\] (\S+)\s", body, re.M)
        values = [states[i]["status"] for i in ids if i in states]
        status = "已完成" if values and all(v == "completed" for v in values) else "未开始" if all(v == "not_started" for v in values) else "进行中"
        return m[1] + " · " + status + "\n" + body
    text = re.sub(r"^(### S\d+ .+?)(?:\s*·\s*(?:未开始|进行中|已完成))?\n(.*?)(?=^### |^## |\Z)", header, text, flags=re.M | re.S)
    return text


class ZsspecDocsAdapter:
    name, profile, api_version = "zsspec", PROFILE, 1
    stages, support = STAGES, ["zsspec-init", "zsspec-archive"]
    scripted = False

    def describe(self, source: Path):
        from ..gate import GateError
        required = [n + "/SKILL.md" for n in sorted(NAMES)] + [
            "README.md", "zsspec-spec/templates/spec.md", "zsspec-plan/templates/plan.md",
            *["zsspec-init/templates/" + n + ".md" for n in ("MAP", "INDEX", "module")]]
        missing = [p for p in required if not (source / p).is_file()]
        if missing:
            raise GateError("Incomplete zsspec 2 package: " + ", ".join(missing))
        for relative, markers in (("zsspec-spec/templates/spec.md", ("## 规格", "## 设计")),
                                  ("zsspec-plan/templates/plan.md", ("预期", "验收", "## 完成"))):
            if any(marker not in (source / relative).read_text(encoding="utf-8-sig") for marker in markers):
                raise GateError("Unknown zsspec document protocol; adapter review required")
        return {"schema": "aidevflow/resolved-method-v1", "adapter": self.name, "profile": self.profile,
                "adapter_api_version": 1, "resolver_version": 2, "stages": STAGES, "source_files": required,
                "operations": {"spec_review": "read", "plan_review": "read", "projection": "gate_write", "archive": "transaction"}}

    def validate(self, contract):
        method, errors = contract.get("method", {}), []
        if method.get("name") != self.name or method.get("stages") != STAGES:
            errors.append("zsspec-docs-v2 requires Design spec/plan, Build apply and Gate-owned review")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", method.get("slug", "")):
            errors.append("method.slug must be a stable kebab-case change identifier")
        canonical = {a["id"] for a in contract.get("acceptance_items", [])}
        mapping = method.get("acceptance_map", {})
        if not isinstance(mapping, dict) or set(mapping) != canonical or any(
            not isinstance(v, list) or not v or any(not isinstance(s, str) or not re.fullmatch(r"S\d+", s) for s in v) for v in mapping.values()):
            errors.append("acceptance_map must map every approved acceptance ID to S specification IDs")
        return errors

    def semantic_content(self, relative, content, plan):
        return plan_semantics(content.decode("utf-8-sig")) if relative.endswith("/plan.md") else content.replace(b"\r\n", b"\n")
