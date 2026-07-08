#!/usr/bin/env python3
"""nmbot_test_agent — активный тест-агент: гоняет сценарии и проверяет codex + H016 + golden.

Использование:
  python3 scripts/nmbot_test_agent.py                       # suite=all, отчёт human-readable
  python3 scripts/nmbot_test_agent.py --suite codex         # только codex
  python3 scripts/nmbot_test_agent.py --suite h016          # только H016 (диалоговая память)
  python3 scripts/nmbot_test_agent.py --suite golden        # только golden-эталоны
  python3 scripts/nmbot_test_agent.py --suite dialog        # контрольные живые диалоги перед отдачей пользователю
  python3 scripts/nmbot_test_agent.py --json                # JSON-режим
  python3 scripts/nmbot_test_agent.py --chat-max-tokens N   # дефолт 10000

Проверяемые правила:
  CODEX:
    - no_greetings:    нет «Уважаемый»/«Дорогой»/имени клиента
    - no_sorry_empty:  при пустом search нет «к сожалению, не нашлось» без альтернативы
    - no_links:        нет novostroy-m.ru в response
    - has_md:          response без markdown-обёртки
    - valid_json:      response парсится в JSON {response, params}
    - html_safe:       LLM не пишет сырой HTML/&-entity (H018: postprocessor _to_html() оборачивает сам)
    - single_emoji:    0-2 эмодзи на сообщение (H018: эмодзи — маркеры состояния, не декорация)
  H016 (диалоговая память):
    - select_option:   «второй» отвечает про 2-й вариант из last_options без нового search
    - sort_price_asc:  «подешевле с ремонтом» сортирует last_options и фильтрует «без отделки»
    - operator_funnel: пустой результат → мягкий «Хотите, предложу оставить номер...» (не «я уточню»)
  GOLDEN:
    - match_pattern:   ответ содержит ожидаемые маркеры (имя ЖК / цена / район)

Exit code: 0 если все pass, 1 если есть fail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
REVIEW_LOG = REPO / "logs" / "dialog_reviews.md"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


def _load_dotenv_if_present() -> None:
    """Локальный .env loader для тест-агента: значения не печатаем, только кладём в os.environ.

    H025: контрольный диалог должен запускаться одной командой, без ручного
    `set -a; source .env`. Иначе тест проверяет не бота, а забытый env.
    """
    import os

    env_path = REPO / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_if_present()

from chat_tester_bot import (  # noqa: E402
    OvermindClient,
    CHAT_SYSTEM_PROMPT,
    STAGE_PRESENTER_ENABLED,
    _button_log_preview,
    _callback_button_text,
    _build_known_option_prompt,
    _build_consultation_answer_prompt,
    _build_negation_response_prompt,
    _build_conversation_answer_prompt,
    _format_option_response,
    _format_options_summary_response,
    _format_numbered_list_spacing,
    _format_operator_handoff_for_context,
    _format_operator_handoff_for_option,
    _fix_complex_name_artifacts,
    _extract_conversation_followup_signals,
    _store_active_conversation_topic,
    _operator_reason_response,
    _continue_selection_response,
    _clarification_from_followup,
    _apply_dialog_plan_to_state,
    _dialog_planner_state_payload,
    _extract_options,
    _followup_state_payload,
    _followup_intent_from_dialog_action,
    _format_history_event,
    _history_search_preview,
    _normalize_conversation_topic,
    _local_followup_intent,
    _markup_from_chat_buttons,
    _prepare_response_text,
    _parse_budget_callback_value,
    _pick_quick_actions,
    _phone_captured_farewell,
    _normalize_phone,
    _extract_phone_from_text,
    _has_phone_capture_context,
    _looks_like_phone_text,
    _non_text_fallback_response,
    _non_text_message_type,
    _phone_needs_context_response,
    _phone_log_meta,
    _response_payload_to_text,
    _pure_option_choice_index,
    _reject_operator_response,
    _reject_selected_option_response,
    _render_stage_first_list,
    _render_stage_recommendation,
    _consultation_question_response,
    _consultation_answer_guidance,
    _selection_logic_response,
    _stage_option_fact_parts,
    _telegram_chunks,
    _normalize_followup_params_delta,
    _reset_dialog_state_preserve_settings,
    _resolve_dialog_intent,
    _remember_bot_response,
    _safe_user_error_message,
    _strip_rejected_options_from_response,
    _strip_unsupported_complex_claims,
    _strip_unrequested_live_data_cta,
    _soften_layout_overclaim,
    _soften_generic_selected_question,
    _operator_cta_for_selected_investment,
    _visible_options_from_chat_meta,
    _visible_options_from_chat_or_response,
    _visible_options_from_response,
)
from followup_intent_classifier import (  # noqa: E402
    DIALOG_STATE_PLANNER_PROMPT,
    normalize_dialog_action,
    normalize_dialog_mode,
    normalize_intent,
)


# ── Типы ─────────────────────────────────────────────────────

@dataclass
class Scenario:
    suite: str  # codex | h016 | golden
    name: str
    query: str
    initial_params: dict = field(default_factory=dict)
    checks: list[Callable[[dict], tuple[bool, str]]] = field(default_factory=list)
    # опционально: ожидаемые маркеры в response (для golden)
    expected_markers: list[str] = field(default_factory=list)
    forbidden_markers: list[str] = field(default_factory=list)
    # для H016 select_option: проверяем, что был dialog_intent, а не новый search
    expect_intent: str | None = None  # "select_option" | "sort_price_asc" | None
    # опционально: подсунуть фейковые last_options (для H016-сценариев без Overmind)
    inject_last_options: list[dict] = field(default_factory=list)


@dataclass
class Result:
    scenario: str
    suite: str
    passed: bool
    checks: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    response_text: str = ""
    dialog_intent: str | None = None
    system_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ControlDialogScenario:
    """Live H026 dialog gate scenario.

    Это не unit-test: сценарий ходит в Overmind/MCP и проверяет, что бот можно
    отдавать человеку, а не только что процесс живой.
    """
    name: str
    query: str
    expected_markers: list[str]
    expected_any_markers: list[str]


CONTROL_DIALOG_SCENARIOS: list[ControlDialogScenario] = [
    ControlDialogScenario(
        name="start_then_two_room_kotelniki_typo",
        query="двувшка в котельниках",
        expected_markers=["котельник"],
        expected_any_markers=["двухкомнат", "двуш", "2-комнат"],
    ),
    ControlDialogScenario(
        name="start_then_one_room_moscow_region",
        query="однушка в московской области",
        expected_markers=["област"],
        expected_any_markers=["однокомнат", "однуш", "1-комнат"],
    ),
    ControlDialogScenario(
        name="start_then_family_flat_with_finish",
        query="квартира для семьи с отделкой",
        expected_markers=[],
        expected_any_markers=["отделк", "ремонт", "готов"],
    ),
]


# ── Утилиты для проверок ────────────────────────────────────

def _json_from_text(text: str) -> dict:
    """Парсит JSON из ответа Ирины (с учётом markdown-обёртки)."""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.MULTILINE)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _extract_response_text(raw: str) -> str:
    """Достаёт поле response из JSON-ответа Ирины."""
    parsed = _json_from_text(raw)
    return parsed.get("response", raw)


def _safe_meta_preview(meta: Any) -> str:
    """Короткий безопасный preview meta для отчёта без секретов."""
    try:
        text = json.dumps(meta, ensure_ascii=False, default=str)
    except TypeError:
        text = str(meta)
    text = re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<redacted>", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text, flags=re.I)
    return text[:500]


def _parse_search_response(search_text: str) -> dict:
    parsed = _json_from_text(search_text)
    return parsed if isinstance(parsed, dict) else {}


def _has_stale_5m_budget(text: str) -> bool:
    """Ловим именно старый бюджет 5 млн, но не цены вроде 15.49 млн."""
    low = _normalise_ru_text(text)
    return bool(re.search(r"(?<![\d.,])5\s*(?:млн|миллион)", low))


def _system_observability_checks(
    *,
    query: str,
    response_text: str,
    new_params: Any,
    search_meta: Any,
    chat_meta: Any,
    expected_markers: list[str],
    expected_any_markers: list[str],
) -> tuple[list[dict], dict]:
    """Проверяет всю цепочку под капотом, а не только финальный ответ.

    Gate смотрит: параметры распознаны, MCP/search_response есть и парсится,
    в facts/near есть реальные варианты, search/chat meta не содержит traceback,
    а финальный ответ не противоречит ожиданиям сценария.
    """
    checks: list[dict] = []

    def add(name: str, ok: bool, msg: str = "") -> None:
        checks.append({"name": name, "passed": ok, "msg": msg})

    search_text = search_meta.get("_response_text", "") if isinstance(search_meta, dict) else ""
    parsed_search = _parse_search_response(search_text)
    facts = parsed_search.get("facts", []) if isinstance(parsed_search.get("facts", []), list) else []
    near = parsed_search.get("near", []) if isinstance(parsed_search.get("near", []), list) else []
    parsed_params = parsed_search.get("params", {}) if isinstance(parsed_search.get("params", {}), dict) else {}
    params = new_params if isinstance(new_params, dict) else {}
    combined_params = {**parsed_params, **params}
    search_low = _normalise_ru_text(search_text)
    response_low = _normalise_ru_text(response_text)
    meta_preview = _safe_meta_preview({"search_meta": search_meta, "chat_meta": chat_meta})

    add("sys_mcp_response_present", bool(search_text.strip()), "empty search_meta._response_text")
    add("sys_mcp_response_valid_json", bool(parsed_search), search_text[:180])
    add("sys_mcp_has_options", bool(facts or near), f"facts={len(facts)} near={len(near)}")
    add("sys_params_updated", bool(combined_params), f"params={combined_params}")

    if "дв" in query.lower():
        add("sys_params_rooms_two", combined_params.get("rooms") == "2", f"params={combined_params}")
    if "одн" in query.lower():
        add("sys_params_rooms_one", combined_params.get("rooms") == "1", f"params={combined_params}")
    if "отдел" in query.lower():
        has_finish_param = combined_params.get("has_renovation") is True or "отдел" in search_low
        add("sys_finish_intent_preserved", has_finish_param, f"params={combined_params}")

    bad_meta = [m for m in ("traceback", "exception", "choices") if m in meta_preview.lower()]
    add("sys_no_backend_error_markers", not bad_meta, f"meta markers={bad_meta}; meta={meta_preview}")

    if expected_any_markers:
        add(
            "sys_mcp_or_response_contains_expected_context",
            any(m in search_low or m in response_low for m in expected_any_markers),
            f"expected_any={expected_any_markers}",
        )

    meta = {
        "query": query,
        "params": params,
        "search_params": parsed_params,
        "facts_count": len(facts),
        "near_count": len(near),
        "search_response_chars": len(search_text),
        "search_meta_keys": sorted(search_meta.keys()) if isinstance(search_meta, dict) else [],
        "chat_meta_keys": sorted(chat_meta.keys()) if isinstance(chat_meta, dict) else [],
        "meta_preview": meta_preview,
    }
    return checks, meta


def _check_no_greetings(r: dict) -> tuple[bool, str]:
    txt = r.get("response_text", "").lower()
    for bad in ("уважаемый", "дорогой", "здравствуйте, уважаемый"):
        if bad in txt:
            return False, f"найдено обращение: «{bad}»"
    return True, ""


def _check_no_links(r: dict) -> tuple[bool, str]:
    txt = r.get("response_text", "")
    if "novostroy-m.ru" in txt:
        return False, "найдена ссылка novostroy-m.ru"
    return True, ""


def _check_no_md(r: dict) -> tuple[bool, str]:
    # Проверяем markdown ТОЛЬКО в чат-ответе (response_text), не в search-фазе
    txt = r.get("response_text", "")
    if txt.lstrip().startswith("```"):
        return False, "markdown-обёртка в чат-ответе"
    return True, ""


def _check_valid_json(r: dict) -> tuple[bool, str]:
    """OvermindClient.ask() возвращает уже распарсенный response_text (строка).
    Проверяем только что чат-ответ не пустой и не markdown."""
    txt = r.get("response_text", "")
    if not txt or len(txt.strip()) < 5:
        return False, f"пустой/слишком короткий response_text ({len(txt)} chars)"
    if txt.lstrip().startswith("```"):
        return False, "чат-ответ вернул markdown-обёртку"
    return True, ""


def _check_no_sorry_empty(r: dict) -> tuple[bool, str]:
    """При пустом search: «к сожалению, не нашлось» без альтернативы — fail."""
    txt = r.get("response_text", "").lower()
    search_empty = r.get("search_empty", False)
    if not search_empty:
        return True, ""
    # «сорян» допустим ТОЛЬКО если дальше идёт альтернатива (оператор / near / МО)
    if "к сожалению" in txt or "сожалею" in txt:
        # Если есть оператор/номер/near/мо — ок
        has_soft = any(
            w in txt
            for w in (
                "оператор",
                "номер",
                "передам",
                "московск",
                "подмосков",
                "близко",
                "рассмотр",
            )
        )
        if not has_soft:
            return False, "«к сожалению» без альтернативы (оператор/near/МО)"
    return True, ""


def _check_operator_funnel(r: dict) -> tuple[bool, str]:
    """Оператор мягко: «Хотите, предложу оставить номер...», НЕ «я уточню/передам оператору»."""
    txt = r.get("response_text", "").lower()
    if not any(w in txt for w in ("оператор", "номер", "связи")):
        return True, ""  # оператор вообще не упомянут — не наша проверка
    forbidden = [
        "я уточню у оператора",
        "я передам оператору",
        "оператор свяжется с вами",
    ]
    for bad in forbidden:
        if bad in txt:
            return False, f"запрещённое обещание: «{bad}»"
    return True, ""


def _check_markers(r: dict) -> tuple[bool, str]:
    """Golden: ответ содержит ожидаемые маркеры и НЕ содержит запрещённых."""
    txt = r.get("response_text", "").lower()
    expected = r.get("expected_markers", [])
    forbidden = r.get("forbidden_markers", [])
    missing = [m for m in expected if m.lower() not in txt]
    found_bad = [m for m in forbidden if m.lower() in txt]
    if missing:
        return False, f"не хватает маркеров: {missing}"
    if found_bad:
        return False, f"найдены запрещённые: {found_bad}"
    return True, ""


# H018: два новых codex-чека — html_safe (LLM не пишет сырой HTML) и single_emoji_per_msg (≤2 эмодзи).
_RAW_HTML_PATTERN = re.compile(r"[<>]|&(?!amp;|lt;|gt;|quot;|#)")
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # символы и пиктограммы
    "\U0001F600-\U0001F64F"  # эмотиконы
    "\U0001F680-\U0001F6FF"  # транспорт и карты
    "\U0001F700-\U0001F77F"  # алхимия
    "\U0001F780-\U0001F7FF"  # доп. геометрия
    "\U0001F800-\U0001F8FF"  # доп. стрелки
    "\U0001F900-\U0001F9FF"  # доп. символы и пиктограммы
    "\U0001FA00-\U0001FAFF"  # шахматы, символы
    "\U00002600-\U000027BF"  # разные символы и пиктограммы
    "\U0001F1E6-\U0001F1FF"  # региональные флаги
    "]+"
)


def _check_html_safe(r: dict) -> tuple[bool, str]:
    """H018: LLM не должна писать сырые HTML-теги или сырые &-entities в response.
    Постprocessor _to_html() в коде сам оборачивает <b> для имён ЖК и цен."""
    txt = r.get("response_text", "")
    if not txt:
        return True, ""
    m = _RAW_HTML_PATTERN.search(txt)
    if m:
        return False, f"сырой HTML/& в response: {m.group(0)!r} (должен обработать postprocessor)"
    return True, ""


def _check_single_emoji_per_msg(r: dict) -> tuple[bool, str]:
    """H018: 0-2 эмодзи-маркера на сообщение. Если 3+ — нарушение CODEX §1 (H018)."""
    txt = r.get("response_text", "")
    if not txt:
        return True, ""
    n = sum(len(m) for m in _EMOJI_PATTERN.findall(txt))
    if n > 2:
        return False, f"слишком много эмодзи: {n} (допускается 0-2 как маркеры состояния)"
    return True, ""



def _check_intent(r: dict) -> tuple[bool, str]:
    """H016: ожидаемый intent (select_option | sort_price_asc)."""
    expected = r.get("expect_intent")
    actual = r.get("dialog_intent")
    if expected and actual != expected:
        return False, f"intent={actual}, ожидался {expected}"
    if not expected and actual and actual != "new_search":
        # Если H016-резолвер сработал там, где не ожидался — тоже fail
        return False, f"неожиданный intent={actual}"
    return True, ""


# ── Сценарии ─────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    # ── CODEX (5 базовых) ──
    Scenario(
        suite="codex",
        name="no_greetings_baseline",
        query="Найди однушку до 8 млн в Москве",
        expected_markers=[],
        forbidden_markers=["уважаемый", "дорогой"],
    ),
    Scenario(
        suite="codex",
        name="no_links_baseline",
        query="Двухкомнатная квартира с отделкой в Москве в пределах МКАД",
        forbidden_markers=["novostroy-m.ru"],
        # search_meta с markdown допустим (это служебный JSON), проверяем только чат-ответ
    ),
    Scenario(
        suite="codex",
        name="valid_json_baseline",
        query="Квартира в Котельниках",
    ),
    Scenario(
        suite="codex",
        name="operator_funnel_soft",
        query="однушка до 3 млн в Москве",  # узкий пустой
        forbidden_markers=["я уточню у оператора", "я передам оператору"],
    ),
    Scenario(
        suite="codex",
        name="non_realty_redirect",
        query="расскажи анекдот",
        expected_markers=["недвижимости"],
    ),

    # ── H016 (4 сценария) ──
    Scenario(
        suite="h016",
        name="setup_options",
        query="двушка с отделкой в МО",
        # Это setup-сценарий: готовит last_options. Сам по себе не проверяет intent.
    ),
    Scenario(
        suite="h016",
        name="select_option_second",
        query="второй",
        initial_params={"rooms": "2", "has_renovation": True, "district": "mo"},
        expect_intent="followup_classifier",
        # Пропускаем вызов Overmind: подсунем фейковые last_options
        inject_last_options=[
            {"idx": 1, "name": "Дом на Микояна, 54", "location": "Сходня",
             "price": "от 2 575 270 руб.", "price_min": 2575270, "finishing": ""},
            {"idx": 2, "name": "ЖК «Шахматово-парк»", "location": "МО",
             "price": "от 3 009 000 руб.", "price_min": 3009000, "finishing": "с отделкой"},
            {"idx": 3, "name": "ЖК «Ривер парк»", "location": "МО",
             "price": "от 3 817 800 руб.", "price_min": 3817800, "finishing": "без отделки"},
        ],
    ),
    Scenario(
        suite="h016",
        name="sort_price_cheaper_with_renov",
        query="подешевле с ремонтом",
        initial_params={"rooms": "2", "has_renovation": True, "district": "mo"},
        expect_intent="followup_classifier",
        inject_last_options=[
            {"idx": 1, "name": "Дом на Микояна, 54", "location": "Сходня",
             "price": "от 2 575 270 руб.", "price_min": 2575270, "finishing": ""},
            {"idx": 2, "name": "ЖК «Шахматово-парк»", "location": "МО",
             "price": "от 3 009 000 руб.", "price_min": 3009000, "finishing": "с отделкой"},
            {"idx": 3, "name": "ЖК «Ривер парк»", "location": "МО",
             "price": "от 3 817 800 руб.", "price_min": 3817800, "finishing": "без отделки"},
        ],
    ),
    Scenario(
        suite="h016",
        name="new_search_fallback",
        query="трёшка в Хамовниках",  # новая тема → new_search
        initial_params={"rooms": "2", "district": "mo"},
        expect_intent="new_search",
    ),

    # ── GOLDEN (3 эталона) ──
    Scenario(
        suite="golden",
        name="golden_kotel_renov",
        query="Двухкомнатная квартира с отделкой в Котельниках",
        # H019: chat-фаза озвучивает полную цену из facts[] в рублях,
        # а не округляет до «X млн». Маркер «млн» заменён на «руб».
        expected_markers=["котельник"],
    ),
    Scenario(
        suite="golden",
        name="golden_msk_budget",
        query="Студия в Москве до 5 млн",
        # Ирина возвращает near-match с ЖК + ценой (H009 codex, narrow-но-есть-near)
        expected_markers=["млн"],
    ),
    Scenario(
        suite="golden",
        name="golden_spb_redirect",
        query="Квартира в Санкт-Петербурге",
        expected_markers=["москв", "московск"],  # должен предложить МО/Мск
    ),
]


# ── Прогон ───────────────────────────────────────────────────

async def _run_scenario(client: OvermindClient, sc: Scenario, chat_max_tokens: int) -> Result:
    t0 = asyncio.get_event_loop().time()
    result = Result(scenario=sc.name, suite=sc.suite, passed=True)
    try:
        # Воссоздаём state (как делает бот)
        from chat_tester_bot import _default_state, _resolve_dialog_intent
        state = _default_state()
        state["params"] = dict(sc.initial_params)
        if sc.inject_last_options:
            state["last_options"] = list(sc.inject_last_options)

        # Проверяем intent через H016-резолвер
        intent = _resolve_dialog_intent(sc.query, state)
        result.dialog_intent = intent.get("intent", "new_search")

        if intent["intent"] == "select_option":
            # H016: отвечаем из памяти, без Overmind
            from chat_tester_bot import _format_option_response
            response_text = _format_option_response(intent["option"])
            search_text = ""
            search_empty = False
        elif intent["intent"] == "sort_price_asc":
            from chat_tester_bot import _format_cheaper_response
            response_text = _format_cheaper_response(intent.get("options", []))
            search_text = ""
            search_empty = False
        else:
            # Обычный путь — спрашиваем Overmind
            # ask() возвращает (response_text, params, search_meta, chat_meta)
            # response_text — уже распарсенный чат-ответ (строка)
            # search_meta["_response_text"] — сырой JSON от search-фазы
            response_text, new_params, search_meta, chat_meta = await client.ask(
                query=sc.query,
                search_model="google/gemini-3.1-flash-lite-preview",
                chat_model="google/gemini-2.5-flash",
                use_mcp=True,
                params=sc.initial_params,
            )
            search_text = search_meta.get("_response_text", "")
            # search_empty: в чат-ответе Ирина говорит «не нашлось / к сожалению»
            txt = response_text.lower()
            search_empty = any(
                w in txt
                for w in ("к сожалению", "не нашлось", "не нашла", "вариантов не")
            )

        result.raw_response = response_text
        result.search_text = search_text
        result.response_text = response_text

        # Применяем проверки
        r_ctx = {
            "response_text": result.response_text,
            "raw_response": result.raw_response,
            "search_text": search_text,
            "search_empty": search_empty,
            "expected_markers": sc.expected_markers,
            "forbidden_markers": sc.forbidden_markers,
            "expect_intent": sc.expect_intent,
            "dialog_intent": result.dialog_intent,
        }
        for check in _check_fns_for(sc.suite):
            ok, msg = check(r_ctx)
            result.checks.append({"name": check.__name__, "passed": ok, "msg": msg})
            if not ok:
                result.passed = False

    except Exception as e:
        result.passed = False
        result.error = f"{type(e).__name__}: {e}"
        result.checks.append({"name": "exception", "passed": False, "msg": result.error})
    finally:
        result.duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
    return result


def _check_fns_for(suite: str) -> list[Callable]:
    """Какие проверки применять к сценарию в зависимости от suite."""
    base = [_check_no_greetings, _check_no_links, _check_no_md, _check_valid_json,
            _check_html_safe, _check_single_emoji_per_msg]
    if suite == "codex":
        return base + [_check_no_sorry_empty, _check_operator_funnel, _check_intent]
    if suite == "h016":
        return [_check_intent, _check_no_greetings, _check_valid_json, _check_html_safe, _check_single_emoji_per_msg]
    if suite == "golden":
        return [_check_markers, _check_no_greetings, _check_no_links, _check_valid_json, _check_html_safe]
    return base


def _normalise_ru_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("ё", "е").lower()).strip()


def _extract_quoted_names(text: str) -> list[str]:
    return [m.strip() for m in re.findall(r"«([^»\n]{2,80})»", text)]


def _ux_check_response(response_text: str, search_text: str) -> list[dict]:
    """H026: строгий UX-чеклист для контрольного диалога.

    Важно: это deterministic gate, не LLM-judge. Проверяем, что ответ
    не выходит за MCP/search_response по именам ЖК и не содержит явных
    выдуманных классов фактов, если таких полей нет в search_text.
    """
    checks: list[dict] = []
    txt = response_text.strip()
    low = _normalise_ru_text(txt)
    search_low = _normalise_ru_text(search_text)

    def add(name: str, ok: bool, msg: str = "") -> None:
        checks.append({"name": name, "passed": ok, "msg": msg})

    quoted = _extract_quoted_names(txt)
    missing_quotes = [name for name in quoted if _normalise_ru_text(name) not in search_low]
    add(
        "ux_mcp_grounded_quoted_complexes",
        bool(search_text.strip()) and not missing_quotes,
        "" if not missing_quotes else f"quoted names absent from MCP/search_response: {missing_quotes}",
    )

    sensitive_fact_markers = {
        # Не используем короткое "м.": оно даёт ложные срабатывания на площади/сокращения.
        "metro": ["метро"],
        "area": ["м²", "кв. м", "квадрат"],
        "developer": ["застройщик", "девелопер"],
        "school_infra": ["школ", "садик", "детск", "парк", "паркинг"],
        "mortgage": ["ипотек", "рассроч"],
    }
    leaked_fact_types: list[str] = []
    for fact_type, markers in sensitive_fact_markers.items():
        in_response = any(m in low for m in markers)
        in_search = any(m in search_low for m in markers)
        if in_response and not in_search:
            leaked_fact_types.append(fact_type)
    add(
        "ux_no_hallucinated_sensitive_facts",
        not leaked_fact_types,
        "" if not leaked_fact_types else f"fact types absent from MCP/search_response: {leaked_fact_types}",
    )

    robotic = ["добрый день", "с удовольствием", "по вашему запросу", "к сожалению"]
    found_robotic = [x for x in robotic if x in low]
    add("ux_natural_human_tone", not found_robotic, "" if not found_robotic else f"robotic/canned phrases: {found_robotic}")

    sales_markers = ["с отделкой", "без отделки", "сдан", "сдача", "готов", "цена", "от ", "стоимость"]
    add(
        "ux_sales_presentation_from_facts",
        any(m in low for m in sales_markers),
        "нет пользы/преимущества из фактов MCP: цена/отделка/статус/готовность",
    )

    question_count = txt.count("?")
    last_question = txt.rsplit("?", 1)[0].rsplit("\n", 1)[-1].lower() if question_count else ""
    multi_axis_question = (
        "бюджет" in last_question
        and any(marker in last_question for marker in ("отделк", "комнат", "срок", "район", "жк"))
    )
    add(
        "ux_one_right_question",
        question_count == 1 and txt.rstrip().endswith("?") and not multi_axis_question,
        f"question_count={question_count}; multi_axis_question={multi_axis_question}; must ask exactly one next-step question",
    )

    looks_multi_option = low.count("жк") >= 2 or len(quoted) >= 2
    has_numbered_lines = bool(re.search(r"(?:^|\n)1\.\s+", txt)) and bool(re.search(r"(?:^|\n)2\.\s+", txt))
    numbered_items = re.findall(r"(?:^|\n)(\d+)\.\s+", txt)
    add(
        "ux_readable_structure_for_multiple_options",
        (not looks_multi_option) or has_numbered_lines,
        "2+ варианта должны быть списком 1./2. с переносами строк",
    )
    add(
        "ux_no_overloaded_first_list",
        len(numbered_items) <= 3,
        f"первый Telegram-ответ перегружен: numbered_items={len(numbered_items)}; максимум 3 варианта",
    )

    tech_markers = ["choices", "openrouter", "traceback", "exception", "```", "{", "}"]
    found_tech = [m for m in tech_markers if m in low]
    add("ux_no_technical_leak", not found_tech, "" if not found_tech else f"technical markers: {found_tech}")

    add("ux_no_stale_5m_budget", not _has_stale_5m_budget(txt), "старый бюджет 5 млн протёк в новый диалог")

    operator_words = ["оператор", "номер", "связи"]
    has_operator = any(w in low for w in operator_words)
    has_real_options = "жк" in low or quoted
    add(
        "ux_cta_timing",
        not (has_operator and has_real_options and question_count > 1),
        "оператор не должен перебивать выбор, когда есть реальные варианты",
    )

    return checks


def _append_dialog_review(scenario: str, result: Result, search_text: str) -> None:
    """H026: каждый контрольный диалог пишем в журнал, включая ответ и MCP evidence."""
    REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    passed = sum(1 for c in result.checks if c.get("passed"))
    total = len(result.checks)
    status = "PASS" if result.passed else "FAIL"
    failed = [c for c in result.checks if not c.get("passed")]
    fail_text = "нет" if not failed else "; ".join(f"{c['name']}: {c.get('msg','')}" for c in failed)
    system_meta = json.dumps(result.system_meta, ensure_ascii=False, indent=2, default=str) if result.system_meta else "{}"
    entry = f"""
## {ts} — {scenario} — {status} ({passed}/{total})

### Что получилось

- Ответ получен за {result.duration_ms} ms.
- Технических ошибок в Telegram-ответе: {'нет' if all(c.get('passed') for c in result.checks if c.get('name') in ('no_technical_error_leak', 'ux_no_technical_leak')) else 'есть'}.

### Что плохо / замечания

- {fail_text}

### Ответ Ирины

```text
{result.response_text}
```

### Что было под капотом

```json
{system_meta}
```

### MCP/search_response evidence

```json
{search_text.strip()}
```

---
"""
    with REVIEW_LOG.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── Отчёт ────────────────────────────────────────────────────

def _print_human(results: list[Result]) -> None:
    print("\n" + "=" * 70)
    print("🧪 nmbot_test_agent — отчёт")
    print("=" * 70)

    by_suite: dict[str, list[Result]] = {}
    for r in results:
        by_suite.setdefault(r.suite, []).append(r)

    total_pass = sum(1 for r in results if r.passed)
    total_fail = len(results) - total_pass

    for suite, items in by_suite.items():
        sp = sum(1 for x in items if x.passed)
        sf = len(items) - sp
        icon = "✅" if sf == 0 else "❌"
        print(f"\n{icon} {suite.upper()}: {sp}/{len(items)} pass")
        for r in items:
            mark = "  ✓" if r.passed else "  ✗"
            print(f"{mark} {r.scenario} ({r.duration_ms}ms){' [' + r.dialog_intent + ']' if r.dialog_intent else ''}")
            if not r.passed:
                for c in r.checks:
                    if not c["passed"]:
                        print(f"      → {c['name']}: {c['msg']}")
                if r.error:
                    print(f"      → exception: {r.error}")

    print("\n" + "-" * 70)
    print(f"ИТОГО: {total_pass}/{len(results)} pass, {total_fail} fail")
    print("=" * 70)


def _print_json(results: list[Result]) -> None:
    out = {
        "summary": {
            "total": len(results),
            "pass": sum(1 for r in results if r.passed),
            "fail": sum(1 for r in results if not r.passed),
        },
        "results": [
            {
                "suite": r.suite,
                "scenario": r.scenario,
                "passed": r.passed,
                "duration_ms": r.duration_ms,
                "dialog_intent": r.dialog_intent,
                "response_text": r.response_text[:200] + ("..." if len(r.response_text) > 200 else ""),
                "checks": r.checks,
                "error": r.error,
                "system_meta": r.system_meta,
            }
            for r in results
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ── main ─────────────────────────────────────────────────────

async def _main(suite: str, json_mode: bool, chat_max_tokens: int) -> int:
    if not json_mode:
        print(f"🔍 Прогоняю сценарии (suite={suite}, max_tokens={chat_max_tokens})…")
    client = OvermindClient()
    results: list[Result] = []

    # H021: unit-тесты для _pick_quick_actions — прямой вызов (без Overmind).
    # Гарантирует что кнопки бюджета опираются на min(price_min), а не на хардкод.
    if suite in ("all", "h021", "h023", "h024", "h026", "h028", "h029", "ux_e2e"):
        if not json_mode:
            print("  (H021/H023/H024/H026/H028/H029/UX_E2E unit-тесты — без Overmind)…")
        for r in _run_h021_unit_tests():
            if suite in ("h021", "h023", "h024", "h026", "h028", "h029") and r.suite != suite:
                continue
            if suite == "ux_e2e" and r.suite != "ux_e2e":
                continue
            results.append(r)
            if not json_mode:
                mark = "✓" if r.passed else "✗"
                print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")

    if suite in ("all", "non_text"):
        if not json_mode:
            print("  (NON_TEXT unit-тесты — без Overmind)…")
        for r in _run_non_text_unit_tests():
            results.append(r)
            if not json_mode:
                mark = "✓" if r.passed else "✗"
                print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")

    if suite == "deploy":
        r = _run_deploy_smoke_test()
        results.append(r)
        if not json_mode:
            mark = "✓" if r.passed else "✗"
            print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")
        _print_json(results) if json_mode else _print_human(results)
        return 0 if r.passed else 1

    if suite == "dialog":
        if not client.session:
            await client.ensure_session()
        try:
            dialog_results = await _run_control_dialog_suite(client, chat_max_tokens)
            results.extend(dialog_results)
            if not json_mode:
                for r in dialog_results:
                    mark = "✓" if r.passed else "✗"
                    print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")
        finally:
            await client.close()
        _print_json(results) if json_mode else _print_human(results)
        return 0 if all(r.passed for r in results) else 1

    try:
        if not client.session:
            await client.ensure_session()
        for sc in SCENARIOS:
            if suite != "all" and sc.suite != suite:
                continue
            r = await _run_scenario(client, sc, chat_max_tokens)
            results.append(r)
            if not json_mode:
                mark = "✓" if r.passed else "✗"
                print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")
        if suite == "all":
            deploy_result = _run_required_deploy_gate()
            results.append(deploy_result)
            if not json_mode:
                mark = "✓" if deploy_result.passed else "✗"
                print(f"  {mark} {deploy_result.suite}/{deploy_result.scenario} ({deploy_result.duration_ms}ms)")
            dialog_results = await _run_control_dialog_suite(client, chat_max_tokens)
            results.extend(dialog_results)
            if not json_mode:
                for r in dialog_results:
                    mark = "✓" if r.passed else "✗"
                    print(f"  {mark} {r.suite}/{r.scenario} ({r.duration_ms}ms)")
    finally:
        await client.close()

    if json_mode:
        _print_json(results)
    else:
        _print_human(results)
    return 0 if all(r.passed for r in results) else 1


# H021: unit-тест для _pick_quick_actions. Прямой вызов (без Overmind), проверка callback_data.
# Гарантирует, что кнопки бюджета генерируются из min(price_min) в last_options, не хардкод.
def _run_h021_unit_tests() -> list[Result]:
    """Юнит-тесты H021: _pick_quick_actions возвращает корректные budget-кнопки."""
    results: list[Result] = []
    started = time.time()

    def add_result(suite_name: str, scenario: str, passed: bool, response_text: str = "", error: str = "") -> None:
        results.append(Result(
            suite=suite_name,
            scenario=scenario,
            passed=passed,
            error="" if passed else error,
            response_text=response_text,
            duration_ms=int((time.time() - started) * 1000),
        ))

    # Тест 1: A-found-some с min(price_min)=7.4 млн → кнопки [8, 10, 12], НЕ [5, ...]
    state_a = {
        "params": {"rooms": "2", "district": "mo"},
        "last_options": [
            {"idx": 1, "name": "ЖК «Мечта»", "price_min": 7_500_000, "finishing": "без отделки"},
            {"idx": 2, "name": "ЖК «Левел Лесной»", "price_min": 7_400_000, "finishing": "с отделкой"},
        ],
        "asked_questions": [],
    }
    rows = _pick_quick_actions(state_a, "A-found-some")
    budget_callbacks = [
        btn["callback_data"]
        for row in rows
        for btn in row
        if btn.get("callback_data", "").startswith("budget:")
    ]
    expected_a = ["budget:8m", "budget:10m", "budget:12m"]
    pass_a = budget_callbacks == expected_a
    results.append(Result(
        suite="h021",
        scenario="budget_buttons_from_min_price_a_found",
        passed=pass_a,
        error="" if pass_a else f"кнопки {budget_callbacks} != ожидаемых {expected_a}",
        response_text=f"callbacks={budget_callbacks}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # Тест 2: G-first-step больше не отдаёт простыню кнопок — только формат квартиры.
    state_g = {
        "params": {},
        "last_options": [
            {"idx": 1, "name": "ЖК «Эконом»", "price_min": 3_500_000, "finishing": ""},
        ],
        "asked_questions": [],
    }
    rows_g = _pick_quick_actions(state_g, "G-first-step")
    callbacks_g = [
        btn["callback_data"]
        for row in rows_g
        for btn in row
    ]
    expected_g = ["rooms:s", "rooms:1", "rooms:2", "rooms:3"]
    pass_g = callbacks_g == expected_g
    results.append(Result(
        suite="h021",
        scenario="g_first_step_buttons_are_short_format_choice",
        passed=pass_g,
        error="" if pass_g else f"кнопки {callbacks_g} != ожидаемых {expected_g}",
        response_text=f"callbacks={callbacks_g}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # Тест 3: A-found-some с пустым last_options → fallback [5, 8, 12]
    state_empty = {
        "params": {"rooms": "2"},
        "last_options": [],
        "asked_questions": [],
    }
    rows_empty = _pick_quick_actions(state_empty, "A-found-some")
    budget_empty = [
        btn["callback_data"]
        for row in rows_empty
        for btn in row
        if btn.get("callback_data", "").startswith("budget:")
    ]
    expected_empty = ["budget:5m", "budget:8m", "budget:12m"]
    pass_empty = budget_empty == expected_empty
    results.append(Result(
        suite="h021",
        scenario="budget_buttons_fallback_when_empty",
        passed=pass_empty,
        error="" if pass_empty else f"fallback {budget_empty} != {expected_empty}",
        response_text=f"callbacks={budget_empty}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H023 test 1: /start должен сбросить params/last_options/asked_questions,
    # но сохранить выбранные модели и MCP.
    stale_state = {
        "search_model": "custom-search",
        "chat_model": "custom-chat",
        "mcp": False,
        "params": {"rooms": "2", "district": "mo", "max_price": 5_000_000},
        "last_result": {"found": True},
        "last_options": [{"name": "ЖК «Новый Зеленоград»", "price_min": 5_600_000}],
        "asked_questions": ["budget"],
    }
    reset_state = _reset_dialog_state_preserve_settings(stale_state)
    pass_reset = (
        reset_state["params"] == {}
        and reset_state["last_options"] == []
        and reset_state["asked_questions"] == []
        and reset_state["search_model"] == "custom-search"
        and reset_state["chat_model"] == "custom-chat"
        and reset_state["mcp"] is False
    )
    results.append(Result(
        suite="h023",
        scenario="start_resets_stale_dialog_params",
        passed=pass_reset,
        error="" if pass_reset else f"/start reset failed: {reset_state}",
        response_text=f"reset_state={reset_state}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H023 test 2: callback parser должен понимать новые H021-кнопки budget:10m/15m/20m.
    parsed = {
        "5m": _parse_budget_callback_value("5m"),
        "10m": _parse_budget_callback_value("10m"),
        "15m": _parse_budget_callback_value("15m"),
        "20m": _parse_budget_callback_value("20m"),
        "none": _parse_budget_callback_value("none"),
    }
    expected_parsed = {"5m": 5_000_000, "10m": 10_000_000, "15m": 15_000_000, "20m": 20_000_000, "none": None}
    pass_parse = parsed == expected_parsed
    results.append(Result(
        suite="h023",
        scenario="budget_callback_parser_supports_dynamic_mln",
        passed=pass_parse,
        error="" if pass_parse else f"parsed {parsed} != {expected_parsed}",
        response_text=f"parsed={parsed}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H024 test: upstream diagnostics should be visible enough for live debugging,
    # but without raw traceback/choices/JSON/secrets.
    technical_error = 'Ошибка сервиса openrouter: 403 - { "success": false, "error": "Access denied by security policy." } choices traceback'
    safe_text = _safe_user_error_message(technical_error)
    required = ["OpenRouter", "403", "Access denied by security policy"]
    forbidden = ["choices", "traceback", "exception", "{", "}", "ошибка при обращении"]
    safe_lower = safe_text.lower()
    leaked = [x for x in forbidden if x in safe_lower]
    missing = [x for x in required if x not in safe_text]
    pass_safe = bool(safe_text.strip()) and not leaked and not missing
    results.append(Result(
        suite="h024",
        scenario="safe_upstream_error_message_shows_sanitized_provider_error",
        passed=pass_safe,
        error="" if pass_safe else f"missing={missing}; leaked={leaked}; text={safe_text!r}",
        response_text=safe_text,
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H027: Telegram handler must send the normal short final answer.
    # Regression caught: handle_message edited/sent only >4000 chars, so users saw
    # a permanent "🔎 Осуществляю поиск..." for ordinary replies.
    bot_source = (REPO / "scripts" / "chat_tester_bot.py").read_text(encoding="utf-8")
    has_short_answer_branch = (
        "else:\n            await indicator.edit_text(_to_html(response), parse_mode=\"HTML\", reply_markup=markup)"
        in bot_source
    )
    results.append(Result(
        suite="h026",
        scenario="telegram_handler_sends_short_final_answer",
        passed=has_short_answer_branch,
        error="" if has_short_answer_branch else "handle_message has no short-response indicator.edit_text branch",
        response_text="short final answer branch present" if has_short_answer_branch else "missing short final answer branch",
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H028/H033: обычный клиентский UX больше не показывает inline-кнопки.
    opt = {"idx": 1, "name": "ЖК «Новые Котельники»", "price": "5.75-6.98 млн", "price_min": 5_750_000}
    state_buttons = {"last_options": [opt], "last_result": {"found": True}, "selected_option": None}
    rows = _markup_from_chat_buttons({"_buttons": [
        {"text": "Да, подробнее", "action": "details", "value": {"option_index": 1}},
        {"text": "MCP JSON", "action": "details", "value": {"option_index": 1}},
        {"text": "📞 Оператор", "action": "operator"},
    ]}, state_buttons, "Хотите узнать подробнее про этот ЖК?", "selected-option")
    expected_rows: list[list[dict[str, str]]] = []
    pass_buttons = rows == expected_rows
    results.append(Result(
        suite="h028",
        scenario="normal_dialog_does_not_show_inline_buttons",
        passed=pass_buttons,
        error="" if pass_buttons else f"rows {rows} != {expected_rows}",
        response_text=f"rows={rows}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    state_selected = {"selected_option": opt, "last_options": [opt]}
    intent_yes = _resolve_dialog_intent("да", state_selected).get("intent")
    intent_details = _resolve_dialog_intent("подробнее", state_selected).get("intent")
    intent_booking = _resolve_dialog_intent("можно забронировать?", state_selected).get("intent")
    pass_memory = intent_yes == "followup_classifier" and intent_details == "followup_classifier" and intent_booking == "followup_classifier"
    results.append(Result(
        suite="h028",
        scenario="selected_option_yes_and_booking_do_not_restart_questionnaire",
        passed=pass_memory,
        error="" if pass_memory else f"intent_yes={intent_yes}; intent_details={intent_details}; intent_booking={intent_booking}",
        response_text=f"intent_yes={intent_yes}; intent_details={intent_details}; intent_booking={intent_booking}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    raw_options = [
        {"idx": 1, "name": "Амурский парк", "location": "msk", "price": "от 20.0 млн"},
        {"idx": 2, "name": "Южные Сады", "location": "msk", "price": "от 16.0 млн"},
        {"idx": 3, "name": "Сиреневый парк", "location": "msk", "price": "от 18.1 млн"},
    ]
    visible_text = (
        "Есть несколько вариантов:\n\n"
        "1. ЖК «Южные Сады», цена от 16.0 млн\n\n"
        "2. ЖК «Сиреневый парк», цена от 18.1 млн\n\n"
        "3. ЖК «Амурский парк», цена от 20.0 млн\n\n"
        "Какой ЖК хотите рассмотреть подробнее?"
    )
    visible_options = _visible_options_from_response(visible_text, raw_options)
    state_visible = {"last_options": raw_options, "visible_options": visible_options, "selected_option": None}
    intent_one = _resolve_dialog_intent("1", state_visible)
    intent_two_text = _resolve_dialog_intent("2. ЖК «Сиреневый парк», цена от 18.1 млн", state_visible)
    intent_budget_mixed = _resolve_dialog_intent("бюджет, у меня только 15 млн на руках", state_visible)
    intent_one_but_expensive = _resolve_dialog_intent("1 но дорого", state_visible)
    normalized_delta = _normalize_followup_params_delta({"budget": 15_000_000, "priority": "budget"})
    pass_visible_select = (
        [o.get("name") for o in visible_options] == ["Южные Сады", "Сиреневый парк", "Амурский парк"]
        and intent_one.get("intent") == "followup_classifier"
        and intent_two_text.get("intent") == "followup_classifier"
        and intent_budget_mixed.get("intent") == "followup_classifier"
        and intent_one_but_expensive.get("intent") == "followup_classifier"
        and _pure_option_choice_index("1") == 1
        and _pure_option_choice_index("15 млн") is None
        and normalized_delta.get("max_price") == 15_000_000
        and "budget" not in normalized_delta
    )
    results.append(Result(
        suite="h028",
        scenario="text_choice_uses_visible_list_order_and_name",
        passed=pass_visible_select,
        error="" if pass_visible_select else f"visible={visible_options}; one={intent_one}; two={intent_two_text}; budget={intent_budget_mixed}; one_exp={intent_one_but_expensive}; delta={normalized_delta}",
        response_text=f"visible={[o.get('name') for o in visible_options]}; one={intent_one.get('intent')}; two={intent_two_text.get('intent')}; budget={intent_budget_mixed.get('intent')}; one_exp={intent_one_but_expensive.get('intent')}; delta={normalized_delta}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    memory_options = [
        {"idx": 1, "name": "ЖК «Первый»", "price": "5 млн", "price_min": 5_000_000, "finishing": "без отделки"},
        {"idx": 2, "name": "ЖК «Второй»", "price": "7 млн", "price_min": 7_000_000, "finishing": "есть отделка"},
    ]
    state_finish = {"last_options": memory_options, "selected_option": None}
    intent_finish = _resolve_dialog_intent("с отделкой", state_finish)
    pass_finish = intent_finish.get("intent") == "followup_classifier"
    results.append(Result(
        suite="h028",
        scenario="finish_refinement_uses_memory_not_new_search",
        passed=pass_finish,
        error="" if pass_finish else f"intent_finish={intent_finish}",
        response_text=f"intent_finish={intent_finish}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    handoff = _format_operator_handoff_for_option(opt).lower()
    pass_handoff = (
        "оператор" in handoff
        and "mcp" not in handoff
        and "json" not in handoff
        and "хотите оставить номер" in handoff
        and "по цене вижу" not in handoff
        and "по площади вижу" not in handoff
    )
    results.append(Result(
        suite="h028",
        scenario="operator_handoff_is_human_and_no_technical_leak",
        passed=pass_handoff,
        error="" if pass_handoff else f"bad handoff text: {handoff}",
        response_text=handoff,
        duration_ms=int((time.time() - started) * 1000),
    ))

    # H029/H030/H031: продающая карточка, weak recovery и лимит fallback-кнопок.
    rich_opt = {
        "idx": 1,
        "name": "Жилой квартал «Новые Котельники»",
        "location": "Котельники",
        "price": "от 5.75 млн",
        "price_min": 5_750_000,
        "area": "от 26.8 м²",
        "ready": "сдан (2025)",
    }
    card = _format_option_response(rich_opt)
    card_low = card.lower()
    dry_markers = ["локация:", "цена:", "отделка:", "готовность/срок:"]
    pass_card = (
        not any(m in card_low for m in dry_markers)
        and "хотите, расскажу подробнее" in card_low
        and "\n\n" in card
        and "по цене вижу" in card_low
        and "баз" not in card_low
    )
    results.append(Result(
        suite="h029",
        scenario="selected_option_is_sales_card_not_field_dump",
        passed=pass_card,
        error="" if pass_card else f"card is not sales-like enough: {card}",
        response_text=card,
        duration_ms=int((time.time() - started) * 1000),
    ))

    # UX_E2E: полный no-buttons путь без Overmind: список → выбор цифрой/текстом → карточка → подробнее из памяти → оператор для актуальности.
    e2e_raw_options = [
        {"idx": 1, "name": "Амурский парк", "location": "msk", "price": "20000000", "price_min": 20_000_000},
        {"idx": 2, "name": "ЖК «Южные Сады»", "location": "msk", "price": "16000000", "price_min": 16_000_000},
        {"idx": 3, "name": "ЖК «Сиреневый парк»", "location": "msk", "price": "17720677", "price_min": 17_720_677},
    ]
    e2e_visible_response = _format_options_summary_response(
        [e2e_raw_options[1], e2e_raw_options[2], e2e_raw_options[0]],
        "Да, есть несколько вариантов, которые подходят для инвестиций",
        "Какой вариант вам интереснее рассмотреть подробнее?",
    )
    e2e_visible_options = _visible_options_from_response(e2e_visible_response, e2e_raw_options)
    e2e_state = {"last_options": e2e_raw_options, "visible_options": e2e_visible_options, "selected_option": None}
    e2e_rows = _markup_from_chat_buttons({"_buttons": [{"text": "Подробнее", "action": "details"}]}, e2e_state, e2e_visible_response, "ux-e2e")
    e2e_select_three = _resolve_dialog_intent("3", e2e_state)
    e2e_select_two_text = _resolve_dialog_intent("2. ЖК «Сиреневый парк», цена от 17,7 млн", e2e_state)
    e2e_option = e2e_visible_options[2] if len(e2e_visible_options) >= 3 else {}
    e2e_card = _format_option_response(e2e_option)
    e2e_card_low = e2e_card.lower()
    e2e_selected_state = {"last_options": e2e_raw_options, "visible_options": e2e_visible_options, "selected_option": e2e_option}
    e2e_more = _resolve_dialog_intent("подробнее", e2e_selected_state)
    e2e_handoff = _format_operator_handoff_for_option(e2e_option)
    e2e_handoff_low = e2e_handoff.lower()
    e2e_pass = (
        e2e_rows == []
        and "\n\n1." in e2e_visible_response
        and "\n\nКакой вариант" in e2e_visible_response
        and [o.get("name") for o in e2e_visible_options] == ["ЖК «Южные Сады»", "ЖК «Сиреневый парк»", "Амурский парк"]
        and e2e_select_three.get("intent") == "followup_classifier"
        and e2e_select_two_text.get("intent") == "followup_classifier"
        and "msk" not in e2e_card_low
        and "17720677" not in e2e_card
        and "москва" in e2e_card_low
        and "млн рублей" in e2e_card_low
        and e2e_more.get("intent") == "followup_classifier"
        and "оператор" in e2e_handoff_low
        and "mcp" not in e2e_handoff_low
        and "json" not in e2e_handoff_low
    )
    add_result(
        "ux_e2e",
        "no_buttons_visible_choice_card_details_operator",
        e2e_pass,
        response_text=f"visible={e2e_visible_response}\n--- card={e2e_card}\n--- handoff={e2e_handoff}",
        error=(
            f"rows={e2e_rows}; visible={[o.get('name') for o in e2e_visible_options]}; "
            f"three={e2e_select_three}; two_text={e2e_select_two_text}; more={e2e_more}; card={e2e_card}; handoff={e2e_handoff}"
        ),
    )

    # UX_E2E: короткие «да/нет/возможно» после карточки не должны снова выбирать тот же ЖК.
    followup_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
        "selected_option_card_shown_count": 1,
    }
    _remember_bot_response(
        followup_state,
        "По «Амурскому парку» вижу цену и локацию.\n\nХотите сравнить этот вариант с похожими?",
        offer_type="compare_selected",
        answer_kind="selected_option_card",
    )
    yes_intent = _resolve_dialog_intent("да", followup_state)
    no_intent = _resolve_dialog_intent("нет", followup_state)
    maybe_intent = _resolve_dialog_intent("возможно", followup_state)
    payload = _followup_state_payload(followup_state)
    clarify_text = _clarification_from_followup({"intent": "clarify", "confidence": 0.4}, followup_state)
    e2e_followup_pass = (
        yes_intent.get("intent") == "followup_classifier"
        and no_intent.get("intent") == "followup_classifier"
        and maybe_intent.get("intent") == "followup_classifier"
        and payload.get("last_offer_type") == "compare_selected"
        and payload.get("last_bot_question") == "Хотите сравнить этот вариант с похожими?"
        and "смысл неясен" not in clarify_text.lower()
        and "уточните" in clarify_text.lower()
        and normalize_intent("has_options") == "clarify"
    )
    add_result(
        "ux_e2e",
        "short_replies_go_to_followup_classifier_not_repeat_card",
        e2e_followup_pass,
        response_text=f"yes={yes_intent}; no={no_intent}; maybe={maybe_intent}; payload={payload}; clarify={clarify_text}",
        error=f"yes={yes_intent}; no={no_intent}; maybe={maybe_intent}; payload={payload}; clarify={clarify_text}",
    )

    # UX_E2E: после вопроса про оператора «зачем» должно объяснять, а «продолжить/подбор» — продолжать подбор,
    # а не крутить один и тот же clarify fallback.
    operator_followup_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
        "selected_option_card_shown_count": 1,
    }
    _remember_bot_response(
        operator_followup_state,
        "Что стоит проверить отдельно: актуальное наличие квартир, конкретные корпуса, этажи и условия покупки.\n\nХотите, передам оператору именно этот ЖК и ваш запрос?",
        offer_type="operator_for_selected",
        answer_kind="selected_option_card",
    )
    reason_text = _operator_reason_response(operator_followup_state).lower()
    continue_text = _continue_selection_response(operator_followup_state).lower()
    operator_loop_pass = (
        normalize_intent("explain_operator_reason") == "explain_operator_reason"
        and normalize_intent("continue_selection") == "continue_selection"
        and "проверить" in reason_text
        and "актуальные квартиры" in reason_text
        and "продолжим подбор" in continue_text
        and "уточните, пожалуйста: продолжить подбор или изменить условия" not in reason_text
        and "уточните, пожалуйста: продолжить подбор или изменить условия" not in continue_text
    )
    add_result(
        "ux_e2e",
        "operator_offer_why_and_continue_do_not_loop_clarify",
        operator_loop_pass,
        response_text=f"reason={reason_text}\n--- continue={continue_text}",
        error=f"reason={reason_text}\n--- continue={continue_text}",
    )

    # UX_E2E: meta-вопрос «как ты подбираешь?» должен получить объяснение логики,
    # а не повторный shortlist через continue_selection.
    selection_logic_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "family"},
        "dialog_window": [],
    }
    _remember_bot_response(
        selection_logic_state,
        "Для сравнения подобрала три варианта. Какой из этих вариантов вам интереснее обсудить подробнее?",
        offer_type="choose_option",
        answer_kind="options_summary",
    )
    selection_logic_resolved = _resolve_dialog_intent("а как ты подбираешь?", selection_logic_state).get("intent")
    selection_logic_text = _selection_logic_response(selection_logic_state).lower()
    selection_logic_pass = (
        normalize_intent("explain_selection_logic") == "explain_selection_logic"
        and selection_logic_resolved == "followup_classifier"
        and "не просто по цене" in selection_logic_text
        and "готов" in selection_logic_text
        and "отдел" in selection_logic_text
        and "продолжим подбор" not in selection_logic_text
        and "explain_selection_logic" in DIALOG_STATE_PLANNER_PROMPT
    )
    add_result(
        "ux_e2e",
        "how_do_you_select_answers_selection_logic_not_repeat_list",
        selection_logic_pass,
        response_text=f"resolved={selection_logic_resolved}; text={selection_logic_text}",
        error=f"resolved={selection_logic_resolved}; text={selection_logic_text}",
    )

    rental_consultation_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "rental"},
        "dialog_window": [],
    }
    _remember_bot_response(
        rental_consultation_state,
        "Подобрала несколько вариантов под аренду. Какой из них разобрать дальше?",
        offer_type="choose_option",
        answer_kind="options_summary",
    )
    rental_consultation_resolved = _resolve_dialog_intent("а что важно для аренды?", rental_consultation_state).get("intent")
    rental_consultation_text = _consultation_question_response(rental_consultation_state, "а что важно для аренды?").lower()
    rental_consultation_pass = (
        normalize_dialog_action("consultation_answer") == "consultation_answer"
        and normalize_intent("consultation_answer") == "consultation_answer"
        and rental_consultation_resolved == "followup_classifier"
        and "аренд" in rental_consultation_text
        and "локац" in rental_consultation_text
        and "отдел" in rental_consultation_text
        and "готов" in rental_consultation_text
        and "продолжим подбор" not in rental_consultation_text
        and "из похожих вариантов" not in rental_consultation_text
        and "какой из них разобрать дальше" not in rental_consultation_text
    )
    add_result(
        "ux_e2e",
        "rental_consultation_question_answers_criteria_not_repeat_list",
        rental_consultation_pass,
        response_text=f"resolved={rental_consultation_resolved}; text={rental_consultation_text}",
        error=f"resolved={rental_consultation_resolved}; text={rental_consultation_text}",
    )

    payment_terms_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "rental"},
        "dialog_window": [],
    }
    payment_terms_text = _consultation_question_response(payment_terms_state, "это все без первоначального взноса?").lower()
    payment_terms_topic = _normalize_conversation_topic("это все без первоначального взноса?", payment_terms_state)
    payment_terms_pass = (
        payment_terms_topic == "payment_terms"
        and "первоначальн" in payment_terms_text
        and "взнос" in payment_terms_text
        and "аренд" not in payment_terms_text
        and "локац" not in payment_terms_text
        and "отдел" not in payment_terms_text
        and "готов" not in payment_terms_text
    )
    add_result(
        "ux_e2e",
        "payment_terms_question_routes_to_payment_terms_topic_not_rental_criteria",
        payment_terms_pass,
        response_text=f"topic={payment_terms_topic}; text={payment_terms_text}",
        error=f"topic={payment_terms_topic}; text={payment_terms_text}",
    )

    family_mortgage_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "family"},
        "dialog_window": [],
    }
    family_signals = _extract_conversation_followup_signals("а под семейную ипотеку?", family_mortgage_state)
    family_mortgage_text = _consultation_question_response(family_mortgage_state, "а под семейную ипотеку?").lower()
    family_mortgage_pass = (
        family_signals.get("topic") == "financing"
        and family_signals.get("subtopic_hint") == "family_mortgage"
        and family_signals.get("mortgage_type") == "family_mortgage"
        and "семейн" in family_mortgage_text
        and "ипотек" in family_mortgage_text
        and "разберу именно эти варианты под семейную ипотеку" in family_mortgage_text
        and "оператор" in family_mortgage_text
        and "продолжим подбор" not in family_mortgage_text
        and "сравню текущие варианты" not in family_mortgage_text
        and "из похожих вариантов" not in family_mortgage_text
    )
    add_result(
        "ux_e2e",
        "family_mortgage_question_extracts_financing_signal_not_generic",
        family_mortgage_pass,
        response_text=f"signals={family_signals}; text={family_mortgage_text}",
        error=f"signals={family_signals}; text={family_mortgage_text}",
    )

    family_mortgage_direct_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "family"},
        "dialog_window": [],
    }
    family_mortgage_direct_text = _consultation_question_response(family_mortgage_direct_state, "под семейную ипотеку подойдет?").lower()
    family_mortgage_direct_pass = (
        family_mortgage_direct_text.startswith("да, под семейную ипотеку")
        and "готовность" in family_mortgage_direct_text
        and "отделк" in family_mortgage_direct_text
        and "локац" in family_mortgage_direct_text
        and "точной ставке" in family_mortgage_direct_text
        and "оператор" in family_mortgage_direct_text
        and "разберу именно эти варианты под семейную ипотеку" in family_mortgage_direct_text
        and "сравню текущие варианты" not in family_mortgage_direct_text
        and "продолжим подбор" not in family_mortgage_direct_text
    )
    add_result(
        "ux_e2e",
        "family_mortgage_question_answers_directly_then_operator_optional",
        family_mortgage_direct_pass,
        response_text=family_mortgage_direct_text,
        error=family_mortgage_direct_text,
    )

    family_mortgage_guidance = _consultation_answer_guidance("под семейную ипотеку подойдет?", family_mortgage_direct_state)
    family_mortgage_prompt = _build_consultation_answer_prompt(
        user_text="под семейную ипотеку подойдет?",
        state=family_mortgage_direct_state,
        dialog_plan={"dialog_action": "consultation_answer", "mode": "conversation", "reason": "family_mortgage"},
    ).lower()
    family_mortgage_prompt_pass = (
        family_mortgage_guidance.get("topic") == "financing"
        and family_mortgage_guidance.get("subtopic_hint") == "family_mortgage"
        and family_mortgage_guidance.get("answer_priority") == "direct_answer_first"
        and family_mortgage_guidance.get("operator_policy") == "optional_after_direct_answer_for_live_terms"
        and family_mortgage_guidance.get("use_current_context")
        and "answer_guidance" in family_mortgage_prompt
        and "direct_answer_first" in family_mortgage_prompt
        and "live_sales_manager" in family_mortgage_prompt
        and "family_mortgage" in family_mortgage_prompt
        and "operator_policy" in family_mortgage_prompt
        and "payment_financing_playbook" in family_mortgage_prompt
        and "реальное действие" in family_mortgage_prompt
        and "нет фонового ожидания" in family_mortgage_prompt
        and "final_question" in family_mortgage_prompt
        and "каждый ответ должен содержать final_question" in family_mortgage_prompt
        and "один мягкий вопрос" in family_mortgage_prompt
        and "не сравнивай варианты по умолчанию" in family_mortgage_prompt
    )
    add_result(
        "ux_e2e",
        "family_mortgage_consultation_prompt_has_answer_guidance_not_operator_first",
        family_mortgage_prompt_pass,
        response_text=f"guidance={family_mortgage_guidance}; prompt={family_mortgage_prompt[:1200]}",
        error=f"guidance={family_mortgage_guidance}; prompt={family_mortgage_prompt[:1200]}",
    )

    down_payment_guidance = _consultation_answer_guidance("это без пв?", payment_terms_state)
    down_payment_prompt = _build_consultation_answer_prompt(
        user_text="это без пв?",
        state=payment_terms_state,
        dialog_plan={"dialog_action": "consultation_answer", "mode": "conversation", "reason": "down_payment"},
    ).lower()
    down_payment_final_question = str(down_payment_guidance.get("final_question") or "").lower()
    down_payment_playbook = "\n".join(str(item).lower() for item in down_payment_guidance.get("payment_financing_playbook") or [])
    down_payment_playbook_pass = (
        down_payment_guidance.get("topic") == "payment_terms"
        and down_payment_guidance.get("subtopic_hint") == "down_payment"
        and "сценарий: клиент спрашивает про без пв" in down_payment_playbook
        and "выбрать жк" in down_payment_playbook
        and "оператор" in down_payment_playbook
        and "передать оператору" in down_payment_final_question
        and "посмотрю" not in down_payment_final_question
        and "уточню" not in down_payment_final_question
        and "как только" in down_payment_prompt
        and "не пиши 'я уточню'" in down_payment_prompt
        and "нет фонового ожидания" in down_payment_prompt
    )
    add_result(
        "ux_e2e",
        "down_payment_playbook_routes_to_operator_not_async_promise",
        down_payment_playbook_pass,
        response_text=f"guidance={down_payment_guidance}; prompt={down_payment_prompt[:1400]}",
        error=f"guidance={down_payment_guidance}; prompt={down_payment_prompt[:1400]}",
    )

    sticky_payment_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "family"},
        "dialog_window": [],
    }
    _store_active_conversation_topic(sticky_payment_state, "это без пв?")
    sticky_yes = _extract_conversation_followup_signals("да", sticky_payment_state)
    sticky_all = _extract_conversation_followup_signals("проверь все", sticky_payment_state)
    sticky_typo_all = _extract_conversation_followup_signals("првоер ьвсе", sticky_payment_state)
    sticky_prompt = _build_consultation_answer_prompt(
        user_text="проверь все",
        state=sticky_payment_state,
        dialog_plan={"dialog_action": "consultation_answer", "mode": "conversation", "reason": "continue_down_payment"},
    ).lower()
    sticky_topic_pass = (
        sticky_payment_state.get("active_conversation_topic", {}).get("topic") == "payment_terms"
        and sticky_payment_state.get("active_conversation_topic", {}).get("subtopic_hint") == "down_payment"
        and sticky_yes.get("topic") == "payment_terms"
        and sticky_yes.get("subtopic_hint") == "down_payment"
        and sticky_all.get("topic") == "payment_terms"
        and sticky_all.get("subtopic_hint") == "down_payment"
        and sticky_typo_all.get("topic") == "payment_terms"
        and sticky_typo_all.get("subtopic_hint") == "down_payment"
        and sticky_typo_all.get("target_scope") == "all_current_options"
        and sticky_typo_all.get("needs_operator") is True
        and "все текущие жк" in str(sticky_typo_all.get("final_question") or "").lower()
        and "active_conversation_topic" in sticky_prompt
        and "payment_financing_playbook" in sticky_prompt
        and "проверь все" in sticky_prompt
        and "не создавай четвёртый режим" in sticky_prompt
    )
    add_result(
        "ux_e2e",
        "sticky_payment_topic_survives_yes_and_check_all",
        sticky_topic_pass,
        response_text=f"state={sticky_payment_state.get('active_conversation_topic')}; yes={sticky_yes}; all={sticky_all}; typo_all={sticky_typo_all}; prompt={sticky_prompt[:1400]}",
        error=f"state={sticky_payment_state.get('active_conversation_topic')}; yes={sticky_yes}; all={sticky_all}; typo_all={sticky_typo_all}; prompt={sticky_prompt[:1400]}",
    )

    legacy_question_payload = {
        "message": "Поняла, центр дорогой — посмотрю дешевле и можно чуть шире по локации.",
        "items": [],
        "question": "Показать варианты не прямо в центре, но с нормальным бюджетом входа?",
    }
    legacy_question_text = _response_payload_to_text(legacy_question_payload).lower()
    simplified_presenter_contract_pass = (
        not STAGE_PRESENTER_ENABLED
        and "центр дорогой" in legacy_question_text
        and "показать варианты" in legacy_question_text
        and legacy_question_text.count("?") == 1
    )
    add_result(
        "ux_e2e",
        "simplified_architecture_stage_presenter_off_and_legacy_question_kept",
        simplified_presenter_contract_pass,
        response_text=f"stage_enabled={STAGE_PRESENTER_ENABLED}; text={legacy_question_text}",
        error=f"stage_enabled={STAGE_PRESENTER_ENABLED}; text={legacy_question_text}",
    )

    list_payload = {
        "message": "Да, нашла несколько вариантов в Котельниках. Покажу самые понятные.",
        "items": [
            {
                "name": "Жилой квартал «Новые Котельники»",
                "location": "Котельники",
                "price_range": "5.75 - 13.81 млн руб.",
                "finishing": "без отделки",
                "ready": "2025 г.",
                "reason": "Подходит как более доступный вход в районе.",
            },
            {
                "name": "Кузьминский лес",
                "location": "Котельники",
                "price_range": "8.88 - 21.79 млн руб.",
                "finishing": "с отделкой",
                "ready": "2025 г.",
                "reason": "Удобен, если важна готовность и ремонт.",
            },
        ],
        "final_question": "Какой ЖК хотите рассмотреть подробнее?",
    }
    list_payload_text = _response_payload_to_text(list_payload).lower()
    list_payload_render_pass = (
        "новые котельники" in list_payload_text
        and "кузьминский лес" in list_payload_text
        and "5.75 - 13.81" in list_payload_text
        and "без отделки" in list_payload_text
        and list_payload_text.count("?") == 1
        and "1." in list_payload_text
        and "2." in list_payload_text
    )
    add_result(
        "ux_e2e",
        "chat_v1_response_items_render_into_visible_list",
        list_payload_render_pass,
        response_text=list_payload_text,
        error=list_payload_text,
    )

    mortgage_typo_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "rental"},
        "dialog_window": [],
    }
    mortgage_typo_signals = _extract_conversation_followup_signals("в ипотеуй подойдет?", mortgage_typo_state)
    mortgage_typo_prompt = _build_conversation_answer_prompt(
        user_text="в ипотеуй подойдет?",
        state=mortgage_typo_state,
        dialog_plan={"dialog_action": "conversation_answer", "mode": "conversation", "reason": "mortgage typo followup"},
    ).lower()
    mortgage_typo_pass = (
        mortgage_typo_signals.get("topic") == "financing"
        and "final_question" in mortgage_typo_prompt
        and "каждый ответ должен содержать final_question" in mortgage_typo_prompt
        and "не обещай ставку" in mortgage_typo_prompt
    )
    add_result(
        "ux_e2e",
        "mortgage_typo_followup_routes_to_financing_with_final_question",
        mortgage_typo_pass,
        response_text=f"signals={mortgage_typo_signals}; prompt={mortgage_typo_prompt[:900]}",
        error=f"signals={mortgage_typo_signals}; prompt={mortgage_typo_prompt[:900]}",
    )

    # UX_E2E: короткое «да» после предложения объяснить логику — это live-dialog answer,
    # а не повторный shortlist. Раньше continue_from_memory падал в continue_selection.
    yes_after_explain_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": None,
        "params": {"purpose": "investment"},
        "dialog_window": [
            {"role": "user", "text": "как ты подираешь жк?"},
            {"role": "assistant", "text": "Подбираю не просто по цене. Хотите, я коротко объясню, почему в прошлой подборке показала именно эти варианты?"},
        ],
    }
    _remember_bot_response(
        yes_after_explain_state,
        "Подбираю не просто по цене. Хотите, я коротко объясню, почему в прошлой подборке показала именно эти варианты?",
        offer_type="explain_selection_logic",
        answer_kind="selection_logic",
    )
    yes_plan = {
        "dialog_action": "continue_from_memory",
        "confidence": 1.0,
        "reason": "Клиент подтвердил согласие на объяснение логики подбора, которое было предложено в последнем сообщении бота.",
    }
    yes_mapped = _followup_intent_from_dialog_action(
        "continue_from_memory",
        yes_plan,
        visible_policy="keep",
        has_options=True,
    )
    conversation_prompt = _build_conversation_answer_prompt(
        user_text="да",
        state=yes_after_explain_state,
        dialog_plan=yes_plan,
    ).lower()
    yes_after_explain_pass = (
        normalize_dialog_action("conversation_answer") == "conversation_answer"
        and normalize_intent("conversation_answer") == "conversation_answer"
        and normalize_dialog_mode("conversation", "conversation_answer") == "conversation"
        and yes_mapped == "conversation_answer"
        and "last_bot_question" in conversation_prompt
        and "conversation_followup" in conversation_prompt
        and "final_question" in conversation_prompt
        and "каждый ответ должен содержать final_question" in conversation_prompt
        and "ровно один вопрос" in conversation_prompt
        and "не повторяй список" in conversation_prompt
        and "хорошо, продолжим подбор" in conversation_prompt
        and "согласился на объяснение" in conversation_prompt
    )
    add_result(
        "ux_e2e",
        "yes_after_explanation_routes_to_conversation_not_shortlist",
        yes_after_explain_pass,
        response_text=f"mapped={yes_mapped}; prompt={conversation_prompt[:1200]}",
        error=f"mapped={yes_mapped}; prompt={conversation_prompt[:1200]}",
    )

    # UX_E2E: если после подробностей бот спросил «оставите номер», короткое «да/хочу» — это согласие на контакт,
    # а не повторная презентация ЖК.
    contact_accept_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
        "selected_option_card_shown_count": 1,
    }
    _remember_bot_response(
        contact_accept_state,
        "По ЖК картина такая: цена и срок понятны.\n\nОставите номер для связи?",
        offer_type="selected_option_details",
        answer_kind="selected_option_details",
    )
    contact_yes = _resolve_dialog_intent("да", contact_accept_state)
    contact_want = _resolve_dialog_intent("хочу", contact_accept_state)
    contact_local = _local_followup_intent("да", {**contact_accept_state, "last_offer_type": "operator_for_selected"})
    contact_accept_pass = (
        contact_yes.get("intent") == "operator_contact_accept"
        and contact_want.get("intent") == "operator_contact_accept"
        and contact_local == "operator_contact_accept"
    )
    add_result(
        "ux_e2e",
        "yes_after_contact_offer_requests_phone_not_repeat_details",
        contact_accept_pass,
        response_text=f"yes={contact_yes}; want={contact_want}; local={contact_local}",
        error=f"yes={contact_yes}; want={contact_want}; local={contact_local}",
    )

    # UX_E2E: телефон — это code-level capture выше LLM/search, а не очередной запрос в подбор.
    # Поддерживаем и текстовый номер, и будущий Telegram contact через общие pure helpers.
    phone_context_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
    }
    _remember_bot_response(
        phone_context_state,
        "Оставите номер для связи?",
        offer_type="operator_for_selected",
        answer_kind="operator_handoff",
    )
    empty_phone_state = {"last_options": [], "visible_options": [], "selected_option": None, "dialog_window": []}
    text_phone = _extract_phone_from_text("+7 999 123-45-67")
    plain_phone = _extract_phone_from_text("89991234567")
    invalid_budget = _extract_phone_from_text("до 200к")
    phone_capture_pass = (
        text_phone == "+79991234567"
        and plain_phone == "89991234567"
        and invalid_budget == ""
        and _looks_like_phone_text("+7 999 123-45-67")
        and not _looks_like_phone_text("до 200к")
        and _has_phone_capture_context(phone_context_state)
        and _has_phone_capture_context({"awaiting_phone": True})
        and not _has_phone_capture_context(empty_phone_state)
        and "не понимаю" in _phone_needs_context_response().lower()
        and _phone_log_meta(text_phone).get("phone_len") == 11
    )
    add_result(
        "ux_e2e",
        "phone_capture_is_code_level_and_requires_context",
        phone_capture_pass,
        response_text=(
            f"text_phone={text_phone}; plain={plain_phone}; budget={invalid_budget}; "
            f"context={_has_phone_capture_context(phone_context_state)}; empty={_has_phone_capture_context(empty_phone_state)}"
        ),
        error=(
            f"text_phone={text_phone}; plain={plain_phone}; budget={invalid_budget}; "
            f"context={_has_phone_capture_context(phone_context_state)}; empty={_has_phone_capture_context(empty_phone_state)}; "
            f"ctx_response={_phone_needs_context_response()}"
        ),
    )

    selected_investment_text = (
        "Для инвестиций это удобный вариант с понятным порогом входа и хорошим горизонтом планирования. "
        "Хотите сравнить этот проект с другими или подробнее разобрать цены?"
    )
    investment_cta = _operator_cta_for_selected_investment(
        selected_investment_text,
        {"name": "ЖК «Южные Сады»"},
        "investment",
    ).lower()
    non_investment_cta = _operator_cta_for_selected_investment(
        selected_investment_text,
        {"name": "ЖК «Южные Сады»"},
        "self_use",
    ).lower()
    selected_investment_cta_pass = (
        "оператор проверит" in investment_cta
        and "оставить номер" in investment_cta
        and "жк «южные сады»" in investment_cta
        and "сравнить этот проект" not in investment_cta
        and "сравнить этот проект" in non_investment_cta
        and "\n\nхотите" in _prepare_response_text(selected_investment_text).lower()
    )
    add_result(
        "ux_e2e",
        "selected_investment_choice_leads_to_operator_cta",
        selected_investment_cta_pass,
        response_text=investment_cta,
        error=f"investment={investment_cta}; non_investment={non_investment_cta}",
    )

    # UX_E2E: расширенный набор коротких follow-up фраз вокруг выбранного ЖК.
    # Это не проверка всех словарных форм, а контракт маршрутизации: явное сравнение сравнивает,
    # явная бронь ведёт к оператору, запрос раскрытия выбранного ЖК идёт в explain, мягкие/неясные фразы уходят в classifier.
    phrase_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
        "selected_option_card_shown_count": 1,
    }
    _remember_bot_response(
        phrase_state,
        "Хотите, передам оператору именно этот ЖК и ваш запрос?",
        offer_type="operator_for_selected",
        answer_kind="selected_option_card",
    )
    phrase_expectations = {
        "да": "followup_classifier",
        "нет": "followup_classifier",
        "возможно": "followup_classifier",
        "наверное": "followup_classifier",
        "зачем": "followup_classifier",
        "продолжить": "followup_classifier",
        "подбор": "followup_classifier",
        "хочу еще варианты": "followup_classifier",
        "сравни": "followup_classifier",
        "не надо": "followup_classifier",
        "что по нему известно": "followup_classifier",
        "расскажи подробнее": "followup_classifier",
        "бронь": "followup_classifier",
        "этажи": "followup_classifier",
    }
    phrase_results = {phrase: _resolve_dialog_intent(phrase, phrase_state).get("intent") for phrase in phrase_expectations}
    phrase_pass = all(phrase_results[p] == expected for p, expected in phrase_expectations.items())
    add_result(
        "ux_e2e",
        "selected_option_short_phrase_routing_matrix",
        phrase_pass,
        response_text=json.dumps(phrase_results, ensure_ascii=False),
        error=json.dumps({"expected": phrase_expectations, "actual": phrase_results}, ensure_ascii=False),
    )

    # UX_E2E: отрицания не должны слепо запускать оператора или новый MCP.
    # Явный отказ от оператора ловит минимальный code guard, продуктовые отрицания уходят в LLM classifier.
    negation_state = {
        "last_options": e2e_raw_options,
        "visible_options": e2e_visible_options,
        "selected_option": e2e_option,
        "params": {"purpose": "investment"},
        "dialog_window": [],
        "selected_option_card_shown_count": 1,
    }
    _remember_bot_response(
        negation_state,
        "По ЖК картина такая. Хотите сравнить этот ЖК с другими или оставить номер для связи?",
        offer_type="selected_option_details",
        answer_kind="selected_option_details",
    )
    negation_results = {
        "не хочу оператора": _resolve_dialog_intent("не хочу оператора", negation_state).get("intent"),
        "не надо звонить": _resolve_dialog_intent("не надо звонить", negation_state).get("intent"),
        "не надо бронь": _resolve_dialog_intent("не надо бронь", negation_state).get("intent"),
        "не этот": _resolve_dialog_intent("не этот", negation_state).get("intent"),
        "не подходит": _resolve_dialog_intent("не подходит", negation_state).get("intent"),
        "1, не надо бронь": _resolve_dialog_intent("1, не надо бронь", negation_state).get("intent"),
    }
    reject_operator_text = _reject_operator_response(negation_state).lower()
    reject_selected_text = _reject_selected_option_response(negation_state).lower()
    negation_pass = (
        negation_results["не хочу оператора"] == "followup_classifier"
        and negation_results["не надо звонить"] == "followup_classifier"
        and negation_results["не надо бронь"] == "followup_classifier"
        and negation_results["не этот"] == "followup_classifier"
        and negation_results["не подходит"] == "followup_classifier"
        and negation_results["1, не надо бронь"] == "followup_classifier"
        and "остаёмся здесь" in reject_operator_text
        and "этот жк убираем" in reject_selected_text
    )
    add_result(
        "ux_e2e",
        "negation_routes_to_classifier_or_minimal_safety_not_operator",
        negation_pass,
        response_text=json.dumps(negation_results, ensure_ascii=False) + f"\nreject_operator={reject_operator_text}\nreject_selected={reject_selected_text}",
        error=json.dumps(negation_results, ensure_ascii=False) + f"\nreject_operator={reject_operator_text}\nreject_selected={reject_selected_text}",
    )

    local_fallback_results = {
        phrase: _local_followup_intent(phrase, phrase_state)
        for phrase in ["да", "нет", "зачем", "продолжить", "подбор"]
    }
    local_fallback_pass = local_fallback_results == {
        "да": "",
        "нет": "",
        "зачем": "",
        "продолжить": "",
        "подбор": "",
    }
    add_result(
        "ux_e2e",
        "local_followup_fallback_handles_operator_offer_without_llm",
        local_fallback_pass,
        response_text=json.dumps(local_fallback_results, ensure_ascii=False),
        error=json.dumps(local_fallback_results, ensure_ascii=False),
    )

    raw_opt = {
        "idx": 3,
        "name": "ЖК «Сиреневый парк»",
        "location": "msk",
        "price": "17720677",
        "price_min": 17_720_677,
    }
    raw_card = _format_option_response(raw_opt)
    raw_low = raw_card.lower()
    pass_raw_format = "по локации вижу: москва" in raw_low and "от 17,7 млн рублей" in raw_low and "17720677" not in raw_card and "msk" not in raw_low
    results.append(Result(
        suite="h029",
        scenario="selected_option_formats_raw_mcp_fields_for_client",
        passed=pass_raw_format,
        error="" if pass_raw_format else f"raw fields leaked to client: {raw_card}",
        response_text=raw_card,
        duration_ms=int((time.time() - started) * 1000),
    ))

    blocks = [block.strip() for block in raw_card.split("\n\n") if block.strip()]
    max_block_len = max((len(block) for block in blocks), default=0)
    pass_selected_card_spacing = (
        len(blocks) >= 4
        and max_block_len <= 260
        and raw_card.rstrip().endswith("?")
    )
    results.append(Result(
        suite="h029",
        scenario="selected_option_card_uses_short_paragraphs",
        passed=pass_selected_card_spacing,
        error="" if pass_selected_card_spacing else f"blocks={len(blocks)} max_block_len={max_block_len}; card={raw_card!r}",
        response_text=raw_card,
        duration_ms=int((time.time() - started) * 1000),
    ))

    missing_opt = {
        "idx": 1,
        "name": "Холланд парк",
        "location": "Москва",
        "price": "уточняется",
        "finishing": "уточняется",
        "ready": "2026",
    }
    missing_card = _format_option_response(missing_opt, purpose="investment")
    missing_list = _format_options_summary_response([missing_opt], "Можно посмотреть", "Какой разобрать дальше?")
    missing_low = (missing_card + "\n" + missing_list).lower()
    pass_no_utochnyaetsya = "уточняется" not in missing_low and "по цене вижу ориентир уточняется" not in missing_low
    results.append(Result(
        suite="h029",
        scenario="missing_fields_do_not_say_utochnyaetsya_to_client",
        passed=pass_no_utochnyaetsya,
        error="" if pass_no_utochnyaetsya else f"bad missing wording: {missing_card}\n---\n{missing_list}",
        response_text=f"{missing_card}\n---\n{missing_list}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    investment_2025_opt = {
        "idx": 1,
        "name": "ЖК «Гранель Ильинойс»",
        "location": "mo",
        "price": "9400000",
        "price_min": 9_400_000,
        "ready": "конец 2025 года",
    }
    investment_card = _format_option_response(investment_2025_opt, purpose="investment")
    investment_low = investment_card.lower()
    pass_investment_2025 = (
        "гранель ильинойс" in investment_low
        and "от 9,4 млн рублей" in investment_low
        and "московская область" in investment_low
        and "уже должен быть сдан" in investment_low
        and "не нужно закладывать долгий срок ожидания" in investment_low
        and "актуальное наличие" in investment_low
        and "2025 года; это вариант с ожиданием" not in investment_low
    )
    results.append(Result(
        suite="h029",
        scenario="selected_option_2025_deadline_is_treated_as_due_for_investment",
        passed=pass_investment_2025,
        error="" if pass_investment_2025 else f"bad investment 2025 card: {investment_card}",
        response_text=investment_card,
        duration_ms=int((time.time() - started) * 1000),
    ))

    family_opt = {
        "idx": 2,
        "name": "Бусиновский парк",
        "location": "Москва",
        "price": "от 12.4 млн",
        "area": "от 38 м²",
        "ready": "сдан",
        "raw": {},
    }
    family_card = _format_option_response(family_opt, purpose="family")
    family_card_low = family_card.lower()
    bad_family_phrases = ["в данных", "подтверждения", "не буду придумывать", "mcp", "json"]
    pass_family_card = (
        "для семьи" in family_card_low
        and any(word in family_card_low for word in ("практич", "переезд", "ребён", "ребен", "площад", "бюджет"))
        and not any(phrase in family_card_low for phrase in bad_family_phrases)
    )
    results.append(Result(
        suite="h029",
        scenario="family_selected_option_has_selling_reason_not_disclaimer",
        passed=pass_family_card,
        error="" if pass_family_card else f"bad family card: {family_card}",
        response_text=family_card,
        duration_ms=int((time.time() - started) * 1000),
    ))

    family_search_payload = json.dumps({
        "facts": [
            {
                "name": "ЖК «Лучи»",
                "location": "Солнцево",
                "price_range": "от 10,6 млн",
                "schools": "2 школы",
                "kindergartens": "4 детских сада",
                "forest": "Чоботовский лес",
                "embankment": "набережная",
                "pharmacies": "аптеки",
                "yard_without_cars": "закрытый двор без машин",
            }
        ]
    }, ensure_ascii=False)
    normalized_family = _extract_options(family_search_payload)[0]
    normalized_family_text = json.dumps(normalized_family, ensure_ascii=False)
    pass_family_aliases = (
        "Чоботовский лес" in str(normalized_family.get("parks"))
        and "набережная" in str(normalized_family.get("parks"))
        and "аптеки" in str(normalized_family.get("clinics"))
        and "закрытый двор без машин" in str(normalized_family.get("yards"))
    )
    results.append(Result(
        suite="h029",
        scenario="family_infrastructure_aliases_are_preserved_from_search_payload",
        passed=pass_family_aliases,
        error="" if pass_family_aliases else f"bad normalized family aliases: {normalized_family_text}",
        response_text=normalized_family_text,
        duration_ms=int((time.time() - started) * 1000),
    ))

    family_first_list_options = [
        {
            "name": "ЖК «Лучи»",
            "location": "Солнцево",
            "price": "от 10,6 млн",
            "schools": "2 школы",
            "kindergartens": "4 детских сада",
            "parks": "Мещерский парк; Чоботовский лес",
        },
        {
            "name": "ЖК «Скандинавия»",
            "location": "Коммунарка",
            "schools": "школа на 1775 мест",
            "kindergartens": "детские сады",
            "clinics": "поликлиника",
            "parks": "парк с ландшафтным дизайном",
            "yards": "закрытые дворы без машин",
        },
        {
            "name": "Город-парк «Переделкино Ближнее»",
            "location": "Внуковское поселение",
            "schools": "2 школы",
            "kindergartens": "7 детских садов",
            "clinics": "поликлиника",
            "parks": "парки; набережная",
        },
        {
            "name": "ЖК «Лишний»",
            "schools": "школа",
        },
    ]
    family_fact_parts = _stage_option_fact_parts(family_first_list_options[0], "family")[:5]
    family_first_list = _render_stage_first_list(family_first_list_options, "family")
    family_first_list_low = family_first_list.lower()
    pass_family_first_list = (
        "4 детских сада" in family_first_list
        and "2 школы" in family_first_list
        and "Мещерский парк" in family_first_list
        and "Чоботовский лес" in family_first_list
        and "поликлиника" in family_first_list
        and "школа на 1775 мест" in family_first_list
        and "ЖК «Лишний»" not in family_first_list
        and family_first_list.count("?") == 1
        and family_first_list_low.rstrip().endswith("какой жк хотите рассмотреть подробнее?")
        and "2 школы" in family_fact_parts
        and any("Мещерский парк" in part for part in family_fact_parts)
    )
    results.append(Result(
        suite="h029",
        scenario="family_first_list_prioritizes_real_infrastructure",
        passed=pass_family_first_list,
        error="" if pass_family_first_list else f"bad family first_list: facts={family_fact_parts}; response={family_first_list}",
        response_text=family_first_list,
        duration_ms=int((time.time() - started) * 1000),
    ))

    weak_state = {"params": {"rooms": "s", "max_price": 5_000_000, "district": "msk"}, "asked_questions": []}
    weak_rows = _pick_quick_actions(weak_state, "C-narrow-empty")
    weak_callbacks = [btn["callback_data"] for row in weak_rows for btn in row]
    pass_weak = (
        "action:operator" in weak_callbacks
        and weak_callbacks[-1] == "action:operator"
        and any(cb in weak_callbacks for cb in ("budget:none", "district:mo", "action:show_near"))
        and len(weak_callbacks) > 1
    )
    results.append(Result(
        suite="h029",
        scenario="weak_result_operator_is_not_only_next_step",
        passed=pass_weak,
        error="" if pass_weak else f"weak callbacks bad: {weak_callbacks}",
        response_text=f"callbacks={weak_callbacks}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    fallback_rows = _markup_from_chat_buttons({"_buttons": []}, {"params": {}, "asked_questions": []}, "Что ищем?", "G-first-step")
    fallback_count = sum(len(row) for row in fallback_rows)
    pass_limit = fallback_count == 0
    results.append(Result(
        suite="h029",
        scenario="fallback_buttons_are_not_shown_in_normal_dialog",
        passed=pass_limit,
        error="" if pass_limit else f"fallback rows should be empty: {fallback_rows}",
        response_text=f"rows={fallback_rows}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    farewell = _phone_captured_farewell().lower()
    pass_farewell = (
        "спасибо" in farewell
        and "номер" in farewell
        and "оператор" in farewell
        and "свяжется" in farewell
        and "актуаль" in farewell
        and "mcp" not in farewell
        and "json" not in farewell
    )
    results.append(Result(
        suite="h029",
        scenario="phone_captured_has_human_farewell",
        passed=pass_farewell,
        error="" if pass_farewell else f"bad farewell: {_phone_captured_farewell()}",
        response_text=_phone_captured_farewell(),
        duration_ms=int((time.time() - started) * 1000),
    ))

    normalized_phone = _normalize_phone("+7 (900) 000-00-01")
    phone_meta = _phone_log_meta(normalized_phone)
    pass_phone_meta = (
        normalized_phone == "+79000000001"
        and phone_meta == {"phone_len": 11, "phone_last4": "0001"}
        and "+79000000001" not in json.dumps(phone_meta, ensure_ascii=False)
    )
    results.append(Result(
        suite="h029",
        scenario="phone_contact_normalized_and_log_is_safe",
        passed=pass_phone_meta,
        error="" if pass_phone_meta else f"phone={normalized_phone}, meta={phone_meta}",
        response_text=f"meta={phone_meta}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    details_prompt = _build_known_option_prompt(rich_opt, "Клиент нажал «Да, подробнее»")
    details_prompt_low = details_prompt.lower()
    pass_details_prompt = (
        "role:" in details_prompt_low
        and "situation:" in details_prompt_low
        and "user_action:" in details_prompt_low
        and "safe_facts:" in details_prompt_low
        and "client_intent:" in details_prompt_low
        and "client_purpose:" in details_prompt_low
        and "allowed_inferences:" in details_prompt_low
        and "missing_or_live_only:" in details_prompt_low
        and "forbidden_phrases_for_client:" in details_prompt_low
        and "response_shape:" in details_prompt_low
        and "output_json:" in details_prompt_low
        and "новый живой ответ" in details_prompt_low
        and "новый широкий поиск не нужен" in details_prompt_low
        and "оставить контакт" in details_prompt_low
        and "актуальные квартиры" in details_prompt_low
        and "не придумывай" in details_prompt_low
        and "запрещённые клиентские фразы" in details_prompt_low
        and "не удалось подтвердить" in details_prompt_low
        and "чтобы не выдумывать" in details_prompt_low
        and "доходность и ликвидность" in details_prompt_low
        and "по этому жк картина такая" in details_prompt_low
        and "по конкретным квартирам и брони лучше" in details_prompt_low
        and "комфорт-класс" in details_prompt_low
        and "не уводи ответ в инвестиции" in details_prompt_low
        and "какой аспект этого жк" in details_prompt_low
        and "конкретные планировки" in details_prompt_low
        and "для любого состава семьи" in details_prompt_low
    )
    results.append(Result(
        suite="h029",
        scenario="details_prompt_uses_llm_context_and_soft_contact",
        passed=pass_details_prompt,
        error="" if pass_details_prompt else f"bad details prompt: {details_prompt}",
        response_text=details_prompt,
        duration_ms=int((time.time() - started) * 1000),
    ))

    unsafe_class_text = (
        "Да, расскажу подробнее про ЖК «Лучи». Это комфорт-класс в районе Солнцево. "
        "Квартиры сдаются с отделкой, цена от 10.6 млн. Что разобрать подробнее?"
    )
    stripped_class = _strip_unsupported_complex_claims(unsafe_class_text, {"name": "ЖК «Лучи»", "location": "Солнцево"})
    pass_strip_class = "комфорт-класс" not in stripped_class.lower() and "цена" in stripped_class.lower()
    results.append(Result(
        suite="h029",
        scenario="unsupported_class_claim_is_removed_without_safe_fact",
        passed=pass_strip_class,
        error="" if pass_strip_class else f"unsupported class survived: {stripped_class}",
        response_text=stripped_class,
        duration_ms=int((time.time() - started) * 1000),
    ))

    prompt_with_class_as_developer = _build_known_option_prompt(
        {"name": "ЖК «Лучи»", "location": "Солнцево", "developer": "comfort", "price": "от 10.6 млн"},
        "Клиент выбрал этот вариант для жизни",
    ).lower()
    safe_facts_block = prompt_with_class_as_developer.split("allowed_inferences:", 1)[0]
    pass_developer_class_guard = '"developer"' not in safe_facts_block and "comfort" not in safe_facts_block
    results.append(Result(
        suite="h029",
        scenario="class_value_is_not_passed_as_developer_to_llm",
        passed=pass_developer_class_guard,
        error="" if pass_developer_class_guard else f"class leaked as developer: {prompt_with_class_as_developer}",
        response_text=prompt_with_class_as_developer,
        duration_ms=int((time.time() - started) * 1000),
    ))

    rejected_response = (
        "Нашла несколько вариантов:\n\n"
        "1. ЖК «Лучи» в Солнцево, от 10.6 млн рублей.\n\n"
        "2. ЖК «Южные Сады» в Южном Бутово, от 11.4 млн рублей.\n\n"
        "Какой ЖК хотите рассмотреть подробнее?"
    )
    stripped_rejected = _strip_rejected_options_from_response(rejected_response, {"rejected_option_names": ["ЖК «Лучи»"]})
    pass_strip_rejected = "лучи" not in stripped_rejected.lower() and "1. жк «южные сады»" in stripped_rejected.lower()
    results.append(Result(
        suite="h029",
        scenario="rejected_option_is_removed_from_next_visible_list",
        passed=pass_strip_rejected,
        error="" if pass_strip_rejected else f"rejected option survived: {stripped_rejected}",
        response_text=stripped_rejected,
        duration_ms=int((time.time() - started) * 1000),
    ))

    fixed_name = _fix_complex_name_artifacts("1. ЖК «ГК «Варшавские ворота»» в Чертаново Южное")
    handoff_name = _format_operator_handoff_for_option({"name": "Квартал Мит"}).lower()
    pass_name_artifacts = (
        "жк «гк" not in fixed_name.lower()
        and "гк «варшавские ворота»" in fixed_name.lower()
        and "по жк «квартал мит»" in handoff_name
        and "лучше не гадать" in handoff_name
        and "оператор посмотрит" in handoff_name
        and "по живому наличию" not in handoff_name
    )
    results.append(Result(
        suite="h029",
        scenario="complex_name_artifacts_are_normalized",
        passed=pass_name_artifacts,
        error="" if pass_name_artifacts else f"bad names: fixed={fixed_name}; handoff={handoff_name}",
        response_text=f"fixed={fixed_name}; handoff={handoff_name}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    early_live_cta = (
        "ЖК «Южные Сады» — с отделкой, от 11.4 млн. "
        "Наличие конкретных планировок и актуальные цены лучше проверить по свежим данным. "
        "Выбор планировок большой, можно подобрать подходящую планировку. "
        "Хотите сравнить этот вариант с чем-то еще или разобрать подробнее планировки и цены? "
        "Какой аспект этого ЖК вам было бы интересно разобрать подробнее?"
    )
    cleaned_early_live_cta = _soften_generic_selected_question(
        _soften_layout_overclaim(
            _strip_unrequested_live_data_cta(early_live_cta, "ищу двухкомнатную квартиру для семьи")
        )
    ).lower()
    pass_cta_timing = (
        "наличие конкретных планировок" not in cleaned_early_live_cta
        and "актуальные цены" not in cleaned_early_live_cta
        and "по свежим данным" not in cleaned_early_live_cta
        and "какой аспект" not in cleaned_early_live_cta
        and "выбор планировок" not in cleaned_early_live_cta
        and "подходящую планировку" not in cleaned_early_live_cta
        and "планировки и цены" not in cleaned_early_live_cta
        and "диапазон площадей" in cleaned_early_live_cta
        and "сравнить" in cleaned_early_live_cta
    )
    results.append(Result(
        suite="h029",
        scenario="selected_presenter_removes_early_live_cta_and_generic_question",
        passed=pass_cta_timing,
        error="" if pass_cta_timing else f"bad selected cta: {cleaned_early_live_cta}",
        response_text=cleaned_early_live_cta,
        duration_ms=int((time.time() - started) * 1000),
    ))

    chat_prompt_low = CHAT_SYSTEM_PROMPT.lower()
    pass_first_answer_guard = (
        "{{scenario_overlay}}" in chat_prompt_low
        and "{{facet_overlays}}" in chat_prompt_low
        and "json-контракт" in chat_prompt_low
        and "scenario overlay" in chat_prompt_low
        and "facet overlay" in chat_prompt_low
        and "основной prompt не хранит сценарную матрицу фактов" in chat_prompt_low
    )
    results.append(Result(
        suite="h029",
        scenario="chat_prompt_no_early_operator_and_no_unsupported_class",
        passed=pass_first_answer_guard,
        error="" if pass_first_answer_guard else "chat prompt lacks first-answer/operator/class guard",
        response_text=CHAT_SYSTEM_PROMPT,
        duration_ms=int((time.time() - started) * 1000),
    ))

    structured_visible_options = _visible_options_from_chat_meta(
        {"_visible_options": [
            # LLM иногда присылает 0-based idx. Имя должно быть главным ключом,
            # иначе похожие ЖК/сдвинутые индексы ломают выбор «1/2/3».
            {"idx": 0, "name": family_opt["name"]},
            {"idx": 1, "name": rich_opt["name"]},
            {"idx": 999, "name": "ЖК «Несуществующий»"},
        ]},
        [rich_opt, family_opt],
    )
    fallback_visible_options = _visible_options_from_chat_or_response(
        {"_visible_options": []},
        f"1. {family_opt['name']}\n2. {rich_opt['name']}",
        [rich_opt, family_opt],
    )
    pass_structured_visible = (
        [o.get("name") for o in structured_visible_options] == [family_opt["name"], rich_opt["name"]]
        and [o.get("visible_idx") for o in structured_visible_options] == [1, 2]
        and [o.get("name") for o in fallback_visible_options] == [family_opt["name"], rich_opt["name"]]
    )
    results.append(Result(
        suite="h029",
        scenario="chat_json_visible_options_drive_visible_order_with_text_fallback",
        passed=pass_structured_visible,
        error="" if pass_structured_visible else f"structured={structured_visible_options}; fallback={fallback_visible_options}",
        response_text=json.dumps({"structured": structured_visible_options, "fallback": fallback_visible_options}, ensure_ascii=False, default=str),
        duration_ms=int((time.time() - started) * 1000),
    ))

    negation_prompt_state = {
        "last_options": [rich_opt, family_opt, investment_2025_opt],
        "visible_options": [rich_opt, family_opt, investment_2025_opt],
        "selected_option": rich_opt,
        "last_bot_question": "Хотите оставить номер для связи?",
        "last_offer_type": "selected_option_details",
        "rejected_option_names": [rich_opt["name"]],
    }
    negation_prompt = _build_negation_response_prompt(
        intent="reject_selected_option",
        user_text="не этот, хочу дешевле",
        state=negation_prompt_state,
        meta={"confidence": 0.9, "reason": "client rejected selected option"},
    )
    negation_prompt_low = negation_prompt.lower()
    pass_negation_prompt = (
        "role:" in negation_prompt_low
        and "situation:" in negation_prompt_low
        and "negation_context:" in negation_prompt_low
        and "negation_intent" in negation_prompt_low
        and "intent_specific_rule" in negation_prompt_low
        and "reject_selected_option" in negation_prompt_low
        and "не продавай его снова" in negation_prompt_low
        and "last_options_except_selected" in negation_prompt_low
        and "global_rules:" in negation_prompt_low
        and "не проси номер" in negation_prompt_low
        and "не повторяй презентацию" in negation_prompt_low
        and "output_json:" in negation_prompt_low
        and "final_question" in negation_prompt_low
        and "каждый ответ должен содержать final_question" in negation_prompt_low
        and "buttons всегда []" in negation_prompt_low
        and "mcp" in negation_prompt_low
        and "json" in negation_prompt_low
    )
    results.append(Result(
        suite="h029",
        scenario="negation_prompt_uses_llm_contract_with_code_fallback",
        passed=pass_negation_prompt,
        error="" if pass_negation_prompt else f"bad negation prompt: {negation_prompt}",
        response_text=negation_prompt,
        duration_ms=int((time.time() - started) * 1000),
    ))

    known_option_prompt = _build_known_option_prompt(
        {
            "name": "ЖК «Лучи»",
            "location": "Солнцево",
            "price": "от 10.6 млн",
            "finishing": "с отделкой",
        },
        "расскажи подробнее",
    )
    known_option_prompt_low = known_option_prompt.lower()
    pass_known_option_prompt = (
        "final_question" in known_option_prompt_low
        and "каждый ответ должен содержать final_question" in known_option_prompt_low
        and "ровно один вопрос" in known_option_prompt_low
        and "inline-кнопки" in known_option_prompt_low
    )
    results.append(Result(
        suite="h029",
        scenario="known_option_prompt_requires_final_question_field",
        passed=pass_known_option_prompt,
        error="" if pass_known_option_prompt else f"bad known option prompt: {known_option_prompt}",
        response_text=known_option_prompt,
        duration_ms=int((time.time() - started) * 1000),
    ))

    chat_v1_text = (REPO / "prompts" / "chat_v1.txt").read_text(encoding="utf-8").lower()
    pass_chat_v1_final_question_contract = (
        "top-level поле `final_question`" in chat_v1_text
        and "каждый ответ должен содержать `final_question`" in chat_v1_text
        and "центр дорогой" in chat_v1_text
        and "в `final_question`" in chat_v1_text
        and "следующий шаг предлагай только в `final_question`" in chat_v1_text
    )
    results.append(Result(
        suite="h029",
        scenario="chat_v1_uses_single_final_question_contract_and_live_refine_intro",
        passed=pass_chat_v1_final_question_contract,
        error="" if pass_chat_v1_final_question_contract else "chat_v1 lacks simplified final_question/refine contract",
        response_text=chat_v1_text[:1600],
        duration_ms=int((time.time() - started) * 1000),
    ))

    planner_prompt_low = DIALOG_STATE_PLANNER_PROMPT.lower()
    pass_dialog_planner_prompt = (
        "dialog_action" in planner_prompt_low
        and "selected_option_action" in planner_prompt_low
        and "rejected_options_add" in planner_prompt_low
        and "visible_options_policy" in planner_prompt_low
        and "numeric_choice_policy" in planner_prompt_low
        and "mode" in planner_prompt_low
        and "search_action" in planner_prompt_low
        and "conversation" in planner_prompt_low
        and "не подходит, хочу ближе к метро" in planner_prompt_low
        and "selected_option_action=\"clear\"" in planner_prompt_low
        and "numeric_choice_policy=\"reject\"" in planner_prompt_low
        and "не придумывай max_price" in planner_prompt_low
        and "recommend_options" in planner_prompt_low
        and "conversation_answer" in planner_prompt_low
        and "consultation_answer" in planner_prompt_low
        and "conversation_followup" in planner_prompt_low
        and "что посоветуешь" in planner_prompt_low
        and "что важно для аренды" in planner_prompt_low
        and "как связаться с оператором" in planner_prompt_low
        and normalize_dialog_action("update_search") == "update_search"
        and normalize_dialog_action("recommend_options") == "recommend_options"
        and normalize_dialog_action("conversation_answer") == "conversation_answer"
        and normalize_dialog_action("consultation_answer") == "consultation_answer"
        and normalize_dialog_mode("", "conversation_answer") == "conversation"
        and normalize_intent("conversation_answer") == "conversation_answer"
        and normalize_intent("consultation_answer") == "consultation_answer"
        and normalize_dialog_action("bad") == "continue_from_memory"
    )
    results.append(Result(
        suite="h029",
        scenario="dialog_planner_prompt_has_state_contract",
        passed=pass_dialog_planner_prompt,
        error="" if pass_dialog_planner_prompt else "dialog planner prompt lacks state contract/examples",
        response_text=DIALOG_STATE_PLANNER_PROMPT,
        duration_ms=int((time.time() - started) * 1000),
    ))

    advice_options = [
        {
            "name": "Город-парк «Переделкино Ближнее»",
            "schools": "2 школы",
            "kindergartens": "7 детских садов",
            "parks": "парки; набережная",
            "clinics": "поликлиника",
            "yards": "спортивные площадки",
            "price_min": 9560000,
            "price_max": 35960000,
            "ready": "2028",
            "finishing": "с отделкой",
        },
        {
            "name": "ЖК «Люблинский парк»",
            "infrastructure": "инфраструктура для детей",
            "yards": "закрытые дворы и зоны отдыха",
            "price_min": 10910000,
            "price_max": 34040000,
            "ready": "2028",
            "finishing": "с отделкой",
        },
        {
            "name": "ЖК «Кузьминский лес»",
            "schools": "школы",
            "kindergartens": "детские сады",
            "parks": "парки; вода",
            "ready": "дом уже сдан",
            "finishing": "с отделкой",
            "price_min": 8880000,
            "price_max": 21790000,
        },
    ]
    advice_text = _render_stage_recommendation(advice_options, "family")
    advice_low = advice_text.lower()
    pass_advice = (
        "переделкино ближнее" in advice_low
        and "я бы сначала смотрела" in advice_low
        and "2 школы" in advice_text
        and "7 детских садов" in advice_text
        and "парки" in advice_low
        and "хотите" in advice_low
        and advice_text.count("?") == 1
        and "давайте сравним" not in advice_low
    )
    results.append(Result(
        suite="h029",
        scenario="family_advice_recommends_best_option_not_compare_loop",
        passed=pass_advice,
        error="" if pass_advice else f"bad advice text: {advice_text}",
        response_text=advice_text,
        duration_ms=int((time.time() - started) * 1000),
    ))

    operator_context_state = {
        "visible_options": advice_options,
        "last_options": advice_options,
        "params": {"purpose": "repeat_search"},
    }
    operator_context_text = _format_operator_handoff_for_context(operator_context_state, "все")
    operator_context_low = operator_context_text.lower()
    pass_operator_context = (
        "напишите номер" in operator_context_low
        and "оператор" in operator_context_low
        and "переделкино ближнее" in operator_context_low
        and "люблинский парк" in operator_context_low
        and "кузьминский лес" in operator_context_low
        and "первый, второй или третий" not in operator_context_low
        and "сценарий" not in operator_context_low
        and "repeat_search" not in operator_context_low
        and "последний вопрос" not in operator_context_low
    )
    results.append(Result(
        suite="h029",
        scenario="operator_without_selected_asks_phone_with_current_context",
        passed=pass_operator_context,
        error="" if pass_operator_context else f"bad operator context text: {operator_context_text}",
        response_text=operator_context_text,
        duration_ms=int((time.time() - started) * 1000),
    ))

    planner_state = {
        "params": {"rooms": "s", "max_price": 13_000_000},
        "selected_option": rich_opt,
        "visible_options": [rich_opt, family_opt],
        "last_options": [rich_opt, family_opt],
        "rejected_option_names": [],
        "numeric_choice_policy": "accept",
    }
    applied = _apply_dialog_plan_to_state(
        planner_state,
        {
            "dialog_action": "update_search",
            "params_delta": {"near_metro": True},
            "selected_option_action": "clear",
            "selected_option_name": None,
            "rejected_options_add": [rich_opt["name"], "ЖК «Несуществующий»"],
            "visible_options_policy": "clear",
            "numeric_choice_policy": "reject",
        },
        user_text="не подходит, хочу ближе к метро",
    )
    pass_apply_dialog_plan = (
        planner_state.get("selected_option") is None
        and rich_opt["name"] in planner_state.get("rejected_option_names", [])
        and "ЖК «Несуществующий»" not in planner_state.get("rejected_option_names", [])
        and planner_state.get("visible_options") == []
        and planner_state.get("numeric_choice_policy") == "reject"
        and planner_state.get("params", {}).get("near_metro") is True
        and "selected_option_clear" in applied.get("applied", [])
    )
    results.append(Result(
        suite="h029",
        scenario="dialog_plan_safely_clears_selected_and_rejects_known_option",
        passed=pass_apply_dialog_plan,
        error="" if pass_apply_dialog_plan else f"bad planner apply: state={planner_state}; applied={applied}",
        response_text=json.dumps({"state": planner_state, "applied": applied}, ensure_ascii=False),
        duration_ms=int((time.time() - started) * 1000),
    ))

    numeric_reject_state = {
        "last_options": [rich_opt, family_opt],
        "visible_options": [rich_opt, family_opt],
        "numeric_choice_policy": "reject",
    }
    numeric_reject_intent = _resolve_dialog_intent("1", numeric_reject_state)
    pass_numeric_policy = numeric_reject_intent.get("intent") == "followup_classifier"
    results.append(Result(
        suite="h029",
        scenario="numeric_choice_policy_reject_blocks_stale_numeric_selection",
        passed=pass_numeric_policy,
        error="" if pass_numeric_policy else f"numeric policy ignored: {numeric_reject_intent}",
        response_text=json.dumps(numeric_reject_intent, ensure_ascii=False, default=str),
        duration_ms=int((time.time() - started) * 1000),
    ))

    numeric_plan_state = {
        "last_options": [rich_opt, family_opt],
        "visible_options": [rich_opt, family_opt],
        "numeric_choice_policy": "reject",
    }
    numeric_plan_applied = _apply_dialog_plan_to_state(
        numeric_plan_state,
        {
            "dialog_action": "select_option",
            "selected_option_action": "set",
            "selected_option_name": rich_opt["name"],
            "numeric_choice_policy": "accept",
        },
        user_text="1",
    )
    pass_numeric_plan_guard = (
        numeric_plan_state.get("selected_option") is None
        and numeric_plan_state.get("numeric_choice_policy") == "reject"
        and "selected_option_set_blocked_by_numeric_policy" in numeric_plan_applied.get("applied", [])
        and "numeric_choice_accept_blocked_by_numeric_policy" in numeric_plan_applied.get("applied", [])
    )
    results.append(Result(
        suite="h029",
        scenario="dialog_plan_cannot_override_rejected_numeric_choice_policy",
        passed=pass_numeric_plan_guard,
        error="" if pass_numeric_plan_guard else f"numeric plan guard failed: state={numeric_plan_state}; applied={numeric_plan_applied}",
        response_text=json.dumps({"state": numeric_plan_state, "applied": numeric_plan_applied}, ensure_ascii=False, default=str),
        duration_ms=int((time.time() - started) * 1000),
    ))

    valid_visible_numeric_state = {
        "last_options": [rich_opt, family_opt],
        "visible_options": [rich_opt, family_opt],
        "numeric_choice_policy": "accept",
    }
    valid_visible_applied = _apply_dialog_plan_to_state(
        valid_visible_numeric_state,
        {
            "dialog_action": "update_search",
            "visible_options_policy": "rebuild",
            "numeric_choice_policy": "reject",
            "rejected_options_add": [rich_opt["name"], family_opt["name"]],
        },
        user_text="покажи другие",
    )
    pass_valid_visible_guard = (
        valid_visible_numeric_state.get("numeric_choice_policy") == "accept"
        and valid_visible_numeric_state.get("visible_options") == [rich_opt, family_opt]
        and not valid_visible_numeric_state.get("rejected_option_names")
        and "numeric_choice_reject_ignored_visible_list_still_valid" in valid_visible_applied.get("applied", [])
    )
    results.append(Result(
        suite="h029",
        scenario="dialog_plan_does_not_break_valid_visible_numeric_choice",
        passed=pass_valid_visible_guard,
        error="" if pass_valid_visible_guard else f"valid visible guard failed: state={valid_visible_numeric_state}; applied={valid_visible_applied}",
        response_text=json.dumps({"state": valid_visible_numeric_state, "applied": valid_visible_applied}, ensure_ascii=False, default=str),
        duration_ms=int((time.time() - started) * 1000),
    ))

    planner_payload = _dialog_planner_state_payload({
        "params": {"purpose": "investment"},
        "selected_option": {**rich_opt, "developer": "comfort"},
        "visible_options": [rich_opt],
        "last_options": [rich_opt, family_opt],
        "rejected_option_names": [family_opt["name"]],
        "last_bot_question": "Какой ЖК?",
        "last_offer_type": "choose_option",
    })
    payload_json = json.dumps(planner_payload, ensure_ascii=False).lower()
    pass_planner_payload = (
        "selected_option" in planner_payload
        and "visible_options" in planner_payload
        and "last_options" in planner_payload
        and "rejected_option_names" in planner_payload
        and "comfort" not in payload_json
    )
    results.append(Result(
        suite="h029",
        scenario="dialog_planner_payload_is_safe_and_structured",
        passed=pass_planner_payload,
        error="" if pass_planner_payload else f"bad planner payload: {planner_payload}",
        response_text=json.dumps(planner_payload, ensure_ascii=False, default=str),
        duration_ms=int((time.time() - started) * 1000),
    ))

    selected_rows = _markup_from_chat_buttons(
        {"_buttons": []},
        {"params": {}, "asked_questions": [], "last_options": [rich_opt], "selected_option": rich_opt},
        card,
        "selected-option",
    )
    pass_contract = selected_rows == []
    results.append(Result(
        suite="h029",
        scenario="selected_context_does_not_render_inline_buttons",
        passed=pass_contract,
        error="" if pass_contract else f"selected rows should be empty: {selected_rows}",
        response_text=f"rows={selected_rows}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    family_prompt = CHAT_SYSTEM_PROMPT.lower()
    pass_family_prompt = (
        "{{scenario_overlay}}" in family_prompt
        and "{{facet_overlays}}" in family_prompt
        and "основной prompt не хранит сценарную матрицу фактов" in family_prompt
        and "scenario overlay" in family_prompt
        and "facet overlay" in family_prompt
        and "purpose" in family_prompt
    )
    results.append(Result(
        suite="h029",
        scenario="family_personalization_is_in_chat_prompt",
        passed=pass_family_prompt,
        error="" if pass_family_prompt else "family/purpose constraints missing in chat prompt",
        response_text="family prompt ok" if pass_family_prompt else CHAT_SYSTEM_PROMPT[:1000],
        duration_ms=int((time.time() - started) * 1000),
    ))

    dense_list = "Нашла варианты:\n1. ЖК «А» — от 7 млн\n2. ЖК «Б» — от 8 млн\n3. ЖК «В» — от 9 млн\n\nКакой выбрать?"
    spaced = _prepare_response_text(dense_list)
    pass_spacing = "1. ЖК" in spaced and "\n\n2. ЖК" in spaced and "\n\n3. ЖК" in spaced
    results.append(Result(
        suite="h029",
        scenario="numbered_list_has_blank_lines_between_items",
        passed=pass_spacing,
        error="" if pass_spacing else f"spacing failed: {spaced!r}",
        response_text=spaced,
        duration_ms=int((time.time() - started) * 1000),
    ))

    sent_rows = [[
        {"text": "Да, подробнее", "callback_data": "action:details:1"},
        {"text": "Сравнить", "callback_data": "action:show_near"},
    ]]
    preview = _button_log_preview(sent_rows)
    pressed_text = _callback_button_text(sent_rows, "action:details:1")
    pass_button_log = (
        preview == [[
            {"text": "Да, подробнее", "callback_data": "action:details:1"},
            {"text": "Сравнить", "callback_data": "action:show_near"},
        ]]
        and pressed_text == "Да, подробнее"
    )
    results.append(Result(
        suite="h029",
        scenario="button_log_preview_and_pressed_text_are_complete",
        passed=pass_button_log,
        error="" if pass_button_log else f"preview={preview}, pressed={pressed_text!r}",
        response_text=f"preview={preview}; pressed={pressed_text}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    history_event = {
        "kind": "user_message",
        "uid": 42,
        "ts": "2026-07-03T12:00:00.000Z",
        "user_text": "мне нужна квартира рядом с парком",
        "dialog_intent": "main_search",
        "dialog_plan": {"dialog_action": "new_search"},
        "search_response": json.dumps({
            "facts": [{"name": "ЖК «Лучи»", "parks": "Мещерский парк"}],
            "missing": [],
        }, ensure_ascii=False),
        "response_text": "Подобрала вариант рядом с парком.",
        "buttons": [[{"text": "Да, подробнее", "callback_data": "action:details:1"}]],
        "cost": {"total_usd": 0.01},
    }
    history_text = _format_history_event(history_event, 1)
    pass_history_format = (
        "Вы: мне нужна квартира рядом с парком" in history_text
        and "Бот: Подобрала вариант рядом с парком." in history_text
        and "intent: main_search" in history_text
        and "plan:" in history_text
        and "MCP/search_response:" in history_text
        and "ЖК «Лучи»" in history_text
        and "Мещерский парк" in history_text
        and "buttons:" in history_text
        and "cost:" in history_text
        and "ЖК «Лучи»" in _history_search_preview(history_event)
    )
    results.append(Result(
        suite="h029",
        scenario="history_event_shows_user_bot_search_plan_buttons_cost",
        passed=pass_history_format,
        error="" if pass_history_format else f"bad history text: {history_text}",
        response_text=history_text,
        duration_ms=int((time.time() - started) * 1000),
    ))

    long_history = "A" * 3900 + "\n\n---\n\n" + "B" * 3900
    chunks = _telegram_chunks(long_history, limit=3800)
    pass_history_chunks = len(chunks) >= 2 and all(len(chunk) <= 3800 for chunk in chunks)
    results.append(Result(
        suite="h029",
        scenario="history_output_is_chunked_for_telegram",
        passed=pass_history_chunks,
        error="" if pass_history_chunks else f"bad chunks: {[len(c) for c in chunks]}",
        response_text=f"chunks={[len(c) for c in chunks]}",
        duration_ms=int((time.time() - started) * 1000),
    ))

    pass_history_command_source = (
        "async def history_command" in bot_source
        and 'CommandHandler("history", history_command)' in bot_source
        and 'CommandHandler("hisotry", history_command)' in bot_source
        and "/history — последние ответы и MCP/search trace" in bot_source
        and "/hisotry — то же самое" in bot_source
    )
    results.append(Result(
        suite="h029",
        scenario="history_and_hisotry_commands_are_registered",
        passed=pass_history_command_source,
        error="" if pass_history_command_source else "history/hisotry command handler or help text missing",
        response_text="history command source ok" if pass_history_command_source else bot_source[bot_source.find("async def history_command") - 500: bot_source.find("async def history_command") + 1000],
        duration_ms=int((time.time() - started) * 1000),
    ))

    return results


def _run_deploy_smoke_test() -> Result:
    """H024: live deploy-smoke — бот должен быть запущен и быть новее кода/промптов."""
    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "nmbot_deploy_smoke.py")],
        cwd=str(REPO),
        text=True,
        capture_output=True,
    )
    output = (proc.stdout + proc.stderr).strip()
    return Result(
        suite="deploy",
        scenario="live_bot_process_fresh_vs_code",
        passed=proc.returncode == 0,
        error="" if proc.returncode == 0 else output,
        response_text=output,
        duration_ms=int((time.time() - started) * 1000),
    )


def _run_non_text_unit_tests() -> list[Result]:
    """Audit item 5: non-text input не должен превращаться в silent return."""
    started = time.time()
    results: list[Result] = []

    class Msg:
        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def add_result(scenario: str, passed: bool, response_text: str = "", error: str = "") -> None:
        results.append(Result(
            suite="non_text",
            scenario=scenario,
            passed=passed,
            error="" if passed else error,
            response_text=response_text,
            duration_ms=int((time.time() - started) * 1000),
        ))

    fallback = _non_text_fallback_response("photo")
    add_result(
        "non_text_message_has_safe_fallback",
        "пока я понимаю только текстовые запросы" in fallback.lower()
        and "район" in fallback.lower()
        and "бюджет" in fallback.lower()
        and "жк" in fallback.lower(),
        response_text=fallback,
        error=f"bad fallback: {fallback}",
    )

    type_cases = {
        "photo": Msg(photo=[object()]),
        "voice": Msg(voice=object()),
        "document": Msg(document=object()),
        "sticker": Msg(sticker=object()),
        "location": Msg(location=object()),
        "unknown": Msg(),
    }
    detected = {expected: _non_text_message_type(message) for expected, message in type_cases.items()}
    add_result(
        "non_text_message_type_is_logged",
        detected == {key: key for key in type_cases},
        response_text=json.dumps(detected, ensure_ascii=False),
        error=f"bad message type detection: {detected}",
    )

    contact_response = _non_text_fallback_response("contact")
    add_result(
        "contact_fallback_mentions_phone_format",
        "контакт" in contact_response.lower()
        and "+7" in contact_response
        and "текст" in contact_response.lower(),
        response_text=contact_response,
        error=f"bad contact fallback: {contact_response}",
    )

    return results


async def _run_control_dialog_test(
    client: OvermindClient,
    chat_max_tokens: int,
    scenario_def: ControlDialogScenario | None = None,
) -> Result:
    """H025/H026: обязательный контрольный диалог перед отдачей live-бота пользователю.

    Имитируем новый /start как пустое состояние, затем реальный пользовательский
    запрос. Проверяем не только отсутствие exception, а сам смысл: ответ должен
    быть не индикатором, не технической ошибкой, без старого бюджета 5 млн,
    grounded в MCP/search_response и с ожидаемыми маркерами сценария.
    """
    started = time.time()
    scenario_def = scenario_def or CONTROL_DIALOG_SCENARIOS[0]
    scenario = scenario_def.name
    checks: list[dict] = []

    def add_check(name: str, ok: bool, msg: str = "") -> None:
        checks.append({"name": name, "passed": ok, "msg": msg})

    try:
        # /start должен давать пустые params. Не используем Telegram API — проверяем
        # тот же client.ask, на котором построен live handler.
        response, new_params, search_meta, chat_meta = await client.ask(
            query=scenario_def.query,
            search_model="google/gemini-3.1-flash-lite-preview",
            chat_model="google/gemini-2.5-flash",
            use_mcp=True,
            params={},
        )
        search_text = search_meta.get("_response_text", "") if isinstance(search_meta, dict) else ""
        txt = response.strip()
        low = txt.lower()

        system_checks, system_meta = _system_observability_checks(
            query=scenario_def.query,
            response_text=txt,
            new_params=new_params,
            search_meta=search_meta,
            chat_meta=chat_meta,
            expected_markers=scenario_def.expected_markers,
            expected_any_markers=scenario_def.expected_any_markers,
        )
        checks.extend(system_checks)

        add_check("not_empty_final_response", len(txt) >= 80, f"len={len(txt)}")
        add_check("not_only_search_indicator", "осуществляю поиск" not in low, txt[:120])
        technical_markers = ["choices", "openrouter", "traceback", "exception", "ошибка при обращении"]
        leaked = [m for m in technical_markers if m in low]
        add_check("no_technical_error_leak", not leaked, f"leaked={leaked}")
        add_check("no_stale_5m_budget", not _has_stale_5m_budget(txt), txt[:180])
        for marker in scenario_def.expected_markers:
            add_check(f"mentions_required_marker:{marker}", marker in low, txt[:180])
        if scenario_def.expected_any_markers:
            add_check(
                "mentions_any_expected_context",
                any(m in low for m in scenario_def.expected_any_markers),
                f"expected_any={scenario_def.expected_any_markers}; text={txt[:180]}",
            )
        useful_markers = ["жк", "цена", "руб", "млн"]
        add_check("has_useful_realty_content", any(m in low for m in useful_markers), txt[:180])
        for c in _ux_check_response(txt, search_text):
            checks.append(c)

        passed = all(c["passed"] for c in checks)
        result = Result(
            suite="dialog",
            scenario=scenario,
            passed=passed,
            checks=checks,
            error="" if passed else "control dialog checks failed",
            duration_ms=int((time.time() - started) * 1000),
            response_text=txt,
            dialog_intent="new_search",
            system_meta=system_meta,
        )
        _append_dialog_review(scenario, result, search_text)
        return result
    except Exception as e:
        result = Result(
            suite="dialog",
            scenario=scenario,
            passed=False,
            checks=[{"name": "exception", "passed": False, "msg": f"{type(e).__name__}: {e}"}],
            error=traceback.format_exc(limit=3),
            duration_ms=int((time.time() - started) * 1000),
            response_text="",
        )
        _append_dialog_review(scenario, result, "")
        return result


async def _run_control_dialog_suite(client: OvermindClient, chat_max_tokens: int) -> list[Result]:
    results: list[Result] = []
    for scenario_def in CONTROL_DIALOG_SCENARIOS:
        results.append(await _run_control_dialog_test(client, chat_max_tokens, scenario_def))
    return results


def _run_required_deploy_gate() -> Result:
    """Deploy-smoke тоже входит в общий gate: нельзя отдавать не тот/старый процесс."""
    return _run_deploy_smoke_test()
def main() -> None:
    p = argparse.ArgumentParser(description="nmbot test agent — codex + H016 + golden")
    p.add_argument("--suite", default="all", choices=["all", "codex", "h016", "golden", "h021", "h023", "h024", "h026", "h028", "h029", "ux_e2e", "non_text", "deploy", "dialog"])
    p.add_argument("--json", action="store_true", help="JSON-режим для CI")
    p.add_argument("--chat-max-tokens", type=int, default=10000)
    args = p.parse_args()
    rc = asyncio.run(_main(args.suite, args.json, args.chat_max_tokens))
    sys.exit(rc)


if __name__ == "__main__":
    main()
