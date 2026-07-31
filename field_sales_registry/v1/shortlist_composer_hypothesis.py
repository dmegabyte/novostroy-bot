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
PROMPT_FILE = ROOT / "shortlist_composer_prompt.md"
SUPPORTED_SCENARIOS = {"family", "commute", "budget", "comfort", "safety", "readiness", "general", "investment", "parking"}
INPUT_KEYS = {"schema_version", "answer_goal", "scenario", "cta_template", "options"}
OPTION_KEYS = {"object_name", "fields"}
FIELD_KEYS = {"field_id", "label", "value", "literal_meaning", "allowed_benefit", "strength", "forbidden_claims", "rendering_rules"}
CANDIDATE_KEYS = {"intro", "options", "recommendation", "final_question"}
CANDIDATE_OPTION_KEYS = {"object_name", "presentation", "decision_role", "used_field_ids"}
OUTPUT_CONTRACT = {
    "type": "strict_json",
    "required_keys": ["intro", "options", "recommendation", "final_question"],
    "option_required_keys": ["object_name", "presentation", "decision_role", "used_field_ids"],
    "additional_properties": False,
    "max_options": 3,
    "final_question": "exact cta_template",
}
ERROR_CODES = {
    "input_not_object", "input_schema", "option_schema", "field_schema", "candidate_not_object",
    "candidate_schema", "text_bounds", "cta_mismatch", "question_contract", "assembled_too_long",
    "unknown_field_id", "ungrounded_field", "unknown_number", "invented_comparative_number",
    "internal_leak", "contact_or_url", "unsupported_claim", "object_name_mismatch", "common_fact_repeated",
    "duplicate_presentation", "decision_role_mismatch", "option_order_mismatch", "option_count_mismatch",
    "bureaucratic_style", "option_name_repeated", "recommendation_cta_repetition",
    "unavailable_field_claim", "undeclared_field_claim", "scenario_field_coverage_missing",
    "investment_counter_inference", "investment_counter_caveat_missing",
}
INTRO_ONLY_SHARED_IDS = {"school", "kindergarten", "ready"}
FACT_MARKERS = {
    "school": re.compile(r"\bшкол\w*\b", re.IGNORECASE),
    "kindergarten": re.compile(r"\bдетск\w*\s+сад\w*\b", re.IGNORECASE),
    "ready": re.compile(r"\b(?:(?:дом|корпус|вариант\w*).{0,16}готов\w*|готов\w*.{0,16}(?:дом|корпус)|дом\s+сдан)\b", re.IGNORECASE),
    "terrace": re.compile(r"\bтеррас\w*\b", re.IGNORECASE),
    "parking": re.compile(r"\b(?:паркинг\w*|машиномест\w*)\b", re.IGNORECASE),
    "mortgage_rate": re.compile(r"\b(?:ипотеч\w*|ставк\w*)\b", re.IGNORECASE),
    "down_payment": re.compile(r"\b(?:первоначальн\w*\s+взнос\w*|взнос\w*)\b", re.IGNORECASE),
    "installment_months": re.compile(r"\b(?:рассрочк\w*|месяц\w*|месяц(?:а|ев)\s+рассрочк\w*)\b", re.IGNORECASE),
    "parking_price": re.compile(r"\b(?:цен\w*\s+(?:паркинг\w*|машиномест\w*)|паркинг\w*.{0,24}(?:рубл|₽)|машиномест\w*.{0,24}(?:рубл|₽))\b", re.IGNORECASE),
    "parking_inventory": re.compile(r"\b(?:остат\w*\s+(?:машиномест\w*|мест\w*)|\d+\s+машиномест\w*)\b", re.IGNORECASE),
    "sales_count": re.compile(r"\b(?:egrn|егрн|продаж\w*)\b", re.IGNORECASE),
    "ads_count": re.compile(r"\b(?:витрин\w*|объявлен\w*)\b", re.IGNORECASE),
    "metro": re.compile(r"\bметро\b", re.IGNORECASE),
    "finishing": re.compile(r"\bотделк\w*\b", re.IGNORECASE),
}
SCENARIO_REQUIRED_FIELDS = {
    "budget": {"mortgage_rate", "down_payment", "installment_months"},
    "parking": {"parking", "parking_price", "parking_inventory"},
    "investment": {"sales_count", "ads_count"},
}

INTERNAL_RE = re.compile(
    r"\b(?:MCP|JSON|payload|diagnostics|registry|field_id|source_field|evidence|canonical|prompt|model|schema|trace|OptionCard|enum|"
    r"карточк\w*|данн\w*|контекст\w*|подтвержд[её]н\w*)\b|```|[{}]",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(r"(?:https?://|www\.|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?:\+?\d[\s().-]*){10,}|Telegram|WhatsApp|телеграм|ватсап)", re.IGNORECASE)
UNSUPPORTED_RE = re.compile(
    r"\b(?:гарант\w*|идеаль\w*|доходност\w*|окупаемост\w*|ликвидн\w*|рост\s+цен\w*|высок\w+\s+спрос|"
    r"одобрен\w*|одобр\w*.{0,24}ипотек\w*|лучш\w*\s+ставк\w*|без\s+переплат\w*|переплат\w*\s+не\s+будет|"
    r"ежемесячн\w*\s+плат[её]ж\w*|плат[её]ж\w*\s+в\s+месяц|"
    r"заброниров\w*\s+(?:мест\w*|машиномест\w*)|брон\w*\s+(?:мест\w*|машиномест\w*)|"
    r"в\s+наличи\w*.{0,20}(?:мест\w*|машиномест\w*)|"
    r"(?:мест[ао]|машиномест\w*)\s+(?:точно\s+)?(?:есть|будут|останут\w*|доступн\w*)|"
     r"мест[ао]\s+(?:в\s+)?(?:школ\w*|сад\w*).{0,20}(?:есть|будут|точно)|"
    r"сразу\s+(?:переехать|заехать|жить)|ключ\w*.{0,15}(?:сразу|уже|получ\w*)|"
    r"(?:рядом|пешком|в\s+\d+\s+минут|\d+\s+минут\s+(?:пешком|до))|лучш\w+\s+локац\w*)\b",
    re.IGNORECASE,
)
BUREAUCRATIC_RE = re.compile(
    r"\b(?:является\s+(?:самым|наиболее|ключевым)|ключев\w*\s+фактор\w*|"
    r"является\s+важн\w*\s+фактор\w*|"
    r"(?:является|может\s+быть|будет)\s+предпочтительн\w*|обусловлен\w*|"
    r"наиболее\s+(?:подходящ\w*|предпочтительн\w*)|ограниченн\w*\s+бюджет\w*)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[\s.,]\d+)*(?![\w])")


def _load_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("ё", "е").strip().lower())


def _norm_number(value: Any) -> str:
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


def _fields(option: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    fields = option.get("fields", [])
    return [item for item in fields if isinstance(item, Mapping)] if isinstance(fields, list) else []


def _field_map(option: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item.get("field_id")): item for item in _fields(option) if isinstance(item.get("field_id"), str)}


def _all_field_ids(composer_input: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for option in composer_input.get("options", []) if isinstance(composer_input.get("options"), list) else []:
        if isinstance(option, Mapping):
            ids.update(_field_map(option))
    return ids


def _field_token(field: Mapping[str, Any]) -> tuple[str, str, str]:
    return (_norm_text(field.get("literal_meaning") or field.get("label")), _norm_text(field.get("value")), _norm_text(field.get("allowed_benefit")))


def derive_comparison_context(composer_input: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    _validate_input(composer_input, errors)
    if errors:
        raise ValueError("invalid shortlist composer input")
    options = list(composer_input["options"])
    maps = [_field_map(option) for option in options]
    common_ids = set(maps[0])
    for fmap in maps[1:]:
        common_ids &= set(fmap)
    common_field_ids = [fid for fid in maps[0] if fid in common_ids]
    shared_field_ids = [fid for fid in maps[0] if fid in common_ids and len({_field_token(fmap[fid]) for fmap in maps}) == 1]

    prices: list[tuple[int, int]] = []
    for idx, fmap in enumerate(maps):
        value = fmap.get("apartment_price", {}).get("value")
        if isinstance(value, int) and not isinstance(value, bool):
            prices.append((idx, value))
    sorted_prices = sorted(prices, key=lambda item: (item[1], item[0]))
    distinct_prices = {price for _idx, price in prices}
    deltas: dict[str, int] = {}
    for left_idx, left_price in prices:
        for right_idx, right_price in prices:
            if left_idx != right_idx:
                deltas[f"{left_idx}:{right_idx}"] = abs(left_price - right_price)

    roles: list[str] = []
    for idx, option in enumerate(options):
        fmap = maps[idx]
        unique_ids = [fid for fid in fmap if fid not in shared_field_ids]
        non_price_unique = [fid for fid in unique_ids if fid != "apartment_price"]
        role_unique_ids = [fid for fid in non_price_unique if not (composer_input.get("scenario") == "investment" and fid in {"sales_count", "ads_count"})]
        price_rank = next((rank for rank, (price_idx, _price) in enumerate(sorted_prices) if price_idx == idx), None)
        only_location_unique = role_unique_ids == ["location"]
        if len(distinct_prices) <= 1 and only_location_unique:
            role = "no_unique_advantage"
        elif price_rank == 0 and len(sorted_prices) > 1 and len(distinct_prices) > 1:
            role = "lowest_price"
        elif price_rank == len(sorted_prices) - 1 and len(sorted_prices) > 1 and len(distinct_prices) > 1 and role_unique_ids:
            role = "highest_price/location_choice" if "location" in role_unique_ids else "highest_price/unique_fact"
        elif price_rank is not None and 0 < price_rank < len(sorted_prices) - 1 and len(distinct_prices) > 1:
            role = "middle_price/location_choice" if "location" in role_unique_ids else "middle_price"
        elif role_unique_ids:
            role = "unique_fact"
        else:
            role = "no_unique_advantage"
        roles.append(role)

    option_contexts = []
    role_instructions = {
        "lowest_price": "Сделай акцент на минимальной цене и бюджете. Не называй ценовую дельту и не повторяй общую формулу про выбор локации.",
        "middle_price/location_choice": "Используй одну ближайшую ценовую разницу; затем коротко назови локацию условием выбора без общей повторяющейся формулы.",
        "middle_price": "Используй одну ближайшую ценовую разницу без выдуманного преимущества.",
        "highest_price/location_choice": "Не называй ценовую дельту. Честно скажи, что ценового преимущества нет; смысл выбора только в нужной локации.",
        "highest_price/unique_fact": "Сопоставь более высокую цену только с уникальным подтвержденным фактом.",
        "unique_fact": "Сделай акцент только на уникальном подтвержденном поле.",
        "no_unique_advantage": "Честно скажи, что подтвержденного преимущества нет; не выбирай победителя.",
    }
    for idx, option in enumerate(options):
        fmap = maps[idx]
        price_comparisons = []
        own_price = fmap.get("apartment_price", {}).get("value")
        if isinstance(own_price, int) and not isinstance(own_price, bool):
            for other_idx, other_option in enumerate(options):
                if other_idx == idx:
                    continue
                other_price = maps[other_idx].get("apartment_price", {}).get("value")
                if not isinstance(other_price, int) or isinstance(other_price, bool):
                    continue
                relation = "cheaper" if own_price < other_price else "more_expensive" if own_price > other_price else "equal"
                price_comparisons.append({
                    "other_object_name": other_option["object_name"],
                    "relation": relation,
                    "delta": abs(own_price - other_price),
                })
        primary_price_comparison = min(price_comparisons, key=lambda item: (item["delta"], item["other_object_name"])) if price_comparisons else None
        option_contexts.append({
            "object_name": option["object_name"],
            "shared_field_ids": shared_field_ids,
            "differing_field_ids": [fid for fid in fmap if fid not in shared_field_ids],
            "decision_role": roles[idx],
            "price_rank": next((rank + 1 for rank, (price_idx, _price) in enumerate(sorted_prices) if price_idx == idx), None),
            "price_comparisons": price_comparisons,
            "primary_price_comparison": primary_price_comparison,
            "role_instruction": role_instructions[roles[idx]],
        })
    return {
        "common_field_ids": common_field_ids,
        "shared_field_ids": shared_field_ids,
        "options": option_contexts,
        "price_deltas": deltas,
        "allowed_numbers": sorted(_allowed_numbers(composer_input)),
    }


def _allowed_numbers(composer_input: Mapping[str, Any]) -> set[str]:
    allowed: set[str] = set()
    for text in [composer_input.get("cta_template")]:
        for match in NUMBER_RE.findall(str(text or "")):
            allowed.add(_norm_number(match))
    options = composer_input.get("options", [])
    if isinstance(options, list):
        allowed.update(str(idx) for idx in range(1, len(options) + 1))
    for option in options if isinstance(options, list) else []:
        if not isinstance(option, Mapping):
            continue
        for match in NUMBER_RE.findall(str(option.get("object_name", ""))):
            allowed.add(_norm_number(match))
        for field in _fields(option):
            for value in _iter_values(field.get("value")):
                for match in NUMBER_RE.findall(value):
                    allowed.add(_norm_number(match))
    context = _context_without_validation(composer_input)
    for delta in context.get("price_deltas", {}).values():
        allowed.add(str(delta))
    return {item for item in allowed if item}


def _context_without_validation(composer_input: Mapping[str, Any]) -> dict[str, Any]:
    try:
        options = list(composer_input.get("options", []))
        prices = []
        for idx, option in enumerate(options):
            if isinstance(option, Mapping):
                value = _field_map(option).get("apartment_price", {}).get("value")
                if isinstance(value, int) and not isinstance(value, bool):
                    prices.append((idx, value))
        return {"price_deltas": {f"{a}:{b}": abs(pa - pb) for a, pa in prices for b, pb in prices if a != b}}
    except Exception:
        return {"price_deltas": {}}


def _validate_input(composer_input: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(composer_input, Mapping):
        _add(errors, "input_not_object")
        return
    if set(composer_input) != INPUT_KEYS or composer_input.get("schema_version") != 1 or composer_input.get("answer_goal") != "present_shortlist" or composer_input.get("scenario") not in SUPPORTED_SCENARIOS:
        _add(errors, "input_schema")
    cta = composer_input.get("cta_template")
    if not isinstance(cta, str) or not cta.strip() or len(cta) > 240 or cta.count("?") != 1 or not cta.rstrip().endswith("?"):
        _add(errors, "input_schema")
    options = composer_input.get("options")
    if not isinstance(options, list) or not (1 <= len(options) <= 3):
        _add(errors, "option_schema")
        return
    seen_names: set[str] = set()
    for option in options:
        if not isinstance(option, Mapping) or set(option) != OPTION_KEYS or not isinstance(option.get("object_name"), str) or not option.get("object_name", "").strip():
            _add(errors, "option_schema")
            continue
        name = option["object_name"]
        if name in seen_names:
            _add(errors, "option_schema")
        seen_names.add(name)
        fields = option.get("fields")
        if not isinstance(fields, list) or not (1 <= len(fields) <= 12):
            _add(errors, "field_schema")
            continue
        seen_fields: set[str] = set()
        for field in fields:
            if not isinstance(field, Mapping) or set(field) != FIELD_KEYS:
                _add(errors, "field_schema")
                continue
            fid = field.get("field_id")
            if not isinstance(fid, str) or not fid or fid in seen_fields:
                _add(errors, "field_schema")
            seen_fields.add(str(fid))
            if isinstance(field.get("value"), Mapping) or (isinstance(field.get("value"), list) and any(isinstance(item, (Mapping, list)) for item in field["value"])):
                _add(errors, "field_schema")


def _validate_candidate_shape(candidate: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(candidate, Mapping):
        _add(errors, "candidate_not_object")
        return
    if set(candidate) != CANDIDATE_KEYS:
        _add(errors, "candidate_schema")
    if not isinstance(candidate.get("intro"), str) or not candidate.get("intro", "").strip() or len(candidate.get("intro", "")) > 420:
        _add(errors, "text_bounds")
    if not isinstance(candidate.get("recommendation"), str) or len(candidate.get("recommendation", "")) > 420:
        _add(errors, "text_bounds")
    if not isinstance(candidate.get("final_question"), str) or len(candidate.get("final_question", "")) > 240:
        _add(errors, "text_bounds")
    options = candidate.get("options")
    if not isinstance(options, list) or not (1 <= len(options) <= 3):
        _add(errors, "candidate_schema")
        return
    for option in options:
        if not isinstance(option, Mapping) or set(option) != CANDIDATE_OPTION_KEYS:
            _add(errors, "candidate_schema")
            continue
        if not isinstance(option.get("object_name"), str) or not option["object_name"].strip():
            _add(errors, "candidate_schema")
        presentation = option.get("presentation")
        if not isinstance(presentation, str):
            _add(errors, "candidate_schema")
        elif not presentation.strip() or len(presentation) > 600:
            _add(errors, "text_bounds")
        elif not (1 <= _sentence_count(presentation) <= 3):
            _add(errors, "text_bounds")
        if not isinstance(option.get("decision_role"), str) or not option["decision_role"].strip():
            _add(errors, "candidate_schema")
        ids = option.get("used_field_ids")
        if not isinstance(ids, list) or not (1 <= len(ids) <= 12) or len(ids) != len(set(ids)) or any(not isinstance(item, str) for item in ids):
            _add(errors, "candidate_schema")


def _field_anchored(field: Mapping[str, Any], text: str) -> bool:
    norm = _norm_text(text)
    value_numbers = {
        _norm_number(match)
        for item in _iter_values(field.get("value"))
        for match in NUMBER_RE.findall(item)
        if _norm_number(match)
    }
    if value_numbers:
        text_numbers = {_norm_number(match) for match in NUMBER_RE.findall(text) if _norm_number(match)}
        return value_numbers <= text_numbers
    for key in ("label", "literal_meaning", "allowed_benefit"):
        value = _norm_text(field.get(key))
        if value and value in norm:
            return True
    for item in _iter_values(field.get("value")):
        item_norm = _norm_text(item)
        if item_norm and item_norm in norm:
            return True
        if item_norm and _contains_inflected_tokens(item_norm, norm):
            return True
        digits = _norm_number(item)
        if digits and digits in {_norm_number(match) for match in NUMBER_RE.findall(text)}:
            return True
    return False


def _contains_inflected_tokens(value: str, text: str) -> bool:
    """Allow conservative Russian case endings for already known literal values."""
    value_tokens = [token for token in re.split(r"\W+", value) if len(token) >= 5]
    text_tokens = [token for token in re.split(r"\W+", text) if len(token) >= 5]
    if not value_tokens or not text_tokens:
        return False
    return all(any(source[:5] == candidate[:5] for candidate in text_tokens) for source in value_tokens)


def _sentence_count(value: str) -> int:
    parts = [part for part in re.split(r"[.!?]+", str(value or "")) if part.strip()]
    return len(parts) or (1 if str(value or "").strip() else 0)


def _similarity(a: str, b: str) -> float:
    sa = {token for token in re.split(r"\W+", _norm_text(a)) if len(token) > 3}
    sb = {token for token in re.split(r"\W+", _norm_text(b)) if len(token) > 3}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(1, len(sa | sb))


def _validate_text(composer_input: Mapping[str, Any], candidate: Mapping[str, Any], errors: list[str]) -> None:
    cta = str(composer_input.get("cta_template", ""))
    if _norm_text(candidate.get("final_question")) != _norm_text(cta):
        _add(errors, "cta_mismatch")
    assembled = assemble_candidate(candidate) if isinstance(candidate, Mapping) else ""
    non_final = assembled.removesuffix(str(candidate.get("final_question", ""))).strip()
    if "?" in non_final or assembled.count("?") != 1 or not assembled.endswith(str(candidate.get("final_question", ""))):
        _add(errors, "question_contract")
    if len(assembled) > 1800:
        _add(errors, "assembled_too_long")
    if INTERNAL_RE.search(assembled):
        _add(errors, "internal_leak")
    if CONTACT_RE.search(assembled):
        _add(errors, "contact_or_url")
    if UNSUPPORTED_RE.search(assembled):
        _add(errors, "unsupported_claim")
    if BUREAUCRATIC_RE.search(assembled):
        _add(errors, "bureaucratic_style")
    if _recommendation_repeats_cta(str(candidate.get("recommendation", "")), str(candidate.get("final_question", ""))):
        _add(errors, "recommendation_cta_repetition")
    allowed_numbers = _allowed_numbers(composer_input)
    allowed_delta_numbers = {str(value) for value in _context_without_validation(composer_input).get("price_deltas", {}).values()}
    for number_match in NUMBER_RE.finditer(assembled):
        match = number_match.group(0)
        digits = _norm_number(match)
        if digits and digits not in allowed_numbers:
            _add(errors, "unknown_number")
    delta_pattern = re.compile(
        rf"(?:на\s+({NUMBER_RE.pattern})(?:\s*руб\w*)?\s+(?:дешевле|дороже)|(?:дешевле|дороже)\s+на\s+({NUMBER_RE.pattern}))",
        re.IGNORECASE,
    )
    for delta_match in delta_pattern.finditer(assembled):
        raw_delta = delta_match.group(1) or delta_match.group(2) or ""
        digits = _norm_number(raw_delta)
        if digits and digits not in allowed_delta_numbers:
            _add(errors, "invented_comparative_number")


def _recommendation_repeats_cta(recommendation: str, final_question: str) -> bool:
    rec = _norm_text(recommendation)
    cta = _norm_text(final_question)
    if _similarity(rec, cta) >= 0.6:
        return True
    asks_for_priority = bool(re.search(r"важн|приоритет|выбер|что\s+для\s+вас", cta))
    generic_choice = bool(re.search(
        r"что\s+для\s+вас\s+важн|выбор\s+(?:будет\s+)?зависит.{0,70}(?:что.{0,20}важн|приоритет)",
        rec,
    ))
    return asks_for_priority and generic_choice


def _validate_shortlist_contract(composer_input: Mapping[str, Any], candidate: Mapping[str, Any], errors: list[str]) -> None:
    if not isinstance(candidate.get("options"), list):
        return
    source_options = composer_input.get("options", []) if isinstance(composer_input.get("options"), list) else []
    if len(candidate["options"]) != len(source_options):
        _add(errors, "option_count_mismatch")
        return
    context = derive_comparison_context(composer_input) if not errors or all(e not in {"input_schema", "option_schema", "field_schema"} for e in errors) else _context_without_validation(composer_input)
    source_names = [option.get("object_name") for option in source_options if isinstance(option, Mapping)]
    candidate_names = [option.get("object_name") for option in candidate["options"] if isinstance(option, Mapping)]
    if candidate_names != source_names:
        _add(errors, "option_order_mismatch")
        _add(errors, "object_name_mismatch")
    shared_ids = set(context.get("shared_field_ids", []))
    common_field_ids = set(context.get("common_field_ids", []))
    intro = str(candidate.get("intro", ""))
    for fid, marker in FACT_MARKERS.items():
        investment_common_counter = composer_input.get("scenario") == "investment" and fid in {"sales_count", "ads_count"} and fid in common_field_ids
        if marker.search(intro) and fid not in shared_ids and not investment_common_counter:
            _add(errors, "unavailable_field_claim")
    option_presentations: list[str] = []
    for idx, option_candidate in enumerate(candidate["options"]):
        if not isinstance(option_candidate, Mapping) or idx >= len(source_options) or not isinstance(source_options[idx], Mapping):
            continue
        fmap = _field_map(source_options[idx])
        presentation = str(option_candidate.get("presentation", ""))
        if _norm_text(source_options[idx].get("object_name")) in _norm_text(presentation):
            _add(errors, "option_name_repeated")
        used = option_candidate.get("used_field_ids", [])
        used_set = {str(fid) for fid in used} if isinstance(used, list) else set()
        scenario_required = SCENARIO_REQUIRED_FIELDS.get(str(composer_input.get("scenario")), set()) & set(fmap)
        if not scenario_required <= used_set:
            _add(errors, "scenario_field_coverage_missing")
        for fid, marker in FACT_MARKERS.items():
            if not marker.search(presentation):
                continue
            if fid not in fmap:
                _add(errors, "unavailable_field_claim")
            elif fid not in used_set:
                _add(errors, "undeclared_field_claim")
        for fid in used if isinstance(used, list) else []:
            field = fmap.get(str(fid))
            if field is None:
                _add(errors, "unknown_field_id")
            elif str(fid) in shared_ids and str(fid) in INTRO_ONLY_SHARED_IDS:
                if not (_field_anchored(field, intro) or _field_anchored(field, presentation)):
                    _add(errors, "ungrounded_field")
            elif not _field_anchored(field, presentation):
                _add(errors, "ungrounded_field")
        for fid in ("apartment_price", "location"):
            field = fmap.get(fid)
            if field and fid in shared_ids and _field_anchored(field, intro):
                continue
            if field and not _field_anchored(field, presentation):
                _add(errors, "ungrounded_field")
        for fid in shared_ids:
            if fid not in INTRO_ONLY_SHARED_IDS:
                continue
            field = fmap.get(fid)
            if field and _field_anchored(field, intro) and _field_anchored(field, presentation):
                _add(errors, "common_fact_repeated")
        expected_role = context.get("options", [{}])[idx].get("decision_role") if idx < len(context.get("options", [])) else None
        if option_candidate.get("decision_role") != expected_role:
            _add(errors, "decision_role_mismatch")
        option_presentations.append(presentation)
    recommendation = str(candidate.get("recommendation", ""))
    all_available_ids = {fid for option in source_options if isinstance(option, Mapping) for fid in _field_map(option)}
    for fid, marker in FACT_MARKERS.items():
        if marker.search(recommendation) and fid not in all_available_ids:
            _add(errors, "unavailable_field_claim")
    if composer_input.get("scenario") == "investment":
        investment_text = " ".join([intro, *option_presentations, recommendation])
        inference_segments = [*option_presentations, recommendation]
        inference_patterns = (
            r"(?:сч[её]тчик\w*|продаж\w*|объявлен\w*|показател\w*).{0,100}(?:оправд\w*|выгод\w*|привлекательн\w*|сильнее|интересн\w*|важн\w*\s+фактор\w*|важнее|приоритет\w*|да[её]т\s+преимуществ\w*)",
            r"если.{0,60}(?:сч[её]тчик\w*|показател\w*|продаж\w*|объявлен\w*).{0,60}(?:важн\w*|приоритет\w*).{0,80}(?:рассмотр\w*|выбира\w*|интересн\w*)",
            r"(?:цен\w*|стоимост\w*).{0,60}(?:может\s+быть\s+)?оправд\w*",
        )
        if any(re.search(pattern, segment, re.IGNORECASE) for segment in inference_segments for pattern in inference_patterns):
            _add(errors, "investment_counter_inference")
        if not re.search(r"буквальн\w*\s+сч[её]тчик|без\s+(?:вывод\w*|прогноз\w*)|не\s+(?:показыва\w*|означа\w*|доказыва\w*|да[её]т\w*\s+вывод\w*)", investment_text, re.IGNORECASE):
            _add(errors, "investment_counter_caveat_missing")
    for idx, left in enumerate(option_presentations):
        for right in option_presentations[idx + 1:]:
            if _norm_text(left) == _norm_text(right) or _similarity(left, right) >= 0.55:
                _add(errors, "duplicate_presentation")


def build_model_input(composer_input: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    _validate_input(composer_input, errors)
    if errors:
        raise ValueError("invalid shortlist composer input")
    prompt = _load_prompt()
    sanitized_options = []
    for option in composer_input["options"]:
        sanitized_options.append({
            "object_name": option["object_name"],
            "fields": [{key: field[key] for key in FIELD_KEYS if key in field} for field in option["fields"]],
        })
    return {
        "system_prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "input": {
            "schema_version": 1,
            "answer_goal": "present_shortlist",
            "scenario": composer_input["scenario"],
            "cta_template": composer_input["cta_template"],
            "options": sanitized_options,
            "comparison_context": derive_comparison_context(composer_input),
        },
        "output_contract": OUTPUT_CONTRACT,
    }


def validate_candidate(composer_input: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    _validate_input(composer_input, errors)
    _validate_candidate_shape(candidate, errors)
    if isinstance(candidate, Mapping):
        _validate_text(composer_input, candidate, errors)
        _validate_shortlist_contract(composer_input, candidate, errors)
    return errors


def assemble_candidate(candidate: Mapping[str, Any]) -> str:
    lines = [str(candidate.get("intro", "")).strip()]
    for idx, option in enumerate(candidate.get("options", []) if isinstance(candidate.get("options"), list) else [], start=1):
        if not isinstance(option, Mapping):
            continue
        presentation = str(option.get("presentation", "")).strip()
        lines.append(f"{idx}. {option.get('object_name')}\n{presentation}".strip())
    lines.extend([str(candidate.get("recommendation", "")).strip(), str(candidate.get("final_question", "")).strip()])
    return "\n\n".join(line for line in lines if line)


def simulate(composer_input: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_candidate(composer_input, candidate)
    valid = not errors
    metadata = {"option_count": len(composer_input.get("options", [])) if isinstance(composer_input, Mapping) and isinstance(composer_input.get("options"), list) else 0}
    if valid:
        metadata["decision_roles"] = [option["decision_role"] for option in candidate["options"]]
        metadata["used_field_ids_by_option"] = [option["used_field_ids"] for option in candidate["options"]]
    else:
        metadata["decision_roles"] = []
        metadata["used_field_ids_by_option"] = []
    return {"valid": valid, "errors": errors, "text": assemble_candidate(candidate) if valid else "", "manual_review_required": True, "metadata": metadata}


def _load_json(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline isolated shortlist Answer Composer hypothesis.")
    parser.add_argument("--input", required=True, help="Shortlist composer input JSON file")
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
