#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
PROMPT_FILE = ROOT / "answer_composer_prompt.md"
SUPPORTED_SCENARIOS = {"family", "commute", "budget", "comfort", "safety", "investment", "parking", "readiness", "general"}
INPUT_KEYS = {"schema_version", "answer_goal", "cta_template", "brief"}
BRIEF_KEYS = {"schema_version", "scenario", "object_name", "fresh_mcp", "fields", "combinations", "constraints", "diagnostics"}
FIELD_KEYS = {"field_id", "label", "value", "literal_meaning", "allowed_benefit", "strength", "required_evidence", "forbidden_claims", "rendering_rules"}
FIELD_REQUIRED_KEYS = FIELD_KEYS - {"allowed_benefit"}
COMBINATION_KEYS = {"id", "client_meaning", "required_cards", "required_evidence", "safe_phrasing", "forbidden_leap"}
CANDIDATE_KEYS = {"intro", "fact_summary", "benefit", "caveat", "final_question", "used_field_ids", "used_combination_ids"}
TEXT_KEYS = ("intro", "fact_summary", "benefit", "caveat", "final_question")
OUTPUT_CONTRACT = {
    "type": "strict_json",
    "required_keys": ["intro", "fact_summary", "benefit", "caveat", "final_question", "used_field_ids", "used_combination_ids"],
    "additional_properties": False,
    "final_question": "exact cta_template",
}

ERROR_CODES = {
    "input_not_object", "input_schema", "brief_schema", "candidate_not_object", "candidate_schema",
    "text_bounds", "cta_mismatch", "question_contract", "assembled_too_long", "unknown_field_id",
    "unknown_combination_id", "ungrounded_field", "unknown_number", "internal_leak", "contact_or_url",
    "unsupported_claim", "object_name_mismatch", "combination_not_grounded", "operator_cta",
    "duplicate_text",
}

INTERNAL_RE = re.compile(
    r"\b(?:MCP|JSON|payload|diagnostics|registry|field_id|source_field|evidence|canonical|prompt|model|schema|trace|OptionCard|enum|"
    r"карточк\w*|данн\w*|контекст\w*|подтвержд[её]н\w*)\b|```|[{}]",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(r"(?:https?://|www\.|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:\+?\d[\s().-]*){10,}|Telegram|WhatsApp|телеграм|ватсап)", re.IGNORECASE)
UNSUPPORTED_RE = re.compile(
    r"\b(?:гарант\w*|идеаль\w*|доходност\w*|окупаемост\w*|ликвидн\w*|рост\s+цен\w*|высок\w+\s+спрос|"
    r"лучш\w+\s+ставк\w*|без\s+переплат\w*|"
    r"(?:точно|всем)\s+одобр\w*|одобр\w*.{0,20}(?:точно|всем|будет)|"
    r"(?:точно).{0,20}(?:брон\w*|налич\w*)|(?:брон\w*|налич\w*).{0,20}(?:точно|будет|сохран\w*)|"
    r"мест[ао]\s+(?:в\s+)?(?:школ\w*|сад\w*).{0,20}(?:есть|будут|точно)|"
    r"сразу\s+(?:переехать|заехать|жить)|ключ\w*.{0,15}(?:сразу|уже|получ\w*)|комфортная\s+жизнь)\b",
    re.IGNORECASE,
)
OPERATOR_RE = re.compile(r"оператор|менеджер|звон|телефон|контакт", re.IGNORECASE)
NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[\s.,]\d+)*(?![\w])")
GUILLEMET_RE = re.compile(r"ЖК\s+[«\"]([^»\"]+)[»\"]", re.IGNORECASE)


def _load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("ё", "е").strip().lower())


def _norm_number(value: str) -> str:
    return re.sub(r"\D", "", str(value))


def _iter_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int, float, bool))]
    if isinstance(value, (str, int, float, bool)):
        return [str(value)]
    return []


def _add(errors: list[str], code: str) -> None:
    if code in ERROR_CODES and code not in errors:
        errors.append(code)


def _brief(composer_input: Mapping[str, Any]) -> Mapping[str, Any]:
    brief = composer_input.get("brief")
    return brief if isinstance(brief, Mapping) else {}


def _field_map(composer_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    fields = _brief(composer_input).get("fields", [])
    return {str(item.get("field_id")): item for item in fields if isinstance(item, Mapping) and isinstance(item.get("field_id"), str)}


def _combo_map(composer_input: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    combos = _brief(composer_input).get("combinations", [])
    return {str(item.get("id")): item for item in combos if isinstance(item, Mapping) and isinstance(item.get("id"), str)}


def _allowed_numbers(composer_input: Mapping[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for text in [composer_input.get("cta_template"), _brief(composer_input).get("object_name")]:
        for match in NUMBER_RE.findall(str(text or "")):
            allowed.add(_norm_number(match))
    for field in _field_map(composer_input).values():
        for value in _iter_values(field.get("value")):
            for match in NUMBER_RE.findall(value):
                allowed.add(_norm_number(match))
    return {item for item in allowed if item}


def _validate_input(composer_input: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(composer_input, Mapping):
        _add(errors, "input_not_object")
        return
    if set(composer_input) != INPUT_KEYS or composer_input.get("schema_version") != 1 or composer_input.get("answer_goal") != "present_selected":
        _add(errors, "input_schema")
    cta = composer_input.get("cta_template")
    if not isinstance(cta, str) or not cta.strip() or len(cta) > 240:
        _add(errors, "input_schema")
    elif cta.count("?") != 1 or not cta.rstrip().endswith("?"):
        _add(errors, "input_schema")
    brief = composer_input.get("brief")
    if not isinstance(brief, Mapping):
        _add(errors, "brief_schema")
        return
    if set(brief) != BRIEF_KEYS or brief.get("schema_version") != 1 or brief.get("scenario") not in SUPPORTED_SCENARIOS:
        _add(errors, "brief_schema")
    if not isinstance(brief.get("object_name"), str) or not brief.get("object_name", "").strip():
        _add(errors, "brief_schema")
    if not isinstance(brief.get("fields"), list) or not (1 <= len(brief.get("fields", [])) <= 12):
        _add(errors, "brief_schema")
        return
    constraints = brief.get("constraints")
    if constraints != {"facts_are_canonical": True, "no_new_facts": True, "registry_version": "v1"}:
        _add(errors, "brief_schema")
    seen_fields: set[str] = set()
    for field in brief.get("fields", []):
        if not isinstance(field, Mapping) or not FIELD_REQUIRED_KEYS <= set(field) <= FIELD_KEYS:
            _add(errors, "brief_schema")
            continue
        field_id = field.get("field_id")
        if not isinstance(field_id, str) or not field_id or field_id in seen_fields:
            _add(errors, "brief_schema")
        else:
            seen_fields.add(field_id)
        value = field.get("value")
        if isinstance(value, Mapping) or (isinstance(value, list) and (len(value) > 8 or any(isinstance(item, (Mapping, list)) for item in value))):
            _add(errors, "brief_schema")
    seen_combinations: set[str] = set()
    combinations = brief.get("combinations")
    if not isinstance(combinations, list):
        _add(errors, "brief_schema")
        return
    for combo in combinations:
        if not isinstance(combo, Mapping) or set(combo) != COMBINATION_KEYS:
            _add(errors, "brief_schema")
            continue
        combo_id = combo.get("id")
        required_cards = combo.get("required_cards")
        if not isinstance(combo_id, str) or not combo_id or combo_id in seen_combinations:
            _add(errors, "brief_schema")
        else:
            seen_combinations.add(combo_id)
        if not isinstance(required_cards, list) or not required_cards or any(field_id not in seen_fields for field_id in required_cards):
            _add(errors, "brief_schema")


def _validate_candidate_shape(candidate: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(candidate, Mapping):
        _add(errors, "candidate_not_object")
        return
    if set(candidate) != CANDIDATE_KEYS:
        _add(errors, "candidate_schema")
    bounds = {"intro": 220, "fact_summary": 420, "benefit": 420, "caveat": 260, "final_question": 240}
    for key, limit in bounds.items():
        value = candidate.get(key)
        if not isinstance(value, str) or len(value) > limit or (key != "caveat" and not value.strip()):
            _add(errors, "text_bounds")
    for key, min_items, max_items in (("used_field_ids", 1, 6), ("used_combination_ids", 0, 3)):
        value = candidate.get(key)
        if not isinstance(value, list) or not (min_items <= len(value) <= max_items) or len(value) != len(set(value)) or any(not isinstance(item, str) for item in value):
            _add(errors, "candidate_schema")


def _field_anchored(field: Mapping[str, Any], text: str) -> bool:
    norm = _norm_text(text)
    label = _norm_text(field.get("label"))
    if label and label in norm:
        return True
    benefit = _norm_text(field.get("allowed_benefit"))
    if benefit and benefit in norm:
        return True
    value = field.get("value")
    if value is True:
        return False
    for item in _iter_values(value):
        item_norm = _norm_text(item)
        if item_norm and item_norm in norm:
            return True
        digits = _norm_number(item)
        if digits and digits in {_norm_number(match) for match in NUMBER_RE.findall(text)}:
            return True
    return False


def _validate_grounding(composer_input: Mapping[str, Any], candidate: Mapping[str, Any], errors: list[str]) -> None:
    fields = _field_map(composer_input)
    combos = _combo_map(composer_input)
    fact_benefit = f"{candidate.get('fact_summary', '')} {candidate.get('benefit', '')}"
    for field_id in candidate.get("used_field_ids", []):
        field = fields.get(str(field_id))
        if field is None:
            _add(errors, "unknown_field_id")
        elif not _field_anchored(field, fact_benefit):
            _add(errors, "ungrounded_field")
    for combo_id in candidate.get("used_combination_ids", []):
        combo = combos.get(str(combo_id))
        if combo is None:
            _add(errors, "unknown_combination_id")
            continue
        benefit = _norm_text(candidate.get("benefit", ""))
        safe = _norm_text(combo.get("safe_phrasing", ""))
        required_labels = [_norm_text(fields.get(str(fid), {}).get("label")) for fid in combo.get("required_cards", [])]
        if not ((safe and safe in benefit) or all(label and label in benefit for label in required_labels)):
            _add(errors, "combination_not_grounded")


def _validate_text(composer_input: Mapping[str, Any], candidate: Mapping[str, Any], errors: list[str]) -> None:
    cta = str(composer_input.get("cta_template", ""))
    if _norm_text(candidate.get("final_question")) != _norm_text(cta):
        _add(errors, "cta_mismatch")
    non_final = " ".join(str(candidate.get(key, "")) for key in ("intro", "fact_summary", "benefit", "caveat"))
    assembled = assemble_candidate(candidate) if isinstance(candidate, Mapping) else ""
    if "?" in non_final or assembled.count("?") != 1 or not assembled.endswith(str(candidate.get("final_question", ""))):
        _add(errors, "question_contract")
    if len(assembled) > 1200:
        _add(errors, "assembled_too_long")
    if INTERNAL_RE.search(assembled):
        _add(errors, "internal_leak")
    if CONTACT_RE.search(assembled):
        _add(errors, "contact_or_url")
    if OPERATOR_RE.search(assembled) and not OPERATOR_RE.search(cta):
        _add(errors, "operator_cta")
    if UNSUPPORTED_RE.search(assembled):
        _add(errors, "unsupported_claim")
    allowed_numbers = _allowed_numbers(composer_input)
    for match in NUMBER_RE.findall(assembled):
        digits = _norm_number(match)
        if digits and digits not in allowed_numbers:
            _add(errors, "unknown_number")
            break
    object_name = str(_brief(composer_input).get("object_name", "")).strip()
    if re.search(r"\bЖК\b", assembled, re.IGNORECASE) and _norm_text(object_name) not in _norm_text(assembled):
        _add(errors, "object_name_mismatch")
    for name in GUILLEMET_RE.findall(assembled):
        if _norm_text(name) != _norm_text(object_name.replace("ЖК", "")) and _norm_text(f"ЖК {name}") != _norm_text(object_name):
            _add(errors, "object_name_mismatch")
    sentences = [re.sub(r"\s+", " ", part.strip().lower()) for part in re.split(r"[.!?]+", assembled) if part.strip()]
    if len(sentences) != len(set(sentences)) or len({str(candidate.get(key, "")).strip().lower() for key in TEXT_KEYS if str(candidate.get(key, "")).strip()}) < len([key for key in TEXT_KEYS if str(candidate.get(key, "")).strip()]):
        _add(errors, "duplicate_text")


def build_model_input(composer_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return sanitized offline model package; diagnostics/source/raw data are omitted."""
    input_errors: list[str] = []
    _validate_input(composer_input, input_errors)
    if input_errors:
        raise ValueError("invalid composer input")
    brief = _brief(composer_input)
    fields = []
    raw_fields = brief.get("fields", [])
    for field in raw_fields if isinstance(raw_fields, list) else []:
        if not isinstance(field, Mapping):
            continue
        entry = {key: field[key] for key in ("field_id", "label", "value", "literal_meaning", "strength", "forbidden_claims", "rendering_rules") if key in field}
        if "allowed_benefit" in field:
            entry["allowed_benefit"] = field["allowed_benefit"]
        fields.append(entry)
    combinations = []
    raw_combinations = brief.get("combinations", [])
    for combo in raw_combinations if isinstance(raw_combinations, list) else []:
        if isinstance(combo, Mapping):
            combinations.append({key: combo[key] for key in ("id", "client_meaning", "safe_phrasing", "forbidden_leap", "required_cards") if key in combo})
    prompt = _load_prompt()
    return {
        "system_prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "input": {
            "schema_version": 1,
            "answer_goal": "present_selected",
            "scenario": brief.get("scenario"),
            "object_name": brief.get("object_name"),
            "fields": fields,
            "combinations": combinations,
            "constraints": {"facts_are_canonical": True, "no_new_facts": True, "registry_version": "v1"},
            "cta_template": composer_input.get("cta_template"),
        },
        "output_contract": OUTPUT_CONTRACT,
    }


def validate_candidate(composer_input: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate_input(composer_input, errors)
    _validate_candidate_shape(candidate, errors)
    if isinstance(candidate, Mapping):
        _validate_grounding(composer_input, candidate, errors)
        _validate_text(composer_input, candidate, errors)
    return errors


def assemble_candidate(candidate: Mapping[str, Any]) -> str:
    parts = [str(candidate.get(key, "")).strip() for key in ("intro", "fact_summary", "benefit", "caveat", "final_question")]
    return "\n\n".join(part for part in parts if part)


def simulate(composer_input: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_candidate(composer_input, candidate)
    valid = not errors
    return {
        "valid": valid,
        "errors": errors,
        "text": assemble_candidate(candidate) if valid else "",
        "manual_review_required": True,
        "metadata": {
            "used_field_ids": list(candidate.get("used_field_ids", [])) if isinstance(candidate, Mapping) and valid else [],
            "used_combination_ids": list(candidate.get("used_combination_ids", [])) if isinstance(candidate, Mapping) and valid else [],
        },
    }


def _load_json(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Answer Composer simulator for field_sales_registry/v1.")
    parser.add_argument("--input", required=True, help="Composer input JSON file")
    parser.add_argument("--candidate", help="Candidate JSON file to validate/simulate")
    parser.add_argument("--print-model-input", action="store_true", help="Print sanitized model-input package")
    args = parser.parse_args(argv)
    composer_input = _load_json(args.input)
    if args.print_model_input:
        json.dump(build_model_input(composer_input), sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if not args.candidate:
        parser.error("--candidate is required unless --print-model-input is used")
    result = simulate(composer_input, _load_json(args.candidate))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
