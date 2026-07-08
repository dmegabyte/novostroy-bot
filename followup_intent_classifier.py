from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import aiohttp

DEFAULT_INTENT = "clarify"
DEFAULT_DIALOG_ACTION = "continue_from_memory"
ALLOWED_INTENTS = {
    "choose_option",
    "compare_selected",
    "operator_for_selected",
    "recommend_options",
    "conversation_answer",
    "consultation_answer",
    "explain_selection_logic",
    "explain_operator_reason",
    "continue_selection",
    "update_search_params",
    "new_search",
    "clarify",
    "reject_offer",
    "reject_operator",
    "reject_phone",
    "reject_selected_option",
    "reject_similar_options",
    "clarify_negation",
}
ALLOWED_DIALOG_ACTIONS = {
    "new_search",
    "update_search",
    "expand_more_options",
    "compare_options",
    "continue_from_memory",
    "select_option",
    "ask_clarification",
    "operator_live_check",
    "recommend_options",
    "conversation_answer",
    "consultation_answer",
    "reject_offer",
    "reject_operator",
    "reject_phone",
    "reject_selected_option",
    "reject_similar_options",
    "clarify_negation",
}
ALLOWED_SELECTED_OPTION_ACTIONS = {"keep", "clear", "set"}
ALLOWED_VISIBLE_OPTIONS_POLICIES = {"keep", "rebuild", "clear"}
ALLOWED_NUMERIC_CHOICE_POLICIES = {"accept", "reject"}

FOLLOWUP_INTENT_PROMPT = """
Ты определяешь, что клиент хочет сделать в продолжении диалога с Ириной.

Ирина помогает подбирать новостройки Москвы и Московской области.

Твоя задача — НЕ писать ответ клиенту, а выбрать действие для кода.
Смотри не только на последнюю фразу клиента, а на короткую историю диалога,
последний вопрос Ирины и состояние подбора.

Верни строго JSON:
{
  "intent": "...",
  "confidence": 0.0,
  "target": "коротко, если есть выбранный ЖК/вариант",
  "params_delta": {},
  "clarification_question": "короткий вопрос, если нужно уточнить",
  "reason": "коротко почему"
}

Допустимые intent:
- choose_option — клиент выбрал вариант из видимого списка;
- compare_selected — клиент согласился сравнить выбранный ЖК с похожими;
- operator_for_selected — клиент хочет оператора/актуальное наличие/бронь/этаж/показ/детали, которых нет в карточке;
- recommend_options — клиент просит совет/рекомендацию по текущему списку: "что посоветуешь", "какой лучше", "что бы ты выбрала";
- conversation_answer — клиент общается по теме недвижимости/подбора, задаёт уточнение или подтверждает предложенное объяснение, но НЕ просит новый список, новый поиск или действие с ЖК;
- consultation_answer — клиент задаёт консультационный вопрос по недвижимости/сценарию, а не просит новый список: "что важно для аренды", "на что смотреть под инвестицию", "что значит отделка", "почему это важно";
- explain_selection_logic — клиент спрашивает, как/по какому принципу Ирина подбирает варианты: "как ты подбираешь", "почему эти варианты", "по каким критериям";
- operator_for_selected — клиент хочет оператора/актуальное наличие/бронь/этаж/показ/детали, которых нет в карточке, или прямо спрашивает как связаться с оператором/менеджером;
- explain_operator_reason — клиент спрашивает зачем нужен оператор / почему нельзя ответить здесь;
- continue_selection — клиент хочет продолжить подбор здесь, посмотреть другие варианты или вернуться к списку;
- update_search_params — клиент уточнил или отверг параметр поиска;
- new_search — клиент начал новый поиск или сильно поменял запрос;
- reject_offer — клиент отказался от последнего предложенного действия;
- reject_operator — клиент явно не хочет оператора / звонок / менеджера;
- reject_phone — клиент не хочет оставлять номер или контакт;
- reject_selected_option — клиент отверг выбранный ЖК/вариант: "не этот", "не подходит";
- reject_similar_options — клиент не хочет похожие/другие варианты;
- clarify_negation — клиент что-то отрицает, но непонятно что именно;
- clarify — по ответу клиента нельзя надёжно понять, чего он хочет.

Правила:
- "да", "нет", "наверное", "возможно", "хочу" всегда понимай через last_bot_question и last_offer_type.
- Если последний вопрос был про сравнение, согласие означает compare_selected, отказ — reject_offer.
- Если последний вопрос был про оператора, согласие означает operator_for_selected, отказ — reject_offer.
- Если последний вопрос был про оператора, согласие означает operator_for_selected, отказ от звонка/оператора/номера — reject_operator или reject_phone.
- Если последний вопрос был про оператора, а клиент спрашивает "зачем", "почему", "для чего" — explain_operator_reason.
- Если клиент после предложения оператора пишет "продолжить", "подбор", "давай дальше", "еще варианты" — continue_selection.
- Если последний вопрос был уточнением "передать оператору или продолжить подбор", выбор продолжить подбор — continue_selection.
- Если бот спросил про параметр поиска, например "подойдёт последний этаж?", ответ "нет" должен стать update_search_params с params_delta.
- Если бот спросил про критерий выбора, например "бюджет или класс объекта?", и клиент отвечает "бюджет, у меня 15 млн" — это update_search_params, а не choose_option.
- Деньги в тексте (`15 млн`, `до 20`, `на руках 10`) — это параметр бюджета. Не трактуй цифру внутри бюджета как номер варианта.
- choose_option выбирай только когда клиент явно выбирает вариант из списка: "1", "первый вариант", или называет конкретный ЖК без дополнительных условий.
- Если не уверен — intent=clarify и один короткий clarification_question.
- Отрицания и отказы не должны автоматически запускать новый поиск.
- Если клиент пишет "не хочу оператора", "без оператора", "не надо звонить" — reject_operator.
- Если клиент спрашивает "что посоветуешь", "твой совет", "какой лучше выбрать" по текущему списку — intent=recommend_options. Не превращай это в compare_selected/compare_options.
- Если клиент задаёт консультационный вопрос, а не просит действие со списком: "что важно для аренды", "на что смотреть под сдачу", "что важно для инвестиций", "что значит с отделкой", "почему это влияет на выбор" — intent=consultation_answer. Сначала ответь на вопрос; не выбирай continue_selection и не запускай новый список.
- Если клиент спрашивает "как ты подбираешь", "по какому принципу", "почему эти варианты", "по каким критериям" — intent=explain_selection_logic. Это вопрос о методе подбора: сначала объясни логику, не показывай новый список и не выбирай continue_selection.
- Если клиент просто общается по теме, уточняет смысл прошлого ответа или отвечает "да" на предложение Ирины объяснить логику/причины — intent=conversation_answer. Не выбирай continue_selection, если клиент прямо не просит "ещё варианты", "продолжить подбор", "покажи другие".
- Если клиент прямо спрашивает "как связаться с оператором", "как связаться с менеджером", "хочу оператора", "позови менеджера" — intent=operator_for_selected даже если selected_option пустой: код передаст оператору текущий список/критерии.
- Если клиент пишет "не оставлю номер", "номер не дам", "не хочу оставлять контакт" — reject_phone.
- Если клиент пишет "не этот", "не подходит", "этот не нравится" про выбранный ЖК — reject_selected_option.
- Если клиент пишет "не надо похожие", "похожие не нужны" — reject_similar_options.
- Если клиент пишет "не надо бронь", "бронь не нужна", "пока без брони" — clarify_negation: прими, что бронь не нужна, и уточни следующий шаг без оператора.
- Если клиент отвергает параметр поиска, например "не с отделкой" или "не в этом районе", intent=update_search_params и params_delta с понятным изменением.
- Если клиент пишет "хочу дешевле", "нужно дешевле", "не подходит, хочу дешевле" БЕЗ конкретной суммы — НЕ придумывай новый max_price. Верни clarify_negation и спроси одним коротким вопросом, до какого бюджета смотреть.
- Если клиент пишет "дешевле до 10 млн", "до 12", "бюджет 9 млн" — intent=update_search_params и params_delta.max_price равен названной сумме.
- Если в отрицании непонятна цель — clarify_negation и короткий вопрос: что именно не подошло.
- Если не уверен — intent=clarify и один короткий clarification_question.
- Не придумывай факты о ЖК, ценах, районах, этажах или наличии.
""".strip()

DIALOG_STATE_PLANNER_PROMPT = """
Ты — dialog state orchestrator для Ирины, бота по новостройкам.

Твоя задача — НЕ отвечать клиенту. Ты решаешь, как обновить состояние диалога
перед тем, как код выполнит поиск, ответ из памяти или уточнение.

Верни строго JSON:
{
  "mode": "search_action | conversation",
  "dialog_action": "new_search | update_search | expand_more_options | compare_options | recommend_options | conversation_answer | consultation_answer | continue_from_memory | select_option | ask_clarification | operator_live_check | reject_offer | reject_operator | reject_phone | reject_selected_option | reject_similar_options | clarify_negation",
  "confidence": 0.0,
  "params_delta": {},
  "selected_option_action": "keep | clear | set",
  "selected_option_name": null,
  "rejected_options_add": [],
  "visible_options_policy": "keep | rebuild | clear",
  "numeric_choice_policy": "accept | reject",
  "conversation_followup": {},
  "clarification_question": "",
  "reason": "коротко почему"
}

Ключевые правила:
- Ты — единственный слой, который понимает смысл фразы клиента. Код ниже только исполняет твой dialog_action.
- Сначала раздели режим: search_action = клиент просит подбор/поиск/сравнение/выбор/оператора; conversation = клиент просто общается по теме, уточняет, спрашивает "почему/как/что важно" или отвечает на предложенное объяснение.
- Если клиент после списка просит ещё/похожие/другие варианты: "подбери похожие", "найди похожие",
  "покажи ещё", "ещё такие", "другие варианты" — dialog_action="expand_more_options",
  visible_options_policy="rebuild", numeric_choice_policy="reject". Не выбирай continue_from_memory.
- Если клиент просит сравнить текущие варианты: "сравни", "чем отличаются", "в чем разница" — dialog_action="compare_options".
- Если клиент просит совет/рекомендацию по текущим вариантам: "что посоветуешь", "твой совет", "какой лучше выбрать" — dialog_action="recommend_options". Не выбирай compare_options: нужен один приоритетный совет по фактам.
- Если клиент задаёт консультационный вопрос внутри диалога — "что важно для аренды", "на что смотреть под сдачу", "что важно для инвестиций", "что важно для жизни", "что значит отделка", "почему это влияет на выбор" — dialog_action="consultation_answer". Это вопрос, а не просьба продолжить подбор: не выбирай expand_more_options, continue_from_memory или new_search, если клиент прямо не просит новые варианты.
- Если клиент спрашивает, как Ирина подбирает варианты: "как ты подбираешь", "по какому принципу", "почему эти варианты", "по каким критериям" — dialog_action="conversation_answer", mode="conversation". Это legacy-смысл explain_selection_logic, но исполняется как живой conversation_answer. Не запускай expand_more_options и не продолжай список.
- Если клиент отвечает "да" на последний вопрос Ирины вида "объяснить почему/как/логику/почему эти варианты" — dialog_action="conversation_answer", mode="conversation". Это согласие на объяснение, а не просьба продолжить подбор.
- Учитывай conversation_followup: если он содержит subtopic_hint=family_mortgage, отвечай именно про семейную ипотеку; если subtopic_hint=down_payment, отвечай про первоначальный взнос. Не своди такой follow-up к generic financing, если в conversation_followup есть более точный сигнал.
- Если клиент прямо спрашивает "как связаться с оператором" или просит связаться с оператором/менеджером — dialog_action="operator_live_check". Если selected_option пустой, всё равно выбирай operator_live_check: код передаст оператору текущий список и критерии.
- Если клиент выбирает вариант номером или названием из visible_options — dialog_action="select_option",
  selected_option_action="set", selected_option_name=точное name из visible_options. Даже для "1"/"2" верни name, а не цифру.
- Если клиент отвергает выбранный ЖК ("не подходит", "не этот", "не нравится") и просит изменить условия,
  добавь текущий selected_option в rejected_options_add, selected_option_action="clear",
  visible_options_policy="clear", numeric_choice_policy="reject".
- Если клиент пишет "не подходит, хочу ближе к метро" — это update_search, params_delta.near_metro=true,
  выбранный ЖК надо очистить и добавить в rejected_options_add.
- Если клиент пишет "хочу дешевле" без суммы — НЕ придумывай max_price: dialog_action="ask_clarification"
  и спроси до какого бюджета смотреть.
- Если клиент пишет "до 12 млн", "до 17 млн", "бюджет 10" — update_search с params_delta.max_price.
- Если клиент просит бронь/наличие/этажи/показ по конкретному ЖК, но selected_option пустой — dialog_action="ask_clarification",
  спроси какой ЖК проверить. Не выбирай старый ЖК сам.
- Если visible_options пустой или список в прошлом ответе был ненадёжный, numeric_choice_policy="reject".
  Нельзя принимать "1"/"2" как выбор, если нет надёжного видимого списка.
- Если клиент выбрал номер из надёжного visible_options — dialog_action="select_option", selected_option_action="set".
- Если поиск обновляется или начинается заново, visible_options_policy="rebuild" после поиска, а до поиска старый numeric_choice_policy="reject".
- Не придумывай ЖК и не добавляй в rejected_options_add то, чего нет в state.selected_option или state.last_options.
- Код проверит твой план, но ты должен объяснить reason.
""".strip()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _overmind_token() -> str:
    token = _env("OVERMIND_TOKEN") or _env("GATEWAY_POLL_TOKEN")
    if not token:
        raise RuntimeError("OVERMIND_TOKEN/GATEWAY_POLL_TOKEN is not set")
    return token


def _trim(value: Any, limit: int = 6000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl > 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _fallback(reason: str, question: str | None = None) -> dict[str, Any]:
    return {
        "intent": DEFAULT_INTENT,
        "confidence": 0.0,
        "target": "",
        "params_delta": {},
        "clarification_question": question or "Уточните, пожалуйста: продолжить подбор или изменить условия?",
        "reason": reason,
        "fallback_used": True,
    }


def normalize_intent(intent: str) -> str:
    value = str(intent or "").strip()
    return value if value in ALLOWED_INTENTS else DEFAULT_INTENT


def normalize_dialog_action(action: str) -> str:
    value = str(action or "").strip()
    return value if value in ALLOWED_DIALOG_ACTIONS else DEFAULT_DIALOG_ACTION


def normalize_dialog_mode(mode: str, action: str = "") -> str:
    value = str(mode or "").strip()
    if value in {"search_action", "conversation"}:
        return value
    return "conversation" if normalize_dialog_action(action) == "conversation_answer" else "search_action"


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    raw = str(value or "").strip()
    return raw if raw in allowed else default


async def classify_followup_intent(
    session: aiohttp.ClientSession,
    *,
    user_text: str,
    dialog_window: list[dict[str, str]] | None = None,
    state: dict[str, Any] | None = None,
    model: str | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Возвращает безопасное действие для короткого follow-up.

    Классификатор не отвечает клиенту и не меняет state сам. Он только предлагает
    intent/params_delta, а код валидирует и применяет результат.
    """
    if _env("NMBOT_FOLLOWUP_CLASSIFIER", "1") == "0":
        return _fallback("followup classifier disabled")

    threshold = float(_env("NMBOT_FOLLOWUP_CONFIDENCE", "0.7"))
    payload = {
        "user_text": user_text,
        "dialog_window": dialog_window or [],
        "state": state or {},
        "allowed_intents": sorted(ALLOWED_INTENTS),
    }
    request_data = {
        "query": _trim(payload),
        "service": "openrouter",
        "model": model or _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview"),
        "system_prompt": FOLLOWUP_INTENT_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 700},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }

    try:
        token = _overmind_token()
        overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        task_payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": timeout,
            "max_retries": 0,
        }
        async with session.post(f"{overmind_url}/api/v1/tasks/api", json=task_payload, headers=headers) as resp:
            task = await resp.json()
        task_id = task.get("id")
        if not task_id:
            return _fallback("task_id missing")

        start = time.monotonic()
        poll_headers = {"Authorization": f"Bearer {token}"}
        while time.monotonic() - start < timeout:
            async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/status", headers=poll_headers) as resp:
                status_data = await resp.json()
            status = status_data.get("status")
            if status in {"completed", "failed", "cancelled"}:
                async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/result", headers=poll_headers) as resp:
                    result = await resp.json()
                result_obj = result.get("result") or result
                raw = result_obj.get("response", "") if isinstance(result_obj, dict) else str(result_obj)
                data = _extract_json(str(raw))
                intent = normalize_intent(str(data.get("intent") or ""))
                confidence = float(data.get("confidence") or 0.0)
                question = str(data.get("clarification_question") or "").strip()
                if intent == DEFAULT_INTENT or confidence < threshold:
                    return _fallback(str(data.get("reason") or "low confidence"), question or None) | {"confidence": confidence}
                params_delta = data.get("params_delta") if isinstance(data.get("params_delta"), dict) else {}
                return {
                    "intent": intent,
                    "confidence": confidence,
                    "target": str(data.get("target") or ""),
                    "params_delta": params_delta,
                    "clarification_question": question,
                    "reason": str(data.get("reason") or ""),
                    "fallback_used": False,
                }
            await asyncio.sleep(1)
    except Exception as e:
        return _fallback(f"{type(e).__name__}: {e}")

    return _fallback("timeout")


async def plan_dialog_state(
    session: aiohttp.ClientSession,
    *,
    user_text: str,
    state: dict[str, Any] | None = None,
    last_response_text: str = "",
    search_response_text: str = "",
    visible_response_text: str = "",
    model: str | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """LLM-orchestrator: предлагает безопасный план обновления состояния.

    Планировщик не отвечает клиенту и не меняет state сам. Код применяет только
    разрешённые поля и отбрасывает всё, что не подтверждено текущей памятью.
    """
    if _env("NMBOT_DIALOG_PLANNER", "1") == "0":
        return {
            "dialog_action": DEFAULT_DIALOG_ACTION,
            "mode": "conversation",
            "confidence": 0.0,
            "params_delta": {},
            "selected_option_action": "keep",
            "selected_option_name": None,
            "rejected_options_add": [],
            "visible_options_policy": "keep",
            "numeric_choice_policy": "accept",
            "clarification_question": "",
            "reason": "dialog planner disabled",
            "fallback_used": True,
        }

    payload = {
        "user_text": user_text,
        "state": state or {},
        "last_response_text": last_response_text,
        "visible_response_text": visible_response_text,
        "search_response_text": search_response_text,
    }
    request_data = {
        "query": _trim(payload, 9000),
        "service": "openrouter",
        "model": model or _env("NMBOT_DIALOG_PLANNER_MODEL", _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview")),
        "system_prompt": DIALOG_STATE_PLANNER_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 900},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }

    fallback = {
        "dialog_action": DEFAULT_DIALOG_ACTION,
        "mode": "conversation",
        "confidence": 0.0,
        "params_delta": {},
        "selected_option_action": "keep",
        "selected_option_name": None,
        "rejected_options_add": [],
        "visible_options_policy": "keep",
        "numeric_choice_policy": "accept",
        "clarification_question": "",
        "reason": "planner fallback",
        "fallback_used": True,
    }
    try:
        token = _overmind_token()
        overmind_url = _env("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        task_payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": timeout,
            "max_retries": 0,
        }
        async with session.post(f"{overmind_url}/api/v1/tasks/api", json=task_payload, headers=headers) as resp:
            task = await resp.json()
        task_id = task.get("id")
        if not task_id:
            return fallback | {"reason": "task_id missing"}

        start = time.monotonic()
        poll_headers = {"Authorization": f"Bearer {token}"}
        while time.monotonic() - start < timeout:
            async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/status", headers=poll_headers) as resp:
                status_data = await resp.json()
            status = status_data.get("status")
            if status in {"completed", "failed", "cancelled"}:
                async with session.get(f"{overmind_url}/api/v1/tasks/api/{task_id}/result", headers=poll_headers) as resp:
                    result = await resp.json()
                result_obj = result.get("result") or result
                raw = result_obj.get("response", "") if isinstance(result_obj, dict) else str(result_obj)
                data = _extract_json(str(raw))
                params_delta = data.get("params_delta") if isinstance(data.get("params_delta"), dict) else {}
                rejected = data.get("rejected_options_add") if isinstance(data.get("rejected_options_add"), list) else []
                return {
                    "dialog_action": normalize_dialog_action(str(data.get("dialog_action") or "")),
                    "mode": normalize_dialog_mode(str(data.get("mode") or ""), str(data.get("dialog_action") or "")),
                    "confidence": float(data.get("confidence") or 0.0),
                    "params_delta": params_delta,
                    "selected_option_action": _normalize_choice(data.get("selected_option_action"), ALLOWED_SELECTED_OPTION_ACTIONS, "keep"),
                    "selected_option_name": data.get("selected_option_name") if data.get("selected_option_name") else None,
                    "rejected_options_add": [str(x) for x in rejected if str(x).strip()][:5],
                    "visible_options_policy": _normalize_choice(data.get("visible_options_policy"), ALLOWED_VISIBLE_OPTIONS_POLICIES, "keep"),
                    "numeric_choice_policy": _normalize_choice(data.get("numeric_choice_policy"), ALLOWED_NUMERIC_CHOICE_POLICIES, "accept"),
                    "clarification_question": str(data.get("clarification_question") or "").strip(),
                    "reason": str(data.get("reason") or ""),
                    "fallback_used": False,
                }
            await asyncio.sleep(1)
    except Exception as e:
        return fallback | {"reason": f"{type(e).__name__}: {e}"}

    return fallback | {"reason": "timeout"}
