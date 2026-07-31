from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from .contracts import V1Action, V1Error
from .response import V1ResponsePlan
from .search_contract import public_safe_scalar
from .state import redact_phone


QUERY_MARKER = "V1_ONE_MODEL_INPUT="
MODEL = "openai/gpt-5.5"
NEXT_ACTIONS = {"none", "inspect_option", "clarify_search", "offer_operator", "request_phone"}

INTERNAL_TERMS_RE = re.compile(r"(?i)\b(?:json|markdown|html|developer prompt|system prompt|planner|mcp|search evidence|raw_search_response|payload|schema|next_action|visible_options)\b")
QUESTION_RE = re.compile(r"[?？]")
MONEY_RE = re.compile(r"(?:\b\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)|\b\d+[,.]\d+\s*млн)", re.IGNORECASE)
MORTGAGE_INTENT_RE = re.compile(r"(?i)(ипотек|семейн|первоначальн|\bпв\b|взнос|оплат|рассроч)")
MORTGAGE_FIELD_RE = re.compile(r"(?i)(mortgage|ипотек|payment|оплат|рассроч|down[_ -]?payment|первоначальн|\bпв\b|взнос)")
MORTGAGE_CONFIRM_RE = re.compile(r"(?i)(семейн\w*\s+ипотек\w*\s+(?:возмож|доступ|есть|подход)|ипотек\w*\s+(?:возмож|доступн|есть|одобрен|подходит)|можно\s+(?:взять|оформить).*ипотек|доступна\s+.*ипотек)")
PROJECT_PREFIX_RE = re.compile(r"(?iu)\b(?:ЖК|жилой\s+комплекс|жилой\s+квартал|жилой\s+район|апарт-отель|МФК)\s+[«\"“„]?([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]*(?:\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]*){0,4})[»\"“”]?")
TITLE_CASE_PROJECT_RE = re.compile(r"(?u)(?<![\w-])([А-ЯЁA-Z][а-яёa-z0-9-]{2,}(?:\s+[А-ЯЁA-Z][а-яёa-z0-9-]{2,}){1,3})(?![\w-])")
CARD_CONTEXT_RE = re.compile(r"(?iu)(?:^\s*\d+[\).]\s+|[—–-]\s*(?:от\s+)?\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)|\b(?:цена|стоимость|проект|жк|квартира|студия|м²|кв\.?)\b)")
QUOTE_TRANS = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "`": "'"})
ALLOWED_FACTUAL_GEOGRAPHIES = {"москва", "новая москва", "московская область", "санкт-петербург"}
LOCATION_KEYS = {"location", "location_id", "district", "area", "region", "city", "metro", "property_metro", "address", "street"}
LOCATION_LIKE_RE = re.compile(r"(?iu)\b(?:москв|област|район|округ|дегунино|метро|улиц|шоссе|проспект|переулок|санкт-петербург|петербург|спб)\b")
GENERIC_TITLE_STARTS = {
    "В текущих", "В базе", "В данных", "В Москве", "В Новой", "По Москве", "По Санкт", "Санкт-Петербург",
    "Московская область", "Новая Москва", "Оператор может", "Проверить другой", "Есть подтвержд", "Рассмотреть Москву",
}
LEADING_CANDIDATE_WORD_RE = re.compile(r"(?iu)^(?:есть|нашла|подобрала|покажу)\s+")
GEOGRAPHY_WORD_RE = re.compile(r"(?iu)^(?:москва|москву|москве|новая|новой|московская|московскую|область|области|санкт-петербург|петербург|петербургу|спб|или)$")


def response_model_bypass(action: Any) -> bool:
    try:
        safe_action = V1Action.coerce(action)
    except Exception:
        return True
    return safe_action in {V1Action.ACCEPT_OPERATOR, V1Action.CAPTURE_NAME, V1Action.CAPTURE_PHONE}


def build_one_model_input(user_text: str, state: Mapping[str, Any], plan: V1ResponsePlan, deterministic_response: str) -> dict[str, Any]:
    return {
        "client_message": str(redact_phone(user_text) or "")[:1000],
        "previous_assistant_message": _previous_assistant_message(state),
        "state_summary": _safe_state_summary(state),
        "evidence": _evidence_from_plan(plan),
        "deterministic_response": str(deterministic_response or "")[:1800],
    }


def parse_one_model_response(raw: Any, model_input: Mapping[str, Any]) -> dict[str, Any]:
    data = _strict_json_object(raw)
    errors = validate_one_model_response(data, model_input)
    if errors:
        raise V1Error("one_model_validation_failed:" + errors[0])
    return dict(data)


def validate_one_model_response(data: Mapping[str, Any], model_input: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    response = data.get("response")
    visible = data.get("visible_options")
    next_action = data.get("next_action")
    evidence = model_input.get("evidence") if isinstance(model_input.get("evidence"), Mapping) else {}
    evidence = evidence if isinstance(evidence, Mapping) else {}
    if not isinstance(response, str) or not response.strip():
        errors.append("response_empty")
        response = ""
    if isinstance(response, str) and len(response) > 1800:
        errors.append("response_too_long")
    if next_action not in NEXT_ACTIONS:
        errors.append("next_action_invalid")
    if not isinstance(visible, list):
        errors.append("visible_options_not_list")
        visible = []
    if isinstance(visible, list) and len(visible) > 3:
        errors.append("visible_options_too_many")
    names = evidence_names(evidence)
    visible_names: list[str] = []
    for idx, item in enumerate(visible if isinstance(visible, list) else []):
        if not isinstance(item, dict) or set(item) != {"name"} or not isinstance(item.get("name"), str):
            errors.append(f"visible_option_{idx}_bad_shape")
            continue
        name = item["name"].strip()
        visible_names.append(name)
        if name not in names:
            errors.append(f"visible_option_{idx}_not_in_evidence")
        if name and not _visible_name_mentioned(name, str(response)) and _norm_mention(name) not in _response_project_mentions(str(response)):
            errors.append(f"visible_option_{idx}_not_mentioned")
    if len(QUESTION_RE.findall(str(response))) > 1:
        errors.append("too_many_questions")
    if INTERNAL_TERMS_RE.search(str(response)):
        errors.append("internal_terms_visible")
    if re.search(r"<[^>]+>|```|^\s*[-*#]", str(response), re.MULTILINE):
        errors.append("markup_visible")
    support = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    for money in MONEY_RE.findall(str(response)):
        if money.strip() and money.strip() not in support:
            errors.append(f"money_not_grounded:{money.strip()[:40]}")
            break
    allowed_mentions, evidence_project_names = _grounded_project_terms(evidence, client_message=str(model_input.get("client_message") or ""))
    response_mentions = _response_project_mentions(str(response))
    unknown_mentions = sorted(m for m in response_mentions if m not in allowed_mentions)
    if unknown_mentions:
        errors.append(f"unknown_project_mention:{unknown_mentions[0][:40]}")
    visible_norms = {_norm_mention(name) for name in visible_names if _norm_mention(name)}
    if visible_names and evidence_project_names:
        missing_visible = sorted(m for m in response_mentions if m in evidence_project_names and m not in visible_norms)
        if missing_visible:
            errors.append(f"project_mention_not_visible:{missing_visible[0][:40]}")
    client = str(model_input.get("client_message") or "")
    if MORTGAGE_INTENT_RE.search(client + "\n" + str(response)) and not evidence_has_mortgage(evidence):
        if MORTGAGE_CONFIRM_RE.search(str(response)):
            errors.append("unsupported_mortgage_confirmation")
        if visible_names:
            errors.append("mortgage_followup_repeated_cards_without_terms")
    return errors


def _strict_json_object(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n") : -len("\n```")].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise V1Error("invalid_json") from exc
    if not isinstance(data, dict):
        raise V1Error("invalid_json")
    if set(data) != {"response", "visible_options", "next_action"}:
        raise V1Error("wrong_keys")
    return data


def _safe_state_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "stage": str(state.get("stage") or "")[:80],
        "hard_constraints": _safe_mapping(state.get("hard_constraints")),
        "preferences": _safe_mapping(state.get("preferences")),
        "visible_option_names": [str(item.get("name") or "")[:120] for item in state.get("visible_options", []) if isinstance(item, Mapping)][:3] if isinstance(state.get("visible_options"), list) else [],
        "selected_project": _safe_name(state.get("selected_project")),
        "selected_lot": _safe_name(state.get("selected_lot")),
    }


def _previous_assistant_message(state: Mapping[str, Any]) -> str:
    for value in _previous_assistant_candidates(state):
        safe = _safe_context_text(value)
        if safe:
            return safe
    return ""


def _previous_assistant_candidates(state: Mapping[str, Any]) -> Iterable[Any]:
    yield state.get("previous_assistant_message")
    for key in ("recent_turns", "dialog_window", "dialogue_turns"):
        turns = state.get(key)
        if not isinstance(turns, list):
            continue
        for turn in reversed(turns[-8:]):
            if not isinstance(turn, Mapping):
                continue
            role = str(turn.get("role") or "").strip().lower()
            if role in {"assistant", "bot"}:
                yield turn.get("text")
            yield turn.get("assistant")
    recent = state.get("recent_safe_turns")
    if isinstance(recent, list):
        yield from reversed(recent[-4:])


def _safe_context_text(value: Any, *, limit: int = 700) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = str(redact_phone(text) or "")
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email redacted]", text)
    if re.search(r"(?i)secret|token|raw_payload|system prompt|developer prompt", text):
        return ""
    return re.sub(r"\s+", " ", text)[:limit]


def _evidence_from_plan(plan: V1ResponsePlan) -> dict[str, Any]:
    return {
        "facts": [_card_to_evidence(card) for card in plan.exact_cards],
        "near": [_card_to_evidence(card) for card in plan.near_cards],
        "missing": [str(item)[:80] for item in plan.missing_facts[:20]],
        "params": {},
    }


def _card_to_evidence(card: Mapping[str, Any]) -> dict[str, Any]:
    facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else {}
    return {"name": str(card.get("name") or "")[:120], **_safe_mapping(facts)}


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:40]:
        key_text = str(key)[:80]
        if re.search(r"phone|тел|email|token|secret|raw|payload", key_text, re.I):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe_item = public_safe_scalar(item)
            if safe_item is None:
                continue
            out[key_text] = safe_item
    return out


def _safe_name(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("name") or "")[:120]
    return ""


def _evidence_items(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group in ("facts", "near"):
        values = evidence.get(group)
        if isinstance(values, list):
            out.extend(item for item in values if isinstance(item, dict))
    return out


def evidence_names(evidence: Mapping[str, Any]) -> set[str]:
    return {str(item.get("name") or "").strip() for item in _evidence_items(evidence) if str(item.get("name") or "").strip()}


def evidence_has_mortgage(evidence: Mapping[str, Any]) -> bool:
    for item in _evidence_items(evidence):
        for key, value in item.items():
            if MORTGAGE_FIELD_RE.search(str(key)) or MORTGAGE_FIELD_RE.search(str(value)):
                return True
    return False


def _project_aliases(item: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for key in ("name", "project", "title", "complex", "alias", "aliases"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            aliases.add(value)
        elif isinstance(value, list):
            aliases.update(str(v) for v in value if str(v).strip())
    return aliases


def _field_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item
            elif isinstance(item, Mapping):
                for nested_value in item.values():
                    yield from _field_values(nested_value)
    elif isinstance(value, Mapping):
        for nested_value in value.values():
            yield from _field_values(nested_value)


def _grounded_non_project_terms(evidence: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for item in _evidence_items(evidence):
        for key, value in item.items():
            key_text = str(key or "").casefold()
            is_location_field = key_text in LOCATION_KEYS or any(part in key_text for part in ("location", "district", "metro", "address", "street"))
            if not is_location_field:
                continue
            for candidate in _field_values(value):
                text = str(candidate or "").strip()
                if not text:
                    continue
                if key_text == "location" or LOCATION_LIKE_RE.search(text):
                    norm = _norm_mention(text)
                    if norm:
                        terms.add(norm)
    return terms


def _grounded_project_terms(evidence: Mapping[str, Any], client_message: str = "") -> tuple[set[str], set[str]]:
    allowed: set[str] = set()
    named: set[str] = set()
    for item in _evidence_items(evidence):
        for alias in _project_aliases(item):
            norm = _norm_mention(alias)
            if norm:
                allowed.add(norm)
                named.add(norm)
    support = json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n" + str(client_message or "")
    for match in PROJECT_PREFIX_RE.finditer(support):
        norm = _norm_mention(match.group(1))
        if norm:
            allowed.add(norm)
    for geo in ALLOWED_FACTUAL_GEOGRAPHIES:
        if geo in _norm_mention(support):
            allowed.add(geo)
    allowed.update(_grounded_non_project_terms(evidence))
    return allowed, named


def _title_case_in_card_context(response: str, start: int, end: int) -> bool:
    line_start = response.rfind("\n", 0, start) + 1
    line_end = response.find("\n", end)
    if line_end == -1:
        line_end = len(response)
    line = response[line_start:line_end]
    relative_start = start - line_start
    before = line[:relative_start]
    after = line[relative_start:]
    nearby = line[max(0, relative_start - 80) : min(len(line), relative_start + 120)]
    return bool(
        re.search(r"^\s*\d+[\).]\s*$", before)
        or re.search(r"^\s*[-*•]\s*$", before)
        or CARD_CONTEXT_RE.search(nearby)
        or re.match(r"(?iu)\s*[—–-]\s*(?:от\s+)?\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)", after)
    )


def _norm_mention(value: str) -> str:
    text = str(value or "").translate(QUOTE_TRANS).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip().strip('"\'.,:;!?()[]{}')
    text = re.sub(r"^(?:жк|жилой комплекс|жилой квартал|жилой район|апарт-отель|мфк)\s+", "", text, flags=re.IGNORECASE)
    return text.casefold()


def _visible_name_mentioned(name: str, response: str) -> bool:
    if not name:
        return False
    if name in response:
        return True
    norm_name = _norm_mention(name)
    if not norm_name:
        return False
    normalized_response = _norm_mention(response)
    return bool(re.search(rf"(?<!\w){re.escape(norm_name)}(?!\w)", normalized_response))


def _response_project_mentions(response: str) -> set[str]:
    mentions: set[str] = set()
    for fragment in re.findall(r"[«\"“„]([^»\"“”]{3,80})[»\"“”]", response):
        norm = _norm_mention(fragment)
        if norm:
            mentions.add(norm)
    for match in PROJECT_PREFIX_RE.finditer(response):
        for fragment in re.split(r"(?iu)\s+и\s+(?=ЖК|жилой\s+комплекс|жилой\s+квартал|жилой\s+район|апарт-отель|МФК)", match.group(1)):
            norm = _norm_mention(fragment)
            if norm:
                mentions.add(norm)
    for match in TITLE_CASE_PROJECT_RE.finditer(response):
        if not _title_case_in_card_context(response, match.start(1), match.end(1)):
            continue
        fragment = re.sub(r"\s+(?:подтверждённых|подтвержденных|данных|вариантов|условия|ипотеке).*$", "", match.group(1), flags=re.IGNORECASE).strip()
        fragment = LEADING_CANDIDATE_WORD_RE.sub("", fragment).strip()
        norm = _norm_mention(fragment)
        if not norm or norm in ALLOWED_FACTUAL_GEOGRAPHIES:
            continue
        if all(GEOGRAPHY_WORD_RE.match(word) for word in fragment.split()):
            continue
        if any(fragment.startswith(start) for start in GENERIC_TITLE_STARTS):
            continue
        mentions.add(norm)
    return mentions
