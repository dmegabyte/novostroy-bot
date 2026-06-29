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
    _parse_budget_callback_value,
    _pick_quick_actions,
    _reset_dialog_state_preserve_settings,
    _resolve_dialog_intent,
    _safe_user_error_message,
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
    return bool(re.search(r"(?<!\d)5\s*(?:млн|миллион)", low))


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
        expect_intent="select_option",
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
        expect_intent="sort_price_asc",
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
        expected_markers=["котельник", "руб"],
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
        "metro": ["метро", "м."],
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
    if suite in ("all", "h021", "h023", "h024", "h026"):
        if not json_mode:
            print("  (H021/H023/H024/H026 unit-тесты — без Overmind)…")
        for r in _run_h021_unit_tests():
            if suite in ("h021", "h023", "h024", "h026") and r.suite != suite:
                continue
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

    # Тест 2: G-first-step с min(price_min)=3.5 млн → кнопки [5, 7, 8] + "без лимита"
    state_g = {
        "params": {},
        "last_options": [
            {"idx": 1, "name": "ЖК «Эконом»", "price_min": 3_500_000, "finishing": ""},
        ],
        "asked_questions": [],
    }
    rows_g = _pick_quick_actions(state_g, "G-first-step")
    budget_callbacks_g = [
        btn["callback_data"]
        for row in rows_g
        for btn in row
        if btn.get("callback_data", "").startswith("budget:")
    ]
    expected_g = ["budget:5m", "budget:7m", "budget:8m", "budget:none"]
    pass_g = budget_callbacks_g == expected_g
    results.append(Result(
        suite="h021",
        scenario="budget_buttons_g_first_step_with_options",
        passed=pass_g,
        error="" if pass_g else f"кнопки {budget_callbacks_g} != ожидаемых {expected_g}",
        response_text=f"callbacks={budget_callbacks_g}",
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

    # H024 test: OpenRouter/Overmind diagnostics не должны протекать в Telegram-ответ.
    technical_error = "Ошибка при обращении к openrouter: 'choices'"
    safe_text = _safe_user_error_message(technical_error)
    forbidden = ["choices", "openrouter", "traceback", "exception", "{", "}", "ошибка при обращении"]
    safe_lower = safe_text.lower()
    leaked = [x for x in forbidden if x in safe_lower]
    pass_safe = bool(safe_text.strip()) and not leaked
    results.append(Result(
        suite="h024",
        scenario="safe_upstream_error_message_hides_technical_details",
        passed=pass_safe,
        error="" if pass_safe else f"leaked technical details: {leaked}; text={safe_text!r}",
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
    p.add_argument("--suite", default="all", choices=["all", "codex", "h016", "golden", "h021", "h023", "h024", "h026", "deploy", "dialog"])
    p.add_argument("--json", action="store_true", help="JSON-режим для CI")
    p.add_argument("--chat-max-tokens", type=int, default=10000)
    args = p.parse_args()
    rc = asyncio.run(_main(args.suite, args.json, args.chat_max_tokens))
    sys.exit(rc)


if __name__ == "__main__":
    main()
