"""Shared requirement validation and rendering, without legacy workflow imports."""
from __future__ import annotations
from typing import Any
INTENT_SCHEMA = "aidevflow/intent-draft-v1"

def validate_intent(draft: Any) -> list[str]:
    if not isinstance(draft, dict):
        return ["top-level value must be an object"]
    required = ("schema", "problem", "users", "desired_outcome", "scope", "non_goals", "constraints", "success_criteria", "assumptions", "open_questions")
    errors = [f"missing {key}" for key in required if key not in draft]
    if errors:
        return errors
    if draft["schema"] != INTENT_SCHEMA:
        errors.append(f"schema must be {INTENT_SCHEMA}")
    for key in ("problem", "desired_outcome"):
        if not isinstance(draft[key], str) or not draft[key].strip():
            errors.append(f"{key} must be a non-empty string")
    for key in ("users", "scope", "non_goals", "constraints", "success_criteria", "assumptions", "open_questions"):
        if not isinstance(draft[key], list):
            errors.append(f"{key} must be an array")
    for key in ("users", "scope", "non_goals", "constraints", "success_criteria"):
        if isinstance(draft[key], list) and any(not isinstance(value, str) or not value.strip() for value in draft[key]):
            errors.append(f"{key} entries must be non-empty strings")
    if isinstance(draft["assumptions"], list):
        for assumption in draft["assumptions"]:
            if not isinstance(assumption, dict) or set(assumption) != {"statement", "reason", "revisit_if"}:
                errors.append("each assumption requires exactly statement, reason, and revisit_if")
            elif any(not isinstance(assumption[field], str) or not assumption[field].strip() for field in ("statement", "reason", "revisit_if")):
                errors.append("assumption fields must be non-empty strings")
    if not isinstance(draft["success_criteria"], list) or not draft["success_criteria"]:
        errors.append("at least one success criterion is required")
    questions = draft.get("open_questions", [])
    seen: set[str] = set()
    decisions = draft.get("decisions", [])
    if not isinstance(decisions, list):
        errors.append("decisions must be an array")
        decisions = []
    decision_ids = [entry.get("question_id") for entry in decisions if isinstance(entry, dict)]
    if len(decision_ids) != len(set(decision_ids)):
        errors.append("each question can have at most one current decision")
    question_map: dict[str, dict[str, Any]] = {}
    for question in questions if isinstance(questions, list) else []:
        if not isinstance(question, dict):
            errors.append("each open question must be an object")
            continue
        qid = question.get("id")
        if not isinstance(qid, str) or not qid:
            errors.append("each open question needs a non-empty id")
            continue
        if qid in seen:
            errors.append(f"duplicate question id {qid}")
        seen.add(qid)
        question_map[qid] = question
        allowed_question_keys = {"id", "question", "blocking", "options", "recommended_option", "recommendation_reason"}
        if set(question) != allowed_question_keys:
            errors.append(f"question {qid} requires exactly {', '.join(sorted(allowed_question_keys))}")
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            errors.append(f"question {qid} needs non-empty question text")
        if not isinstance(question.get("blocking"), bool):
            errors.append(f"question {qid} needs boolean blocking")
        options = question.get("options")
        if not isinstance(options, list):
            errors.append(f"question {qid} options must be an array")
            options = []
        option_ids = [opt.get("id") for opt in options if isinstance(opt, dict)]
        if len(option_ids) != len(options) or any(not isinstance(value, str) or not value for value in option_ids) or len(set(option_ids)) != len(option_ids):
            errors.append(f"question {qid} options need unique non-empty ids and object entries")
        for option in options:
            if isinstance(option, dict):
                if set(option) != {"id", "label", "impact"}:
                    errors.append(f"question {qid} option requires exactly id, label, and impact")
                elif any(not isinstance(option.get(field), str) or not option[field].strip() for field in ("id", "label", "impact")):
                    errors.append(f"question {qid} options require non-empty id, label, and impact")
        if question.get("blocking") and not 2 <= len(options) <= 4:
            errors.append(f"blocking question {qid} must offer 2-4 options")
        recommended = question.get("recommended_option")
        if question.get("blocking") and recommended not in option_ids:
            errors.append(f"question {qid} must recommend one listed option")
        if not question.get("blocking") and recommended is not None and recommended not in option_ids:
            errors.append(f"question {qid} recommended_option must be null or a listed option")
        if not isinstance(question.get("recommendation_reason"), str):
            errors.append(f"question {qid} recommendation_reason must be a string")
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("question_id") not in seen:
            errors.append("decision references an unknown question")
            continue
        if set(decision) != {"question_id", "option_id", "answer", "recorded_at"}:
            errors.append(f"decision for {decision['question_id']} has unexpected or missing fields")
        question = question_map[decision["question_id"]]
        option_id = decision.get("option_id")
        option_ids = {option.get("id") for option in question.get("options", []) if isinstance(option, dict)}
        if option_id is not None and option_id not in option_ids:
            errors.append(f"decision for {decision['question_id']} references an unknown option")
        if not isinstance(decision.get("answer"), str) or not decision["answer"].strip():
            errors.append(f"decision for {decision['question_id']} needs a non-empty answer")
        if not isinstance(decision.get("recorded_at"), str) or not decision["recorded_at"].strip():
            errors.append(f"decision for {decision['question_id']} needs recorded_at")
    extra = set(draft) - set(required) - {"decisions"}
    if extra:
        errors.append("unexpected properties: " + ", ".join(sorted(extra)))
    return errors

def render_intent(task_id: str, title: str, draft: dict[str, Any]) -> str:
    def bullets(values: list[Any]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- 无"

    decisions = {item["question_id"]: item for item in draft.get("decisions", [])}
    questions = []
    for item in draft["open_questions"]:
        decision = decisions.get(item["id"])
        status = f"已决：{decision['answer']}" if decision else ("阻塞" if item["blocking"] else "非阻塞")
        questions.append(f"- **{item['id']}**：{item['question']}（{status}）")
    assumptions = [f"{a['statement']}；原因：{a['reason']}；复核条件：{a['revisit_if']}" for a in draft["assumptions"]]
    return (
        f"# Intent: {title}\n\n"
        f"- Task: `{task_id}`\n- Schema: `{draft['schema']}`\n\n"
        f"## 要解决的问题\n\n{draft['problem']}\n\n"
        f"## 受影响用户\n\n{bullets(draft['users'])}\n\n"
        f"## 期望结果\n\n{draft['desired_outcome']}\n\n"
        f"## 本次范围\n\n{bullets(draft['scope'])}\n\n"
        f"## 非目标\n\n{bullets(draft['non_goals'])}\n\n"
        f"## 约束\n\n{bullets(draft['constraints'])}\n\n"
        f"## 成功标准\n\n{bullets(draft['success_criteria'])}\n\n"
        f"## 假设\n\n{bullets(assumptions)}\n\n"
        f"## 未决问题与用户决策\n\n{bullets(questions)}\n"
    )

def validate_requirements(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["requirements must be an object"]
    errors = []
    if set(value) != {"schema", "intent", "prd", "requirements", "scenarios"} or value.get("schema") != "aidevflow/requirements-v1":
        errors.append("requirements-v1 requires exactly schema, intent, prd, requirements and scenarios")
    errors += validate_intent(value.get("intent"))
    if not isinstance(value.get("prd"), str) or not value["prd"].strip():
        errors.append("prd must be non-empty Markdown")
    requirements, scenarios = value.get("requirements"), value.get("scenarios")
    if not isinstance(requirements, list) or not requirements or not isinstance(scenarios, list) or not scenarios:
        return errors + ["non-empty requirements and scenarios arrays are required"]
    req_ids, scenario_ids, acceptance_ids = set(), set(), set()
    for req in requirements:
        if not isinstance(req, dict) or set(req) != {"id", "statement", "source", "acceptance_ids"}:
            errors.append("each requirement needs id, statement, source and acceptance_ids")
            continue
        if any(not isinstance(req[k], str) or not req[k].strip() for k in ("id", "statement", "source")):
            errors.append("requirement fields must be non-empty strings")
            continue
        if req["id"] in req_ids:
            errors.append("duplicate requirement id")
        req_ids.add(req["id"])
        ids = req["acceptance_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids):
            errors.append("requirement acceptance_ids must be non-empty strings")
        else:
            acceptance_ids.update(ids)
    covered, scenario_acceptance = set(), set()
    for scn in scenarios:
        if not isinstance(scn, dict) or set(scn) != {"id", "requirement_ids", "acceptance_ids", "given", "when", "then"}:
            errors.append("scenario requires id, requirement_ids, acceptance_ids, given, when and then")
            continue
        if any(not isinstance(scn[k], str) or not scn[k].strip() for k in ("id", "given", "when", "then")):
            errors.append("scenario text must be non-empty")
            continue
        if scn["id"] in scenario_ids:
            errors.append("duplicate scenario id")
        scenario_ids.add(scn["id"])
        for field, known, target in (("requirement_ids", req_ids, covered), ("acceptance_ids", acceptance_ids, scenario_acceptance)):
            ids = scn[field]
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in known for i in ids):
                errors.append(f"scenario {field} must reference declared IDs")
            else:
                target.update(ids)
    if req_ids - covered or acceptance_ids - scenario_acceptance:
        errors.append("scenarios must cover every requirement and acceptance ID")
    return errors
