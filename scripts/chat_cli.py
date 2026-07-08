#!/usr/bin/env python3
"""
CLI-клиент для тестового чат-бота Novostroy AI (nmbot).

Использует gateway-agent + OpenRouter + MCP novostroym.

Запуск:
    cd projects/nmbot
    source .venv/bin/activate
    export $(grep -v '^#' .env | xargs)

    python scripts/chat_cli.py "Найди однушку до 8 млн в Москве"
    python scripts/chat_cli.py --search-model google/gemini-3.1-flash-lite-preview "Найди однушку"
    python scripts/chat_cli.py --chat-model google/gemini-2.5-flash "Найди однушку"
    python scripts/chat_cli.py --no-mcp "Кто ты?"
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scene_classifier
from style_scenes import get_scene_rules
import text_style_tool

# ── Конфигурация ─────────────────────────────────────────────

OVERMIND_URL = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
OVERMIND_TOKEN = os.getenv("OVERMIND_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Experiment logging
ACTIVE_H_ID: str = os.getenv("NMBOT_H_ID", "H001")
LOGS_DIR: Path = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

import logging  # noqa: E402
_LOGGER = logging.getLogger("nmbot_cli")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _fh = logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _LOGGER.addHandler(_fh)


def _log_event(event: dict[str, Any]) -> None:
    """Пишет событие в logs/dialogs-YYYY-MM-DD.jsonl. Не падает при ошибке записи."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        event.setdefault("h_id", ACTIVE_H_ID)
        event.setdefault("source", "cli")
        path = LOGS_DIR / f"dialogs-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[LOG-ERROR] {e}", file=sys.stderr)


def _strip_markdown(text: str) -> str:
    """H006: снимает markdown-обёртку ```json ... ``` (или ``` ... ```) вокруг JSON-блока.
    Если обёртки нет — возвращает текст как есть. Только для записи в лог."""
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


def _extract_response_text(text: str) -> str:
    raw = _strip_markdown(text).strip()
    try:
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(raw[s:e])
            if isinstance(data, dict):
                response = data.get("response")
                if isinstance(response, (dict, list)):
                    return response
                if isinstance(response, str):
                    return response
    except json.JSONDecodeError:
        pass
    return raw


def _looks_structured_json(text: str) -> bool:
    raw = text.strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("items"), list)


def _try_parse_json(text: str) -> dict[str, Any] | list[Any] | None:
    raw = _strip_markdown(text).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _has_contract_response(text: Any) -> bool:
    if isinstance(text, dict):
        return isinstance(text.get("items"), list)
    if not isinstance(text, str):
        return False
    raw = _strip_markdown(text).strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    response = parsed.get("response")
    return isinstance(response, dict) and isinstance(response.get("items"), list)


def _truthy_fact(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"нет", "false", "0", "none", "null", "nan"}
    if isinstance(value, (list, tuple, set)):
        return any(_truthy_fact(item) for item in value)
    if isinstance(value, dict):
        return any(_truthy_fact(item) for item in value.values())
    return True


def _scenario_compact_bits(item: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    apartment_types = item.get("apartment_types")
    if _truthy_fact(apartment_types):
        bits.append("есть компактные форматы")
    ads = item.get("ads")
    if _truthy_fact(ads):
        bits.append("есть объявления для проверки входа")
    counter = item.get("counter_novos")
    if _truthy_fact(counter):
        bits.append("есть активность по объявлениям")
    egrn = item.get("egrn_top_novos") or item.get("egrn_sales")
    if _truthy_fact(egrn):
        bits.append("есть данные сделок ЕГРН")
    finance = item.get("mortgage_calc") or item.get("mortgage") or item.get("discount") or item.get("payment_by_installments")
    if _truthy_fact(finance):
        bits.append("есть финансовые условия в карточке")
    return bits


def _compact_reason(item: dict[str, Any]) -> str:
    for key in ("why_family", "why_close", "why_rental", "why_investment", "reason"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    bits: list[str] = []
    bits.extend(_scenario_compact_bits(item))
    if item.get("finishing"):
        bits.append(str(item["finishing"]))
    if item.get("ready") or item.get("delivered"):
        bits.append(str(item.get("ready") or item.get("delivered")))
    infra = item.get("family_infrastructure") or item.get("infrastructure")
    if isinstance(infra, dict):
        if infra.get("schools") or infra.get("school"):
            bits.append("есть школы")
        if infra.get("kindergartens") or infra.get("kindergarten"):
            bits.append("есть детские сады")
        if infra.get("parks") or infra.get("park_near"):
            bits.append("рядом парки")
        if infra.get("yard_without_cars"):
            bits.append("двор без машин")
    return ", ".join(bits[:4]) or "подходит по подтверждённым данным MCP"


def _coerce_chat_response_to_json(query: str, search_response: str, draft_response: Any) -> str:
    parsed = _try_parse_json(search_response)
    if not isinstance(parsed, dict):
        message = str(draft_response or "").strip() or "По этому запросу нужно уточнить детали."
        wrapped = {
            "response": {"message": message, "items": [], "question": "Какой главный ориентир для подбора?"},
            "params": {"purpose": "default"},
            "visible_options": [],
            "buttons": [],
        }
        return json.dumps(wrapped, ensure_ascii=False)

    facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
    near = parsed.get("near") if isinstance(parsed.get("near"), list) else []
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    request = parsed.get("mcp_request") if isinstance(parsed.get("mcp_request"), dict) else {}
    purpose = str(request.get("purpose") or params.get("purpose") or "search")
    source_items = [item for item in (facts + near) if isinstance(item, dict)][:3]

    items: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    for idx, item in enumerate(source_items):
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        out = {"name": name, "reason": _compact_reason(item)}
        for key in (
            "location",
            "price_range",
            "finishing",
            "ready",
            "metro",
            "why_family",
            "why_investment",
            "why_rental",
            "family_infrastructure",
            "infrastructure",
            "ads",
            "apartment_types",
            "counter_novos",
            "egrn_top_novos",
            "mortgage_calc",
            "mortgage",
            "discount",
        ):
            if item.get(key):
                out[key] = item[key]
        items.append(out)
        visible.append({"idx": idx, "name": name})

    if purpose == "repeat_search":
        message = "Посмотрела другие варианты, не повторяя прошлый список."
        question = "Какой вариант хотите рассмотреть подробнее?"
    elif len(items) == 1:
        message = "Нашла один подтверждённый вариант по вашему запросу."
        question = "Хотите рассмотреть этот вариант подробнее?"
    elif items:
        message = "Нашла несколько подтверждённых вариантов по вашему запросу."
        question = "Какой вариант хотите рассмотреть подробнее?"
    else:
        message = str(draft_response or "").strip() or "По этому запросу не нашла подтверждённых вариантов."
        question = "Могу расширить параметры поиска?"

    wrapped = {
        "response": {"message": message, "items": items, "question": question},
        "params": {**params, **({"purpose": purpose} if purpose else {})},
        "visible_options": visible,
        "buttons": [],
    }
    return json.dumps(wrapped, ensure_ascii=False)


def _merge_unique(values: Any, extra: list[str]) -> list[str]:
    result: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and item and item not in result:
                result.append(item)
    for item in extra:
        if item not in result:
            result.append(item)
    return result


def _has_mortgage_signal(text: str, params: dict[str, Any]) -> bool:
    mortgage_type = str(params.get("mortgage_type") or "").strip().lower()
    facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    if mortgage_type or any(str(item).strip().lower() == "mortgage" for item in facets):
        return True
    return any(
        token in text
        for token in (
            "ипотек",
            "it-ипот",
            "айти-ипот",
            "it ипот",
            "айти ипот",
            "льготн",
            "господдерж",
            "семейную ипот",
            "семейная ипот",
            "маткапитал",
            "материнск",
            "первонач",
            "первый взнос",
            "ставк",
            "рассроч",
            "скидк",
            "платеж",
            "платёж",
        )
    )


def _mortgage_type_from_text(text: str, params: dict[str, Any]) -> str | None:
    existing = str(params.get("mortgage_type") or "").strip().lower()
    if existing:
        return existing
    if any(token in text for token in ("it-ипот", "айти-ипот", "it ипот", "айти ипот")):
        return "it_mortgage"
    if "семей" in text and "ипот" in text:
        return "family_mortgage"
    if any(token in text for token in ("льготн", "господдерж")):
        return "subsidized_mortgage"
    return None


def _ensure_min_count(values: Any, minimum: int) -> int:
    try:
        current = int(values)
    except Exception:
        current = 0
    return max(current, minimum)


def _is_operator_request(text: str) -> bool:
    return any(
        token in text
        for token in (
            "заброни",
            "брон",
            "оператор",
            "менеджер",
            "связаться",
            "контакт",
            "показ",
            "просмотр",
            "оставить номер",
        )
    )


def _is_offtopic_request(text: str) -> bool:
    real_estate_tokens = (
        "жк",
        "квартир",
        "новостро",
        "дом",
        "студи",
        "однуш",
        "двуш",
        "трёш",
        "треш",
        "ипотек",
        "застрой",
        "район",
        "метро",
        "брон",
        "показ",
        "покуп",
        "сдач",
        "отделк",
    )
    offtopic_tokens = ("анекдот", "шутк", "погода", "рецепт", "курс валют", "стих")
    return any(token in text for token in offtopic_tokens) and not any(token in text for token in real_estate_tokens)


def _is_repeat_search_request(text: str) -> bool:
    return any(
        token in text
        for token in (
            "другие варианты",
            "другие",
            "новые варианты",
            "эти не подходят",
            "найди новые",
            "ещё варианты",
            "еще варианты",
        )
    )


def _is_explain_selection_request(text: str) -> bool:
    return any(
        token in text
        for token in (
            "почему",
            "по какому принципу",
            "чем лучше",
            "почему эти",
            "почему этот",
        )
    )


def _visible_option_names(request: dict[str, Any]) -> list[str]:
    return [
        str(item.get("name") or "").strip()
        for item in request.get("visible_options", [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def _build_mcp_request(query: str, initial_params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    request = dict(initial_params or {})
    low = query.lower()
    purpose = str(request.get("purpose") or "").strip().lower()

    if purpose == "operator" or _is_operator_request(low):
        request["purpose"] = "operator"
        request.pop("count", None)
    elif _is_repeat_search_request(low):
        request["purpose"] = "repeat_search"
        request["count"] = _ensure_min_count(request.get("count"), 3)
        visible_names = _visible_option_names(request)
        if visible_names:
            request["exclude"] = _merge_unique(request.get("exclude"), visible_names)
    elif purpose == "family" or any(token in low for token in ("для семьи", "с ребён", "с ребен", "семейн")):
        request["purpose"] = "family"
        request["count"] = _ensure_min_count(request.get("count"), 3)
        request["need"] = _merge_unique(
            request.get("need"),
            ["schools", "kindergartens", "parks", "yard_without_cars"],
        )
    elif purpose == "investment" or any(token in low for token in ("под инвестиц", "инвестиц", "ликвидност", "перепродаж")):
        request["purpose"] = "investment"
        request["count"] = _ensure_min_count(request.get("count"), 3)
        request["need"] = _merge_unique(
            request.get("need"),
            ["entry_price", "mortgage", "egrn_sales", "counter_novos", "compact_lots"],
        )
    elif purpose == "rental" or any(token in low for token in ("под сдачу", "для аренды", "аренд")):
        request["purpose"] = "rental"
        request["count"] = _ensure_min_count(request.get("count"), 3)
        request["need"] = _merge_unique(
            request.get("need"),
            ["compact", "finishing", "metro", "ready", "demand"],
        )
    else:
        request.setdefault("purpose", purpose or "search")
        if request.get("purpose") == "search":
            request["count"] = _ensure_min_count(request.get("count"), 3)

    if _has_mortgage_signal(low, request):
        request["facets"] = _merge_unique(request.get("facets"), ["mortgage"])
        mortgage_type = _mortgage_type_from_text(low, request)
        if mortgage_type:
            request["mortgage_type"] = mortgage_type
        request["need"] = _merge_unique(
            request.get("need"),
            [
                "mortgage_calc",
                "mortgage",
                "discount",
                "payment_by_installments",
                "price",
            ],
        )
        if request.get("purpose") in {"family", "investment", "rental", "search", "repeat_search"}:
            request["count"] = _ensure_min_count(request.get("count"), 3)

    return request, json.dumps(request, ensure_ascii=False)


def _chat_quality_guardrail(search_response: str, query: str = "") -> str:
    parsed = _try_parse_json(search_response)
    if not isinstance(parsed, dict):
        return ""
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    purpose = str(params.get("purpose") or "").strip().lower()
    if not purpose:
        low_query = (query or "").lower()
        if any(token in low_query for token in ("под сдачу", "для аренды", "аренд")):
            purpose = "rental"
        elif any(token in low_query for token in ("под инвестиц", "инвестиц", "перепродаж", "ликвидност")):
            purpose = "investment"
        elif any(token in low_query for token in ("для семьи", "семейн", "с ребён", "с ребен")):
            purpose = "family"
    if purpose == "family":
        return (
            "Качество-ограничение: не добивай shortlist слабым near без школ/садов/парка/двора без машин. "
            "Лучше 1-2 сильных семейных варианта, чем третий общий вариант ради количества."
        )
    if purpose == "rental":
        return (
            "Качество-ограничение: не используй слова про высокий спрос, востребованность, ликвидность или доход, "
            "если они не подтверждены отдельными сигналами ads / counter_novos / egrn_top_novos. "
            "Опирайся только на компактность, отделку, метро, готовность и район."
        )
    if purpose == "investment":
        return (
            "Качество-ограничение: не делай вывод 'высокая ликвидность' только из количества объявлений и не уходи в логику арендатора, если сценарий не rental. "
            "Не упоминай сдачу, аренду, продажу дороже, арендатора или покупателя под сдачу, если пользователь не просит rental-сценарий. "
            "Опирайся только на подтверждённые сигналы: price, compact_lots, egrn/counter, ready, metro, finishing, mortgage."
        )
    return ""


def _search_reason_bits(item: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    price = str(item.get("price_range") or item.get("price") or "").strip()
    area = str(item.get("area") or "").strip()
    metro = str(item.get("metro") or item.get("property_metro") or "").strip()
    finishing = str(item.get("finishing") or "").strip()
    ready = str(item.get("ready") or item.get("status") or "").strip()
    if price:
        bits.append(f"вход {price}")
    if area:
        bits.append(f"компактный формат {area}")
    if finishing:
        bits.append(f"{finishing}")
    if ready:
        bits.append(f"готовность {ready}")
    if metro:
        bits.append(f"метро {metro}")
    return bits


def _normalize_search_response_for_chat(search_response: str, query: str = "") -> str:
    parsed = _try_parse_json(search_response)
    if not isinstance(parsed, dict):
        return search_response
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    purpose = str(params.get("purpose") or "").strip().lower()
    if not purpose:
        low_query = (query or "").lower()
        if any(token in low_query for token in ("под сдачу", "для аренды", "аренд")):
            purpose = "rental"
        elif any(token in low_query for token in ("под инвестиц", "инвестиц", "перепродаж", "ликвидност")):
            purpose = "investment"
        elif any(token in low_query for token in ("для семьи", "семейн", "с ребён", "с ребен")):
            purpose = "family"
    if purpose not in {"rental", "investment"}:
        return search_response
    for section, key in (("facts", f"why_{purpose}"), ("near", f"why_{purpose}")):
        items = parsed.get(section) if isinstance(parsed.get(section), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            bits = _search_reason_bits(item)
            if purpose == "rental":
                # Никакого demand-language: только компактность/отделка/готовность/метро/цена.
                reason_bits = []
                if bits:
                    reason_bits.append("; ".join(bits[:3]))
                if item.get("area") or item.get("apartment_types"):
                    reason_bits.append("удобная база для аренды")
                item[key] = ". ".join(reason_bits).strip() or "удобная база для аренды"
            else:
                # Никакой ликвидности/спроса из объявлений: только вход/компактность/готовность/метро/отделка.
                reason_bits = []
                if bits:
                    reason_bits.append("; ".join(bits[:3]))
                if item.get("price_range") or item.get("area"):
                    reason_bits.append("понятный инвестиционный вход")
                item[key] = ". ".join(reason_bits).strip() or "понятный инвестиционный вход"
    return json.dumps(parsed, ensure_ascii=False)


def _followup_search_hint(query: str, request: dict[str, Any]) -> str:
    low = query.lower()
    visible_names = _visible_option_names(request)
    if not visible_names:
        return ""
    names = ", ".join(visible_names)
    if request.get("purpose") == "repeat_search" or _is_repeat_search_request(low):
        return (
            "Задача поиска: это repeat_search. Не повторяй visible_options; "
            f"исключи {names} и верни до 3 других вариантов в facts или near."
        )
    if _is_explain_selection_request(low):
        return (
            "Задача поиска: это explain_selection. Верни подтверждённые факты именно по visible_options "
            f"({names}) в facts/near, чтобы можно было объяснить выбор без выдумок."
        )
    return ""


async def _maybe_style_text(
    session: aiohttp.ClientSession,
    text: str,
    *,
    context: str,
    intent: str,
    scene: str,
    scene_rules: str = "",
) -> str:
    if not STYLE_TOOL_ENABLED or not text.strip():
        return text
    try:
        styled, _meta = await text_style_tool.rewrite_text(
            session,
            text=text,
            context=context,
            intent=intent,
            tone="live",
            scene=scene,
            scene_rules=scene_rules,
            model=STYLE_TOOL_MODEL,
        )
        styled = (styled or "").strip()
        return styled or text
    except Exception as e:
        print(f"[WARN] text style tool failed: {e}", file=sys.stderr)
        return text

SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
CHAT_MODEL = "google/gemini-2.5-flash"
STYLE_TOOL_ENABLED = os.getenv("NMBOT_TEXT_STYLE_TOOL", "1") != "0"
STYLE_TOOL_MODEL = os.getenv("NMBOT_STYLE_MODEL", "google/gemini-3.1-flash-lite-preview")

# Промпты читаются из prompts/*.txt (P002 — единый источник правды)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Читает промпт из prompts/{name}.txt. Бросает FileNotFoundError с подсказкой."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip()


SEARCH_SYSTEM_PROMPT = _load_prompt("search_v1.txt")
CHAT_SYSTEM_PROMPT = _load_prompt("chat_v1.txt")


def _load_prompt_path(relative_path: str) -> str:
    path = _PROMPTS_DIR / relative_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _prompt_slug(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch == "_")


def _chat_system_prompt_for_params(params: dict[str, Any] | None) -> str:
    params = params if isinstance(params, dict) else {}
    prompt = CHAT_SYSTEM_PROMPT
    scenario_block = "Сценарий не задан: используй общий search/default контекст и не добавляй сценарных выгод."
    purpose = _prompt_slug(params.get("purpose"))
    if purpose:
        overlay = _load_prompt_path(f"scenarios/{purpose}_v1.txt")
        if overlay:
            scenario_block = overlay
    facet_blocks: list[str] = []
    facets = params.get("facets") if isinstance(params.get("facets"), list) else []
    for facet in facets:
        slug = _prompt_slug(facet)
        overlay = _load_prompt_path(f"facets/{slug}_v1.txt") if slug else ""
        if overlay:
            facet_blocks.append(overlay)
    facet_block = "\n\n".join(facet_blocks) if facet_blocks else "Facet не задан: не добавляй ипотечные/скидочные/рассрочные claims без фактов."
    if "{{SCENARIO_OVERLAY}}" in prompt:
        prompt = prompt.replace("{{SCENARIO_OVERLAY}}", scenario_block)
    else:
        prompt = f"{prompt}\n\n## Сценарный модуль\n{scenario_block}"
    if "{{FACET_OVERLAYS}}" in prompt:
        prompt = prompt.replace("{{FACET_OVERLAYS}}", facet_block)
    else:
        prompt = f"{prompt}\n\n## Дополнительный facet-модуль\n{facet_block}"
    return prompt

# ── Overmind API ─────────────────────────────────────────────


async def create_task(
    session: aiohttp.ClientSession,
    request_data: dict,
    timeout_seconds: int = 300,
) -> dict:
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": request_data,
        "timeout_seconds": timeout_seconds,
        "max_retries": 0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OVERMIND_TOKEN}",
    }
    url = f"{OVERMIND_URL.rstrip('/')}/api/v1/tasks/api"
    async with session.post(url, json=payload, headers=headers) as resp:
        result = await resp.json()
        if resp.status not in (200, 201):
            print(f"[ERROR] Создание задачи: HTTP {resp.status}", file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return {}
        print(f"[OK] Задача создана: id={result.get('id')}", file=sys.stderr)
        return result


async def poll_task(session: aiohttp.ClientSession, task_id: int, timeout: int = 300) -> dict:
    headers = {"Authorization": f"Bearer {OVERMIND_TOKEN}"}
    base = OVERMIND_URL.rstrip("/")
    start = time.time()

    while time.time() - start < timeout:
        async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
            status_data = await resp.json()

        status = status_data.get("status")
        if status in ("completed", "failed", "cancelled"):
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                return await resp.json()

        elapsed = int(time.time() - start)
        print(f"[WAIT] Задача {task_id}: статус={status} ({elapsed}с)", file=sys.stderr)
        await asyncio.sleep(3)

    raise TimeoutError(f"Задача {task_id} не завершилась за {timeout}с")


def print_result(result: dict) -> None:
    status = result.get("status", "unknown")
    result_obj = result.get("result") or result

    if isinstance(result_obj, dict):
        response_text = result_obj.get("response", "")
        error = result_obj.get("error", "")
        metadata = result_obj.get("metadata", {})

        print()
        print("─" * 60)
        print(f"Статус: {status}")
        if error:
            print(f"Ошибка: {error}")
        if response_text:
            print()
            if isinstance(response_text, (dict, list)):
                print(json.dumps(response_text, ensure_ascii=False, indent=2))
            else:
                print(response_text)
        if metadata:
            print()
            print("Метаданные:")
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
        print("─" * 60)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


async def ask_overmind(
    session: aiohttp.ClientSession,
    query: str,
    model: str,
    system_prompt: str,
    use_mcp: bool,
    timeout: int,
    max_tokens: int = 5000,
) -> tuple[str, dict]:
    request_data = {
        "query": query,
        "service": "openrouter",
        "model": model,
        "system_prompt": system_prompt,
        "parameters": {
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        "external_api_key": OPENROUTER_API_KEY,
    }
    if use_mcp:
        request_data["mcp_servers"] = ["novostroym"]

    task = await create_task(session, request_data, timeout_seconds=timeout)
    task_id = task.get("id")
    if not task_id:
        return "", {}

    result = await poll_task(session, task_id, timeout=timeout)
    result_obj = result.get("result") or result
    if not isinstance(result_obj, dict):
        return json.dumps(result, ensure_ascii=False), result
    return result_obj.get("response", ""), result_obj


async def ask_two_stage(
    session: aiohttp.ClientSession,
    query: str,
    search_model: str,
    chat_model: str,
    use_mcp: bool,
    timeout: int,
    search_max_tokens: int = 5000,
    chat_max_tokens: int = 5000,
) -> tuple[str, str, dict, dict, int]:
    """Возвращает (search_text, chat_text, search_metadata, chat_metadata, chat_retries)."""
    search_response, search_meta = await ask_overmind(
        session=session,
        query=query,
        model=search_model,
        system_prompt=SEARCH_SYSTEM_PROMPT,
        use_mcp=use_mcp,
        timeout=timeout,
        max_tokens=search_max_tokens,
    )
    if not search_response:
        return "", "", search_meta, {}, 0

    search_response = _normalize_search_response_for_chat(search_response, query=query)
    parsed_search = _try_parse_json(search_response)
    search_params = parsed_search.get("params") if isinstance(parsed_search, dict) and isinstance(parsed_search.get("params"), dict) else {}
    guardrail = _chat_quality_guardrail(search_response, query=query)
    guardrail_block = f"\n\n{guardrail}" if guardrail else ""
    chat_query = (
        f"Запрос клиента: {query}\n\n"
        f"{guardrail_block}\n"
        f"Найденные факты, которыми можно пользоваться:\n{search_response}"
    )
    chat_response, chat_meta = await ask_overmind(
        session=session,
        query=chat_query,
        model=chat_model,
        system_prompt=_chat_system_prompt_for_params(search_params),
        use_mcp=False,
        timeout=timeout,
        max_tokens=chat_max_tokens,
    )
    # H007-A: strip markdown-обёртку ДО парсинга/печати/логирования.
    chat_response = _extract_response_text(chat_response)
    if not isinstance(chat_response, (dict, list)) and not _looks_structured_json(chat_response):
        scene_meta = await scene_classifier.classify_scene(
            session,
            user_text=query,
            search_response=search_response,
            memory={},
            draft_response=chat_response,
        )
        scene = str(scene_meta.get("scene") or "default_safe_reply")
        chat_response = await _maybe_style_text(
            session,
            chat_response,
            context=search_response,
            intent="chat_response",
            scene=scene,
            scene_rules=get_scene_rules(scene),
        )
    # H004: retry на пустой/мусорный chat-ответ (≤2 повтора)
    retries = 0
    while retries < 2 and (not chat_response or "{" not in chat_response):
        retries += 1
        _log_event({
            "kind": "user_message_retry",
            "stage": "chat",
            "attempt": retries,
            "raw_len": len(chat_response),
            "reason": "empty" if not chat_response else "no_json_brace",
        })
        chat_response, chat_meta = await ask_overmind(
            session=session,
            query=chat_query,
            model=chat_model,
            system_prompt=CHAT_SYSTEM_PROMPT,
            use_mcp=False,
            timeout=timeout,
            max_tokens=chat_max_tokens,
        )
        chat_response = _strip_markdown(chat_response)  # H007-A
    return search_response, chat_response, search_meta, chat_meta, retries


# ── Главная ──────────────────────────────────────────────────


async def main():
    # Проверка токенов
    if not OVERMIND_TOKEN:
        print("[ERROR] OVERMIND_TOKEN не задан", file=sys.stderr)
        print("  export OVERMIND_TOKEN='...'", file=sys.stderr)
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY не задан", file=sys.stderr)
        print("  export OPENROUTER_API_KEY='...'", file=sys.stderr)
        sys.exit(1)

    # Парсинг аргументов
    args = sys.argv[1:]
    search_model = SEARCH_MODEL
    chat_model = CHAT_MODEL
    use_mcp = True
    query_parts = []
    timeout = 300
    chat_max_tokens = 5000
    initial_params: dict = {}

    i = 0
    while i < len(args):
        if args[i] == "--search-model" and i + 1 < len(args):
            search_model = args[i + 1]
            i += 2
        elif args[i] == "--chat-model" and i + 1 < len(args):
            chat_model = args[i + 1]
            i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            search_model = args[i + 1]
            chat_model = args[i + 1]
            i += 2
        elif args[i] == "--no-mcp":
            use_mcp = False
            i += 1
        elif args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i] == "--chat-max-tokens" and i + 1 < len(args):
            chat_max_tokens = int(args[i + 1])
            i += 2
        elif args[i] == "--params" and i + 1 < len(args):
            import json as _json
            try:
                initial_params = _json.loads(args[i + 1])
            except _json.JSONDecodeError as _e:
                print(f"[ERROR] --params: невалидный JSON: {_e}", file=sys.stderr)
                sys.exit(1)
            i += 2
        elif args[i] == "--help" or args[i] == "-h":
            print(__doc__)
            sys.exit(0)
        else:
            query_parts.append(args[i])
            i += 1

    if not query_parts:
        print("[ERROR] Укажите запрос", file=sys.stderr)
        print("  python scripts/chat_cli.py 'Найди однушку в Москве'", file=sys.stderr)
        sys.exit(1)

    query = " ".join(query_parts)

    if _is_offtopic_request(query.lower()):
        response = {
            "response": {
                "message": "Я консультирую только по недвижимости.",
                "items": [],
                "question": "Могу помочь с подбором новостройки в Москве или Московской области?",
            },
            "params": {"purpose": "off_topic"},
            "visible_options": [],
            "buttons": [],
        }
        print(f"Поиск:        {search_model}", file=sys.stderr)
        print(f"Общение:      {chat_model}", file=sys.stderr)
        print("MCP поиск:    False", file=sys.stderr)
        print(f"chat_max_tok: {chat_max_tokens}", file=sys.stderr)
        print(f"Таймаут:      {timeout}с", file=sys.stderr)
        print(f"Запрос:       {query}", file=sys.stderr)
        print(file=sys.stderr)
        print("MCP-запрос:   {}", file=sys.stderr)
        print()
        print("─" * 60)
        print("Поисковые факты:")
        print(json.dumps({"facts": [], "near": [], "missing": [], "params": {"purpose": "off_topic"}}, ensure_ascii=False, indent=2))
        print()
        print("Ответ клиенту:")
        print(json.dumps(response, ensure_ascii=False))
        print()
        print("─" * 60)
        return

    # Вывод информации
    print(f"Поиск:        {search_model}", file=sys.stderr)
    print(f"Общение:      {chat_model}", file=sys.stderr)
    print(f"MCP поиск:    {use_mcp}", file=sys.stderr)
    print(f"chat_max_tok: {chat_max_tokens}", file=sys.stderr)
    print(f"Таймаут:      {timeout}с", file=sys.stderr)
    print(f"Запрос:       {query}", file=sys.stderr)
    if initial_params:
        print(f"Начальные params: {json.dumps(initial_params, ensure_ascii=False)}", file=sys.stderr)
    print(file=sys.stderr)

    # H008: подмешиваем initial_params и сценарные MCP-опоры в query
    mcp_request, mcp_request_text = _build_mcp_request(query, initial_params)
    if mcp_request:
        print(f"MCP-запрос:   {mcp_request_text}", file=sys.stderr)
        hint = _followup_search_hint(query, mcp_request)
        hint_block = f"\n\n{hint}" if hint else ""
        query = f"Текущие параметры: {mcp_request_text}{hint_block}\n\nКлиент: {query}"

    # Запуск
    t0 = time.monotonic()
    is_error = False
    error_msg = ""
    search_response = ""
    chat_response = ""
    search_meta: dict = {}
    chat_meta: dict = {}
    chat_retries: int = 0
    async with aiohttp.ClientSession() as session:
        try:
            search_response, chat_response, search_meta, chat_meta, chat_retries = await ask_two_stage(
                session=session,
                query=query,
                search_model=search_model,
                chat_model=chat_model,
                use_mcp=use_mcp,
                timeout=timeout,
                chat_max_tokens=chat_max_tokens,
            )
        except TimeoutError as e:
            is_error = True
            error_msg = str(e)
            print(f"[TIMEOUT] {e}", file=sys.stderr)

        duration_ms = int((time.monotonic() - t0) * 1000)

        # H007-B': Overmind отдаёт только tokens_used (одно число).
        # tokens_in/tokens_out/cost_usd недоступны — для биллинга использовать scripts/or_cost.py.
        def _extract(meta: dict) -> dict:
            if not isinstance(meta, dict):
                return {}
            md = meta["metadata"] if "metadata" in meta else meta
            if not isinstance(md, dict):
                return {}
            return {
                "tokens_used": md.get("tokens_used"),
            }

        search_cost = _extract(search_meta)
        chat_cost = _extract(chat_meta)
        total_tokens_used = sum(
            v for v in [search_cost.get("tokens_used"), chat_cost.get("tokens_used")] if isinstance(v, (int, float))
        )

        if isinstance(chat_response, (dict, list)):
            response_text_for_log = json.dumps(chat_response, ensure_ascii=False)
        else:
            response_text_for_log = _strip_markdown(chat_response)

        # Логируем в JSONL
        search_response_json = _try_parse_json(search_response)
        chat_response_json = _try_parse_json(chat_response) if isinstance(chat_response, str) else chat_response

        _log_event({
            "kind": "user_message",
            "user_text": query,
            "initial_params": initial_params or {},
            "effective_query": query,
            "mcp_request": mcp_request,
            "search_model": search_model,
            "chat_model": chat_model,
            "mcp": use_mcp,
            "search_response": search_response,
            "search_response_json": search_response_json,
            "search_response_len": len(search_response) if search_response else 0,
            "search_meta": search_meta,
            "response_text": response_text_for_log,
            "response_json": chat_response_json,
            "response_len": len(response_text_for_log) if response_text_for_log else 0,
            "chat_meta": chat_meta,
            "duration_ms": duration_ms,
            "chat_retries": chat_retries,
            "is_error": is_error,
            "error": error_msg,
            "cost": {
                "search": search_cost,
                "chat": chat_cost,
                "total_tokens_used": total_tokens_used or None,
            },
        })

        if is_error:
            sys.exit(1)

        print()
        print("─" * 60)
        print("Поисковые факты:")
        print(search_response)
        print()
        print("Ответ клиенту:")
        print(chat_response)
        print()
        # H007-B': печатаем tokens_used в stderr, cost_usd недоступен (см. or_cost.py)
        if total_tokens_used:
            print(f"📊 tokens_used={total_tokens_used} (cost: or_cost.py)", file=sys.stderr)
        print("─" * 60)


if __name__ == "__main__":
    asyncio.run(main())
