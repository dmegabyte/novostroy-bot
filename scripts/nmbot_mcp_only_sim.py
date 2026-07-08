#!/usr/bin/env python3
"""MCP-only dialog simulator for nmbot UX hypotheses.

Purpose: quickly see how the proposed MCP-only flow looks without touching the
live Telegram bot and without making extra MCP/LLM calls for follow-up turns.

Usage:
  python3 scripts/nmbot_mcp_only_sim.py
  python3 scripts/nmbot_mcp_only_sim.py --turn "1" --turn "расскажи подробнее" --turn "можно бронь?"
  python3 scripts/nmbot_mcp_only_sim.py --search-json /tmp/search_response.json --turn "ЖК Лучи" --turn "что по нему?"
  python3 scripts/nmbot_mcp_only_sim.py --mode new-arch
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from chat_tester_bot import (  # noqa: E402
    _extract_options,
    _format_operator_handoff_for_option,
    _format_options_summary_response,
    _format_option_response,
    _prepare_response_text,
    _resolve_dialog_intent,
    _visible_options_from_response,
)


SAMPLE_SEARCH_RESPONSE: dict[str, Any] = {
    "facts": [
        {
            "name": "ЖК «Лучи»",
            "location": "Солнцево",
            "price_range": "от 10 591 869 до 31 582 642 руб.",
            "finishing": "с отделкой",
            "metro": "информация уточняется",
            "area": "от 22.5 до 86.5 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_luchi",
            "developer": "информация уточняется",
        },
        {
            "name": "Бусиновский парк",
            "location": "Западное Дегунино",
            "price_range": "от 12 103 290 до 36 645 507 руб.",
            "finishing": "с отделкой",
            "metro": "информация уточняется",
            "area": "от 20 до 89.3 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jiloy_kompleks_businovskiy_park",
            "developer": "информация уточняется",
        },
        {
            "name": "ЖК «Русич Кантемировский»",
            "location": "Царицыно",
            "price_range": "от 10 822 140 до 25 533 402 руб.",
            "finishing": "без отделки",
            "metro": "информация уточняется",
            "area": "информация уточняется",
            "ready": "2026 г., 3 квартал",
            "link": "jk_kavkazskiy_bulvar_512",
            "developer": "информация уточняется",
        },
    ],
    "near": [
        {
            "name": "ЖК «Южные Сады»",
            "location": "Южное Бутово",
            "price_range": "от 11 399 922 до 37 921 655 руб.",
            "finishing": "с отделкой",
            "why_close": "отличие: находится на расстоянии 6 км от МКАД",
            "metro": "информация уточняется",
            "area": "от 21.8 до 187.8 м²",
            "ready": "2027 г., 2 квартал",
            "link": "jk_yujnye_sady",
        }
    ],
    "missing": (
        "Не удалось подтвердить точное наличие студий в продаже в режиме реального времени, "
        "а также близость к метро для каждого корпуса."
    ),
    "params": {"rooms": "s", "district": "msk", "purpose": "investment"},
}


def _load_search_response(path: str | None) -> dict[str, Any]:
    if not path:
        return SAMPLE_SEARCH_RESPONSE
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _extract_options_from_structured(search_response: dict[str, Any]) -> list[dict[str, Any]]:
    # Reuse production parser so field normalization matches the bot.
    return _extract_options(json.dumps(search_response, ensure_ascii=False))


def _explain_from_saved_mcp(option: dict[str, Any], search_response: dict[str, Any]) -> str:
    name = option.get("name") or "этот ЖК"
    bits: list[str] = []
    if option.get("location"):
        bits.append(f"локация — {option['location']}")
    if option.get("price"):
        bits.append(f"стоимость — {option['price']}")
    if option.get("area") and "уточняется" not in str(option.get("area")).lower():
        bits.append(f"площади — {option['area']}")
    if option.get("finishing"):
        bits.append(f"отделка — {option['finishing']}")
    if option.get("ready"):
        bits.append(f"срок — {option['ready']}")
    if option.get("why_close"):
        bits.append(str(option["why_close"]))

    intro = f"По {name} картина такая." if bits else f"По {name} есть короткая карточка, от которой можно оттолкнуться."
    def _sentence_from_bits(items: list[str]) -> str:
        if not items:
            return ""
        text = "; ".join(items).strip().rstrip(".")
        return text[:1].upper() + text[1:] + "."

    main_facts = _sentence_from_bits(bits[:3])
    extra_facts = _sentence_from_bits(bits[3:])
    benefit = ""
    if "отдел" in str(option.get("finishing") or "").lower() and "без отдел" not in str(option.get("finishing") or "").lower():
        benefit = "Отделка снижает объём ремонта на старте."
    elif option.get("price"):
        benefit = "По цене уже понятен бюджет входа, а дальше важно выбрать конкретную квартиру."

    missing = str(search_response.get("missing") or "").strip()
    missing_sentence = (
        "По конкретным квартирам, корпусам и брони лучше отдельно посмотреть актуальные варианты."
        if missing else ""
    )
    blocks = [intro, main_facts, extra_facts, benefit, missing_sentence, "Хотите сравнить этот ЖК с другими вариантами или проверить актуальное наличие?"]
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _format_no_results_area_response(search_response: dict[str, Any]) -> str:
    params = search_response.get("params") or {}
    area = params.get("district") or params.get("location") or "этому району"
    area_text = str(area).strip() or "этому району"
    return (
        f"По {area_text} сейчас не вижу актуальных новостроек от застройщика в переданных данных. "
        "Могу посмотреть близкие районы или варианты поблизости.\n\n"
        "Показать новостройки рядом?"
    )


def _sim_journal_paths() -> tuple[Path, Path]:
    date = datetime.now(timezone.utc).astimezone().date().isoformat()
    log_dir = REPO / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"sim_journal-{date}.jsonl", log_dir / f"sim_journal-{date}.md"


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_options": state.get("last_options") or [],
        "visible_options": state.get("visible_options") or [],
        "selected_option": state.get("selected_option"),
        "params": state.get("params") or {},
        "last_answer_kind": state.get("last_answer_kind"),
        "last_offer_type": state.get("last_offer_type"),
    }


def _expected_from_diagnostic(item: dict[str, Any]) -> str:
    routing = str(item.get("routing") or "")
    problem = str(item.get("problem") or "").lower()
    if "no_results_area_expansion" in problem:
        return "Reply with nearby-expansion wording, without secondary-market advice."
    if "entity_type_mismatch" in problem:
        return "Keep mortgage memory isolated from ЖК selection flows."
    if "operator_live_check_executor_gap" in problem:
        return "Route the request to operator handoff."
    if "selected_complex_should_progress_to_operator" in problem:
        return "After a concrete ЖК is selected and the user shows interest, progress to operator handoff instead of another open-ended clarification."
    if "fresh_expand_more_options" in problem:
        return "Trigger a fresh MCP search, exclude already shown ЖК, and present new similar options."
    if "non_mcp_fact_leak" in problem:
        return "Stay fully grounded in MCP/search JSON and avoid invented facts."
    if "compare" in problem:
        return "Use compare_others on the saved options list."
    if "budget" in problem:
        return "Filter saved options locally before launching a new search."
    if "purpose" in problem or "семейн" in problem:
        return "Update purpose in state without clearing saved options."
    if item.get("status") == "ok":
        if routing == "select_option":
            return "Select the saved option and keep it available for follow-ups."
        if routing == "explain_selected_option":
            return "Explain the selected option using only saved MCP facts."
        if routing == "compare_others":
            return "Compare the saved options and keep the selection flow intact."
        if routing == "operator_for_selected":
            return "Hand off to an operator while preserving the selected option context."
        if routing == "new_search":
            return "Trigger a fresh MCP search with updated parameters."
        return f"Route should be {routing or 'stable'} and keep state coherent."
    if item.get("status") == "watch":
        return "Consider adding a deterministic branch before the generic classifier."
    return "Apply the specific deterministic fix described in the patch hint."


def _actual_from_diagnostic(item: dict[str, Any]) -> str:
    bot_text = str(item.get("bot_text") or "").strip()
    routed = str(item.get("routing") or "")
    status = str(item.get("status") or "")
    if bot_text:
        snippet = bot_text.replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        return f"routing={routed}; status={status}; bot_text={snippet}"
    return f"routing={routed}; status={status}"


def _canonical_turn_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn": item.get("turn"),
        "routing": item.get("routing"),
        "status": item.get("status"),
        "expected": _expected_from_diagnostic(item),
        "actual": _actual_from_diagnostic(item),
        "mismatch": item.get("problem") or "",
        "patch": {
            "where": item.get("fix_location") or "",
            "hint": item.get("patch_hint") or "",
        },
        "acceptance": item.get("acceptance") or "status=ok",
    }


def _run_summary_lines(turns: list[dict[str, Any]]) -> list[str]:
    if not turns:
        return ["No turns recorded."]

    status_counts: dict[str, int] = {}
    patch_hints: list[str] = []
    for item in turns:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        patch = item.get("patch") or {}
        hint = str(patch.get("hint") or "").strip().lstrip("- ").strip()
        if status == "needs_patch" and hint:
            patch_hints.append(hint)

    summary = [
        f"turns={len(turns)}",
        f"ok={status_counts.get('ok', 0)}",
        f"needs_patch={status_counts.get('needs_patch', 0)}",
        f"watch={status_counts.get('watch', 0)}",
    ]
    lines = ["; ".join(summary)]
    if patch_hints:
        lines.append("Top patch hints:")
        for hint in dict.fromkeys(patch_hints[:3]):
            clean_hint = re.sub(r"^\s*[-•–—]+\s*", "", str(hint).strip())
            if clean_hint:
                lines.append(f"- {clean_hint}")
    return lines


def _diagnose_sim_turn(turn: str, intent: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Describe what the simulator found so one later patch can be assembled from the journal."""
    t = turn.lower().replace("ё", "е")
    kind = intent.get("intent")
    base: dict[str, Any] = {
        "turn": turn,
        "routing": kind,
        "status": "ok",
        "problem": "",
        "fix_location": "",
        "patch_hint": "",
        "acceptance": "route stays deterministic and state stays coherent",
    }

    options = state.get("last_options") or state.get("visible_options") or []
    has_mortgage_option = any(
        str((option.get("raw") or {}).get("entity_type") or "").lower() == "mortgage_program"
        or "ипотек" in str(option.get("name") or "").lower()
        or "банк" in str(option.get("name") or "").lower()
        for option in options
        if isinstance(option, dict)
    )
    asks_for_project_or_selection = bool(
        re.search(r"\bжк\b|новый московский|проект|комплекс|продолжить\s+подбор|подбор", t)
    )

    if has_mortgage_option and asks_for_project_or_selection:
        base.update({
            "status": "needs_patch",
            "problem": "entity_type_mismatch: в last_options лежит ипотечная программа, но downstream использует этот список как варианты ЖК/подбора.",
            "fix_location": "state contract: last_options writer + dialog_plan apply + _resolve_dialog_intent",
            "patch_hint": "Разделить memory slots: realty_options/complex_options отдельно от mortgage_options; при project_name/new_search очищать или изолировать mortgage_options и не строить choose-option вопрос по ипотеке.",
            "acceptance": "mortgage programs never appear as selectable ЖК in chooser flows",
        })
        return base

    asks_for_operator_contact = bool(
        re.search(r"позвон|созвон|связат|связь|оператор|менеджер|обсудить\s+детал|номер|контакт", t)
    )
    has_realty_context = bool(state.get("selected_option") or options or state.get("last_search_response"))
    if asks_for_operator_contact and has_realty_context and kind not in {"operator_for_selected", "operator_contact_accept"}:
        base.update({
            "status": "needs_patch",
            "problem": "operator_live_check_executor_gap: пользователь просит контакт/звонок по объекту, но routing не ведёт в операторский handoff.",
            "fix_location": "scripts/chat_tester_bot.py: dialog_plan executor around followup_intent/dialog_action handling",
            "patch_hint": "Смапить dialog_action=operator_live_check или contact request phrases в operator_for_selected/operator_contact_request; если selected_option пустой, выбрать единственный last_options[0] или создать selected_project из текущего search context.",
            "acceptance": "operator request results in proper handoff text / state",
        })
        return base

    selected = state.get("selected_option")
    selected_name = str((selected or {}).get("name") or "").strip() if isinstance(selected, dict) else ""
    shows_handoff_readiness = bool(
        selected_name
        and re.search(r"интерес|подходит|что\s+дальше|дальше|хочу\s+посмотреть|готов|беру|устраивает", t)
    )
    if shows_handoff_readiness and kind not in {"operator_for_selected", "operator_contact_accept"}:
        base.update({
            "status": "needs_patch",
            "problem": "selected_complex_should_progress_to_operator: клиент уже выбрал конкретный ЖК и показывает интерес/готовность, но routing продолжает уточнять или уходит в classifier вместо операторской воронки.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent + selected-option CTA policy",
            "patch_hint": "Добавить handoff_readiness_score: выбранный ЖК + показанная карточка + сигнал интереса/что дальше => operator_for_selected/operator_contact_accept; не задавать бесконечные вопросы про цену/срок.",
            "acceptance": "selected ЖК + interest/what-next turn routes to operator handoff or asks for phone, while first search still avoids early operator",
        })
        return base

    wants_more_similar = bool(re.search(r"(похож(ие|и|их)?\s+вариант|ещё\s+вариант|еще\s+вариант|другие\s+вариант|альтернатив)", t))
    if wants_more_similar and kind != "expand_more_options":
        base.update({
            "status": "needs_patch",
            "problem": "fresh_expand_more_options: пользователь просит ещё похожие варианты, но routing не делает свежий MCP search с исключением уже показанных ЖК.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent + followup handler for expand_more_options",
            "patch_hint": "Добавить отдельный intent expand_more_options; в проде вызвать fresh MCP search и отфильтровать visible/last options из результата.",
            "acceptance": "similar-more turns trigger a fresh search and do not repeat already shown ЖК",
        })
        return base

    if kind != "followup_classifier":
        if kind == "new_search" and (state.get("last_options") or state.get("visible_options")):
            base.update({
                "status": "needs_patch",
                "problem": "Есть сохранённые варианты, но turn ушёл в новый поиск.",
                "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent",
                "patch_hint": "Перед new_search добавить детерминированную обработку уточнений по last_options/visible_options.",
                "acceptance": "new_search is only used when no saved options can answer the turn",
            })
        return base

    if re.search(r"(различ|сравн|отлич)", t):
        base.update({
            "status": "needs_patch",
            "problem": "Запрос на сравнение текущих вариантов ушёл в LLM follow-up classifier вместо сравнения сохранённого списка.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent",
            "patch_hint": "Если есть options и текст содержит различ/сравн/отлич, вернуть compare_others с options[:3] даже без selected_option.",
            "acceptance": "compare turns use compare_others on saved options",
        })
    elif re.search(r"\b(до|бюджет|млн|миллион|тыс|к)\b", t):
        base.update({
            "status": "needs_patch",
            "problem": "Бюджетное уточнение после списка не фильтрует сохранённые варианты локально.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent + handler for budget refinement",
            "patch_hint": "Распознать budget refinement, обновить params.budget, сначала отфильтровать last_options по price_min/price_max; MCP search делать только если сохранённых совпадений нет.",
            "acceptance": "budget refinement filters saved options before any new MCP search",
        })
    elif re.search(r"(семь|семьи|ребен|дет|для себя|жить|инвестиц|передум)", t):
        base.update({
            "status": "needs_patch",
            "problem": "Смена цели/семейный сценарий после списка уходит в classifier и может потерять текущие варианты.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent + dialog_plan/state update",
            "patch_hint": "Распознать purpose refinement, обновить params.purpose без сброса last_options; ответ строить как переоценку текущих вариантов под новую цель.",
            "acceptance": "purpose refinement keeps last_options and only updates params.purpose",
        })
    else:
        base.update({
            "status": "watch",
            "problem": "Смысловая фраза после списка требует LLM classifier; нужно решить, стоит ли делать локальное правило.",
            "fix_location": "scripts/chat_tester_bot.py::_resolve_dialog_intent",
            "patch_hint": "Если этот turn повторяется в журнале, добавить отдельное детерминированное правило перед общим followup_classifier.",
            "acceptance": "repeated watch items are promoted to deterministic rules",
        })
    return base


def _diagnose_mcp_grounding(turn: str, routing: str, bot_text: str, search_response: dict[str, Any]) -> dict[str, Any] | None:
    """Total ban: simulator flags any sensitive claim that is absent from MCP/search JSON."""
    response_low = str(bot_text or "").lower().replace("ё", "е")
    evidence_low = json.dumps(search_response or {}, ensure_ascii=False).lower().replace("ё", "е")
    if not response_low.strip():
        return None

    sensitive_markers: dict[str, list[str]] = {
        "metro": ["метро", "мцд", "мцк"],
        "school_infra": ["школ", "детсад", "детск", "садик", "парк", "двор", "инфраструктур"],
        "parking": ["паркинг", "парков"],
        "mortgage": ["ипотек", "рассроч", "ставк", "первоначальн"],
        "developer": ["застройщик", "девелопер"],
        "class_segment": ["комфорт-класс", "бизнес-класс", "премиум", "элитн"],
        "rent_resale": ["аренд", "перепродаж", "доходност", "ликвидност"],
        "live_inventory": ["налич", "брон", "этаж", "корпус", "планировк"],
    }
    leaked: list[str] = []
    for fact_type, markers in sensitive_markers.items():
        response_has = any(marker in response_low for marker in markers)
        evidence_has = any(marker in evidence_low for marker in markers)
        if response_has and not evidence_has:
            leaked.append(fact_type)

    if not leaked:
        return None
    return {
        "turn": turn,
        "routing": routing,
        "status": "needs_patch",
        "problem": "non_mcp_fact_leak: ответ содержит чувствительные факты/выгоды, которых нет в MCP/search JSON: " + ", ".join(leaked),
        "fix_location": "prompts/chat_v1.txt + scripts/chat_tester_bot.py formatters/postprocess + simulator grounding guard",
        "patch_hint": "Наложить total ban: любые метро/школы/парки/ипотека/застройщик/класс/аренда/перепродажа/наличие/этажи/корпуса/планировки можно произносить только если маркер есть в MCP/search JSON; иначе говорить нейтрально и предлагать оператору проверить детали.",
        "acceptance": "selected-option detail explanation stays fully MCP-grounded",
    }


def _diagnose_no_results_response(bot_text: str, search_response: dict[str, Any]) -> dict[str, Any] | None:
    """Detect the empty-area-result case: no secondary-market advice, offer nearby expansion."""
    facts = search_response.get("facts") or []
    near = search_response.get("near") or []
    if facts or near:
        return None

    params = search_response.get("params") or {}
    missing = str(search_response.get("missing") or "").lower().replace("ё", "е")
    has_area_context = bool(params.get("district") or params.get("location") or re.search(r"район|ясенево|локац", missing))
    if not has_area_context:
        return None

    response_low = str(bot_text or "").lower().replace("ё", "е")
    bad_empty_summary = "нашла несколько вариантов" in response_low or "какой жк" in response_low
    mentions_secondary = "вторич" in response_low
    missing_nearby_offer = not re.search(r"поблиз|соседн|рядом|расшир", response_low)
    if not (bad_empty_summary or mentions_secondary or missing_nearby_offer):
        return None

    return {
        "turn": "[first_mcp_search:no_results_area]",
        "routing": "no_results_area",
        "status": "needs_patch",
        "problem": "no_results_area_expansion: в указанном районе нет facts/near, но ответ не должен советовать вторичку или делать пустой options-summary; нужно сразу предлагать новостройки поблизости/соседние районы.",
        "fix_location": "prompts/chat_v1.txt no-results branch + scripts/nmbot_mcp_only_sim.py first_mcp_search formatting",
        "patch_hint": "Добавить сценарий facts=[]/near=[] + район: 'По этому району сейчас не вижу актуальных новостроек от застройщика. Могу посмотреть близкие районы или варианты поблизости. Показать?' Запретить 'вторичный рынок'. Не называть конкретные районы/ЖК без facts/near.",
        "acceptance": "no вторичк, no empty options-summary, ask about nearby districts",
    }


def _append_sim_journal(run: dict[str, Any]) -> None:
    jsonl_path, md_path = _sim_journal_paths()
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")

    with md_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {run['ts']} — {run['title']}\n\n")
        f.write(f"Hypothesis: {run.get('hypothesis') or 'MCP-only dialog hypothesis'}\n\n")
        f.write(f"Source: {run.get('source') or 'simulator run'}\n\n")
        f.write("Summary:\n")
        for line in _run_summary_lines(run.get("turns") or []):
            f.write(f"{line}\n")
        f.write("\n")
        f.write("Input:\n")
        input_block = run.get("input") or {}
        f.write(f"- search_response: {json.dumps(input_block.get('search_response') or {}, ensure_ascii=False)}\n")
        f.write(f"- state: {json.dumps(input_block.get('state') or {}, ensure_ascii=False)}\n")
        f.write(f"- command: {input_block.get('command') or ''}\n\n")
        f.write("Turns:\n")
        for item in run.get("turns") or []:
            marker = "✅" if item.get("status") == "ok" else "⚠️"
            f.write(f"- {marker} `{item.get('turn')}` → `{item.get('routing')}` / `{item.get('status')}`\n")
            if item.get("actual"):
                f.write(f"  - Actual: {item.get('actual')}\n")
            if item.get("expected"):
                f.write(f"  - Expected: {item.get('expected')}\n")
            if item.get("mismatch"):
                f.write(f"  - Mismatch: {item.get('mismatch')}\n")
            patch = item.get("patch") or {}
            if patch.get("where"):
                f.write(f"  - Where: `{patch.get('where')}`\n")
            if patch.get("hint"):
                f.write(f"  - Patch hint: {patch.get('hint')}\n")
            if item.get("acceptance"):
                f.write(f"  - Acceptance: {item.get('acceptance')}\n")

        needs_patch = [d for d in run.get("turns") or [] if d.get("status") == "needs_patch"]
        if needs_patch:
            f.write("\nPatch summary:\n")
            for hint in dict.fromkeys((d.get("patch") or {}).get("hint") for d in needs_patch):
                clean_hint = re.sub(r"^\s*[-•–—]+\s*", "", str(hint or "").strip())
                if clean_hint:
                    f.write(f"- {clean_hint}\n")


def run_simulation(search_response: dict[str, Any], turns: list[str], *, write_journal: bool = True) -> None:
    options = _extract_options_from_structured(search_response)[:3]
    print("BOT [first_mcp_search]")
    if options:
        visible_response = _format_options_summary_response(
            options,
            "Нашла несколько вариантов по текущим данным",
            "Какой ЖК хотите рассмотреть подробнее?",
        )
    else:
        visible_response = _format_no_results_area_response(search_response)
    print(visible_response)
    print()

    state: dict[str, Any] = {
        "last_search_response": search_response,
        "last_options": options,
        "visible_options": _visible_options_from_response(visible_response, options),
        "selected_option": None,
        "params": search_response.get("params") or {},
        "dialog_window": [],
        "last_bot_question": "Какой ЖК хотите рассмотреть подробнее?",
        "last_offer_type": "choose_option",
        "last_answer_kind": "options_summary",
        "selected_option_card_shown_count": 0,
    }
    diagnostics: list[dict[str, Any]] = []
    initial_grounding = _diagnose_mcp_grounding("[first_mcp_search]", "options_summary", visible_response, search_response)
    if initial_grounding:
        initial_grounding["bot_text"] = visible_response
        initial_grounding["expected"] = _expected_from_diagnostic(initial_grounding)
        initial_grounding["actual"] = _actual_from_diagnostic({**initial_grounding, "bot_text": visible_response})
        initial_grounding["acceptance"] = initial_grounding.get("acceptance") or "MCP-only summary stays grounded"
        diagnostics.append(initial_grounding)
    initial_no_results = _diagnose_no_results_response(visible_response, search_response)
    if initial_no_results:
        initial_no_results["bot_text"] = visible_response
        initial_no_results["expected"] = _expected_from_diagnostic(initial_no_results)
        initial_no_results["actual"] = _actual_from_diagnostic({**initial_no_results, "bot_text": visible_response})
        diagnostics.append(initial_no_results)

    for turn in turns:
        print(f"USER: {turn}")
        intent = _resolve_dialog_intent(turn, state)
        kind = intent.get("intent")
        print(f"ROUTING: {kind}")
        turn_diag = _diagnose_sim_turn(turn, intent, state)
        bot_text = ""

        if kind == "select_option":
            option = intent.get("option") or {}
            state["selected_option"] = option
            state["selected_option_card_shown_count"] = int(state.get("selected_option_card_shown_count") or 0) + 1
            state["last_offer_type"] = "selected_option"
            state["last_answer_kind"] = "selected_option_card"
            state["last_bot_question"] = "Хотите, расскажу подробнее по этому ЖК или сравним его с другими вариантами?"
            print("BOT [select_option, production_routing, from_saved_mcp]")
            bot_text = _prepare_response_text(_format_option_response(option, search_response.get("params", {}).get("purpose")))
            print(bot_text)
        elif kind == "explain_selected_option":
            option = intent.get("option") or state.get("selected_option") or {}
            state["selected_option"] = option
            state["last_offer_type"] = "selected_option_details"
            state["last_answer_kind"] = "selected_option_details"
            state["last_bot_question"] = "Хотите сравнить этот ЖК с другими вариантами или проверить актуальное наличие?"
            print("BOT [explain_selected_option, production_routing, from_saved_mcp]")
            bot_text = _explain_from_saved_mcp(option, search_response)
            print(bot_text)
        elif kind == "operator_for_selected":
            option = intent.get("option") or state.get("selected_option") or {}
            state["selected_option"] = option
            state["last_offer_type"] = "operator_for_selected"
            state["last_answer_kind"] = "operator_handoff"
            state["last_bot_question"] = "Хотите оставить номер для связи?"
            print("BOT [operator_for_selected, production_routing, no_new_mcp]")
            bot_text = _prepare_response_text(_format_operator_handoff_for_option(option))
            print(bot_text)
        elif kind == "operator_contact_accept":
            state["awaiting_phone"] = True
            state["last_offer_type"] = "awaiting_phone"
            state["last_answer_kind"] = "operator_contact_request"
            state["last_bot_question"] = "Напишите номер для связи"
            print("BOT [operator_contact_accept, production_routing]")
            bot_text = "Отлично, напишите номер для связи текстом — передам оператору этот ЖК и ваш запрос вместе с контекстом диалога."
            print(bot_text)
        elif kind == "reject_operator":
            print("BOT [reject_operator, production_routing, no_new_mcp]")
            bot_text = "Хорошо, тогда остаёмся здесь. Можем сравнить варианты или поменять условия поиска — что удобнее?"
            print(bot_text)
        elif kind == "compare_others":
            compare_options = intent.get("options") or []
            state["last_offer_type"] = "choose_option"
            state["last_answer_kind"] = "options_summary"
            state["selected_option"] = None
            print("BOT [compare_others, production_routing, from_saved_mcp]")
            bot_text = _format_options_summary_response(compare_options, "Можно сравнить с другими вариантами", "Какой из них разобрать подробнее?")
            print(bot_text)
        elif kind == "expand_more_options":
            state["last_offer_type"] = "choose_option"
            state["last_answer_kind"] = "options_summary"
            print("BOT [expand_more_options, production_routing, fresh_mcp_search_required]")
            bot_text = "Продакшен делает свежий MCP search, исключая уже показанные ЖК; симулятор не исполняет новый поиск, а только подтверждает правильный маршрут."
            print(bot_text)
        elif kind == "sort_price_asc":
            cheaper_options = intent.get("options") or []
            state["last_offer_type"] = "choose_option"
            state["last_answer_kind"] = "options_summary"
            state["selected_option"] = None
            print("BOT [sort_price_asc, production_routing, from_saved_mcp]")
            bot_text = _format_options_summary_response(cheaper_options, "По бюджету из последнего списка ближе всего", "Какой из этих вариантов рассмотреть подробнее?")
            print(bot_text)
        elif kind == "filter_finish":
            finish_options = intent.get("options") or []
            state["last_offer_type"] = "choose_option"
            state["last_answer_kind"] = "options_summary"
            state["selected_option"] = None
            print("BOT [filter_finish, production_routing, from_saved_mcp]")
            bot_text = _format_options_summary_response(finish_options, "С отделкой по последнему списку вижу", "Какой из этих вариантов рассмотреть подробнее?")
            print(bot_text)
        elif kind == "followup_classifier":
            option = intent.get("option") or state.get("selected_option")
            target = f" для {option.get('name')}" if isinstance(option, dict) and option.get("name") else ""
            print("BOT [followup_classifier_needed, production_routing, no_mcp_in_sim]")
            bot_text = f"Продакшен здесь вызывает LLM follow-up classifier{target}; симулятор не делает OpenRouter-запрос."
            print(bot_text)
        elif kind == "new_search":
            print("BOT [new_search_needed, production_routing]")
            bot_text = "Это новый поиск или уточнение без подходящей памяти — в продакшене нужен MCP search с обновлёнными параметрами."
            print(bot_text)
        else:
            print(f"BOT [{kind or 'unknown'}, production_routing]")
            bot_text = "Симулятор показал intent, но не исполняет эту ветку, чтобы не становиться вторым ботом."
            print(bot_text)
        grounding = _diagnose_mcp_grounding(turn, str(kind or ""), bot_text, search_response)
        if grounding:
            grounding["bot_text"] = bot_text
            grounding["expected"] = _expected_from_diagnostic(grounding)
            grounding["actual"] = _actual_from_diagnostic({**grounding, "bot_text": bot_text})
            diagnostics.append(grounding)
        turn_diag["bot_text"] = bot_text
        turn_diag["expected"] = _expected_from_diagnostic(turn_diag)
        turn_diag["actual"] = _actual_from_diagnostic({**turn_diag, "bot_text": bot_text})
        diagnostics.append(turn_diag)
        print()

    if write_journal:
        turn_records = [_canonical_turn_entry(item) for item in diagnostics if item.get("turn") != "[first_mcp_search]" or item.get("status") != "ok"]
        if not turn_records:
            turn_records = [_canonical_turn_entry(item) for item in diagnostics]
        run = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "title": "MCP-only simulator run",
            "hypothesis": "mixed MCP-only dialog hypotheses",
            "source": "simulator run",
            "input": {
                "search_response": search_response,
                "state": _state_snapshot(state),
                "command": f"python3 scripts/nmbot_mcp_only_sim.py {' '.join(f'--turn \"{t}\"' for t in turns)}",
            },
            "turns": turn_records,
        }
        _append_sim_journal(run)
        jsonl_path, md_path = _sim_journal_paths()
        print(f"SIM JOURNAL: {jsonl_path}")
        print(f"SIM JOURNAL MD: {md_path}")


def _run_new_arch_mode() -> None:
    from nmbot_new_arch_sim import SAMPLE_SEARCH_RESPONSE, run_scenario, scenarios

    report = [run_scenario(scenario.name, scenario.turns, SAMPLE_SEARCH_RESPONSE) for scenario in scenarios()]
    final = {
        "scenarios": len(report),
        "turns": sum(item["summary"]["turns"] for item in report),
        "passed": sum(item["summary"]["passed"] for item in report),
        "failed": sum(item["summary"]["failed"] for item in report),
    }
    print("FINAL:", json.dumps(final, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate proposed MCP-only selected-option flow")
    parser.add_argument("--search-json", help="Path to structured MCP search_response JSON")
    parser.add_argument("--turn", action="append", dest="turns", help="User follow-up turn; can be repeated")
    parser.add_argument("--no-journal", action="store_true", help="Do not append simulator diagnostics to logs/sim_journal-*.jsonl/.md")
    parser.add_argument("--mode", choices=["mcp-only", "new-arch"], default="mcp-only", help="Simulation mode")
    args = parser.parse_args()
    if args.mode == "new-arch":
        _run_new_arch_mode()
        return
    turns = args.turns or ["1", "расскажи подробнее", "можно бронь?"]
    run_simulation(_load_search_response(args.search_json), turns, write_journal=not args.no_journal)


if __name__ == "__main__":
    main()
