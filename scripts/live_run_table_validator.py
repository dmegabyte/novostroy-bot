#!/usr/bin/env python3
"""Prepare live nmbot run rows for analysis tables and validate them locally.

This script is intentionally non-mutating: it does not rewrite model answers.
It parses a `logs/live_model_run_*.txt` file, adds a run `version`, and emits
warnings that should be reviewed before writing rows to Google Sheets.

Canonical process: docs/LLM_SCENARIO_EVAL_RUBRIC.md
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]

DEFAULT_FORBIDDEN_WORDS = (
    "в базе",
    "поиск выполнен",
    "mcp",
    "json",
    "regex",
)

TYPO_PATTERNS = {
    "typo_podbobom": re.compile(r"подбоб\w*", re.IGNORECASE),
}

EMPTY_AD_WORDS = (
    "развитая инфраструктура",
    "семейная инфраструктура",
    "высокий спрос",
    "ликвидность",
    "перспектив",
    "востребован",
    "хороший вариант",
)

FAMILY_EVIDENCE_MARKERS = (
    "школ",
    "сад",
    "детск",
    "парк",
    "лес",
    "двор без машин",
    "двор",
    "playground",
    "площад",
    "quiet yard",
)

INVESTMENT_RESPONSE_MARKERS = (
    "инвест",
    "вход",
    "сдел",
    "егрн",
    "объяв",
    "скид",
    "ипотек",
    "метро",
    "готов",
    "сдан",
    "компакт",
    "студи",
    "одноком",
)

RENTAL_RESPONSE_MARKERS = (
    "аренд",
    "студи",
    "одноком",
    "компакт",
    "отделк",
    "метро",
    "готов",
    "сдан",
    "объяв",
    "сдел",
    "егрн",
)

OVERCLAIM_RENTAL_MARKERS = (
    "высокий спрос",
    "хорошо сдается",
    "хорошо сдаётся",
    "быстро сдать",
    "быстрой сдачи",
    "привлекательным для арендаторов",
)

YIELD_PROMISE_MARKERS = (
    "доходность",
    "окупаемость",
    "рост цены",
    "вырастет в цене",
    "гарантирован",
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _paragraph_blocks(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]


def _response_payload(response: Any) -> tuple[str, list[Any], Any]:
    if isinstance(response, dict):
        items = response.get("items") if isinstance(response.get("items"), list) else []
        return json.dumps(response, ensure_ascii=False), items, response
    if isinstance(response, list):
        return json.dumps({"items": response}, ensure_ascii=False), response, response
    text = str(response or "")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
            return json.dumps(parsed, ensure_ascii=False), parsed.get("items", []), parsed
    except Exception:
        pass
    return text, [], text

EVIDENCE_WORDS = (
    "школ",
    "сад",
    "детск",
    "парк",
    "лес",
    "двор",
    "метро",
    "отделк",
    "сдан",
    "готов",
    "цена",
    "млн",
    "руб",
    "сделк",
    "ипотек",
    "взнос",
    "ставк",
    "скид",
    "студи",
    "однокомнат",
    "компакт",
)

MIN_REASON_LENGTH = 30

SELLING_MARKERS = (
    "главн",
    "выдел",
    "сильн",
    "почему",
    "это значит",
    "для семьи",
    "для аренды",
    "для переезда",
    "проще",
    "удобн",
    "спокойн",
    "прогул",
    "логист",
    "ликвид",
    "аренд",
    "инвест",
)

GENERIC_REASON_MARKERS = (
    "сдан",
    "отделк",
    "проект",
    "бюджет",
    "понят",
    "комфорт",
    "ликвид",
    "спрос",
    "порог",
    "привлекает",
    "подходит",
)

CONTENT_EVIDENCE_MARKERS = (
    "школ",
    "сад",
    "детск",
    "парк",
    "лес",
    "двор",
    "без машин",
    "метро",
    "отделк",
    "сдан",
    "готов",
    "ипотек",
    "ставк",
    "взнос",
    "расср",
    "скид",
    "охрана",
    "площад",
    "спорт",
)


def _fact_tokens(text: str) -> set[str]:
    low = text.lower()
    tokens: set[str] = set()
    for marker in CONTENT_EVIDENCE_MARKERS:
        if marker in low:
            tokens.add(marker)
    # Keep numeric claims check coarse: if response uses numbers, raw MCP must
    # contain the same digit sequence somewhere. This intentionally catches
    # hallucinated counts like "4 сада" / "2 школы".
    for number in re.findall(r"\d+(?:[,.]\d+)?", text):
        if number in {"1", "2", "3"}:
            continue
        tokens.add(f"num:{number.replace(',', '.')}")
    return tokens


def _mcp_blob(search: dict[str, Any]) -> str:
    payload = {key: value for key, value in search.items() if key != "mcp_request"}
    return " \n".join(_flatten_for_signals(payload)).lower()


def _has_honest_sparse_reason(search: dict[str, Any]) -> bool:
    missing = search.get("missing")
    if not isinstance(missing, list):
        return False
    text = " ".join(str(item).lower() for item in missing)
    return any(token in text for token in ("подтвержден", "подтверждена", "только", "не имеют актуальных", "превышают бюджет"))


def _unsupported_response_tokens(search: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    response_text, response_items, _response_obj = _response_payload(answer.get("response"))
    visible = answer.get("visible_options") if isinstance(answer.get("visible_options"), list) else []
    if not response_text.strip():
        return []
    # Boundary/no-search answers are allowed to have no MCP facts.
    if not search.get("facts") and not search.get("near") and not visible:
        return []

    blob = _mcp_blob(search)
    unsupported: list[str] = []
    for token in sorted(_fact_tokens(response_text)):
        if token.startswith("num:"):
            number = token.removeprefix("num:")
            if number not in blob:
                unsupported.append(token)
        elif not _token_supported_by_mcp(token, blob):
            unsupported.append(token)
    return unsupported


def _token_supported_by_mcp(token: str, blob: str) -> bool:
    """Return whether a response fact token is supported by raw MCP payload.

    The response is Russian, while MCP/search payload often uses normalized
    field names (`delivered`, `renovation`, `yard_without_cars`). This mapping
    keeps the rule strict without requiring literal Russian words in MCP.
    """
    aliases = {
        "отделк": ("отделк", "finish", "finishing", "renovation", "white box", "final"),
        "сдан": ("сдан", "delivered", "ready", "готов", "built", "корпус сдан"),
        "готов": ("готов", "ready", "delivered", "сдан"),
        "двор": ("двор", "yard", "yard_without_cars"),
        "без машин": ("без машин", "yard_without_cars", "car-free"),
        "школ": ("школ", "school", "schools"),
        "детск": ("детск", "kindergarten", "kindergartens", "children_ground"),
        "сад": ("сад", "kindergarten", "kindergartens"),
        "парк": ("парк", "park", "parks", "park_near"),
        "площад": ("площад", "playground", "children_ground", "sports_ground"),
        "спорт": ("спорт", "sports_ground"),
        "охрана": ("охрана", "security", "secure"),
        "метро": ("метро", "metro", "property_metro"),
        "ипотек": ("ипотек", "mortgage", "mortgage_calc"),
        "взнос": ("взнос", "initial", "down_payment", "first_payment"),
        "ставк": ("ставк", "rate", "mortgage_calc"),
        "расср": ("расср", "installment", "payment_by_installments"),
        "скид": ("скид", "discount"),
    }
    candidates = aliases.get(token, (token,))
    return any(candidate in blob for candidate in candidates)


@dataclass
class CaseRow:
    version: str
    case: str
    command: str
    exit_code: str
    facts_count: int
    near_count: int
    visible_count: int
    facts_names: str
    visible_names: str
    response: str
    params: str
    mcp_request: str
    mcp_response: str
    warnings: str
    prompt_master_verdict: str
    prompt_metrics: str
    answer_latency_metrics: str


def _file_size_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path.relative_to(REPO) if path.is_relative_to(REPO) else path), "exists": False}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(path.relative_to(REPO) if path.is_relative_to(REPO) else path),
        "exists": True,
        "chars": len(text),
        "lines": text.count("\n") + (1 if text else 0),
    }


def prompt_size_metrics() -> dict[str, Any]:
    """Prompt-size metadata for prompt-master reports.

    This is intentionally static/read-only: prompt shortening decisions should
    compare these numbers with prompt_master verdicts and task-list latency, not
    rely on intuition.
    """
    chat = _file_size_metrics(REPO / "prompts" / "chat_v1.txt")
    prompt_master = _file_size_metrics(REPO / "prompts" / "eval" / "prompt_master_v1.txt")
    scenario_dir = REPO / "prompts" / "scenarios"
    scenario_files = sorted(scenario_dir.glob("*_v1.txt")) if scenario_dir.exists() else []
    scenarios = [_file_size_metrics(path) for path in scenario_files]
    return {
        "chat_v1": chat,
        "prompt_master_v1": prompt_master,
        "scenario_overlays": {
            "count": len(scenarios),
            "total_chars": sum(int(row.get("chars") or 0) for row in scenarios),
            "files": scenarios,
        },
    }


def _latency_from_health(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        health = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {"available": False, "reason": f"health_json_parse_failed:{type(exc).__name__}"}
    checks = health.get("checks") if isinstance(health, dict) else []
    if not isinstance(checks, list):
        return {"available": False, "reason": "health_json_without_checks"}
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "answer_model_task_latency":
            data = check.get("data") if isinstance(check.get("data"), dict) else {}
            recent = data.get("recent") if isinstance(data.get("recent"), dict) else {}
            previous = data.get("previous") if isinstance(data.get("previous"), dict) else {}
            return {
                "available": bool(data.get("available")),
                "source": str(path),
                "recent": {
                    "count": recent.get("count"),
                    "avg_duration_sec": recent.get("avg_duration_sec"),
                    "median_duration_sec": recent.get("median_duration_sec"),
                    "avg_query_chars": recent.get("avg_query_chars"),
                    "avg_system_prompt_chars": recent.get("avg_system_prompt_chars"),
                },
                "previous": {
                    "count": previous.get("count"),
                    "avg_duration_sec": previous.get("avg_duration_sec"),
                    "median_duration_sec": previous.get("median_duration_sec"),
                    "avg_query_chars": previous.get("avg_query_chars"),
                    "avg_system_prompt_chars": previous.get("avg_system_prompt_chars"),
                },
                "delta_recent_minus_previous": data.get("delta_recent_minus_previous") or {},
            }
    return {"available": False, "reason": "answer_model_task_latency_missing"}


def _prompt_master_console_preview(raw: str) -> str:
    try:
        data = json.loads(raw)
    except Exception:
        return raw[:500]
    return json.dumps(
        {
            "score": data.get("score"),
            "verdict": data.get("verdict"),
            "problem_level": data.get("problem_level"),
            "next_fix": data.get("next_fix"),
        },
        ensure_ascii=False,
    )


def _extract_balanced_json(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    brace = text.find("{", start)
    if brace < 0:
        return None, start
    depth = 0
    in_string = False
    escape = False
    for i in range(brace, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                raw = text[brace : i + 1]
                try:
                    return json.loads(raw), i + 1
                except json.JSONDecodeError:
                    return None, i + 1
    return None, len(text)


def _split_cases(raw: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^=== CASE: (.+?) ===$", raw, flags=re.MULTILINE))
    chunks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        chunks.append((name, raw[match.start() : end]))
    return chunks


def _names(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            result.append(str(item["name"]))
    return result


def _has_mcp_response_payload(search: dict[str, Any]) -> bool:
    """Return True only when `search` contains a real MCP/search payload.

    `mcp_request` is a request contract, not a response. When a CLI run crashes
    after printing `MCP-запрос:` but before `Поисковые факты:`, the parser may
    still have a request object. That request must not be published as
    `mcp_response`.
    """
    return any(key in search for key in ("facts", "near", "missing", "params"))


def _has_evidence(value: str) -> bool:
    low = value.lower()
    return any(word in low for word in EVIDENCE_WORDS) or bool(re.search(r"\d", value))


def _item_reasons(items: list[Any]) -> list[str]:
    reasons: list[str] = []
    for item in items:
        if isinstance(item, dict):
            reason = item.get("reason")
            if isinstance(reason, str) and reason.strip():
                reasons.append(reason.strip())
    return reasons


def _is_generic_reason(reason: str) -> bool:
    low = reason.lower()
    return any(marker in low for marker in GENERIC_REASON_MARKERS) and not _has_evidence(reason)


def _is_short_reason(reason: str) -> bool:
    clean = re.sub(r"\s+", " ", reason).strip()
    return len(clean) < MIN_REASON_LENGTH


def _flatten_for_signals(value: Any, prefix: str = "") -> list[str]:
    items: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}{key}:" if prefix else f"{key}:"
            items.extend(_flatten_for_signals(child, child_prefix))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}{idx}:" if prefix else f"{idx}:"
            items.extend(_flatten_for_signals(child, child_prefix))
    elif isinstance(value, str):
        items.append(f"{prefix}{value}" if prefix else value)
    elif value is not None:
        items.append(f"{prefix}{value}" if prefix else str(value))
    return items


def _family_search_signals(search: dict[str, Any]) -> dict[str, bool]:
    signals = {"schools": False, "kindergartens": False, "parks": False, "yard_without_cars": False}
    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []

    for bucket in (facts, near):
        for item in bucket:
            blob = " \n".join(_flatten_for_signals(item)).lower() if isinstance(item, dict) else str(item).lower()
            if any(token in blob for token in ("school", "школ")):
                signals["schools"] = True
            if any(token in blob for token in ("kindergarten", "сад", "детск")):
                signals["kindergartens"] = True
            if any(token in blob for token in ("park", "лес", "зелен", "прогул")):
                signals["parks"] = True
            if any(token in blob for token in ("yard_without_cars", "двор без машин", "без машин", "car-free")):
                signals["yard_without_cars"] = True
    return signals


def _family_search_score(signals: dict[str, bool]) -> int:
    return sum(1 for value in signals.values() if value)


def _mortgage_need(request: dict[str, Any]) -> list[str]:
    need = request.get("need") if isinstance(request.get("need"), list) else []
    return [str(item).strip().lower() for item in need if isinstance(item, str) and item.strip()]


def _mortgage_requested(request: dict[str, Any], params: dict[str, Any], command: str = "") -> bool:
    facets = request.get("facets") if isinstance(request.get("facets"), list) else []
    params_facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    mortgage_type = str(request.get("mortgage_type") or params.get("mortgage_type") or "").strip()
    blob = " ".join([command, json.dumps(request, ensure_ascii=False), json.dumps(params, ensure_ascii=False)]).lower()
    return (
        mortgage_type != ""
        or any(str(item).strip().lower() == "mortgage" for item in facets + params_facets)
        or any(token in blob for token in ("ипотек", "it ипот", "айти ипот", "льготн", "ставк", "первонач", "рассроч"))
    )


def _mortgage_request_signals(request: dict[str, Any]) -> dict[str, bool]:
    need = _mortgage_need(request)
    facets = request.get("facets") if isinstance(request.get("facets"), list) else []
    return {
        "facet": any(str(item).strip().lower() == "mortgage" for item in facets),
        "mortgage_calc": any("mortgage_calc" in item or "ипотеч" in item for item in need),
        "mortgage": any(item == "mortgage" or "mortgage" in item for item in need),
        "discount": any("discount" in item or "скид" in item for item in need),
        "payment_by_installments": any("payment_by_installments" in item or "расср" in item for item in need),
        "price": any("price" in item or "цена" in item for item in need),
    }


def _mortgage_search_signals(search: dict[str, Any]) -> dict[str, bool]:
    signals = {
        "mortgage_calc": False,
        "mortgage": False,
        "discount": False,
        "payment_by_installments": False,
        "price": False,
    }
    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []
    blob_parts: list[str] = []
    for bucket in (facts, near):
        for item in bucket:
            blob_parts.extend(_flatten_for_signals(item))
    blob = " \n".join(blob_parts).lower()
    if "mortgage_calc" in blob or "ипотеч" in blob or "ставк" in blob or "взнос" in blob:
        signals["mortgage_calc"] = True
    if "mortgage" in blob or "ипотек" in blob:
        signals["mortgage"] = True
    if "discount" in blob or "скид" in blob or "акци" in blob:
        signals["discount"] = True
    if "payment_by_installments" in blob or "расср" in blob:
        signals["payment_by_installments"] = True
    if "price" in blob or "цена" in blob or "млн" in blob or "руб" in blob or "price_range" in blob:
        signals["price"] = True
    return signals


def _signal_score(signals: dict[str, bool]) -> int:
    return sum(1 for value in signals.values() if value)


def _family_request_need(request: dict[str, Any]) -> list[str]:
    need = request.get("need") if isinstance(request.get("need"), list) else []
    return [str(item).strip().lower() for item in need if isinstance(item, str) and item.strip()]


def _family_request_signals(request: dict[str, Any]) -> dict[str, bool]:
    need = _family_request_need(request)
    return {
        "schools": any(token in item for item in need for token in ("school", "школ")),
        "kindergartens": any(token in item for item in need for token in ("kindergarten", "сад", "детск")),
        "parks": any(token in item for item in need for token in ("park", "forest", "зелен", "лес")),
        "yard_without_cars": any(token in item for item in need for token in ("yard_without_cars", "car-free", "без машин")),
    }


def _request_need(request: dict[str, Any]) -> list[str]:
    need = request.get("need") if isinstance(request.get("need"), list) else []
    return [str(item).strip().lower() for item in need if isinstance(item, str) and item.strip()]


def _investment_request_signals(request: dict[str, Any]) -> dict[str, bool]:
    need = _request_need(request)
    return {
        "entry_price": any("entry_price" in item or "price" in item or "цена" in item for item in need),
        "mortgage": any("mortgage" in item or "ипот" in item for item in need),
        "egrn_sales": any("egrn" in item or "сдел" in item for item in need),
        "counter_novos": any("counter" in item or "ads" in item or "объяв" in item for item in need),
        "compact_lots": any("compact" in item or "apartment_types" in item or "студи" in item for item in need),
    }


def _rental_request_signals(request: dict[str, Any]) -> dict[str, bool]:
    need = _request_need(request)
    return {
        "compact": any("compact" in item or "apartment_types" in item or "студи" in item for item in need),
        "finishing": any("finishing" in item or "finish" in item or "отдел" in item for item in need),
        "metro": any("metro" in item or "метро" in item or "transport" in item for item in need),
        "ready": any("ready" in item or "delivered" in item or "готов" in item or "сдан" in item for item in need),
        "demand": any("demand" in item or "counter" in item or "egrn" in item or "ads" in item for item in need),
    }


def _scenario_search_blob(search: dict[str, Any]) -> str:
    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []
    blob_parts: list[str] = []
    for bucket in (facts, near):
        for item in bucket:
            blob_parts.extend(_flatten_for_signals(item))
    return " \n".join(blob_parts).lower()


def _investment_search_signals(search: dict[str, Any]) -> dict[str, bool]:
    blob = _scenario_search_blob(search)
    return {
        "entry_price": any(token in blob for token in ("price", "price_range", "min_price", "цена", "млн", "руб")),
        "compact_lots": any(token in blob for token in ("compact", "apartment_types", "студи", "одноком", "area", "rooms")),
        "egrn_sales": any(token in blob for token in ("egrn", "sales", "сдел")),
        "counter_novos": any(token in blob for token in ("counter_novos", "count_ads", "count_discounts", "объяв")),
        "finance": any(token in blob for token in ("mortgage", "mortgage_calc", "discount", "скид", "ипот")),
    }


def _rental_search_signals(search: dict[str, Any]) -> dict[str, bool]:
    blob = _scenario_search_blob(search)
    return {
        "compact": any(token in blob for token in ("compact", "apartment_types", "студи", "одноком", "rooms", "area")),
        "finishing": any(token in blob for token in ("finishing", "renovation", "отдел")),
        "metro": any(token in blob for token in ("metro", "property_metro", "walk_minutes", "транспорт")),
        "ready": any(token in blob for token in ("ready", "delivered", "сдан", "готов")),
        "demand": any(token in blob for token in ("ads", "counter_novos", "count_ads", "egrn_top_novos", "sales")),
    }


def validate_case(search: dict[str, Any], answer: dict[str, Any], forbidden_words: tuple[str, ...], command: str = "") -> list[str]:
    warnings: list[str] = []
    response, response_items, _response_obj = _response_payload(answer.get("response"))
    low_response = response.lower()
    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []
    visible = answer.get("visible_options") if isinstance(answer.get("visible_options"), list) else []
    request = search.get("mcp_request") if isinstance(search.get("mcp_request"), dict) else {}
    params = answer.get("params") if isinstance(answer.get("params"), dict) else {}
    request_purpose = str(request.get("purpose") or "").strip().lower()
    answer_purpose = str(params.get("purpose") or "").strip().lower()
    purpose = request_purpose or answer_purpose
    scenario = purpose
    mortgage_requested = _mortgage_requested(request, params, command)
    if search.get("mcp_response_missing_after_request"):
        warnings.append("mcp_response_missing_after_request")
    try:
        requested_count = int(request.get("count") or params.get("count") or 0)
    except Exception:
        requested_count = 0

    for name, pattern in TYPO_PATTERNS.items():
        if pattern.search(response):
            warnings.append(name)

    for word in forbidden_words:
        if word.lower() in low_response:
            warnings.append(f"forbidden_word:{word}")

    unsupported_tokens = _unsupported_response_tokens(search, answer)
    if unsupported_tokens:
        warnings.append("response_not_supported_by_mcp:" + ",".join(unsupported_tokens[:8]))

    if len(facts) >= 3 and len(visible) < 3:
        warnings.append(f"facts_visible_mismatch:facts={len(facts)} visible={len(visible)}")

    if len(visible) >= 1 and len(response_items) == 0:
        warnings.append("response_not_structured_json")
    elif len(response_items) > 0 and len(visible) > 0 and len(response_items) != len(visible):
        warnings.append(f"response_items_visible_mismatch:items={len(response_items)} visible={len(visible)}")

    if len(facts) >= 3 and len(response_items) < 3 and purpose not in {"default", "off_topic", "operator"}:
        warnings.append(f"top3_shortlist_missing:have={len(facts)} shown={len(response_items)}")

    if purpose in {"family", "investment", "rental", "search", "repeat_search"} and requested_count >= 3:
        if len(facts) < requested_count and not near and not (len(facts) >= 1 and _has_honest_sparse_reason(search)):
            warnings.append(
                f"mcp_shortlist_sparse:requested={requested_count} got={len(facts) + len(near)}"
            )

    for idx, fact in enumerate(facts, start=1):
        if not isinstance(fact, dict):
            continue
        for key, value in fact.items():
            if not key.startswith("why_") or not isinstance(value, str) or not value.strip():
                continue
            low = value.lower()
            empty_ad = any(word in low for word in EMPTY_AD_WORDS)
            if empty_ad and not _has_evidence(value):
                warnings.append(f"empty_why_without_evidence:fact={idx}:{key}")
            if _is_short_reason(value):
                warnings.append(f"reason_too_short:fact={idx}:{key}")

    if purpose == "family":
        request_signals = _family_request_signals(request)
        family_signals = _family_search_signals(search)
        if _family_search_score(request_signals) == 0:
            warnings.append("mcp_family_request_missing")
        elif _family_search_score(request_signals) < 2:
            warnings.append("mcp_family_request_sparse")
        if _family_search_score(family_signals) == 0:
            warnings.append("mcp_family_missing")
        elif _family_search_score(family_signals) < 2:
            warnings.append("mcp_family_sparse")

    if purpose == "investment":
        request_signals = _investment_request_signals(request)
        search_signals = _investment_search_signals(search)
        if _signal_score(request_signals) == 0:
            warnings.append("mcp_investment_request_missing")
        elif _signal_score(request_signals) < 3:
            warnings.append("mcp_investment_request_sparse")
        if _signal_score(search_signals) == 0:
            warnings.append("mcp_investment_missing")
        elif _signal_score(search_signals) < 2:
            warnings.append("mcp_investment_sparse")
        if not _contains_any(low_response, INVESTMENT_RESPONSE_MARKERS):
            warnings.append("investment_response_missing")
        if _contains_any(low_response, YIELD_PROMISE_MARKERS):
            warnings.append("investment_yield_promise")

    if purpose == "rental":
        request_signals = _rental_request_signals(request)
        search_signals = _rental_search_signals(search)
        if _signal_score(request_signals) == 0:
            warnings.append("mcp_rental_request_missing")
        elif _signal_score(request_signals) < 3:
            warnings.append("mcp_rental_request_sparse")
        if _signal_score(search_signals) == 0:
            warnings.append("mcp_rental_missing")
        elif _signal_score(search_signals) < 2:
            warnings.append("mcp_rental_sparse")
        if not _contains_any(low_response, RENTAL_RESPONSE_MARKERS):
            warnings.append("rental_response_missing")
        if _contains_any(low_response, OVERCLAIM_RENTAL_MARKERS) and not any(
            search_signals.get(key) for key in ("demand",)
        ):
            warnings.append("rental_demand_overclaim")
        if _contains_any(low_response, YIELD_PROMISE_MARKERS):
            warnings.append("rental_yield_promise")

    if scenario == "operator":
        if _contains_any(low_response, ("дду", "эскроу", "официальн", "застройщик")):
            warnings.append("operator_legal_or_booking_claim")
        if not _contains_any(low_response, ("оператор", "менеджер", "оставить номер", "номер для связи")):
            warnings.append("operator_handoff_missing")

    if scenario == "fact_check":
        if not _contains_any(low_response, ("да", "нет", "не подтверж", "нет подтверж")):
            warnings.append("fact_check_no_clear_verdict")
        if not facts and not near and not visible:
            warnings.append("fact_check_evidence_missing")

    if scenario == "repeat_search":
        exclude = request.get("exclude") if isinstance(request.get("exclude"), list) else []
        if not exclude:
            warnings.append("repeat_search_exclude_missing")

    if mortgage_requested:
        mortgage_request_signals = _mortgage_request_signals(request)
        mortgage_search_signals = _mortgage_search_signals(search)
        if not mortgage_request_signals.get("facet"):
            warnings.append("mcp_mortgage_facet_missing")
        if _signal_score(mortgage_request_signals) < 3:
            warnings.append("mcp_mortgage_request_sparse")
        if _signal_score(mortgage_search_signals) == 0:
            warnings.append("mcp_mortgage_missing")
        elif _signal_score(mortgage_search_signals) < 2:
            warnings.append("mcp_mortgage_sparse")
        if _signal_score(mortgage_search_signals) >= 2 and "ипот" not in low_response and "ставк" not in low_response and "взнос" not in low_response and "расср" not in low_response and "скид" not in low_response:
            warnings.append("mortgage_response_missing")

    return sorted(set(warnings))


def _scenario_from_answer(case: str, answer: dict[str, Any]) -> str:
    params = answer.get("params") if isinstance(answer.get("params"), dict) else {}
    purpose = str(params.get("purpose") or "").strip()
    if case:
        return case
    return purpose or "unknown"


def prompt_master_verdict(
    case: str,
    command: str,
    search: dict[str, Any],
    answer: dict[str, Any],
    warnings: list[str],
    *,
    prompt_metrics: dict[str, Any] | None = None,
    answer_latency_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Local prompt-master style verdict.

    This mirrors prompts/eval/prompt_master_v1.txt and is deliberately
    non-mutating. It is deterministic so table writes do not require extra LLM
    cost; if later we wire a real evaluator model, keep the same JSON shape.
    """
    response, response_items, _response_obj = _response_payload(answer.get("response"))
    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []
    visible = answer.get("visible_options") if isinstance(answer.get("visible_options"), list) else []
    response_text, _, _ = _response_payload(answer.get("response"))
    scenario = _scenario_from_answer(case, answer)
    request = search.get("mcp_request") if isinstance(search.get("mcp_request"), dict) else {}
    params = answer.get("params") if isinstance(answer.get("params"), dict) else {}
    request_purpose = str(request.get("purpose") or "").strip().lower()
    answer_purpose = str(params.get("purpose") or "").strip().lower()
    purpose = request_purpose or answer_purpose
    low = response.lower()
    warning_text = "; ".join(warnings)
    blocks = _paragraph_blocks(response)
    reasons = _item_reasons(response_items)
    normalized_reasons = [re.sub(r"\s+", " ", r.lower()).strip() for r in reasons if r.strip()]
    request_signals = _family_request_signals(request) if purpose == "family" else {}
    family_signals = _family_search_signals(search) if purpose == "family" else {}
    family_signal_score = _family_search_score(family_signals) if family_signals else 0
    family_request_score = _family_search_score(request_signals) if request_signals else 0
    mortgage_requested = _mortgage_requested(request, params, command)
    mortgage_request_signals = _mortgage_request_signals(request) if mortgage_requested else {}
    mortgage_search_signals = _mortgage_search_signals(search) if mortgage_requested else {}

    score = 100
    problem_levels: list[str] = []
    issues: list[str] = []
    fixes: list[str] = []

    if any(w.startswith("typo_") or w.startswith("forbidden_word:") for w in warnings):
        score -= 35
        problem_levels.append("safety")
        issues.append("в ответе есть опечатка или запрещённое слово")
        fixes.append("усилить safety validator / финальную проверку текста")

    if any(w.startswith("response_not_supported_by_mcp") for w in warnings):
        score = 0
        problem_levels.append("safety")
        issues.append("ответ содержит факты, которых нет в сыром MCP/search payload")
        fixes.append("перепроверить MCP response и убрать/запретить неподтверждённые факты")

    if any(w.startswith("mcp_response_missing_after_request") for w in warnings):
        score = 0
        problem_levels.append("mcp_search")
        issues.append("MCP/search payload не пришёл или не был распарсен")
        fixes.append("устранить падение до получения Поисковые факты или починить MCP/search transport")

    if any(w.startswith("facts_visible_mismatch") for w in warnings):
        score -= 25
        problem_levels.append("response")
        issues.append("поиск дал больше вариантов, чем ответ показал клиенту")
        fixes.append("усилить response prompt и visible_options contract")

    if any(w.startswith("response_not_structured_json") for w in warnings):
        score -= 15
        problem_levels.append("response")
        issues.append("ответ не оформлен как JSON-объект с items[]")
        fixes.append("вернуть response как JSON-объект с items/message/question")

    if any(w.startswith("response_items_visible_mismatch") for w in warnings):
        score -= 10
        problem_levels.append("response")
        issues.append("число items не совпадает с visible_options")
        fixes.append("синхронизировать response.items и visible_options")

    if any(w.startswith("top3_shortlist_missing") for w in warnings):
        score -= 20
        problem_levels.append("response")
        issues.append("shortlist схлопнулся, хотя search дал 3+ вариантов")
        fixes.append("показывать top-3, если facts+near уже дают несколько вариантов")

    if any(w.startswith("empty_why_without_evidence") for w in warnings):
        score -= 20
        problem_levels.append("mcp_search")
        issues.append("why_* звучит рекламно, но в нём мало проверяемого evidence")
        fixes.append("уточнить MCP query/profile и нормализацию why_*")

    if any(w.startswith("mcp_family_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("family MCP/search не принёс family facts")
        fixes.append("расширить family MCP query/profile: школы, сады, парки, двор без машин")

    if any(w.startswith("mcp_family_sparse") for w in warnings):
        score -= 20
        problem_levels.append("mcp_search")
        issues.append("family MCP/search слишком бедный: мало family facts")
        fixes.append("усилить family MCP query/profile и нормализацию family facts")

    if any(w.startswith("mcp_family_request_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("family MCP request не содержит family-опор")
        fixes.append("добавить в request need: schools, kindergartens, parks, yard_without_cars")

    if any(w.startswith("mcp_family_request_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("family MCP request слишком узкий")
        fixes.append("сделать family request шире по школам/садам/паркам/двору без машин")

    if any(w.startswith("mcp_family_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("family MCP/search не принёс family facts")
        fixes.append("расширить family MCP query/profile: школы, сады, парки, двор без машин")

    if any(w.startswith("mcp_family_sparse") for w in warnings):
        score -= 20
        problem_levels.append("mcp_search")
        issues.append("family MCP/search слишком бедный: мало family facts")
        fixes.append("усилить family MCP query/profile и нормализацию family facts")

    if any(w.startswith("mcp_shortlist_sparse") for w in warnings):
        score -= 25
        problem_levels.append("mcp_search")
        issues.append("по request был запрошен shortlist, но search вернул слишком мало вариантов")
        fixes.append("расширить MCP request/profile и поиск до 3 вариантов, если это возможно")

    if any(w.startswith("mcp_mortgage_facet_missing") for w in warnings):
        score -= 25
        problem_levels.append("mcp_search")
        issues.append("ипотечный запрос не был оформлен как mortgage facet")
        fixes.append("добавить facets:[\"mortgage\"] и mortgage_type в request builder")

    if any(w.startswith("mcp_mortgage_request_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("mortgage request слишком узкий")
        fixes.append("добавить mortgage_calc, mortgage, discount, payment_by_installments, price в need")

    if any(w.startswith("mcp_mortgage_missing") for w in warnings):
        score -= 25
        problem_levels.append("mcp_search")
        issues.append("MCP/search не вернул ипотечные факты по ипотечному запросу")
        fixes.append("расширить finance MCP profile или честно пометить gap без ставок/взносов")

    if any(w.startswith("mcp_mortgage_sparse") for w in warnings):
        score -= 10
        problem_levels.append("mcp_search")
        issues.append("MCP/search вернул слишком мало ипотечных фактов")
        fixes.append("проверить mortgage_calc/mortgage/discount/payment_by_installments в search profile")

    if any(w.startswith("mortgage_response_missing") for w in warnings):
        score -= 15
        problem_levels.append("response")
        issues.append("ответ не объяснил ипотечный слой, хотя finance-факты пришли")
        fixes.append("добавить короткий ипотечный блок в response по реальным finance fields")

    if any(w.startswith("mcp_investment_request_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("investment MCP request не содержит инвестиционные need")
        fixes.append("добавить entry_price, mortgage, egrn_sales, counter_novos, compact_lots в investment request")
    if any(w.startswith("mcp_investment_request_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("investment MCP request слишком узкий")
        fixes.append("расширить investment request до цены входа, сделок/объявлений и компактных форматов")
    if any(w.startswith("mcp_investment_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("investment MCP/search не вернул инвестиционные опоры")
        fixes.append("починить investment MCP profile/card: price, egrn/counter, apartment_types, finance")
    if any(w.startswith("mcp_investment_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("investment MCP/search вернул слишком мало investment evidence")
        fixes.append("усилить investment card и нормализацию вложенных блоков")
    if any(w.startswith("investment_response_missing") for w in warnings):
        score -= 20
        problem_levels.append("response")
        issues.append("investment response не объяснил инвестиционную пользу")
        fixes.append("сначала показывать цену входа, компактность, сделки/объявления, finance-факты")
    if any(w.startswith("investment_yield_promise") for w in warnings):
        score = min(score, 40)
        problem_levels.append("safety")
        issues.append("investment response обещает доходность/рост/окупаемость")
        fixes.append("запретить обещания доходности и оставить только проверяемые факты")

    if any(w.startswith("mcp_rental_request_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("rental MCP request не содержит арендные need")
        fixes.append("добавить compact, finishing, metro, ready, demand в rental request")
    if any(w.startswith("mcp_rental_request_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("rental MCP request слишком узкий")
        fixes.append("расширить rental request до компактности, отделки, метро, готовности и demand evidence")
    if any(w.startswith("mcp_rental_missing") for w in warnings):
        score -= 30
        problem_levels.append("mcp_search")
        issues.append("rental MCP/search не вернул арендные опоры")
        fixes.append("починить rental MCP profile/card: apartment_types, ads/counter/egrn, finishing, metro, ready")
    if any(w.startswith("mcp_rental_sparse") for w in warnings):
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("rental MCP/search вернул слишком мало rental evidence")
        fixes.append("усилить rental card и нормализацию вложенных blocks")
    if any(w.startswith("rental_response_missing") for w in warnings):
        score -= 20
        problem_levels.append("response")
        issues.append("rental response не объяснил арендо-пригодность")
        fixes.append("сначала показывать компактность, отделку, метро, готовность и подтверждённый demand evidence")
    if any(w.startswith("rental_demand_overclaim") for w in warnings):
        score = min(score, 50)
        problem_levels.append("safety")
        issues.append("rental response заявляет высокий спрос без подтверждения в card")
        fixes.append("запретить фразы про спрос без ads/counter/egrn evidence")
    if any(w.startswith("rental_yield_promise") for w in warnings):
        score = min(score, 40)
        problem_levels.append("safety")
        issues.append("rental response обещает доходность/окупаемость")
        fixes.append("убрать доходность/окупаемость из rental без подтверждённых данных")

    if any(w.startswith("operator_legal_or_booking_claim") for w in warnings):
        score -= 30
        problem_levels.append("safety")
        issues.append("operator response придумывает юридические или броневые условия")
        fixes.append("оставить мягкий handoff оператору без ДДУ/эскроу/официальных представителей")
    if any(w.startswith("operator_handoff_missing") for w in warnings):
        score -= 20
        problem_levels.append("response")
        issues.append("operator response не ведёт к оператору/номеру")
        fixes.append("коротко предложить оставить номер для проверки актуальных условий")
    if any(w.startswith("fact_check_no_clear_verdict") for w in warnings):
        score -= 20
        problem_levels.append("response")
        issues.append("fact_check не дал ясный вердикт да/нет/не подтверждено")
        fixes.append("начинать fact_check с явного подтверждения или отсутствия подтверждения")
    if any(w.startswith("fact_check_evidence_missing") for w in warnings):
        score -= 25
        problem_levels.append("mcp_search")
        issues.append("fact_check не имеет evidence по выбранному ЖК")
        fixes.append("перед ответом подтягивать card/facts по selected_option и fact_to_check")
    if any(w.startswith("repeat_search_exclude_missing") for w in warnings):
        score -= 20
        problem_levels.append("scenario")
        issues.append("repeat_search не передал exclude старых вариантов")
        fixes.append("добавить visible_options в exclude перед новым MCP search")

    if any(w.startswith("reason_too_short") for w in warnings):
        score -= 15
        problem_levels.append("response")
        issues.append("reason у объекта слишком короткий и похож на ярлык")
        fixes.append("сделать reason длиннее и объяснять пользу, а не маркировать факт")

    if len(response_items) >= 2 and len(visible) > 0 and len(response_items) != len(visible):
        score -= 10
        problem_levels.append("response")
        issues.append("JSON items не совпадают с visible_options")
        fixes.append("сделать response.items и visible_options одинаковыми по числу и порядку")
    elif not response_items and len(visible) >= 2 and response.count("\n\n") < max(1, len(visible)):
        score -= 10
        problem_levels.append("response")
        issues.append("объекты слиты в один плотный блок")
        fixes.append("сделать response.items JSON-массивом или разнести каждый ЖК в отдельный блок")

    if len(response_items) >= 2:
        if normalized_reasons and len(set(normalized_reasons)) == 1:
            score -= 20
            problem_levels.append("response")
            issues.append("у нескольких ЖК одна и та же сухая причина")
            fixes.append("сделать у каждого ЖК свой продающий акцент")
        generic_count = sum(1 for reason in reasons if _is_generic_reason(reason))
        if generic_count >= max(1, len(reasons) // 2):
            score -= 15
            problem_levels.append("response")
            issues.append("причины у ЖК слишком общие и почти не продают")
            fixes.append("добавить отличия: школы, сады, парки, двор, метро, готовность, переезд")
        if not any(marker in low for marker in SELLING_MARKERS) and not any(
            marker in " ".join(normalized_reasons) for marker in SELLING_MARKERS
        ):
            score -= 10
            problem_levels.append("response")
            issues.append("в ответе нет продающего акцента")
            fixes.append("сделать ответ более консультативным и сравнивающим")
        short_reasons = [reason for reason in reasons if _is_short_reason(reason)]
        if short_reasons:
            score -= 15
            problem_levels.append("response")
            issues.append("reason у объекта слишком короткий и похож на ярлык")
            fixes.append("сделать reason объясняющим пользу, а не короткой подписью")

    if reasons and len(response_items) < 2:
        short_reasons = [reason for reason in reasons if _is_short_reason(reason)]
        if short_reasons:
            score -= 10
            problem_levels.append("response")
            issues.append("reason у объекта слишком короткий")
            fixes.append("сделать reason длиннее и объяснять пользу, а не маркировать факт")

    blocks = _paragraph_blocks(response)
    if not response_items and len(visible) == 1 and len(blocks) <= 1 and len(response) > 220:
        score -= 5
        problem_levels.append("response")
        issues.append("одиночный ответ выглядит слишком плотным")
        fixes.append("сделать 2-4 коротких абзаца вместо длинной простыни")

    if scenario == "family" and not any(token in low for token in ("школ", "сад", "парк", "двор")):
        score -= 20
        problem_levels.append("response")
        issues.append("семейный ответ не показал семейные опорные факты")
        fixes.append("усилить family response prompt/card fields")
    if scenario == "family" and family_signal_score == 0:
        score -= 20
        problem_levels.append("mcp_search")
        issues.append("в family MCP/search нет школ, садов, парков или двора без машин")
        fixes.append("пересобрать family MCP query/profile до response")
    if scenario == "family" and family_request_score == 0:
        score -= 15
        problem_levels.append("mcp_search")
        issues.append("в family MCP request не заданы семейные need")
        fixes.append("добавить family need в query builder до запуска поиска")
    if scenario == "family" and len(response_items) >= 2 and normalized_reasons:
        if all(_is_generic_reason(reason) for reason in reasons):
            score -= 15
            problem_levels.append("response")
            issues.append("семейный shortlist слишком сухой и одинаковый")
            fixes.append("добавить в family сценарий явный сравнительный акцент и family facts")
        if not any(token in " ".join(normalized_reasons) for token in ("школ", "сад", "парк", "двор", "прогул", "ребён")):
            score -= 20
            problem_levels.append("response")
            issues.append("family response не показал семейную пользу в reason")
            fixes.append("добавить в family reason реальные family facts и человеческую пользу")
        if any(_is_short_reason(reason) for reason in reasons):
            score -= 10
            problem_levels.append("response")
            issues.append("family reason слишком короткий для продающей подачи")
            fixes.append("сделать family reason длиннее и объяснить пользу")
    if scenario == "repeat_search":
        if not _contains_any(low, ("друг", "нов", "ещё", "еще", "повтор", "другие", "новые")):
            score -= 15
            problem_levels.append("response")
            issues.append("repeat_search не показал, что это новые варианты")
            fixes.append("сказать, что это другие варианты, и обновить сравнительный акцент")
    if scenario == "default":
        if response_items:
            score -= 15
            problem_levels.append("response")
            issues.append("default не должен показывать ЖК до распознавания сценария")
            fixes.append("убрать items и оставить только один уточняющий вопрос")
        if response.count("?") != 1:
            score -= 10
            problem_levels.append("response")
            issues.append("default должен задавать ровно один уточняющий вопрос")
            fixes.append("оставить один вопрос про главный ориентир")

    if scenario == "investment" and not any(token in low for token in ("сдел", "ипотек", "взнос", "ставк", "скид", "вход")):
        score -= 20
        problem_levels.append("response")
        issues.append("инвестиционный ответ не объяснил инвестиционную причину")
        fixes.append("расширить investment MCP profile или response prompt")

    if scenario == "rental" and not any(token in low for token in ("аренд", "студи", "одноком", "отделк", "метро", "сдан", "готов")):
        score -= 20
        problem_levels.append("response")
        issues.append("rental-ответ не объяснил арендо-пригодность")
        fixes.append("усилить rental subprompt/card presentation")

    if len(facts) == 0 and len(near) == 0 and case not in {"off_topic", "default", "operator", "fact_check"}:
        score -= 20
        problem_levels.append("mcp_search")
        issues.append("MCP/search не вернул вариантов для предметного сценария")
        fixes.append("проверить MCP query profile или расширение поиска")

    if not response.strip():
        score = 0
        problem_levels.append("response")
        issues.append("ответ пустой")
        fixes.append("проверить chat model output parsing")

    score = max(0, min(100, score))
    unique_levels = sorted(set(problem_levels))
    if not unique_levels:
        problem_level = "none"
    elif len(unique_levels) == 1:
        problem_level = unique_levels[0]
    else:
        problem_level = "mixed"

    if score >= 90 and not warnings:
        verdict = "good"
    elif score >= 70:
        verdict = "watch"
    else:
        verdict = "bad"

    if not issues:
        issues.append("серьёзных проблем не найдено")
    if not fixes:
        fixes.append("оставить как baseline и отслеживать регресс по version")

    mcp_signals: dict[str, Any] = {}
    if purpose == "family":
        mcp_signals = {
            "request": request_signals,
            "search": family_signals,
        }
    if mortgage_requested:
        mcp_signals["mortgage"] = {
            "request": mortgage_request_signals,
            "search": mortgage_search_signals,
        }

    verdict_payload = {
        "score": score,
        "verdict": verdict,
        "problem_level": problem_level,
        "what_went_wrong": "; ".join(dict.fromkeys(issues)),
        "next_fix": "; ".join(dict.fromkeys(fixes)),
        "source_warnings": warning_text,
        "mcp_signals": mcp_signals,
        "prompt": "prompts/eval/prompt_master_v1.txt",
        "scenario": scenario,
        "command": command,
    }
    if prompt_metrics:
        verdict_payload["prompt_metrics"] = prompt_metrics
    if answer_latency_metrics:
        verdict_payload["answer_latency_metrics"] = answer_latency_metrics
    return verdict_payload


def parse_case(
    name: str,
    chunk: str,
    version: str,
    forbidden_words: tuple[str, ...],
    *,
    prompt_metrics: dict[str, Any] | None = None,
    answer_latency_metrics: dict[str, Any] | None = None,
) -> CaseRow:
    cmd = ""
    exit_code = ""
    cmd_match = re.search(r"^\[CMD\]\s*(.+)$", chunk, flags=re.MULTILINE)
    if cmd_match:
        cmd = cmd_match.group(1).strip()
    if not cmd:
        query_match = re.search(r"^Запрос:\s*(.+)$", chunk, flags=re.MULTILINE)
        if query_match:
            cmd = query_match.group(1).strip()
    exit_match = re.search(r"^\[EXIT\]\s*(.+)$", chunk, flags=re.MULTILINE)
    if exit_match:
        exit_code = exit_match.group(1).strip()

    facts_marker = chunk.find("Поисковые факты:")
    request_marker = chunk.find("MCP-запрос:")
    answer_marker = chunk.find("Ответ клиенту:")
    search: dict[str, Any] = {}
    answer: dict[str, Any] = {}
    mcp_request: dict[str, Any] = {}
    if request_marker >= 0:
        parsed, _ = _extract_balanced_json(chunk, request_marker)
        if isinstance(parsed, dict):
            mcp_request = parsed
    if facts_marker >= 0:
        parsed, _ = _extract_balanced_json(chunk, facts_marker)
        if isinstance(parsed, dict):
            search = parsed
    if answer_marker >= 0:
        parsed, _ = _extract_balanced_json(chunk, answer_marker)
        if isinstance(parsed, dict):
            answer = parsed

    facts = search.get("facts") if isinstance(search.get("facts"), list) else []
    near = search.get("near") if isinstance(search.get("near"), list) else []
    visible = answer.get("visible_options") if isinstance(answer.get("visible_options"), list) else []
    search_for_validation = dict(search)
    if mcp_request:
        search_for_validation["mcp_request"] = mcp_request
    if mcp_request and not _has_mcp_response_payload(search):
        search_for_validation["mcp_response_missing_after_request"] = True
    warnings = validate_case(search_for_validation, answer, forbidden_words, cmd)
    if exit_code and exit_code != "0":
        warnings.append(f"cli_exit_nonzero:{exit_code}")
    pm_verdict = prompt_master_verdict(
        name,
        cmd,
        search_for_validation,
        answer,
        warnings,
        prompt_metrics=prompt_metrics,
        answer_latency_metrics=answer_latency_metrics,
    )
    response_text, _, _ = _response_payload(answer.get("response"))
    mcp_response_payload = search if _has_mcp_response_payload(search) else {}

    return CaseRow(
        version=version,
        case=name,
        command=cmd,
        exit_code=exit_code,
        facts_count=len(facts),
        near_count=len(near),
        visible_count=len(visible),
        facts_names="; ".join(_names(facts)),
        visible_names="; ".join(_names(visible)),
        response=response_text,
        params=json.dumps(answer.get("params") or {}, ensure_ascii=False),
        mcp_request=json.dumps(mcp_request or {}, ensure_ascii=False),
        mcp_response=json.dumps(mcp_response_payload, ensure_ascii=False),
        warnings="; ".join(warnings),
        prompt_master_verdict=json.dumps(pm_verdict, ensure_ascii=False),
        prompt_metrics=json.dumps(prompt_metrics or {}, ensure_ascii=False),
        answer_latency_metrics=json.dumps(answer_latency_metrics or {}, ensure_ascii=False),
    )


def parse_live_run(
    path: Path,
    version: str,
    forbidden_words: tuple[str, ...],
    *,
    prompt_metrics: dict[str, Any] | None = None,
    answer_latency_metrics: dict[str, Any] | None = None,
) -> list[CaseRow]:
    raw = path.read_text(encoding="utf-8")
    return [
        parse_case(
            name,
            chunk,
            version,
            forbidden_words,
            prompt_metrics=prompt_metrics,
            answer_latency_metrics=answer_latency_metrics,
        )
        for name, chunk in _split_cases(raw)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate live run rows before Google Sheet write")
    parser.add_argument("log", type=Path, help="Path to logs/live_model_run_*.txt")
    parser.add_argument("--version", default="v2", help="Run/prompt version to write into the table")
    parser.add_argument("--jsonl-out", type=Path, help="Optional path to write prepared rows as JSONL")
    parser.add_argument("--forbidden-word", action="append", default=[], help="Additional forbidden word")
    parser.add_argument(
        "--health-json",
        type=Path,
        help="Optional output of `scripts/nmbot_health.py --json`; adds answer latency metadata to prompt-master rows",
    )
    args = parser.parse_args()

    forbidden_words = tuple(DEFAULT_FORBIDDEN_WORDS + tuple(args.forbidden_word))
    prompt_metrics = prompt_size_metrics()
    answer_latency_metrics = _latency_from_health(args.health_json)
    rows = parse_live_run(
        args.log,
        args.version,
        forbidden_words,
        prompt_metrics=prompt_metrics,
        answer_latency_metrics=answer_latency_metrics,
    )

    if args.jsonl_out:
        args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl_out.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")

    chat_chars = ((prompt_metrics.get("chat_v1") or {}).get("chars")) if prompt_metrics else None
    latency_recent = (answer_latency_metrics.get("recent") or {}) if answer_latency_metrics else {}
    latency_avg = latency_recent.get("avg_duration_sec")
    latency_text = f" latency_avg={latency_avg}s" if latency_avg is not None else ""
    print(f"VALIDATOR: rows={len(rows)} version={args.version} chat_v1_chars={chat_chars}{latency_text}")
    for row in rows:
        status = "WARN" if row.warnings else "OK"
        print(
            f"{status}: {row.case} facts={row.facts_count} visible={row.visible_count} "
            f"warnings={row.warnings or '-'} prompt_master={_prompt_master_console_preview(row.prompt_master_verdict)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
