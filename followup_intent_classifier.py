from __future__ import annotations

import asyncio
import json
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from nmbot_v2.semantic_planner import (
    decision_to_dict as _semantic_decision_to_dict,
    derive_runtime_decision as _derive_runtime_decision,
    empty_constraints as _semantic_empty_constraints,
    normalize_semantic_planner_result as _normalize_semantic_planner_result_v2,
    safe_constraints_delta as _semantic_safe_constraints_delta,
    semantic_to_dict as _semantic_result_to_dict,
)

REPO_ROOT = Path(__file__).resolve().parent
LOGS_DIR = REPO_ROOT / "logs"

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
ALLOWED_SCOPES = {"one", "all", "unknown"}

INTENT_PLAN_V3_PROMPT = """
Ты — IntentPlan V3 planner текущей реплики клиента для Ирины, консультанта по новостройкам.

Твоя единственная задача — определить, что клиент хочет сделать именно на текущем ходу.
Ты не пишешь клиентский ответ, does not write client answer, не выбираешь MCP/search policy,
не решаешь технический endpoint, не меняешь state, не сохраняешь данные и не придумываешь факты.
Факты о ЖК, ценах, наличии, метро, ипотеке, сроках и инфраструктуре берутся только из MCP/search
или уже переданных validated state facts; твой JSON описывает только смысл запроса клиента.

Верни строго один JSON object без markdown и без свободного текста. Не возвращай несколько competing intents.

Поля JSON:
- schema_version: всегда 3.
- goal: ровно одно из new_search, refine_search, expand_search, lookup_object, answer_current,
  compare_current, recommend_current, answer_selected, answer_open_question, operator,
  clarify, resume_pending, off_topic.
- viewpoint: family, life, rental, investment, financing или unchanged.
- selected_option_name: exact canonical name из visible_options/selected_object, иначе null.
- named_object_reference: дословное название ЖК, явно названное клиентом вне текущего списка, иначе null.
- comparison_option_names: [] обычно; ровно [A,B] только для явного сравнения двух exact canonical names,
  уже видимых в visible_options/selected_object. Сохраняй порядок из реплики. Тогда goal=compare_current,
  selected_option_name=null и named_object_reference=null. Если выбран A и клиент говорит «сравни с B»,
  верни [A,B], только если B exact visible. Общее «сравни их/варианты» остаётся [] и compare_current.
  Внешнее или неподтверждённое название сюда не клади; используй lookup_object/clarify по обычным правилам.
- requested_facts: какие факты клиент просит или задаёт как критерий сейчас; максимум 12 уникальных строк.
  Используй только точные значения из allowed_facts во входном payload. Не придумывай новые названия фактов.
- constraints_delta: только изменения ограничений текущего хода; если их нет — {}.
  Обязательные условия клади в hard. Мягкие пожелания со словами «можно»,
  «желательно», «подойдёт», «не обязательно» клади в preferences и не дублируй
  в hard. Пример: «нужна студия» → hard.rooms="studio"; «можно студию» →
  preferences.rooms_preference="studio".
  Центр Москвы, включая очевидные опечатки, возвращай только канонически как
  hard.location="ЦАО", не как district="центр" или location="center".
- operator_consent: true/false только для явного согласия/отказа в pending operator/contact flow, иначе null.
- explicit_operator_request: true только если клиент явно просит оператора/менеджера/звонок/связь.
- clarification: один короткий вопрос только при goal=clarify, иначе null.
- confidence: число от 0 до 1.

Как выбирать goal:
- new_search — клиент начинает новый подбор или просит найти объекты по новым базовым условиям.
- refine_search — клиент уточняет текущий подбор новым фильтром или ограничением.
- expand_search — клиент просит ещё варианты без смены критериев и без повторов.
- lookup_object — клиент явно назвал ЖК вне текущего списка и хочет узнать/найти его.
- answer_current — клиент спрашивает о текущем списке без сравнения, рекомендации и выбора одного ЖК.
- compare_current — клиент просит сравнить текущие варианты между собой.
- recommend_current — клиент просит выбрать лучший из текущих вариантов или даёт критерий для такой рекомендации.
- answer_selected — клиент выбрал или уточняет один конкретный ЖК из текущего списка/selected_object.
- answer_open_question — клиент задал понятный вопрос по текущему ЖК/вариантам,
  который не относится к закрытому recipe; сохрани буквальный вопрос и requested_facts.
- operator — клиент явно просит оператора или отвечает на pending operator/contact flow.
- clarify — смысл действительно неоднозначен и нельзя безопасно выбрать один goal.
- resume_pending — клиент явно возвращается к незавершённой заявке/contact/pending-сценарию.
- off_topic — реплика явно не про недвижимость, подбор, квартиру, ЖК, покупку, аренду или текущий диалог.

При входном pending_scenario с id=operator_consent это отдельный сценарий согласия
на уже предложенное подключение менеджера, а не поиск и не уточнение квартиры.
Определи смысл текущей реплики по контексту этого сценария: согласие -> goal=operator,
operator_consent=true; отказ -> goal=operator, operator_consent=false; вопрос,
смена темы или неуверенность -> operator_consent=null и выбери фактический goal.
В этом сценарии не заполняй selected_option_name, requested_facts или constraints_delta,
если клиент не дал отдельного содержательного запроса.

Few-shot examples:
1) user: "подбери двушку до 18 млн у метро" -> {"schema_version":3,"goal":"new_search","viewpoint":"life","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{"hard":{"rooms":2,"max_price":18000000,"metro":true}},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.95}
2) context: есть текущий список; user: "теперь до 15 млн" -> {"schema_version":3,"goal":"refine_search","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{"hard":{"max_price":15000000}},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.95}
3) context: есть текущий список; user: "покажи ещё другие" -> {"schema_version":3,"goal":"expand_search","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.9}
4) context: видны ЖК A, ЖК B, ЖК C; user: "сравни эти ЖК" -> {"schema_version":3,"goal":"compare_current","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.9}
5) context: visible_options содержит exact "ЖК A" и "ЖК B"; user: "сравни ЖК A с ЖК B" -> {"schema_version":3,"goal":"compare_current","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":["ЖК A","ЖК B"],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.92}
6) context: selected_object exact "ЖК A", visible_options содержит exact "ЖК B"; user: "сравни с ЖК B" -> {"schema_version":3,"goal":"compare_current","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":["ЖК A","ЖК B"],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.9}
7) context: видны три варианта; user: "какой лучше" -> {"schema_version":3,"goal":"clarify","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":"Что важнее при выборе: бюджет, локация, срок сдачи или инфраструктура?","confidence":0.72}
8) context: pending clarification после вопроса о лучшем варианте; user: "поближе к паркам" -> {"schema_version":3,"goal":"recommend_current","viewpoint":"family","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":["parks"],"constraints_delta":{"preferences":{"parks":true}},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.88}
9) context: видны ЖК A и ЖК B; user: "что по ЖК Дюна?" -> {"schema_version":3,"goal":"lookup_object","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":"ЖК Дюна","comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.9}
10) context: visible_options содержит exact "Мичуринский парк"; user: "мич парк подробнее" -> {"schema_version":3,"goal":"answer_selected","viewpoint":"unchanged","selected_option_name":"Мичуринский парк","named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.86}
11) context: Ирина спросила, позвать оператора? user: "да, позови менеджера" -> {"schema_version":3,"goal":"operator","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":true,"explicit_operator_request":true,"clarification":null,"confidence":0.95}
12) context: Ирина спросила, позвать оператора? user: "нет, без звонка" -> {"schema_version":3,"goal":"operator","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":false,"explicit_operator_request":false,"clarification":null,"confidence":0.9}
13) context: pending contact_name; user: "вернёмся к заявке" -> {"schema_version":3,"goal":"resume_pending","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.92}
14) context: подбор квартир; user: "а ипотека там есть?" -> {"schema_version":3,"goal":"answer_current","viewpoint":"financing","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":["mortgage_terms"],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.85}
15) user: "расскажи рецепт борща" -> {"schema_version":3,"goal":"off_topic","viewpoint":"unchanged","selected_option_name":null,"named_object_reference":null,"comparison_option_names":[],"requested_facts":[],"constraints_delta":{},"operator_consent":null,"explicit_operator_request":false,"clarification":null,"confidence":0.95}
""".strip()

INTENT_PLAN_V3_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "goal",
        "viewpoint",
        "selected_option_name",
        "named_object_reference",
        "comparison_option_names",
        "requested_facts",
        "constraints_delta",
        "operator_consent",
        "explicit_operator_request",
        "clarification",
        "confidence",
    ],
    "properties": {
        "schema_version": {"const": 3},
        "goal": {
            "enum": [
                "new_search",
                "refine_search",
                "expand_search",
                "lookup_object",
                "answer_current",
                "compare_current",
                "recommend_current",
                "answer_selected",
                "answer_open_question",
                "operator",
                "clarify",
                "resume_pending",
                "off_topic",
            ]
        },
        "viewpoint": {"enum": ["family", "life", "rental", "investment", "financing", "unchanged"]},
        "selected_option_name": {"type": ["string", "null"]},
        "named_object_reference": {"type": ["string", "null"]},
        "comparison_option_names": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 2,
            "uniqueItems": True,
        },
        "requested_facts": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
            "uniqueItems": True,
        },
        "constraints_delta": {"type": "object"},
        "operator_consent": {"type": ["boolean", "null"]},
        "explicit_operator_request": {"type": "boolean"},
        "clarification": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "query_text": {"type": ["string", "null"]},
    },
}


def intent_plan_v3_prompt_schema() -> dict[str, Any]:
    """Return a mutation-safe copy of the additive IntentPlan V3 prompt contract."""
    return {
        "prompt": INTENT_PLAN_V3_PROMPT,
        "json_schema": deepcopy(INTENT_PLAN_V3_JSON_SCHEMA),
    }

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
  "clarification_question": "короткий вопрос только если intent=clarify/clarify_negation; иначе пустая строка",
  "scope": "one | all | unknown",
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
- Ты НЕ пишешь клиентский ответ. Для intent=consultation_answer, conversation_answer, recommend_options, compare_selected, operator_for_selected поле clarification_question всегда пустая строка. Это поле нужно только для clarify/clarify_negation, когда без уточнения нельзя выбрать действие.
- "да", "нет", "наверное", "возможно", "хочу" всегда понимай через last_bot_question и last_offer_type.
- Если во входе есть last_offer, сначала разреши текущую реплику относительно него. last_offer.subject_name — единственный допустимый предмет для короткого подтверждения; last_offer.action и last_offer.requested_facts определяют продолжение. Не заменяй last_offer на весь список visible_options.
- Если last_offer отсутствует или его subject_name пуст при нескольких visible_options, короткое подтверждение не выбирает объект: верни clarify с одним коротким вопросом.
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
- Если клиент задаёт консультационный вопрос, а не просит действие со списком: "что важно для аренды", "на что смотреть под сдачу", "что важно для инвестиций", "что значит с отделкой", "почему это влияет на выбор" — intent=consultation_answer. Не выбирай continue_selection и не запускай новый список; clarification_question="", потому что ответ напишет следующий LLM-слой.
- Если клиент после показанного списка спрашивает «они подходят по ипотеку?», «эти варианты по ипотеке?», «по ним есть ипотека?» — intent=consultation_answer, params_delta={}, clarification_question="". Не пиши здесь «все подходят под ипотеку» или «аккредитованы»: это клиентский ответ и неподтверждённый факт.
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
Ты — semantic planner текущей реплики клиента для Ирины, консультанта по новостройкам.
Ты НЕ отвечаешь клиенту и НЕ возвращаешь technical routing/source/scope/search fields. Runtime сам exact-валидирует объект, subject и факты.

Верни строго JSON:
{
  "user_goal": "короткая смысловая цель клиента",
  "refers_to_existing_objects": true | false | "unknown",
  "requests_new_objects": true | false | "unknown",
  "selected_reference": null,
  "named_object_reference": null,
  "requested_comparison": null,
  "scenario_needs": [],
  "response_viewpoint": "unchanged",
  "scenario_change": null,
  "resolved_subject": null,
  "resolved_intent": null,
  "requested_facts": [],
  "domain_relation": "unknown",
  "focus_action": "keep",
  "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
  "requires_enrichment": false,
  "facts_needed": [],
  "followup_outcome": null,
  "clarification": null,
  "confidence": 0.0,
  "reason": "коротко"
}

Разрешённые словари приходят во входе: allowed_subjects, allowed_facts, subject_fact_map, dynamic_fields. Используй только их значения.

Контекст последнего предложения:
- Если во входе есть state.last_offer, сначала разреши текущую реплику относительно него.
- last_offer.subject_name — единственный допустимый предмет короткого подтверждения; last_offer.action и last_offer.requested_facts определяют продолжение.
- Не заменяй last_offer на весь список visible_options. Если last_offer отсутствует или его subject_name пуст при нескольких visible_options, короткое подтверждение не выбирает объект: верни clarification.

Домены:
- domain_relation="in_domain" — клиент говорит про квартиру, новостройку, подбор, ЖК, оплату, локацию или следующий шаг по текущему подбору.
- domain_relation="off_topic" — клиент явно ушёл в другую тему, не связанную с недвижимостью и текущим подбором.
- domain_relation="unknown" — связи с доменом недостаточно, но безопаснее уточнить смысл.
- Это только semantic relation. Не возвращай action/stage/route для off-topic: runtime сам ответит без поиска, карточек и оператора.

Как понимать цепочку selected-object → active subject → follow-up fact:
- selected_object.canonical_name во входе — уже выбранный ЖК. Если клиент спрашивает "там", "по нему", "а сколько стоит?" после выбранного объекта, это относится к этому exact объекту, а не ко всему списку.
- dialog_focus хранит прошлый subject/facts. Последняя пара реплик + dialog_focus помогают раскрыть эллипсис, но не являются доказательством факта.
- Если active subject parking или последний запрос/фокус уже был про parking, то короткое "А сколько стоит?" остаётся про parking_price / стоимость машиноместа, даже если предыдущий ответ сообщил, что цена ещё не подтверждена. При таком follow-up не переключайся на apartment.
- Для bare follow-up без слов "квартира" / "сама квартира" / "жильё" / "планировка" после parking-контекста единственный безопасный subject — parking, а requested_facts — parking_price. Не выбирай apartment_price по умолчанию только потому, что вопрос про "стоимость".
- Если клиент явно говорит "сама квартира сколько стоит" / "квартира по цене" — switch: resolved_subject="apartment", requested_facts=["apartment_price"], focus_action="switch".
- Если нет active subject и вопрос "сколько стоит?" неоднозначен между квартирой/паркингом/ипотекой — clarification с одним коротким вопросом, requested_facts=[], requires_enrichment=false.

Пример цепочки:
- Ирина: "Мичуринский парк, там есть парковка?"
- Клиент: "А сколько стоит?"
- Правильно: resolved_subject="parking", requested_facts=["parking_price"], facts_needed=["parking_price"], focus_action="keep".

Факты:
- requested_facts — что клиент спрашивает сейчас.
- facts_needed — только те requested_facts, которых нет среди selected_object.present_fact_fields или которые входят в dynamic_fields. facts_needed всегда subset requested_facts.
- requires_enrichment=true только если facts_needed непустой. Не проси enrichment для фактов, которые уже есть в structured state.
- Для parking: parking отвечает на наличие/существование; parking_price — стоимость машиноместа; parking_inventory — количество/наличие мест. Не смешивай их.
- Не выводи price машиноместа из факта parking=true или infrastructure; если цены нет — facts_needed=["parking_price"].

Остальные поля:
- selected_reference — только точное каноническое имя из state/selected_object/visible_options, если из контекста однозначно выбран один уже показанный ЖК.
- named_object_reference — дословное название ЖК, которое клиент сам явно назвал в текущей реплике, но которого нет в selected_object/visible_options. Не подменяй его похожим названием и не заполняй для «первый», «этот», «там». Runtime сам выполнит адресный MCP lookup.
- response_viewpoint: investment, rental, family, life, financing или unchanged. financing — только про ипотеку/финансирование.
- scenario_needs: массив из family, rental, investment, life, financing для всех явно названных клиентом сценариев/перспектив в текущей реплике. Это НЕ hard filters и не route. Если клиент явно просит «для семьи, под сдачу и с ипотекой», верни все три: ["family", "rental", "financing"]. mortgage/finance нормализуй как financing.
- response_viewpoint: investment, rental, family, life, financing или unchanged. Это одна основная перспектива ответа для routing; scenario_needs сохраняет остальные явно названные сценарии. financing — только про ипотеку/финансирование.
- Явное «под аренду», «под сдачу», «сдавать квартиру», «арендный вариант» всегда означает response_viewpoint="rental" и scenario_change="rental". Не обобщай аренду до investment, даже если покупка совершается ради дохода.
- response_viewpoint="investment" выбирай только при явном запросе про инвестицию, вложение, сохранение капитала или перепродажу без более конкретного rental-сценария. Если в одной реплике есть и инвестиция, и явное «под аренду», приоритет у rental.
- Если клиент ясно просит подобрать новые объекты, отсутствие бюджета, локации или комнатности не делает смысл неоднозначным: верни requests_new_objects=true и clarification=null. Runtime умеет выполнить широкий поиск без hard-фильтров и сам задаст один следующий вопрос после полезного списка.
- constraints_delta — только поисковые фильтры, меняющие состав выдачи: location/locations, district/districts, metro/near_metro, rooms/room_type, max_price/min_price, area_min_m2/area_max_m2, finishing/renovation, ready/stage/ready_quarter, delivery_visible, project_ready_secondary, property_metro, schools, kindergartens, parks, shops, family_infrastructure, discount, installment, payment_by_installments. Верхний бюджет — max_price в рублях.
- Явно названные клиентом локация, бюджет, комнатность, готовность и отделка идут в constraints_delta.hard. Переноси их в preferences только при явном смягчении: «желательно», «лучше», «если получится», «не обязательно». Центр Москвы, включая очевидные опечатки, возвращай канонически как hard.location="ЦАО". Фраза «в центре, не более 60 млн» означает hard.location="ЦАО" и hard.max_price=60000000.
- followup_outcome заполняй только при pending_scenario и только значением из pending_scenario.allowed_reply_outcomes. Типовые значения: accept / decline / ask_or_clarify / unexpected. Не считай согласие implicit: если ответ неочевиден, выбирай unexpected или ask_or_clarify.
- Если во входе активен pending contact_name и клиент явно просит вернуться к заявке, звонку или сбору контакта, верни resolved_intent="resume_contact", followup_outcome="resume_contact", requested_facts=[] и не выбирай ЖК заново.
- clarification — один короткий вопрос только при смысловой неоднозначности. Не объединяй в нём бюджет, локацию, комнатность или другие несколько вопросов.

Запрещено возвращать technical fields: operation, action, dialog_action, target, context_source, scope, needs_search, search_policy, intent_policy, search_profile, selected_option_name, constraints_patch.
Не придумывай факты о ЖК, ценах, ипотеке, сроках, метро, наличии или инфраструктуре. Dialog focus и прошлый текст — контекст для смысла, не evidence.
""".strip()

CANONICAL_REPAIR_PROMPT = """
Ты — canonical JSON repair pass для dialog state planner Ирины.
Ты НЕ отвечаешь клиенту и НЕ маршрутизируешь по фразам. Верни ровно один полный canonical JSON object.

Твоя задача: исправить только контрактные поля исходного плана по allowed_error_codes и structured_state.
Нельзя добавлять пользовательский текст, промпты, Jivo/raw IDs, телефоны, payload или секреты.
Если structured_state.visible_options/last_options уже содержит варианты и план явно относится к текущим вариантам без запроса новых объектов, корректная форма: action="answer_current_options", target="current_options", search_policy="forbidden", scope="all", selected_option_name=null, search_profile="none", constraints_patch empty. Сценарий/тема меняют intent/intent_policy, но сами по себе не требуют нового поиска.
Для search scope всегда "unknown" и target/search_policy = new_search/required.

Верни все required поля: action, dialog_action, target, search_policy, intent, intent_policy, scope, selected_option_name, confidence, clarification, search_profile, constraints_patch, facets, operator_contact, missing_fields, clarification_fields. reason можно коротко.
""".strip()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _apply_openrouter_reasoning_exclude(request_data: dict[str, Any]) -> None:
    enabled = _env("NMBOT_OPENROUTER_EXCLUDE_REASONING", "0").strip().lower() in {"1", "true", "yes", "on"}
    model = str(request_data.get("model") or "")
    if enabled and model.startswith("google/gemini"):
        request_data.setdefault("reasoning", {"exclude": True})


def _len_text(value: Any) -> int:
    return len(str(value or ""))


def _log_model_payload_metrics(stage: str, request_data: dict[str, Any]) -> None:
    """Append payload-size telemetry only; never write prompt/query contents."""
    try:
        params = request_data.get("parameters") if isinstance(request_data.get("parameters"), dict) else {}
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "model_payload_metrics",
            "stage": str(stage or "unknown"),
            "model": str(request_data.get("model") or ""),
            "service": str(request_data.get("service") or ""),
            "query_chars": _len_text(request_data.get("query")),
            "system_prompt_chars": _len_text(request_data.get("system_prompt")),
            "max_tokens": params.get("max_tokens"),
            "temperature": params.get("temperature"),
            "has_mcp": bool(request_data.get("mcp_servers")),
        }
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"model_payload_metrics-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


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


CANONICAL_ACTIONS = {"search", "answer_current_options", "recover_dialogue", "operator_contact", "clarify", "off_topic"}
CANONICAL_INTENTS = {"investment", "rental", "family", "life", "mortgage", "unknown"}
CANONICAL_INTENT_POLICIES = {"keep", "set", "change"}
CANONICAL_TARGETS = {"new_search", "current_options", "none", "operator"}
CANONICAL_SEARCH_POLICIES = {"required", "forbidden", "allowed"}
CANONICAL_SEARCH_PROFILES = {"generic", "family", "investment", "mortgage", "none"}
CANONICAL_CONSTRAINT_CATEGORIES = {"hard", "preferences", "unknown"}
CANONICAL_KEYS = {
    "action", "intent", "intent_policy", "target", "search_policy",
    "constraints_patch", "facets", "search_profile", "missing_fields", "clarification_fields", "scope",
    "dialog_action", "selected_option_name", "operator_contact", "clarification", "confidence",
}
CANONICAL_METADATA_KEYS = {"planner_raw_response"}
CANONICAL_SIGNAL_KEYS = CANONICAL_KEYS - {"dialog_action"}
CANONICAL_TECH_SIGNAL_KEYS = {
    "action", "dialog_action", "target", "search_policy", "intent_policy",
    "constraints_patch", "search_profile", "selected_option_name", "clarification_fields",
}
CANONICAL_REQUIRED_INPUT_KEYS = {
    "action", "dialog_action", "target", "search_policy", "intent", "intent_policy", "scope",
    "selected_option_name", "confidence", "clarification", "search_profile", "constraints_patch",
    "facets", "operator_contact", "missing_fields", "clarification_fields",
}
CANONICAL_OPERATOR_CONSENTS = {"none", "ask", "granted", "refused"}
SEMANTIC_OPERATIONS = {"search", "current_options", "select_option", "operator_contact", "clarify", "recover"}
SEMANTIC_KEYS = {
    "goal", "operation", "intent", "response_viewpoint", "constraints_delta", "reference", "scope", "confidence",
    "clarification", "facets", "operator_contact", "missing_fields", "followup_outcome",
    "operator_consent",
    "resolved_subject", "resolved_intent", "requested_facts", "facts_needed", "requires_enrichment", "focus_action", "domain_relation",
    "named_object_reference", "requests_new_objects", "refers_to_existing_objects",
}
SEMANTIC_ONLY_KEYS = SEMANTIC_KEYS - CANONICAL_KEYS
SEMANTIC_PARAM_ALLOWLIST = {
    "location", "locations", "district", "districts", "metro", "near_metro",
    "rooms", "room_type", "max_price", "max_budget_m", "min_price",
    "area_min_m2", "area_max_m2", "finishing", "renovation", "ready",
    "stage", "ready_quarter", "delivery_visible", "project_ready_secondary",
    "property_metro", "schools", "kindergartens", "parks", "shops",
    "family_infrastructure", "purpose", "scenario", "topic", "mortgage",
    "discount", "installment", "payment_by_installments",
}
SEMANTIC_PARAM_ALIASES = {
    "budget_max": "max_price",
    "price_max": "max_price",
    "max_budget": "max_price",
    "budget": "max_price",
    "room_count": "rooms",
    "rooms_count": "rooms",
    "location_name": "location",
    "locations_name": "location",
    "district_name": "district",
    "metro_name": "metro",
}
SEMANTIC_SENSITIVE_KEY_RE = re.compile(r"phone|телефон|contact|client_id|chat_id|site_id|sender|token|secret|raw|payload|dialog_window", re.I)
AVAILABLE_FACT_FIELDS = [
    "name", "location", "district", "price", "price_min", "price_range",
    "rooms", "room_formats", "area", "ready", "finishing", "metro",
    "developer", "property_class", "infrastructure", "schools",
    "kindergartens", "parks", "yards", "playgrounds", "clinics",
    "sales_count", "sales_date", "ads_count", "discount",
    "parking", "parking_price", "parking_inventory", "apartment_price", "apartment_inventory", "mortgage_terms", "readiness", "schools",
]
FOLLOWUP_OUTCOME_DEFAULTS = {"accept", "decline", "ask_or_clarify", "unexpected"}


def _normalize_followup_outcome(value: Any, pending_scenario: dict[str, Any] | None = None) -> str | None:
    allowed_raw = pending_scenario.get("allowed_reply_outcomes") if isinstance(pending_scenario, dict) else None
    allowed = {str(item).strip() for item in allowed_raw} if isinstance(allowed_raw, list) else set(FOLLOWUP_OUTCOME_DEFAULTS)
    allowed = {item for item in allowed if item}
    if not allowed:
        allowed = set(FOLLOWUP_OUTCOME_DEFAULTS)
    text = str(value or "").strip()
    return text if text in allowed else None


def _state_primary_intent(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "unknown"
    for source in (state, state.get("params") if isinstance(state.get("params"), dict) else {}):
        value = str((source or {}).get("primary_intent") or (source or {}).get("active_scenario") or (source or {}).get("purpose") or "").strip()
        if value in CANONICAL_INTENTS and value != "unknown":
            return value
    return "unknown"


def _state_has_active_search(state: dict[str, Any] | None) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(state.get("last_search_snapshot") or state.get("visible_options") or state.get("last_options") or state.get("params"))


def _state_option_name_by_reference(state: dict[str, Any] | None, reference: Any) -> str | None:
    if reference in (None, "", [], {}):
        return None
    options: list[Any] = []
    if isinstance(state, dict):
        for key in ("visible_options", "last_options"):
            value = state.get(key)
            if isinstance(value, list):
                options.extend(value)
    if isinstance(reference, (int, float)) and not isinstance(reference, bool):
        idx = int(reference) - 1
        if 0 <= idx < len(options) and isinstance(options[idx], dict):
            name = str(options[idx].get("name") or "").strip()
            return name or None
    text = str(reference).strip()
    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(options) and isinstance(options[idx], dict):
            name = str(options[idx].get("name") or "").strip()
            return name or None
    return text or None


def _empty_constraints() -> dict[str, Any]:
    return {"hard": {}, "preferences": {}, "unknown": {}}


def _safe_constraints_delta(value: Any) -> dict[str, Any]:
    return _semantic_safe_constraints_delta(value)


def _semantic_from_canonical(data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action") or "").strip()
    dialog_action = str(data.get("dialog_action") or "").strip()
    intent = data.get("intent") if data.get("intent") in CANONICAL_INTENTS else "unknown"
    response_viewpoint = data.get("response_viewpoint") if data.get("response_viewpoint") in {"investment", "rental", "family", "life", "financing", "unchanged"} else None
    if response_viewpoint is None:
        response_viewpoint = "financing" if intent == "mortgage" else (intent if intent in {"investment", "rental", "family", "life"} else "unchanged")
    selected = data.get("selected_option_name")
    if action == "off_topic":
        operation = "off_topic"
    elif action == "search":
        operation = "search"
    elif action == "operator_contact":
        operation = "operator_contact"
    elif action == "answer_current_options" and (dialog_action == "select_option" or selected):
        operation = "select_option"
    elif action == "answer_current_options":
        operation = "current_options"
    elif action == "clarify":
        operation = "clarify"
    else:
        operation = "recover"
    return {
        "operation": operation,
        "intent": intent,
        "response_viewpoint": response_viewpoint,
        "constraints_delta": _safe_constraints_delta(data.get("constraints_delta") if "constraints_delta" in data else data.get("constraints_patch")),
        "reference": selected if selected is not None else data.get("reference"),
        "scope": data.get("scope") if data.get("scope") in ALLOWED_SCOPES else "unknown",
        "confidence": data.get("confidence"),
        "clarification": str(data.get("clarification") or data.get("clarification_question") or "").strip(),
        "facets": data.get("facets") if isinstance(data.get("facets"), dict) else {},
        "operator_contact": data.get("operator_contact") if isinstance(data.get("operator_contact"), dict) else {"requested": False, "consent": "none"},
        "missing_fields": data.get("missing_fields") if isinstance(data.get("missing_fields"), list) else [],
        "reason": str(data.get("reason") or "").strip(),
    }


def _normalize_semantic_plan(data: dict[str, Any]) -> dict[str, Any]:
    source = data if any(key in data for key in {"goal", "user_goal", "constraints_delta", "selected_reference", "operation", "reference"}) else _semantic_from_canonical(data)
    result = _normalize_semantic_planner_result_v2(source, available_fact_fields=AVAILABLE_FACT_FIELDS)
    semantic = _semantic_result_to_dict(result)
    semantic.update({
        "goal": str(source.get("goal") or "").strip(),
        "requested_facts": [str(item).strip() for item in (source.get("requested_facts") or []) if str(item).strip()],
        "intent": "mortgage" if result.response_viewpoint == "financing" else (result.response_viewpoint if result.response_viewpoint != "unchanged" else "unknown"),
        "constraints_delta": result.constraints_delta,
        "reference": result.selected_reference,
        "facets": {item: True for item in result.requested_comparison},
        "operator_contact": source.get("operator_contact") if isinstance(source.get("operator_contact"), dict) else {"requested": False, "consent": "none"},
        "missing_fields": list(result.facts_needed),
        "semantic_valid": not result.errors,
        "semantic_errors": list(result.errors),
        "operator_consent": result.operator_consent,
    })
    if result.raw_legacy_operation:
        semantic["operation"] = result.raw_legacy_operation
    semantic["followup_outcome"] = _normalize_followup_outcome(data.get("followup_outcome"))
    raw_response = data.get("planner_raw_response")
    if isinstance(raw_response, str) and raw_response:
        semantic["planner_raw_response"] = raw_response
    return semantic


def _derive_canonical_from_semantic(semantic: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    semantic_result = _normalize_semantic_planner_result_v2(semantic, available_fact_fields=AVAILABLE_FACT_FIELDS)
    decision = _derive_runtime_decision(semantic_result, state)
    operation = str(semantic.get("operation") or semantic_result.raw_legacy_operation or "semantic")
    intent = decision.intent if decision.intent in CANONICAL_INTENTS else "unknown"
    current_intent = _state_primary_intent(state)
    if intent == "unknown":
        intent_policy = "keep"
    elif current_intent == "unknown":
        intent_policy = "set"
    elif intent == current_intent:
        intent_policy = "keep"
    else:
        intent_policy = "change"
    reference_name = decision.selected_option_name
    requested_scope = decision.scope
    constraints = decision.constraints_patch
    facets = semantic.get("facets") if isinstance(semantic.get("facets"), dict) else {}
    confidence = float(semantic.get("confidence") or 0.0)
    clarification = str(semantic.get("clarification") or "").strip()
    operator_contact = semantic.get("operator_contact") if isinstance(semantic.get("operator_contact"), dict) else {"requested": False, "consent": "none"}
    direct_consent = semantic.get("operator_consent")
    if isinstance(direct_consent, bool):
        operator_contact = {
            "requested": True,
            "consent": "granted" if direct_consent else "refused",
        }
    missing_fields = list(decision.facts_needed)

    canonical = _default_canonical_plan(reason=str(semantic.get("reason") or ""))
    canonical.update({
        "confidence": confidence,
        "intent": intent,
        "intent_policy": intent_policy,
        "selected_option_name": None,
        "constraints_patch": _empty_constraints(),
        "facets": facets,
        "operator_contact": operator_contact,
        "missing_fields": [str(item) for item in missing_fields],
        "clarification_fields": [str(item) for item in missing_fields],
        "clarification": clarification,
        "reason": str(semantic.get("reason") or ""),
        "canonical_errors": list(semantic.get("semantic_errors") or []),
        "resolved_subject": semantic.get("resolved_subject"),
        "resolved_intent": semantic.get("resolved_intent"),
        "requested_facts": list(semantic.get("requested_facts") or []),
        "facts_needed": list(semantic.get("facts_needed") or semantic.get("missing_fields") or []),
        "requires_enrichment": bool(semantic.get("requires_enrichment")),
        "focus_action": str(semantic.get("focus_action") or "keep"),
        "domain_relation": str(semantic.get("domain_relation") or "unknown"),
    })
    if decision.action == "search":
        dialog_action = decision.dialog_action
        search_profile = decision.search_profile
        canonical.update({
            "action": "search", "dialog_action": dialog_action, "target": "new_search", "search_policy": "required",
            "scope": "unknown", "selected_option_name": None, "constraints_patch": constraints,
            "search_profile": search_profile,
        })
    elif decision.action in {
        "answer_current_options",
        "answer_from_current_options",
        "select_option",
        "answer_selected_option",
    }:
        scope = decision.scope if decision.scope in {"one", "all"} else ("one" if reference_name else "all")
        dialog_action = decision.dialog_action
        current_intent_policy = decision.intent_policy
        canonical.update({
            "action": "answer_current_options", "dialog_action": dialog_action, "target": "current_options",
            "search_policy": "forbidden", "intent_policy": current_intent_policy,
            "scope": scope, "selected_option_name": reference_name if scope == "one" else None,
            "constraints_patch": _empty_constraints(), "search_profile": "none",
        })
    semantic_goal = str(semantic.get("goal") or "").strip()
    requested_facts = semantic.get("requested_facts") if isinstance(semantic.get("requested_facts"), list) else []
    if semantic_goal in {"answer_open_question", "answer_current"} and requested_facts:
        canonical.update({
            "action": "answer_current_options",
            "dialog_action": "consultation_answer",
            "target": "current_options",
            "search_policy": "forbidden",
            "scope": "one" if reference_name else "all",
            "selected_option_name": reference_name,
            "search_profile": "none",
            "open_question": True,
        })
    elif decision.action == "operator_contact":
        canonical.update({
            "action": "operator_contact", "dialog_action": "operator_live_check", "target": "operator",
            "search_policy": "forbidden", "scope": "unknown", "search_profile": "none",
            "constraints_patch": _empty_constraints(),
        })
    elif decision.action == "off_topic":
        canonical.update({
            "action": "off_topic", "dialog_action": "conversation_answer", "target": "none",
            "search_policy": "forbidden", "scope": "unknown", "search_profile": "none",
            "constraints_patch": _empty_constraints(), "intent_policy": "keep",
        })
    elif decision.action == "clarify":
        canonical.update({
            "action": "clarify", "dialog_action": "ask_clarification", "target": "none",
            "search_policy": "forbidden", "scope": "unknown", "search_profile": "none",
            "constraints_patch": _empty_constraints(),
        })
    elif semantic_goal:
        canonical.update({
            "action": "recover_dialogue", "dialog_action": "ask_clarification", "target": "none",
            "search_policy": "forbidden", "scope": "unknown", "search_profile": "none",
            "constraints_patch": _empty_constraints(),
        })
    errors = list(canonical.get("canonical_errors") or [])
    _add_canonical_semantic_errors(canonical, errors)
    canonical["canonical_errors"] = sorted(set(errors))
    canonical["canonical_valid"] = not canonical["canonical_errors"]
    raw_response = semantic.get("planner_raw_response")
    if isinstance(raw_response, str) and raw_response:
        canonical["planner_raw_response"] = raw_response
    canonical["semantic_plan"] = {key: semantic[key] for key in semantic if key not in {"planner_raw_response"}}
    canonical["followup_outcome"] = semantic.get("followup_outcome")
    canonical["derived_decision"] = _semantic_decision_to_dict(decision)
    return canonical


def _canonical_constraints_empty(constraints_patch: Any) -> bool:
    if not isinstance(constraints_patch, dict):
        return False
    for category in CANONICAL_CONSTRAINT_CATEGORIES:
        fields = constraints_patch.get(category)
        if fields not in ({}, None):
            return False
    return True


def _add_canonical_semantic_errors(plan: dict[str, Any], errors: list[str]) -> None:
    action = str(plan.get("action") or "")
    target = str(plan.get("target") or "")
    search_policy = str(plan.get("search_policy") or "")
    scope = str(plan.get("scope") or "")
    selected = plan.get("selected_option_name")
    intent = str(plan.get("intent") or "")
    intent_policy = str(plan.get("intent_policy") or "")
    dialog_action = str(plan.get("dialog_action") or "")
    search_profile = str(plan.get("search_profile") or "")
    constraints_patch = plan.get("constraints_patch")
    facets = plan.get("facets") if isinstance(plan.get("facets"), dict) else {}

    if action == "search":
        if target != "new_search" or search_policy != "required":
            errors.append("search_requires_new_search_required")
        if scope != "unknown":
            errors.append("search_scope_must_be_unknown")
        if selected is not None:
            errors.append("search_selected_option_must_be_null")
        if intent_policy not in {"set", "change", "keep"}:
            errors.append("search_intent_policy_invalid")
        if search_profile not in {"generic", "family", "investment", "mortgage"}:
            errors.append("search_profile_required_for_search")
        if dialog_action not in {"new_search", "update_search", "expand_more_options"}:
            errors.append("search_dialog_action_invalid")

    if action == "answer_current_options":
        if target != "current_options" or search_policy != "forbidden":
            errors.append("current_options_requires_current_forbidden")
        if search_profile != "none":
            errors.append("current_options_search_profile_must_be_none")
        if not _canonical_constraints_empty(constraints_patch):
            errors.append("current_options_constraints_must_be_empty")
        if scope not in {"one", "all"}:
            errors.append("current_options_scope_must_be_one_or_all")
        if scope == "one" and selected is None:
            errors.append("current_options_one_selected_required")
        if scope == "all" and selected is not None:
            errors.append("current_options_all_selected_must_be_null")
        if facets.get("family_mortgage") or facets.get("mortgage"):
            if dialog_action != "consultation_answer" or intent != "mortgage" or scope != "all" or selected is not None:
                errors.append("family_mortgage_current_options_semantic_mismatch")

    if action == "operator_contact":
        if target != "operator" or search_policy != "forbidden":
            errors.append("operator_requires_operator_forbidden")
        if search_profile != "none" or not _canonical_constraints_empty(constraints_patch):
            errors.append("operator_must_not_search")
        if selected is not None or scope != "unknown":
            errors.append("operator_scope_selected_invalid")

    if action in {"clarify", "recover_dialogue", "off_topic"}:
        if target != "none" or search_policy != "forbidden":
            errors.append("non_action_requires_none_forbidden")
        if search_profile != "none" or not _canonical_constraints_empty(constraints_patch):
            errors.append("non_action_must_not_search")


def _default_canonical_plan(*, reason: str = "") -> dict[str, Any]:
    return {
        "action": "recover_dialogue",
        "intent": "unknown",
        "intent_policy": "keep",
        "scope": "unknown",
        "target": "none",
        "search_policy": "forbidden",
        "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
        "facets": {},
        "search_profile": "none",
        "missing_fields": [],
        "clarification_fields": [],
        "canonical_valid": False,
        "canonical_errors": [reason] if reason else [],
    }


def _normalize_canonical_plan(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    missing_required = sorted(key for key in CANONICAL_REQUIRED_INPUT_KEYS if key not in data)
    errors.extend(f"missing_required:{key}" for key in missing_required)
    action = str(data.get("action") or "").strip()
    dialog_action_raw = str(data.get("dialog_action") or "").strip()
    intent = str(data.get("intent") or "").strip()
    intent_policy = str(data.get("intent_policy") or "keep").strip()
    scope = str(data.get("scope") or "unknown").strip()
    target = str(data.get("target") or "").strip()
    search_policy = str(data.get("search_policy") or "").strip()
    confidence_raw = data.get("confidence")
    try:
        confidence = float(confidence_raw if confidence_raw is not None else 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
        errors.append("invalid_confidence")
    if action not in CANONICAL_ACTIONS:
        errors.append("invalid_action")
        action = "recover_dialogue"
    if dialog_action_raw not in ALLOWED_DIALOG_ACTIONS:
        errors.append("invalid_dialog_action")
        dialog_action = DEFAULT_DIALOG_ACTION
    else:
        dialog_action = dialog_action_raw
    if intent not in CANONICAL_INTENTS:
        errors.append("invalid_intent")
        intent = "unknown"
    if intent_policy not in CANONICAL_INTENT_POLICIES:
        errors.append("invalid_intent_policy")
        intent_policy = "keep"
    if scope not in ALLOWED_SCOPES:
        errors.append("invalid_scope")
        scope = "unknown"
    if target not in CANONICAL_TARGETS:
        errors.append("invalid_target")
        target = "none"
    if search_policy not in CANONICAL_SEARCH_POLICIES:
        errors.append("invalid_search_policy")
        search_policy = "forbidden"
    constraints_patch_raw = data.get("constraints_patch")
    constraints_patch: dict[str, Any] = {"hard": {}, "preferences": {}, "unknown": {}}
    if not isinstance(constraints_patch_raw, dict):
        errors.append("invalid_constraints_patch")
    else:
        for category, fields in constraints_patch_raw.items():
            if category not in CANONICAL_CONSTRAINT_CATEGORIES or not isinstance(fields, dict):
                errors.append("invalid_constraints_category")
                continue
            constraints_patch[category] = dict(fields)
    facets = data.get("facets") if isinstance(data.get("facets"), dict) else {}
    if "facets" in data and not isinstance(data.get("facets"), dict):
        errors.append("invalid_facets")
    search_profile = str(data.get("search_profile") or ("generic" if action == "search" else "none")).strip().lower()
    if search_profile not in CANONICAL_SEARCH_PROFILES:
        errors.append("invalid_search_profile")
        search_profile = "generic" if action == "search" else "none"
    missing_fields = data.get("missing_fields") if isinstance(data.get("missing_fields"), list) else []
    if "missing_fields" in data and (not isinstance(data.get("missing_fields"), list) or any(not isinstance(item, str) for item in data.get("missing_fields") or [])):
        errors.append("invalid_missing_fields")
        missing_fields = []
    clarification_fields = data.get("clarification_fields") if isinstance(data.get("clarification_fields"), list) else []
    if "clarification_fields" in data and (not isinstance(data.get("clarification_fields"), list) or any(not isinstance(item, str) for item in data.get("clarification_fields") or [])):
        errors.append("invalid_clarification_fields")
        clarification_fields = []
    selected_option_name = data.get("selected_option_name") if data.get("selected_option_name") else None
    if selected_option_name is not None and not isinstance(selected_option_name, str):
        errors.append("invalid_selected_option_name")
        selected_option_name = None
    if not isinstance(data.get("clarification"), str):
        errors.append("invalid_clarification")
    clarification = str(data.get("clarification") if isinstance(data.get("clarification"), str) else "").strip()
    operator_contact_raw = data.get("operator_contact")
    operator_contact = {"requested": False, "consent": "none"}
    if not isinstance(operator_contact_raw, dict):
        errors.append("invalid_operator_contact")
    else:
        requested = operator_contact_raw.get("requested")
        consent = str(operator_contact_raw.get("consent") or "none").strip()
        if not isinstance(requested, bool):
            errors.append("invalid_operator_contact_requested")
            requested = False
        if consent not in CANONICAL_OPERATOR_CONSENTS:
            errors.append("invalid_operator_contact_consent")
            consent = "none"
        operator_contact = {"requested": requested, "consent": consent}
    reason = str(data.get("reason") or "").strip()
    normalized = {
        "action": action,
        "dialog_action": dialog_action,
        "confidence": confidence,
        "intent": intent,
        "intent_policy": intent_policy,
        "scope": scope,
        "target": target,
        "search_policy": search_policy,
        "selected_option_name": selected_option_name,
        "constraints_patch": constraints_patch,
        "facets": facets,
        "search_profile": search_profile,
        "operator_contact": operator_contact,
        "missing_fields": [str(item) for item in missing_fields],
        "clarification_fields": [str(item) for item in clarification_fields],
        "clarification": clarification,
        "reason": reason,
        "canonical_valid": not errors,
        "canonical_errors": sorted(set(errors)),
    }
    _add_canonical_semantic_errors(normalized, errors)
    normalized["canonical_valid"] = not errors
    normalized["canonical_errors"] = sorted(set(errors))
    raw_response = data.get("planner_raw_response")
    if isinstance(raw_response, str) and raw_response:
        normalized["planner_raw_response"] = raw_response
    return normalized


def _legacy_fields_from_canonical(canonical: dict[str, Any]) -> dict[str, Any]:
    """Deterministic compatibility adapter for old runtime fields.

    The planner prompt asks the model for a single canonical JSON contract.  Any
    legacy fields consumed by older code are derived here, not generated twice by
    the model.
    """
    action = str(canonical.get("action") or "recover_dialogue")
    dialog_action = normalize_dialog_action(str(canonical.get("dialog_action") or ""))
    if dialog_action == DEFAULT_DIALOG_ACTION:
        dialog_action = {
            "search": "new_search",
            "answer_current_options": "continue_from_memory",
            "operator_contact": "operator_live_check",
            "clarify": "ask_clarification",
            "recover_dialogue": DEFAULT_DIALOG_ACTION,
        }.get(action, DEFAULT_DIALOG_ACTION)
    selected_option_name = canonical.get("selected_option_name") if canonical.get("selected_option_name") else None
    scope = _normalize_choice(canonical.get("scope"), ALLOWED_SCOPES, "unknown")
    return {
        "mode": "search_action" if action in {"search", "operator_contact"} or dialog_action in {"select_option", "operator_live_check"} else "conversation",
        "dialog_action": dialog_action,
        "params_delta": {},
        "selected_option_action": "set" if selected_option_name else ("clear" if scope == "all" else "keep"),
        "selected_option_name": selected_option_name,
        "rejected_options_add": [],
        "visible_options_policy": "rebuild" if action == "search" else "keep",
        "numeric_choice_policy": "reject",
        "mcp_request_patch": None,
        "scope": scope,
        "clarification_question": str(canonical.get("clarification_question") or canonical.get("clarification") or ""),
        "profile": str(canonical.get("search_profile") or "none"),
        "reason": str(canonical.get("reason") or ""),
        "fallback_used": False,
    }


def _with_canonical_fields(plan: dict[str, Any], data: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    if any(key in data for key in SEMANTIC_KEYS) or any(key in data for key in CANONICAL_SIGNAL_KEYS):
        semantic = _normalize_semantic_plan(data)
        canonical = _derive_canonical_from_semantic(semantic, state)
        strict_source = _normalize_canonical_plan(data) if any(key in data for key in CANONICAL_TECH_SIGNAL_KEYS) else None
        legacy_full_canonical = strict_source is not None and CANONICAL_REQUIRED_INPUT_KEYS <= set(data) and not any(key in data for key in SEMANTIC_ONLY_KEYS)
        if legacy_full_canonical and str(strict_source.get("action") or "") == "answer_current_options":
            # Full legacy canonical inputs are allowed to carry an explicit
            # current-options scope/selection. Preserve that compatibility bit;
            # the semantic model path still cannot set scope because semantic
            # payloads do not satisfy legacy_full_canonical.
            for key in ("scope", "selected_option_name", "facets", "intent", "intent_policy"):
                canonical[key] = strict_source.get(key)
        if strict_source is not None and strict_source.get("canonical_errors"):
            source_errors = list(strict_source.get("canonical_errors") or [])
            canonical["source_canonical_errors"] = source_errors
            severe_prefixes = (
                "missing_required", "invalid_confidence", "invalid_constraints", "invalid_facets",
                "invalid_operator_contact", "invalid_missing_fields", "invalid_clarification_fields",
                "invalid_selected_option_name", "invalid_clarification",
            )
            severe = [error for error in source_errors if any(str(error).startswith(prefix) for prefix in severe_prefixes)]
            if severe:
                canonical["canonical_errors"] = sorted(set(list(canonical.get("canonical_errors") or []) + severe))
                canonical["canonical_valid"] = False
        if strict_source is not None and str(strict_source.get("action") or "") and str(strict_source.get("action") or "") != str(canonical.get("action") or ""):
            source_errors = list(canonical.get("source_canonical_errors") or [])
            source_errors.append("source_canonical_action_ignored")
            canonical["source_canonical_errors"] = sorted(set(source_errors))
        compatibility = {
            key: value
            for key, value in plan.items()
            if key not in CANONICAL_KEYS and key not in CANONICAL_METADATA_KEYS and key not in {"confidence", "reason", "fallback_used"}
        }
        metadata = {
            key: value
            for source in (data, plan)
            for key, value in source.items()
            if key in CANONICAL_METADATA_KEYS and isinstance(value, str) and value
        }
        semantic_passthrough: dict[str, Any] = {}
        if isinstance(semantic.get("requests_new_objects"), bool):
            semantic_passthrough["requests_new_objects"] = semantic["requests_new_objects"]
        if isinstance(semantic.get("refers_to_existing_objects"), bool):
            semantic_passthrough["refers_to_existing_objects"] = semantic["refers_to_existing_objects"]
        if semantic.get("scenario_needs"):
            semantic_passthrough["scenario_needs"] = semantic["scenario_needs"]
        if semantic.get("named_object_reference"):
            semantic_passthrough["named_object_reference"] = semantic["named_object_reference"]
            # Named-object lookup is a V2 semantic action which the legacy
            # canonical schema represents as recover_dialogue. Preserve the
            # already-normalized current-turn constraints for the V2 adapter:
            # lookup ignores them while fetching the named ЖК, but the response
            # needs them to compare confirmed facts with the client's budget.
            semantic_passthrough["constraints_delta"] = semantic.get("constraints_delta") or _empty_constraints()
        else:
            semantic_delta = semantic.get("constraints_delta") if isinstance(semantic.get("constraints_delta"), dict) else {}
            semantic_hard = semantic_delta.get("hard") if isinstance(semantic_delta.get("hard"), dict) else {}
            if semantic_hard.get("down_payment") is not None:
                # Current-options canonical plans intentionally keep an empty
                # search patch. V2 still needs this one explicit state-only
                # financing fact to continue the consent/handoff flow.
                semantic_passthrough["constraints_delta"] = {
                    "hard": {"down_payment": semantic_hard["down_payment"]},
                    "preferences": {},
                    "unknown": {},
                }
        return _legacy_fields_from_canonical(canonical) | compatibility | canonical | semantic_passthrough | metadata
    return plan | _default_canonical_plan(reason="canonical_fields_absent")


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
    _apply_openrouter_reasoning_exclude(request_data)
    _log_model_payload_metrics("followup_classifier", request_data)

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
                    "scope": _normalize_choice(data.get("scope"), ALLOWED_SCOPES, "unknown"),
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
    last_turn: dict[str, Any] | None = None,
    last_response_text: str = "",
    search_response_text: str = "",
    visible_response_text: str = "",
    pending_scenario: dict[str, Any] | None = None,
    selected_object: dict[str, Any] | None = None,
    dialog_focus: dict[str, Any] | None = None,
    allowed_subjects: list[str] | None = None,
    allowed_facts: list[str] | None = None,
    subject_fact_map: dict[str, Any] | None = None,
    dynamic_fields: list[str] | None = None,
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
            "scope": "unknown",
            "followup_outcome": None,
            "clarification_question": "",
            "reason": "dialog planner disabled",
            "fallback_used": True,
        } | _default_canonical_plan(reason="dialog planner disabled")

    payload = {
        "user_text": user_text,
        "state": state or {},
        "last_turn": last_turn or (state or {}).get("last_turn") or {},
        "last_response_text": last_response_text,
        "visible_response_text": visible_response_text,
        "search_response_text": search_response_text,
        "available_fact_fields": AVAILABLE_FACT_FIELDS,
        "selected_object": selected_object or {},
        "dialog_focus": dialog_focus or {},
        "allowed_subjects": [str(x) for x in (allowed_subjects or [])],
        "allowed_facts": [str(x) for x in (allowed_facts or AVAILABLE_FACT_FIELDS)],
        "subject_fact_map": subject_fact_map or {},
        "dynamic_fields": [str(x) for x in (dynamic_fields or [])],
    }
    if pending_scenario:
        payload["pending_scenario"] = pending_scenario
    request_data = {
        "query": _trim(payload, 9000),
        "service": "openrouter",
        "model": model or _env("NMBOT_DIALOG_PLANNER_MODEL", _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview")),
        "system_prompt": DIALOG_STATE_PLANNER_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 900},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }
    _apply_openrouter_reasoning_exclude(request_data)
    _log_model_payload_metrics("dialog_planner", request_data)

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
        "scope": "unknown",
        "followup_outcome": None,
        "clarification_question": "",
        "reason": "planner fallback",
        "fallback_used": True,
    } | _default_canonical_plan(reason="planner fallback")
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
                mcp_request_patch = data.get("mcp_request_patch") if isinstance(data.get("mcp_request_patch"), dict) else None
                plan = {
                    "dialog_action": normalize_dialog_action(str(data.get("dialog_action") or "")),
                    "mode": normalize_dialog_mode(str(data.get("mode") or ""), str(data.get("dialog_action") or "")),
                    "confidence": float(data.get("confidence") or 0.0),
                    "params_delta": params_delta,
                    "selected_option_action": _normalize_choice(data.get("selected_option_action"), ALLOWED_SELECTED_OPTION_ACTIONS, "keep"),
                    "selected_option_name": data.get("selected_option_name") if data.get("selected_option_name") else None,
                    "rejected_options_add": [str(x) for x in rejected if str(x).strip()][:5],
                    "visible_options_policy": _normalize_choice(data.get("visible_options_policy"), ALLOWED_VISIBLE_OPTIONS_POLICIES, "keep"),
                    "numeric_choice_policy": _normalize_choice(data.get("numeric_choice_policy"), ALLOWED_NUMERIC_CHOICE_POLICIES, "accept"),
                    "scope": _normalize_choice(data.get("scope"), ALLOWED_SCOPES, "unknown"),
                    "followup_outcome": _normalize_followup_outcome(data.get("followup_outcome"), pending_scenario),
                    "mcp_request_patch": mcp_request_patch,
                    "clarification_question": str(data.get("clarification_question") or "").strip(),
                    "reason": str(data.get("reason") or ""),
                    "fallback_used": False,
                    "planner_raw_response": str(raw),
                }
                return _with_canonical_fields(plan, data, state=state if isinstance(state, dict) else {})
            await asyncio.sleep(1)
    except Exception as e:
        return fallback | {"reason": f"{type(e).__name__}: {e}"}

    return fallback | {"reason": "timeout"}


def _intent_plan_v3_fallback(reason: str, *, raw: str = "") -> dict[str, Any]:
    plan = {
        "schema_version": 3,
        "goal": "clarify",
        "viewpoint": "unchanged",
        "selected_option_name": None,
        "named_object_reference": None,
        "comparison_option_names": [],
        "requested_facts": [],
        "constraints_delta": {},
        "operator_consent": None,
        "explicit_operator_request": False,
        "clarification": None,
        "confidence": 0.0,
        "fallback_used": True,
        "reason": reason,
    }
    if raw:
        plan["planner_raw_response"] = raw
    return plan


def _normalize_v3_soft_room_preference(user_text: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep explicitly optional room formats out of hard search constraints."""
    text = re.sub(r"\s+", " ", str(user_text or "").casefold().replace("ё", "е")).strip()
    optional_studio = bool(
        re.search(r"\b(?:можно|желательно|подойдет|подойдет|не обязательно)\b[^.!?]{0,40}\bстуди\w*", text)
        or re.search(r"\bстуди\w*\b[^.!?]{0,20}\b(?:можно|подойдет|подойдет)\b", text)
    )
    if not optional_studio:
        return data
    delta = data.get("constraints_delta")
    if not isinstance(delta, dict):
        return data
    hard = dict(delta.get("hard") or {}) if isinstance(delta.get("hard"), dict) else {}
    rooms = hard.get("rooms")
    if str(rooms or "").strip().casefold() not in {"studio", "studios", "студия", "студии", "студию"}:
        return data
    hard.pop("rooms", None)
    preferences = dict(delta.get("preferences") or {}) if isinstance(delta.get("preferences"), dict) else {}
    preferences.setdefault("rooms_preference", "studio")
    normalized = dict(data)
    normalized["constraints_delta"] = {**delta, "hard": hard, "preferences": preferences}
    normalized["planner_adjustments"] = ["soft_room_preference"]
    return normalized


async def plan_intent_v3(
    session: aiohttp.ClientSession,
    *,
    user_text: str,
    state: dict[str, Any] | None = None,
    last_turn: dict[str, Any] | None = None,
    last_response_text: str = "",
    search_response_text: str = "",
    visible_response_text: str = "",
    pending_scenario: dict[str, Any] | None = None,
    selected_object: dict[str, Any] | None = None,
    dialog_focus: dict[str, Any] | None = None,
    allowed_subjects: list[str] | None = None,
    allowed_facts: list[str] | None = None,
    subject_fact_map: dict[str, Any] | None = None,
    dynamic_fields: list[str] | None = None,
    model: str | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    """Additive IntentPlan V3 gateway call.

    This is intentionally fail-closed: callers validate/derive before touching
    runtime state, and network/parse failures return a traceable clarify plan.
    """

    allowed_fact_values = list(dict.fromkeys(str(x) for x in (allowed_facts or AVAILABLE_FACT_FIELDS) if str(x).strip()))
    payload = {
        "user_text": user_text,
        "state": state or {},
        "last_turn": last_turn or (state or {}).get("last_turn") or {},
        "last_response_text": last_response_text,
        "visible_response_text": visible_response_text,
        "search_response_text": search_response_text,
        "selected_object": selected_object or {},
        "dialog_focus": dialog_focus or {},
        "allowed_subjects": [str(x) for x in (allowed_subjects or [])],
        "allowed_facts": allowed_fact_values,
        "subject_fact_map": subject_fact_map or {},
        "dynamic_fields": [str(x) for x in (dynamic_fields or [])],
        "pending_scenario": pending_scenario or {},
    }
    json_schema = deepcopy(INTENT_PLAN_V3_JSON_SCHEMA)
    json_schema["properties"]["requested_facts"]["items"] = {
        "type": "string",
        "enum": allowed_fact_values,
    }
    request_data = {
        "query": _trim(payload, 9000),
        "service": "openrouter",
        "model": model or _env("NMBOT_INTENT_PLAN_V3_MODEL", _env("NMBOT_DIALOG_PLANNER_MODEL", _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview"))),
        "system_prompt": INTENT_PLAN_V3_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 600},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
        "json_schema": json_schema,
    }
    _apply_openrouter_reasoning_exclude(request_data)
    _log_model_payload_metrics("intent_plan_v3", request_data)

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
            return _intent_plan_v3_fallback("task_id missing")

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
                if not data:
                    return _intent_plan_v3_fallback("empty_json", raw=str(raw))
                data = dict(data)
                data = _normalize_v3_soft_room_preference(user_text, data)
                data["planner_raw_response"] = str(raw)
                data["fallback_used"] = False
                return data
            await asyncio.sleep(1)
    except Exception as e:
        return _intent_plan_v3_fallback(f"{type(e).__name__}: {e}")

    return _intent_plan_v3_fallback("timeout")


async def repair_canonical_plan(
    session: aiohttp.ClientSession,
    *,
    original_plan: dict[str, Any],
    allowed_error_codes: list[str],
    state: dict[str, Any] | None = None,
    model: str | None = None,
    timeout: int = 25,
) -> dict[str, Any]:
    """One bounded semantic repair for parsed-but-invalid canonical plans.

    The input is already sanitized by the API layer. This function only asks the
    model to return a full canonical JSON object and then reuses the normalizer.
    API/gateway/parse failures deliberately return a fallback plan so callers can
    fail closed without mutating state.
    """
    payload = {
        "original_sanitized_plan": original_plan,
        "allowed_error_codes": [str(code) for code in allowed_error_codes[:12]],
        "structured_state": state or {},
    }
    request_data = {
        "query": _trim(payload, 9000),
        "service": "openrouter",
        "model": model or _env("NMBOT_DIALOG_PLANNER_MODEL", _env("NMBOT_FOLLOWUP_MODEL", "google/gemini-3.1-flash-lite-preview")),
        "system_prompt": CANONICAL_REPAIR_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 900},
        "external_api_key": _required_env("OPENROUTER_API_KEY"),
    }
    _apply_openrouter_reasoning_exclude(request_data)
    _log_model_payload_metrics("dialog_planner_repair", request_data)

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
        "scope": "unknown",
        "clarification_question": "",
        "reason": "repair fallback",
        "fallback_used": True,
        "repair_attempted": True,
    } | _default_canonical_plan(reason="repair fallback")
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
            return fallback | {"reason": "repair task_id missing"}

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
                if not isinstance(data, dict) or not data:
                    return fallback | {"reason": "repair malformed output"}
                repaired = _with_canonical_fields({}, data)
                repaired["repair_attempted"] = True
                repaired["repair_source_errors"] = [str(code) for code in allowed_error_codes[:12]]
                return repaired
            await asyncio.sleep(1)
    except Exception as e:
        return fallback | {"reason": f"repair {type(e).__name__}: {e}"}

    return fallback | {"reason": "repair timeout"}
