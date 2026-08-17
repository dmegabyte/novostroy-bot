"""Strict, synthetic-only assertions shared by the V6 prompt diagnostics."""

import json
import re


SPECIALIST_CTA = "Подключить специалиста, чтобы он проверил актуальные варианты по вашему запросу?"
UNSUPPORTED = re.compile(r"\b(?:гарантир\w*|точно\s+доступ\w*|одобрят|проверка\s+уже\s+начал)\b", re.I)


def _fail(reason):
    return {"pass": False, "score": 0.0, "reason": reason}


def _vars(context):
    return context.get("vars", context) if isinstance(context, dict) else {}


def _json(output):
    value = json.loads(output.strip())
    if not isinstance(value, dict):
        raise ValueError("JSON root is not an object")
    return value


def _prompt1(result, expected):
    if expected == "request_phone":
        return set(result) == {"action"} and result.get("action") == "request_phone"
    if expected == "continue_empty":
        return (set(result) == {"action", "facts", "near", "missing", "params", "ambiguity"}
                and result.get("action") == "continue" and result.get("facts") == []
                and result.get("near") == [] and result.get("missing") == []
                and result.get("params") == {} and result.get("ambiguity") is None)
    if expected == "clarify_max_price":
        return (set(result) == {"action", "ambiguity", "params"} and result.get("action") == "clarify"
                and result.get("params") == {} and result.get("ambiguity") == {
                    "parameter": "max_price", "reason_code": "multiple_interpretations"})
    return False


def _prompt2(result, variables):
    if set(result) != {"action", "response", "final_question"}:
        return "exact reply JSON keys required"
    response, question = result.get("response"), result.get("final_question")
    if result.get("action") != "reply" or not isinstance(response, str) or not response.strip():
        return "action must be reply with non-empty response"
    if "?" in response:
        return "response must not contain a question"
    if not isinstance(question, str) or (question and question.count("?") != 1):
        return "final_question must be empty or exactly one question"
    if question and re.search(r"\b(?:или|либо)\b", question, re.I):
        return "final_question must not offer alternative slots or actions"
    if UNSUPPORTED.search(f"{response}\n{question}"):
        return "unsupported claim"
    for value in filter(None, variables.get("required_values", "").split("|")):
        if value.lower() not in response.lower():
            return f"missing required grounded value: {value}"
    mode = variables.get("expected_mode")
    if mode == "empty" and question:
        return "case requires empty final_question"
    if mode == "area" and (not question or not re.search(r"площад|метр", question, re.I)):
        return "case requires exactly one area question"
    if mode == "ambiguity_max_price" and (not question or not re.search(r"бюджет|цен", question, re.I)):
        return "ambiguity must ask only max_price"
    if mode == "specialist" and question != SPECIALIST_CTA:
        return "specialist CTA must be exact"
    return None


def get_assert(output, context):
    """Promptfoo Python assertion entry point."""
    try:
        result = _json(output)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _fail(f"strict JSON required: {exc}")
    variables = _vars(context)
    if variables.get("layer") == "prompt1":
        if _prompt1(result, variables.get("expected_action")):
            return {"pass": True, "score": 1.0, "reason": "pass"}
        return _fail("Prompt 1 action, parameter, or variant shape differs from the expected contract")
    reason = _prompt2(result, variables)
    return _fail(reason) if reason else {"pass": True, "score": 1.0, "reason": "pass"}
