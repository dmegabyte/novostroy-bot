import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import DialogFocus, ExecutableTurn, ExecutionResult, IntentGoal, LotExample, OptionCard, ResponseBrief, ResponsePlan, SafeTurnContext, SearchResult, SemanticPlan, Stage, StateDelta, TurnAction, to_jsonable
import nmbot_v2.ports as ports
import nmbot_v2.conversation as conversation_module
import nmbot_v2.response as response_module
import nmbot_v2.runtime as runtime_module
from nmbot_v2.response import build_response_plan, render_response
from nmbot_v2.runtime import TurnProcessor, _safe_execution_error_code
from nmbot_v2.conversation import build_native_conversation_answer
from nmbot_v2.response_composer import build_response_brief
from nmbot_v2.response_composer import ComposerAttemptResult, compose_response_one_shot_async
from nmbot_v2.state import ConversationState


class Planner:
    def __init__(self, plan):
        self.plan_value = plan

    def plan(self, context, state):
        return self.plan_value


class SearchService:
    def __init__(self, result=None, selected=None, fail=False, selected_fail=False):
        self.result = result
        self.selected = selected
        self.fail = fail
        self.selected_fail = selected_fail
        self.calls = 0
        self.selected_calls = 0

    def search(self, plan, state):
        self.calls += 1
        if self.fail:
            raise RuntimeError("transport")
        return self.result

    def enrich_selected(self, option, state, plan):
        self.selected_calls += 1
        if self.selected_fail:
            raise TimeoutError("slow")
        return self.selected or option


class SearchWithAttempts(SearchService):
    def __init__(self, result=None):
        super().__init__(result=result)
        self.last_attempts = (
            {"stage": "gateway_attempt", "model": "google/gemini", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-1", "duration_ms": 45, "parse_status": "ok", "raw": "secret"},
        )


class SelectedEnrichmentFailureSearch(SearchService):
    def enrich_selected(self, option, state, plan):
        self.selected_calls += 1
        self.last_fresh_facts = ()
        self.last_enrichment_error_code = "selected_enrichment_technical_failure"
        self.last_enrichment_trace = {
            "stage": "v2_option_enrichment",
            "enabled": True,
            "applied": False,
            "outcome": "technical_failure",
            "recovery": {"attempted": True, "count": 1, "classes": ["parse"], "final": "parse"},
        }
        return option


class Journal:
    def __init__(self):
        self.rows = []

    def append(self, result):
        self.rows.append(result)


class Trace:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


class Composer:
    def __init__(self, outputs=None, fail=False):
        self.outputs = list(outputs or [])
        self.fail = fail
        self.calls = []

    def compose(self, brief, *, repair_errors=()):
        self.calls.append((brief, repair_errors))
        if self.fail:
            raise RuntimeError("composer down")
        if self.outputs:
            return self.outputs.pop(0)
        names = [card.name for card in brief.canonical_cards[:3]]
        return '{"intro":"Нашла вариант.","options":[{"name":"' + names[0] + '","facts":"Цена или основные параметры указаны в карточке.","description":"Это можно спокойно сравнить с другими вариантами."}],"missing_note":"","final_question":"Какой вариант смотрим?"}'


class OneShotComposer:
    def __init__(self, result=None, fail=False):
        self.result = result
        self.fail = fail
        self.calls = []

    def compose_response(self, brief, *, fallback_text):
        self.calls.append((brief, fallback_text))
        if self.fail:
            raise RuntimeError("composer down")
        if self.result is not None:
            return self.result
        return ComposerAttemptResult(status="primary", text="Модельный ответ.\n\nКакой вариант смотрим?", attempts=1)


class ManagerRewriter:
    def __init__(self, text="Живой ответ менеджера.", fail=False):
        self.text = text
        self.fail = fail
        self.calls = []

    def rewrite_manager_answer(self, *, transcript, current_question, prepared_answer, brief):
        self.calls.append({"transcript": transcript, "current_question": current_question, "prepared_answer": prepared_answer, "brief": brief})
        if self.fail:
            raise RuntimeError("rewriter down")
        return self.text


class RetryInsideSearch:
    def __init__(self):
        self.provider_attempts = 0

    def search(self, plan, state):
        self.provider_attempts += 2
        return SearchResult.from_dict({"facts": [{"name": "Retry Park", "location": "Москва", "price": "от 10 млн рублей"}]})

    def enrich_selected(self, option, state, plan):
        return option


def ctx(text="текст"):
    return SafeTurnContext(conversation_ref="local", user_text=text)


def test_one_pipeline_for_search_and_selection_actions():
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]}))
    first = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))
    state = ConversationState.from_dict(first.state)
    selected = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Лучи"))).process(ctx("первый"), state)

    assert first.action == TurnAction.SEARCH
    assert selected.action == TurnAction.ANSWER_SELECTED_OPTION
    assert first.trace["accepted_state"] is True
    assert selected.trace["accepted_state"] is True


def test_execution_path_reflects_actual_search_invocation_and_skip():
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]}))
    first = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))
    state = ConversationState(visible_options=(OptionCard(name="Лучи", metro="Сокол"),))
    no_search = TurnProcessor(planner=Planner(SemanticPlan(operation="answer_open_question", requested_facts=("metro",)))).process(ctx("метро?"), state)

    first_stages = {item["stage_id"]: item for item in first.trace["execution_path"]["stages"]}
    no_search_stages = {item["stage_id"]: item for item in no_search.trace["execution_path"]["stages"]}
    assert first.trace["execution_path"]["schema"] == "nmbot.execution_path.v1"
    assert first.trace["execution_path"]["path_id"] == "v2.turn.v1"
    assert first_stages["v2.planner"]["status"] == "completed"
    assert first_stages["v2.search"]["status"] == "completed"
    assert first_stages["v2.runtime_finalize"]["status"] == "completed"
    assert no_search_stages["v2.search"]["status"] == "skipped"


def test_execution_path_skips_search_on_transition_error_and_missing_service():
    invalid = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Нет такого"))).process(ctx("первый"), ConversationState())
    missing_service = TurnProcessor(planner=Planner(SemanticPlan(operation="search"))).process(ctx("найди"))

    invalid_search = {item["stage_id"]: item for item in invalid.trace["execution_path"]["stages"]}["v2.search"]
    invalid_transition = {item["stage_id"]: item for item in invalid.trace["execution_path"]["stages"]}["v2.transition"]
    missing_search = {item["stage_id"]: item for item in missing_service.trace["execution_path"]["stages"]}["v2.search"]
    assert invalid.action == TurnAction.SAFE_ERROR
    assert invalid.execution.error_code == "selected_option_not_in_visible_list"
    assert invalid_transition == {"stage_id": "v2.transition", "status": "failed", "error_code": "selected_option_not_in_visible_list"}
    assert invalid_search == {"stage_id": "v2.search", "status": "skipped"}
    assert missing_service.action == TurnAction.SEARCH
    assert missing_service.execution.error_code == "search_service_missing"
    assert missing_search == {"stage_id": "v2.search", "status": "skipped"}


def test_selected_enrichment_exhaustion_offers_human_without_technical_retry_phrases():
    search = SelectedEnrichmentFailureSearch()
    plan = SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("parking_price",))
    state = ConversationState(visible_options=(OptionCard(name="Лучи"),), selected_option_name="Лучи")

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("сколько стоит парковка?"), state)

    forbidden = [
        "Не получилось обновить подбор",
        "Не получилось обновить сведения",
        "Попробовать ещё раз по тем же условиям",
        "Повторить поиск?",
        "Попробовать проверить ещё раз",
    ]
    assert turn.execution.ok is True
    assert turn.execution.error_code == "selected_enrichment_technical_failure"
    assert turn.response_plan.operator_prompt is True
    assert turn.response_text.rstrip().endswith("Передать оператору запрос?")
    assert all(text not in turn.response_text for text in forbidden)
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["operator_offered"] is True
    assert turn.trace["runtime_summary"]["call_counts"]["selected_enrichment"] == 1


def test_selected_valid_missing_fact_keeps_truthful_operator_semantics_not_technical_error():
    search = SearchService(selected=OptionCard(name="Лучи"))
    plan = SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("parking_price",))
    state = ConversationState(visible_options=(OptionCard(name="Лучи"),), selected_option_name="Лучи")

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("парковка?"), state)

    assert turn.execution.error_code is None
    assert "Сейчас не могу надёжно проверить" not in turn.response_text
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["operator_offered"] is False


def test_execution_path_marks_search_failed_only_after_invoked_exception():
    search = SearchService(SearchResult.from_dict({"facts": []}), fail=True)

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))

    search_stage = {item["stage_id"]: item for item in turn.trace["execution_path"]["stages"]}["v2.search"]
    assert search.calls == 1
    assert search_stage == {"stage_id": "v2.search", "status": "failed", "error_code": "runtimeerror"}


def test_failed_generic_search_exhaustion_renders_human_operator_offer_without_retry_wording():
    search = SearchService(SearchResult.from_dict({"facts": []}), fail=True)

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))

    assert turn.execution.ok is False
    assert turn.response_plan.operator_prompt is True
    assert turn.response_plan.answer_kind == "safe_upstream_fallback"
    assert turn.response_text.rstrip().endswith("Передать оператору запрос?")
    forbidden = ("не получилось обновить", "повторить поиск", "попробовать ещё раз", "попробовать еще раз")
    assert all(text not in turn.response_text.casefold() for text in forbidden)


def test_execution_path_uses_composer_attempts_and_manager_meta_only():
    composer_result = ComposerAttemptResult(
        status="fallback",
        text="fallback",
        error_code="schema_invalid_options",
        attempt_summaries=(
            {"stage": "writer", "status": "ok", "error_code": None, "gateway_task_id": "secret-task"},
            {"stage": "formatter", "status": "failed", "error_code": "schema_invalid_options", "raw_text": "secret"},
        ),
    )
    plan = SemanticPlan(operation="search")
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]}))
    turn = TurnProcessor(
        planner=Planner(plan),
        search_service=search,
        response_composer=OneShotComposer(result=composer_result),
        response_composer_mode="publish",
        manager_rewriter=ManagerRewriter(),
        manager_rewriter_mode="publish",
    ).process(ctx("найди"))

    stages = {item["stage_id"]: item for item in turn.trace["execution_path"]["stages"]}
    assert stages["v2.response_writer"] == {"stage_id": "v2.response_writer", "status": "completed", "published": False}
    assert stages["v2.response_formatter"] == {"stage_id": "v2.response_formatter", "status": "fallback", "published": False, "error_code": "schema_invalid_options"}
    assert stages["v2.manager_rewriter"]["status"] == "completed"
    assert stages["v2.manager_rewriter"]["published"] is True
    dumped = json.dumps(turn.trace["execution_path"], ensure_ascii=False)
    assert "secret" not in dumped
    assert "task" not in dumped


def test_execution_path_attributes_published_formatter_repair_not_writer():
    composer_result = ComposerAttemptResult(
        status="repaired",
        text="Отформатированный ответ.",
        attempts=2,
        attempt_summaries=(
            {"stage": "writer", "status": "ok"},
            {"stage": "formatter", "status": "ok"},
        ),
    )
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]}))

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=search,
        response_composer=OneShotComposer(result=composer_result),
        response_composer_mode="publish",
    ).process(ctx("найди"))

    stages = {item["stage_id"]: item for item in turn.trace["execution_path"]["stages"]}
    assert stages["v2.response_writer"] == {"stage_id": "v2.response_writer", "status": "completed", "published": False}
    assert stages["v2.response_formatter"] == {"stage_id": "v2.response_formatter", "status": "completed", "published": True}


def test_execution_path_attributes_terminal_publication_to_manager_only():
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]}))
    composer_result = ComposerAttemptResult(
        status="primary",
        text="Модельный ответ.\n\nКакой вариант смотрим?",
        attempts=1,
        attempt_summaries=({"stage": "writer", "status": "ok"},),
    )
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=search,
        response_composer=OneShotComposer(result=composer_result),
        response_composer_mode="publish",
        manager_rewriter=ManagerRewriter(),
        manager_rewriter_mode="publish",
    ).process(ctx("найди"))

    stages = {item["stage_id"]: item for item in turn.trace["execution_path"]["stages"]}
    assert stages["v2.response_writer"]["published"] is False
    assert stages["v2.manager_rewriter"]["published"] is True


def test_answerable_open_question_answers_from_current_options_without_search():
    state = ConversationState(
        visible_options=(OptionCard(name="Лучи", metro="Сокол", price="от 12 млн рублей"),),
    )
    plan = SemanticPlan(
        operation="answer_open_question",
        query_text="А метро рядом есть?",
        requested_facts=("metro",),
        resolved_subject="transport",
    )
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Не должен вызываться"}]}))

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("А метро рядом есть?"), state)

    assert turn.action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert search.calls == 0
    assert "А метро рядом есть?" in turn.response_text
    assert "метро — Сокол" in turn.response_text
    assert "номер телефона" not in turn.response_text
    assert turn.state.get("pending_followup") is None


def test_missing_open_question_offers_operator_consent_without_search_or_site_developer_wording():
    state = ConversationState(
        visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"),),
    )
    plan = SemanticPlan(
        operation="answer_open_question",
        query_text="Есть ли свободные квартиры прямо сейчас?",
        requested_facts=("apartment_inventory",),
        facts_needed=("apartment_inventory",),
        resolved_subject="apartment",
    )
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Не должен вызываться"}]}))

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("Есть ли свободные квартиры прямо сейчас?"), state)

    assert search.calls == 0
    assert turn.response_plan.answer_kind == "answer_open_question"
    assert "Есть ли свободные квартиры прямо сейчас?" in turn.response_text
    assert "Точный ответ уточнит оператор" in turn.response_text
    assert turn.response_text.rstrip().endswith("В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?")
    lowered = turn.response_text.casefold()
    assert "телефон" not in lowered
    assert "номер" not in lowered
    assert "застройщик" not in lowered
    assert "сайт" not in lowered
    assert "на объект" not in lowered
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["operator_offered"] is True
    assert turn.state["contact_consent"] is False


def test_v3_answer_current_developer_answers_directly_without_search_or_shortlist():
    state = ConversationState(
        visible_options=(OptionCard(name="ЖК Лучи", developer="ГК Лучи", price="от 12 млн рублей"),),
    )
    plan = ExecutableTurn(
        goal=IntentGoal.ANSWER_CURRENT,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        query_text="А кто застройщик?",
        requested_facts=("developer",),
        facts_needed=("developer",),
    )
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Не должен вызываться"}]}))

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("А кто застройщик?"), state)

    assert turn.semantic_plan is plan
    assert turn.semantic_plan.goal is IntentGoal.ANSWER_CURRENT
    assert search.calls == 0
    assert "застройщик — ГК Лучи" in turn.response_text
    assert "1. ЖК Лучи" not in turn.response_text
    assert turn.response_plan.cards == ()
    assert turn.state.get("pending_followup") is None


def test_v3_answer_current_missing_developer_offers_operator_consent_without_generic_shortlist():
    state = ConversationState(visible_options=(OptionCard(name="ЖК Лучи", price="от 12 млн рублей"),))
    plan = ExecutableTurn(
        goal=IntentGoal.ANSWER_CURRENT,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        query_text="А кто застройщик?",
        requested_facts=("developer",),
        facts_needed=("developer",),
    )
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Не должен вызываться"}]}))

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("А кто застройщик?"), state)

    assert search.calls == 0
    assert turn.response_plan.answer_kind == "answer_open_question"
    assert "Точный ответ уточнит оператор" in turn.response_text
    assert turn.response_text.rstrip().endswith("В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?")
    assert "застройщик" in turn.response_text
    assert "developer" not in turn.response_text
    assert "по теме:" not in turn.response_text
    assert "1. ЖК Лучи" not in turn.response_text
    assert "телефон" not in turn.response_text.casefold()
    assert "номер" not in turn.response_text.casefold()
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["operator_offered"] is True
    assert turn.state["contact_consent"] is False


def test_v3_michurinsky_current_missing_availability_waits_for_operator_consent_then_name():
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", price="от 14 млн рублей"),),
        selected_option_name="Мичуринский парк",
    )
    plan = SemanticPlan(
        operation="answer_open_question",
        selected_option_name="Мичуринский парк",
        query_text="Есть ли свободные квартиры в Мичуринском парке?",
        requested_facts=("apartment_inventory",),
        facts_needed=("apartment_inventory",),
        resolved_subject="availability",
    )

    first = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(ctx("Есть ли свободные квартиры в Мичуринском парке?"), state)

    assert "Есть ли свободные квартиры в Мичуринском парке?" in first.response_text
    assert "В текущих данных это не подтверждено" in first.response_text
    assert first.response_text.rstrip().endswith("Передать оператору запрос?")
    assert "телефон" not in first.response_text.casefold()
    assert "номер" not in first.response_text.casefold()
    assert first.state["pending_followup"] == "selected_live_fact_consent"
    assert first.state["operator_offered"] is True
    assert first.state["contact_consent"] is False

    accepted = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept"))).process(ctx("да"), ConversationState.from_dict(first.state))
    ambiguous = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform"))).process(ctx("ну наверное"), ConversationState.from_dict(first.state))

    assert accepted.action == TurnAction.ACCEPT_OPERATOR
    assert accepted.state["pending_followup"] == "contact_phone"
    assert accepted.state["contact_consent"] is True
    assert accepted.response_text.rstrip().endswith("На какой номер вам удобно позвонить?")
    assert ambiguous.action == TurnAction.CLARIFY_SELECTED_LIVE_FACT
    assert ambiguous.state["pending_followup"] == "selected_live_fact_consent"
    assert ambiguous.state["contact_consent"] is False


def test_v3_executable_turn_does_not_recompute_transition(monkeypatch):
    calls = {"planner": 0, "legacy_transition": 0}

    class CountingPlanner:
        def plan(self, context, state):
            calls["planner"] += 1
            return ExecutableTurn(
                goal=IntentGoal.RECOMMEND_CURRENT,
                stage=Stage.CURRENT_OPTIONS,
                action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
            )

    def forbidden_transition(*_args, **_kwargs):
        calls["legacy_transition"] += 1
        raise AssertionError("V3 ExecutableTurn must not re-enter derive_transition")

    monkeypatch.setattr(runtime_module, "derive_transition", forbidden_transition)

    turn = TurnProcessor(planner=CountingPlanner()).process(ctx("что посоветуешь?"), ConversationState(visible_options=(OptionCard(name="ЖК Лучи"),)))

    assert calls == {"planner": 1, "legacy_transition": 0}
    assert turn.action is TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert turn.semantic_plan.goal is IntentGoal.RECOMMEND_CURRENT


def test_v3_compare_and_recommend_preserve_goal_in_answer_kind():
    state = ConversationState(visible_options=(OptionCard(name="ЖК Лучи", price="от 12 млн рублей"), OptionCard(name="ЖК Берег", price="от 14 млн рублей")))

    for goal in (IntentGoal.COMPARE_CURRENT, IntentGoal.RECOMMEND_CURRENT):
        plan = ExecutableTurn(goal=goal, stage=Stage.CURRENT_OPTIONS, action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS)
        turn = TurnProcessor(planner=Planner(plan)).process(ctx("сравни"), state)

        assert turn.semantic_plan.goal is goal
        assert turn.response_plan.answer_kind == goal.value
        assert turn.response_plan.answer_kind != "generic"


def test_selected_renderer_outputs_two_lots_comparison_and_one_question() -> None:
    card = OptionCard(
        name="Томилинский бульвар",
        price_min=7_500_000,
        lot_examples=(
            LotExample(id=6375479, rooms="студия", area_m2=19, floor=6, floors_total=25, full_price=8_133_900, renovation="с отделкой"),
            LotExample(id=5976219, rooms="1", area_m2=32.8, floor=17, floors_total=25, full_price=10_318_880, renovation="с отделкой"),
        ),
    )
    plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", intent="rental", selected_option_name=card.name),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name=card.name, active_topic="rental"),
    )

    text = render_response(plan)

    assert "студия" in text.casefold()
    assert "19 м²" in text
    assert "6-й этаж из 25" in text
    assert "8 133 900 рублей" in text
    assert "Однокомнатная квартира: 32,8 м², 17-й этаж из 25, стоимость 10 318 880 рублей" in text
    assert "стоит посмотреть студию" in text
    assert "разница в цене — 2 184 980 рублей" in text
    assert "она больше на 13,8 м²" in text
    assert text.count("?") == 1
    assert text.rstrip().endswith("Какой вариант показать подробнее: студию или однокомнатную квартиру?")
    lowered = text.casefold()
    assert "спрос" not in lowered and "доход" not in lowered and "yield" not in lowered
    assert "объявлен" not in lowered and "егрн" not in lowered


def test_selected_renderer_outputs_one_lot_without_fabricated_comparison() -> None:
    card = OptionCard(
        name="Томилинский бульвар",
        lot_examples=(LotExample(id=6375479, rooms="студия", area_m2=19, floor=6, floors_total=25, full_price=8_133_900, renovation="с отделкой"),),
    )
    plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", intent="rental", selected_option_name=card.name),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name=card.name, active_topic="rental"),
    )

    text = render_response(plan)

    assert "студия" in text.casefold() and "8 133 900 рублей" in text
    assert "дешевле" not in text.casefold()
    assert "больше" not in text.casefold()
    assert text.count("?") == 1
    assert text.rstrip().endswith("Показать студию подробнее?")


def test_selected_renderer_disambiguates_two_lots_with_same_room_format() -> None:
    card = OptionCard(
        name="Томилинский бульвар",
        lot_examples=(
            LotExample(id=6375479, rooms="студия", area_m2=19, full_price=8_133_900),
            LotExample(id=6478325, rooms="студия", area_m2=21.3, full_price=8_355_990),
        ),
    )
    plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", intent="rental", selected_option_name=card.name),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name=card.name, active_topic="rental"),
    )

    text = render_response(plan)

    assert "стоит посмотреть студию площадью 19 м²" in text
    assert "дополнительное пространство — студия площадью 21,3 м²" in text
    assert text.rstrip().endswith("Какой вариант показать подробнее: студию площадью 19 м² или студию площадью 21,3 м²?")
    assert "студия или студия" not in text.casefold()


def test_selected_renderer_without_lots_keeps_existing_response_shape() -> None:
    card = OptionCard(name="Лучи", price_min=12_000_000)
    plan = build_response_plan(
        stage=Stage.SELECTED_OBJECT,
        plan=SemanticPlan(operation="select_option", selected_option_name=card.name),
        execution=ExecutionResult(ok=True, selected=card),
        delta=StateDelta(),
        state=ConversationState(visible_options=(card,), selected_option_name=card.name),
    )
    text = render_response(plan)

    assert "1. ЖК «Лучи»" in text
    assert "Если хотите, дальше можно отдельно проверить квартиры" in text
    assert text.rstrip().endswith("Хотите сравнить его с другим ЖК или проверить актуальное наличие?")


def test_v3_lookup_selected_search_operator_guards_still_execute():
    lookup_result = SearchResult.from_dict({"facts": [{"name": "ЖК Дюна", "developer": "Девелопер"}]})
    lookup = TurnProcessor(
        planner=Planner(ExecutableTurn(goal=IntentGoal.LOOKUP_OBJECT, stage=Stage.REFINEMENT, action=TurnAction.SEARCH, reference="ЖК Дюна", named_object_reference="ЖК Дюна")),
        search_service=SearchService(lookup_result),
    ).process(ctx("ЖК Дюна"), ConversationState())
    assert lookup.state["selected_option_name"] == "ЖК Дюна"

    selected = TurnProcessor(
        planner=Planner(ExecutableTurn(goal=IntentGoal.ANSWER_SELECTED, stage=Stage.SELECTED_OBJECT, action=TurnAction.ANSWER_SELECTED_OPTION, selected_option_name="ЖК Лучи"))
    ).process(ctx("первый"), ConversationState(visible_options=(OptionCard(name="ЖК Лучи"),)))
    assert selected.action is TurnAction.ANSWER_SELECTED_OPTION

    search = SearchService(SearchResult.from_dict({"facts": [{"name": "ЖК Новый"}]}))
    searched = TurnProcessor(
        planner=Planner(ExecutableTurn(goal=IntentGoal.NEW_SEARCH, stage=Stage.FIRST_LIST, action=TurnAction.SEARCH)),
        search_service=search,
    ).process(ctx("найди"), ConversationState())
    assert searched.action is TurnAction.SEARCH and search.calls == 1

    operator = TurnProcessor(
        planner=Planner(ExecutableTurn(goal=IntentGoal.OPERATOR, stage=Stage.OPERATOR_HANDOFF, action=TurnAction.OFFER_OPERATOR))
    ).process(ctx("оператор"), ConversationState(visible_options=(OptionCard(name="ЖК Лучи"),)))
    assert operator.state["pending_followup"] == "contact_name"


def test_missing_open_question_response_brief_sets_operator_consent_contract():
    state = ConversationState(visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"),))
    plan = SemanticPlan(
        operation="answer_open_question",
        query_text="Есть ли свободные квартиры прямо сейчас?",
        requested_facts=("apartment_inventory",),
        facts_needed=("apartment_inventory",),
        resolved_subject="apartment",
    )
    turn = TurnProcessor(planner=Planner(plan)).process(ctx("Есть ли свободные квартиры прямо сейчас?"), state)

    brief = build_response_brief(
        stage=turn.stage,
        plan=turn.semantic_plan,
        execution=turn.execution,
        delta=turn.state_delta,
        state=state,
        response_plan=turn.response_plan,
    )

    assert brief.answer_goal == "answer_open_question"
    assert brief.user_question == "Есть ли свободные квартиры прямо сейчас?"
    assert brief.question_subject == "apartment"
    assert brief.requested_facts == ("apartment_inventory",)
    assert brief.missing_facts == ("apartment_inventory",)
    assert brief.response_policy == "operator_consent_offer"
    assert brief.operator_handoff_template == "Точный ответ уточнит оператор."
    assert brief.cta_template == "В текущих данных это не подтверждено. Оператор сможет проверить. Передать оператору запрос?"


def test_explicit_delta_and_no_repeated_known_fields_in_answer():
    state = ConversationState(params={"rooms": 2}, visible_options=(OptionCard(name="Старый", price_min=10_000_000, price="от 10 млн рублей"),))
    plan = SemanticPlan(operation="refine_search", constraints_delta={"location": "центр"})
    result = SearchResult.from_dict(
        {
            "facts": [{"name": "Центр", "location": "центр", "price": "от 15 млн рублей", "price_min": 15_000_000}],
            # Production search can echo the whole accumulated request.
            "params": {"rooms": 2, "location": "центр"},
        }
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("а в центре есть?"), state)

    assert turn.state_delta.params_update["location"] == "центр"
    assert turn.state_delta.params_update == {"location": "центр"}
    assert "Искала двухкомнатные квартиры в локации центр" in turn.response_text
    assert "Бюджет пока не ограничивала" in turn.response_text
    assert "комнатность теперь 2" not in turn.response_text


def test_business_purpose_is_rendered_in_natural_russian_without_internal_enum():
    plan = SemanticPlan(
        operation="search",
        intent="investment",
        constraints_delta={"hard": {"max_price": 30_000_000}, "preferences": {"purpose": "investment"}},
    )
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price": "от 12 млн рублей"}]})

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("под инвестицию до 30 млн"))

    assert "Искала квартиры для инвестиций в бюджете до 30 млн" in turn.response_text


def test_ambiguous_financing_amount_asks_clarification_before_contact_consent() -> None:
    state = ConversationState(params={"rooms": 2, "purpose": "family"}, selected_option_name="Бусиновский парк")
    plan = SemanticPlan(
        operation="financing",
        intent="mortgage",
        clarification="10 млн — это весь бюджет или первоначальный взнос?",
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(ctx("у нас 10 млн и семейная ипотека"), state)

    assert turn.state.get("pending_followup") is None
    assert turn.state["params"] == {"rooms": 2, "purpose": "family"}
    assert turn.response_text.count("10 млн — это весь бюджет или первоначальный взнос?") == 1
    assert "Как к вам обращаться" not in turn.response_text
    assert "Проверить условия" not in turn.response_text
    assert "investment" not in turn.response_text.casefold()


def test_selected_name_exact_membership_only():
    state = ConversationState(visible_options=(OptionCard(name="Кронштадтский 9"),))
    plan = SemanticPlan(operation="select_option", selected_option_name="кронштадтский 9")

    turn = TurnProcessor(planner=Planner(plan)).process(ctx("кроштатский"), state)

    assert turn.execution.ok is False
    assert turn.trace["accepted_state"] is False
    assert turn.state == state.to_dict()


def test_facts_only_rendering_uses_only_card_fields():
    result = SearchResult.from_dict({"facts": [{"name": "Факт Парк", "location": "Москва", "price": "от 11 млн рублей"}]})
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process(ctx())

    assert "школ" not in turn.response_text.lower()
    assert "парк рядом" not in turn.response_text.lower()
    assert "Факт Парк" in turn.response_text


def test_search_result_shortlist_orders_dedupes_and_limits_cards() -> None:
    result = SearchResult(
        facts=(OptionCard(name="ЖК Лучи"), OptionCard(name="ЖК Берег"), OptionCard(name=" жк лучи ")),
        near=(OptionCard(name="ЖК Берег", is_near=True), OptionCard(name="ЖК Парк", is_near=True), OptionCard(name="ЖК Сад", is_near=True)),
    )

    cards = result.shortlist(3)

    assert [card.name for card in cards] == ["ЖК Лучи", "ЖК Берег"]
    assert [card.is_near for card in cards] == [False, False]
    assert [card.name for card in result.facts] == ["ЖК Лучи", "ЖК Берег", " жк лучи "]
    assert [card.name for card in result.near] == ["ЖК Берег", "ЖК Парк", "ЖК Сад"]
    assert result.shortlist(0) == ()
    assert [card.name for card in result.shortlist("invalid")] == ["ЖК Лучи", "ЖК Берег"]


def test_search_result_shortlist_uses_near_only_when_exact_facts_are_empty() -> None:
    result = SearchResult(
        facts=(),
        near=(OptionCard(name="ЖК Парк", is_near=True), OptionCard(name=" жк парк ", is_near=True), OptionCard(name="ЖК Сад", is_near=True)),
    )

    cards = result.shortlist(2)

    assert [card.name for card in cards] == ["ЖК Парк", "ЖК Сад"]
    assert [card.is_near for card in cards] == [True, True]


def test_search_failure_keeps_safe_adapter_stage_code():
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=SearchService(fail=True),
    ).process(ctx("квартира в Москве"))

    assert turn.execution.ok is False
    assert turn.execution.error_code == "RuntimeError"
    assert _safe_execution_error_code(RuntimeError("v2_search_contract_invalid:fact_0_violates_hard:rooms")) == "v2_search_contract_invalid"


def test_runtime_summary_is_safe_aggregated_shape_for_empty_search() -> None:
    state = ConversationState(
        params={"rooms": 2, "location": "секретная локация"},
        visible_options=(OptionCard(name="Секретный ЖК"),),
        selected_option_name="Секретный ЖК",
        active_topic="family",
    )
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=SearchService(SearchResult(facts=(), near=(), params={"rooms": 2})),
    ).process(ctx("мой телефон +7 999 123-45-67"), state)

    summary = turn.trace["runtime_summary"]
    assert summary["stage"] in {"first_list", "refinement"}
    assert summary["action"] == "search"
    assert summary["call_counts"] == {"planner": 1, "search": 1, "selected_enrichment": 0, "gateway_attempts": 0}
    assert summary["state_before"]["param_keys"] == ["location", "rooms"]
    assert summary["state_before"]["visible_options_count"] == 1
    assert summary["state_before"]["selected_present"] is True
    assert summary["state_after"]["param_keys"] == ["location", "rooms"]
    assert summary["question_count"] == turn.response_text.count("?")
    assert summary["grounding_scope"] == "canonical_response_plan"
    assert "search_without_cards" in summary["quality_blockers"]
    dumped = str(summary)
    assert "Секретный ЖК" not in dumped
    assert "+7 999" not in dumped
    assert "секретная локация" not in dumped


def test_runtime_summary_preserves_bounded_gateway_attempt_details() -> None:
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=SearchWithAttempts(SearchResult(facts=(OptionCard(name="Лучи"),), near=(), params={})),
    ).process(ctx("найди"))

    summary = turn.trace["runtime_summary"]
    assert summary["call_counts"]["gateway_attempts"] == 1
    assert summary["gateway_attempt_details"] == [
        {"stage": "gateway_attempt", "model": "google_gemini", "ok": True, "empty": False, "safe": False, "gateway_task_id": "task-1", "duration_ms": 45, "parse_status": "ok"}
    ]
    assert "secret" not in json.dumps(summary, ensure_ascii=False)


def test_runtime_summary_counts_selected_enrichment_attempt_and_blocker() -> None:
    class EnrichingSearch(SearchService):
        def enrich_selected(self, option, state, plan):
            self.selected_calls += 1
            self.last_enrichment_trace = {
                "stage": "v2_option_enrichment",
                "outcome": "technical_failure",
                "availability_evidence": {
                    "requested": True,
                    "confirmation": "confirmed",
                    "source": "gateway",
                    "gateway_task_id": "task-1/unsafe suffix",
                    "inventory_value": 5242,
                    "raw_mcp_text": "секретный сырой ответ",
                    "query": "наличие квартир secret",
                },
            }
            self.last_enrichment_error_code = "selected_enrichment_timeout"
            return option

    state = ConversationState(visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"),), selected_option_name="Лучи")
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Лучи", requested_facts=("mortgage_terms",), facts_needed=("mortgage_terms",))),
        search_service=EnrichingSearch(),
    ).process(ctx("ипотека?"), state)

    summary = turn.trace["runtime_summary"]
    assert summary["call_counts"]["selected_enrichment"] == 1
    assert summary["call_counts"]["gateway_attempts"] == 1
    assert summary["option_enrichment"] == {
        "availability_evidence": {
            "requested": True,
            "confirmation": "confirmed",
            "source": "gateway",
            "gateway_task_id": "task-1_unsafe_suffix",
        }
    }
    assert "enrichment_error" in summary["quality_blockers"]
    dumped = json.dumps(summary, ensure_ascii=False).lower()
    for forbidden in ("5242", "raw_mcp", "query", "секрет", "secret"):
        assert forbidden not in dumped


def test_operator_decline_is_sticky_and_decline_does_not_reoffer():
    decline = TurnProcessor(planner=Planner(SemanticPlan(operation="operator", operator_consent=False))).process(ctx("не надо"))
    state = ConversationState.from_dict(decline.state)
    next_turn = TurnProcessor(planner=Planner(SemanticPlan(operation="current_options"))).process(ctx("дальше"), state)

    assert state.operator_declined is True
    assert "Оставите номер" not in next_turn.response_text


def test_selected_mortgage_answer_keeps_context_without_inventing_terms():
    state = ConversationState(
        visible_options=(OptionCard(name="Бусиновский парк", price="от 12,4 млн рублей"),),
        selected_option_name="Бусиновский парк",
    )
    plan = SemanticPlan(
        operation="financing",
        intent="mortgage",
        selected_option_name="Бусиновский парк",
        scope="one",
    )

    turn = TurnProcessor(planner=Planner(plan)).process(ctx("а ипотека по нему есть?"), state)

    assert turn.stage.value == "financing_clarification"
    assert "Бусиновский парк" in turn.response_text
    assert "Проверить условия по этому ЖК?" in turn.response_text
    assert "зависят от банка, застройщика и конкретной квартиры" in turn.response_text
    assert "ставка" not in turn.response_text.lower()
    assert "аккредит" not in turn.response_text.lower()
    assert turn.state["pending_followup"] == "financing_consent"


def test_selected_mortgage_fact_check_uses_selected_object_financing_copy():
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", location="Очаково-Матвеевское"),),
        selected_option_name="Мичуринский парк",
    )
    plan = SemanticPlan(
        operation="select_option",
        intent="mortgage",
        selected_option_name="Мичуринский парк",
        requested_facts=("mortgage_terms",),
        facts_needed=("mortgage_terms",),
        requires_enrichment=True,
    )

    turn = TurnProcessor(planner=Planner(plan)).process(ctx("а ипотека по нему есть?"), state)

    assert turn.stage.value == "selected_object"
    assert "Мичуринский парк" in turn.response_text
    assert "условий по ипотеке" in turn.response_text.lower()
    assert "Проверить условия по этому ЖК?" in turn.response_text
    assert "сейчас покажу самое полезное" not in turn.response_text.lower()
    assert turn.state["pending_followup"] == "financing_consent"
    assert turn.state["active_topic"] == "financing"


def test_financing_consent_then_opens_phone_capture():
    state = ConversationState(pending_followup="financing_consent", active_topic="financing")
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept")),
    ).process(ctx("да"), state)

    assert turn.action == TurnAction.ACCEPT_OPERATOR
    assert turn.state["pending_followup"] == "contact_phone"
    assert turn.state["contact_consent"] is True


def test_financing_consent_outcomes_are_registry_mapped_without_contact_capture():
    base = ConversationState(pending_followup="financing_consent", active_topic="financing", selected_option_name="Лучи", visible_options=(OptionCard(name="Лучи"),))

    accept = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept"))).process(ctx("да"), base)
    decline = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(ctx("нет"), base)
    clarify = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="ask_or_clarify"))).process(ctx("что значит проверить?"), base)
    unexpected = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="unexpected"))).process(ctx("синий"), base)
    invalid = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept maybe"))).process(ctx("да наверное"), base)
    missing = TurnProcessor(planner=Planner(SemanticPlan(operation="operator"))).process(ctx("да"), base)

    assert accept.action == TurnAction.ACCEPT_OPERATOR
    assert accept.state["pending_followup"] == "contact_phone"
    assert accept.state["contact_consent"] is True

    assert decline.action == TurnAction.DECLINE_OPERATOR
    assert decline.state["operator_declined"] is True
    assert decline.state.get("pending_followup") is None

    for turn in (clarify, unexpected, invalid, missing):
        assert turn.action == TurnAction.CLARIFY_FINANCING
        assert turn.state["pending_followup"] == "financing_consent"
        assert turn.state.get("contact_name") is None
        assert turn.state.get("contact_consent") is False
        assert "Проверить условия по этому ЖК?" in turn.response_text
    assert "Чтобы проверить их, нужно ваше согласие." in clarify.response_text
    assert "мне нужно ваше явное согласие" in unexpected.response_text
    assert clarify.response_text != unexpected.response_text
    assert invalid.action != TurnAction.ACCEPT_OPERATOR
    assert missing.action != TurnAction.ACCEPT_OPERATOR


def test_current_options_financing_default_asks_selection_without_pending_consent():
    state = ConversationState(
        visible_options=(OptionCard(name="Лучи", price="от 12 млн"), OptionCard(name="Саларьево парк", price="от 13 млн")),
    )
    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="current_options", query_text="а ипотека по ним есть?", intent="mortgage", scope="all", facets=("mortgage",))),
    ).process(ctx("а ипотека по ним есть?"), state)

    first_paragraph = turn.response_text.split("\n\n", 1)[0].casefold()

    assert turn.action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert turn.state.get("pending_followup") is None
    assert "ипотек" in first_paragraph or "услов" in first_paragraph
    assert "не подтверж" in first_paragraph or "пока нет" in first_paragraph
    assert "По какому ЖК проверить условия ипотеки?" in turn.response_text
    assert "Проверить условия по всем этим ЖК?" not in turn.response_text


def test_current_options_financing_explicit_all_sets_consent_and_accept_opens_phone_capture():
    state = ConversationState(
        visible_options=(OptionCard(name="Лучи", price="от 12 млн"), OptionCard(name="Саларьево парк", price="от 13 млн")),
    )
    first = TurnProcessor(
        planner=Planner(SemanticPlan(operation="current_options", query_text="все проверь", intent="mortgage", scope="all", facets=("mortgage",))),
    ).process(ctx("все проверь"), state)

    assert first.action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert first.state["pending_followup"] == "financing_consent"
    assert "Проверить условия по всем этим ЖК?" in first.response_text

    accepted = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept"))).process(ctx("да"), ConversationState.from_dict(first.state))
    check_word_accepted = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="accept"))).process(ctx("проверь"), ConversationState.from_dict(first.state))

    for turn in (accepted, check_word_accepted):
        assert turn.action == TurnAction.ACCEPT_OPERATOR
        assert turn.state["pending_followup"] == "contact_phone"
        assert turn.state["contact_consent"] is True
        assert "Проверить условия по всем этим ЖК?" not in turn.response_text
        assert turn.response_text.rstrip().endswith("На какой номер вам удобно позвонить?")


def test_current_options_financing_decline_and_clarify_stay_on_registry_transitions():
    base = ConversationState(
        pending_followup="financing_consent",
        active_topic="financing",
        visible_options=(OptionCard(name="Лучи", price="от 12 млн"), OptionCard(name="Саларьево парк", price="от 13 млн")),
    )

    decline = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="decline"))).process(ctx("нет"), base)
    clarify = TurnProcessor(planner=Planner(SemanticPlan(operation="freeform", followup_outcome="ask_or_clarify"))).process(ctx("что значит проверить?"), base)

    assert decline.action == TurnAction.DECLINE_OPERATOR
    assert decline.state.get("pending_followup") is None
    assert decline.state["operator_declined"] is True

    assert clarify.action == TurnAction.CLARIFY_FINANCING
    assert clarify.state["pending_followup"] == "financing_consent"
    assert "Проверить условия по" in clarify.response_text


def test_regular_current_options_answer_does_not_set_financing_consent():
    state = ConversationState(
        visible_options=(OptionCard(name="Лучи", price="от 12 млн"), OptionCard(name="Саларьево парк", price="от 13 млн")),
    )

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="current_options", intent="family", scope="all")),
    ).process(ctx("повтори варианты"), state)

    assert turn.action == TurnAction.ANSWER_FROM_CURRENT_OPTIONS
    assert turn.state.get("pending_followup") is None
    assert "Проверить условия по всем этим ЖК?" not in turn.response_text


def test_selected_object_response_sounds_like_a_recommendation_not_a_stub():
    state = ConversationState(
        visible_options=(OptionCard(name="ЖК «Бусиновский парк»", price_min=12_400_000, ready="сдан", location="Западное Дегунино"),),
        selected_option_name="ЖК «Бусиновский парк»",
    )
    plan = SemanticPlan(operation="select_option", selected_option_name="ЖК «Бусиновский парк»")

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(selected=OptionCard(name="ЖК «Бусиновский парк»", price_min=12_400_000, ready="сдан", location="Западное Дегунино"))).process(ctx("Бусиновский парк"), state)

    assert "в данных есть вот что" not in turn.response_text
    assert "могу рассказать вот что" in turn.response_text
    assert "По ЖК «Бусиновский парк»" in turn.response_text
    assert "ЖК «ЖК «" not in turn.response_text
    assert "не нужно ждать окончания стройки" in turn.response_text
    assert "дальше можно отдельно проверить квартиры" in turn.response_text
    assert turn.state["selected_option_name"] == "ЖК «Бусиновский парк»"


def test_selected_schools_fact_includes_school_and_kindergarten() -> None:
    card = OptionCard(
        name="Бусиновский парк",
        infrastructure=("школа", "детский сад", "парк"),
    )
    plan = SemanticPlan(operation="answer_selected", requested_facts=("schools",))

    text = response_module._selected_fact_acknowledgement(card, plan)

    assert text is not None
    assert "Рядом указаны: школа, детский сад" in text
    assert "Рядом указаны: школа, детский сад, парк" not in text

    open_question_lines = conversation_module._open_question_fact_lines(card, ("schools",))
    assert open_question_lines == ["школы и детские сады рядом: школа, детский сад"]


def test_selected_enrichment_called_once_no_broad_search_and_renders_enriched_fields():
    state = ConversationState(visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"),))
    search_service = SearchService(selected=OptionCard(name="Лучи", price="от 12 млн рублей", developer="ПИК", infrastructure=("парк",), room_formats=("двухкомнатные",)))
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Лучи")), search_service=search_service).process(ctx("Лучи"), state)

    assert search_service.selected_calls == 1
    assert search_service.calls == 0
    assert "застройщик ПИК" in turn.response_text
    assert "рядом: парк" in turn.response_text
    assert turn.state["selected_enriched"]["developer"] == "ПИК"


def test_selected_fact_question_uses_memory_first_and_updates_focus() -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), price_min=14_000_000),),
        selected_option_name="Мичуринский парк",
        dialog_focus=DialogFocus(subject="parking", last_requested_facts=("parking",), last_answered_facts=("parking",)),
    )
    search = SearchService(selected=OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), price_min=14_000_000))
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="parking",
        requested_facts=("parking_price",),
        facts_needed=("parking_price",),
        requires_enrichment=True,
        focus_action="keep",
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("А сколько стоит?"), state)

    assert search.calls == 0
    assert search.selected_calls == 1
    assert "паркинг" in turn.response_text
    assert "сейчас не вижу" in turn.response_text
    assert turn.state["dialog_focus"]["subject"] == "parking"
    assert turn.state["dialog_focus"]["last_requested_facts"] == ["parking_price"]
    assert turn.state["dialog_focus"]["last_answered_facts"] == []


def test_current_stored_parking_fact_avoids_selected_enrichment() -> None:
    class MemoryFirstSearch(SearchService):
        def enrich_selected(self, option, state, plan):
            from nmbot_v2.fact_context import split_requested_facts
            if not split_requested_facts(plan.requested_facts, option).missing:
                return option
            return super().enrich_selected(option, state, plan)

    state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",)),), selected_option_name="Мичуринский парк")
    search = MemoryFirstSearch(selected=OptionCard(name="Мичуринский парк", developer="should not fetch"))
    plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", resolved_subject="parking", requested_facts=("parking",), focus_action="switch")

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(ctx("там есть парковка?"), state)

    assert search.selected_calls == 0
    assert "паркинг" in turn.response_text
    assert turn.state["dialog_focus"]["last_answered_facts"] == ["parking"]


def test_missing_selected_parking_fact_answers_honestly_instead_of_replaying_generic_card() -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", price_min=14_000_000),),
    )
    search = SearchService(selected=OptionCard(name="Мичуринский парк", price_min=14_000_000))
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="parking",
        requested_facts=("parking",),
        facts_needed=("parking",),
        requires_enrichment=True,
        focus_action="switch",
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=search).process(
        ctx("Мичуринский парк, там есть парковка?"), state
    )

    assert search.calls == 0
    assert search.selected_calls == 1
    assert "наличие паркинга" in turn.response_text
    assert "сейчас не вижу" in turn.response_text
    assert "сейчас покажу самое полезное" not in turn.response_text
    assert "Передать оператору запрос по паркингу для ЖК «Мичуринский парк»?" in turn.response_text
    assert turn.state["pending_followup"] == "selected_live_fact_consent"
    assert turn.state["selected_option_name"] == "Мичуринский парк"
    assert turn.state["dialog_focus"]["last_answered_facts"] == []


def test_selected_parking_then_price_chain_stays_one_object_and_no_broad_search() -> None:
    class FactSpySearch(SearchService):
        def __init__(self) -> None:
            super().__init__()
            self.exact_refreshes: list[tuple[str, tuple[str, ...]]] = []

        def enrich_selected(self, option, state, plan):
            from nmbot_v2.fact_context import split_requested_facts
            self.selected_calls += 1
            split = split_requested_facts(plan.requested_facts or plan.facts_needed, option)
            if split.missing:
                self.exact_refreshes.append((option.name, tuple(plan.facts_needed or split.missing)))
            return option

    search = FactSpySearch()
    state = ConversationState(
        visible_options=(
            OptionCard(name="Бусиновский парк", price_min=12_000_000),
            OptionCard(name="Мичуринский парк", price_min=14_000_000, infrastructure=("паркинг",)),
        )
    )
    first_plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="parking",
        requested_facts=("parking",),
        focus_action="switch",
    )
    first = TurnProcessor(planner=Planner(first_plan), search_service=search).process(ctx("Мичуринский парк, там есть парковка?"), state)
    state = ConversationState.from_dict(first.state)

    assert first.state["selected_option_name"] == "Мичуринский парк"
    assert first.state["dialog_focus"]["subject"] == "parking"
    assert first.state["dialog_focus"]["last_answered_facts"] == ["parking"]
    assert search.calls == 0
    assert search.exact_refreshes == []
    assert "Бусиновский парк" not in first.response_text
    assert "Проверить точную актуальность" not in first.response_text

    second_plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="parking",
        requested_facts=("parking_price",),
        facts_needed=("parking_price",),
        requires_enrichment=True,
        focus_action="keep",
    )
    second = TurnProcessor(planner=Planner(second_plan), search_service=search).process(ctx("А сколько стоит?"), state)

    assert search.calls == 0
    assert search.exact_refreshes == [("Мичуринский парк", ("parking_price",))]
    assert second.state["selected_option_name"] == "Мичуринский парк"
    assert second.state["visible_options"][0]["name"] == "Бусиновский парк"
    assert second.state["dialog_focus"]["subject"] == "parking"
    assert second.state["dialog_focus"]["last_requested_facts"] == ["parking_price"]
    assert second.state["dialog_focus"]["last_answered_facts"] == []
    assert "стоимость машиноместа" in second.response_text.casefold()
    assert "сейчас не вижу" in second.response_text
    assert "Бусиновский парк" not in second.response_text
    assert second.response_text.count("ЖК «Мичуринский парк»") == 2


def test_cached_dynamic_parking_price_still_requests_refresh_but_failure_keeps_value() -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",), parking_price="от 1,8 млн рублей"),),
        selected_option_name="Мичуринский парк",
    )
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="parking",
        requested_facts=("parking_price",),
        facts_needed=("parking_price",),
        requires_enrichment=True,
        focus_action="keep",
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(selected_fail=True)).process(ctx("А сколько стоит?"), state)

    assert turn.execution.ok is True
    assert turn.execution.error_code == "selected_enrichment_TimeoutError"
    assert turn.execution.fresh_facts == ()
    assert "Сейчас не могу надёжно проверить по паркингу" in turn.response_text
    assert turn.response_text.rstrip().endswith("Передать оператору запрос?")
    assert "Не получилось обновить сведения" not in turn.response_text
    assert "Попробовать проверить ещё раз" not in turn.response_text
    assert "от 1,8 млн рублей" not in turn.response_text
    assert turn.state.get("pending_followup") == "selected_live_fact_consent"
    assert turn.state["dialog_focus"]["last_answered_facts"] == []


def test_successful_exact_dynamic_enrichment_marks_fresh_answered_and_no_repeat_check_cta() -> None:
    class FreshParkingSearch(SearchService):
        def enrich_selected(self, option, state, plan):
            self.selected_calls += 1
            self.last_fresh_facts = ("parking_price",)
            return OptionCard(name=option.name, infrastructure=("паркинг",), parking_price="от 1,9 млн рублей")

    state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", infrastructure=("паркинг",)),), selected_option_name="Мичуринский парк")
    plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", resolved_subject="parking", requested_facts=("parking_price",), facts_needed=("parking_price",), requires_enrichment=True, focus_action="keep")

    turn = TurnProcessor(planner=Planner(plan), search_service=FreshParkingSearch()).process(ctx("А сколько стоит?"), state)

    assert turn.execution.fresh_facts == ("parking_price",)
    assert "сейчас вижу" in turn.response_text
    assert "от 1,9 млн рублей" in turn.response_text
    assert "Проверить точную актуальность" not in turn.response_text
    assert turn.state["dialog_focus"]["last_answered_facts"] == ["parking_price"]


def test_dynamic_identity_mismatch_simulation_retains_cached_value_without_fresh_answer() -> None:
    class MismatchLikeSearch(SearchService):
        def enrich_selected(self, option, state, plan):
            self.selected_calls += 1
            self.last_fresh_facts = ()
            return option

    state = ConversationState(visible_options=(OptionCard(name="Мичуринский парк", parking_price="от 1,8 млн рублей"),), selected_option_name="Мичуринский парк")
    plan = SemanticPlan(operation="select_option", selected_option_name="Мичуринский парк", resolved_subject="parking", requested_facts=("parking_price",), facts_needed=("parking_price",), requires_enrichment=True, focus_action="keep")

    turn = TurnProcessor(planner=Planner(plan), search_service=MismatchLikeSearch()).process(ctx("А сколько стоит?"), state)

    assert turn.execution.fresh_facts == ()
    assert "вижу ориентир" in turn.response_text
    assert "от 1,8 млн рублей" in turn.response_text
    assert "Передать оператору запрос по паркингу" in turn.response_text
    assert turn.state["dialog_focus"]["last_answered_facts"] == []


def test_explicit_apartment_price_switches_focus_from_parking() -> None:
    state = ConversationState(
        visible_options=(OptionCard(name="Мичуринский парк", price_min=14_000_000, infrastructure=("паркинг",)),),
        selected_option_name="Мичуринский парк",
        dialog_focus=DialogFocus(subject="parking", last_requested_facts=("parking",), last_answered_facts=("parking",)),
    )
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name="Мичуринский парк",
        resolved_subject="apartment",
        requested_facts=("apartment_price",),
        focus_action="switch",
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(ctx("сама квартира сколько стоит?"), state)

    assert turn.state["dialog_focus"]["subject"] == "apartment"
    assert turn.state["dialog_focus"]["last_answered_facts"] == ["apartment_price"]
    assert "14 млн" in turn.response_text


def test_new_search_clears_dialog_focus() -> None:
    state = ConversationState(dialog_focus=DialogFocus(subject="parking", last_requested_facts=("parking",)), visible_options=(OptionCard(name="Старый"),))
    result = SearchResult.from_dict({"facts": [{"name": "Новый", "price_min": 10_000_000}]})

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process(ctx("новый поиск"), state)

    assert turn.state["dialog_focus"] == {"last_requested_facts": [], "last_answered_facts": []}


def test_selected_enrichment_timeout_falls_back_to_base_card_and_keeps_state_coherent():
    state = ConversationState(visible_options=(OptionCard(name="Лучи", price="от 12 млн рублей"),))
    search_service = SearchService(selected_fail=True)
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Лучи")), search_service=search_service).process(ctx("Лучи"), state)

    assert turn.execution.ok is True
    assert turn.execution.error_code == "selected_enrichment_TimeoutError"
    assert "Лучи" in turn.response_text
    assert turn.state["selected_option_name"] == "Лучи"


def test_current_options_scenario_switch_updates_topic_without_search_and_keeps_constraints():
    state = ConversationState(params={"location": "Сокол", "rooms": 2}, visible_options=(OptionCard(name="Сокол Парк"),))
    search = SearchService(SearchResult())
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="current_options", intent="rental", scope="all")), search_service=search).process(ctx("под аренду все"), state)

    assert search.calls == 0
    assert turn.state["active_topic"] == "rental"
    assert turn.state["params"] == {"location": "Сокол", "rooms": 2}


def test_native_rental_current_options_formats_numeric_prices_and_one_question():
    state = ConversationState(
        active_topic="investment",
        visible_options=(
            OptionCard(name="ЖК Полар", location="Северное Медведково", price="12200000", finishing="с отделкой"),
            OptionCard(name="Дом 56", location="Басманный", price_min=23_266_500, ready="2027"),
        ),
    )

    answer = build_native_conversation_answer(SemanticPlan(operation="current_options", intent="rental"), state)

    assert "ЖК «Полар»" in answer
    assert "цены от 12,2 млн рублей" in answer
    assert "цены от 23,3 млн рублей" in answer
    assert "12200000" not in answer
    assert "23266500" not in answer
    assert answer.count("?") == 1
    assert answer.endswith("Какой из этих ЖК хотите рассмотреть подробнее?")


def test_native_rental_cards_get_distinct_fact_grounded_comparison_notes():
    state = ConversationState(
        visible_options=(
            OptionCard(name="Дорогой", location="Басманный", price_min=23_300_000, ready="4 кв. 2026"),
            OptionCard(name="Средний", location="Замоскворечье", price_min=21_400_000, ready="4 кв. 2026"),
            OptionCard(name="Доступный", location="Пресненский", price_min=19_100_000, ready="3 кв. 2026"),
        ),
    )

    answer = build_native_conversation_answer(SemanticPlan(operation="current_options", intent="rental"), state)

    assert "самый высокий бюджет входа" in answer
    assert "промежуточный вариант по начальному бюджету" in answer
    assert "самая низкая начальная цена" in answer
    assert answer.count("Срок готовности помогает понять") == 0
    assert "спрос" not in answer.casefold()
    assert "доход" not in answer.casefold()
    assert answer.count("?") == 1


def test_operator_decline_blocks_later_offer_unless_explicit_new_request():
    state = ConversationState(operator_declined=True, selected_option_name="Лучи", visible_options=(OptionCard(name="Лучи"),))
    ordinary = TurnProcessor(planner=Planner(SemanticPlan(operation="operator"))).process(ctx("ну что"), state)
    explicit = TurnProcessor(planner=Planner(SemanticPlan(operation="operator", explicit_operator_request=True, operator_reason="актуальное наличие"))).process(ctx("позови человека"), state)

    assert "Оставите номер" not in ordinary.response_text
    assert "Как к вам обращаться?" in explicit.response_text


def test_financing_down_payment_does_not_overwrite_total_budget():
    state = ConversationState(params={"max_price": 20_000_000})
    plan = SemanticPlan(operation="financing", constraints_delta={"hard": {"initial_payment": 10_000_000}, "preferences": {"financing": "mortgage"}})
    turn = TurnProcessor(planner=Planner(plan)).process(ctx("это первоначальный взнос"), state)

    assert turn.state["params"]["max_price"] == 20_000_000
    assert turn.state["params"]["down_payment"] == 10_000_000
    assert "первоначальный взнос 10 млн" in turn.response_text


def test_selected_financing_acknowledges_down_payment_and_asks_concrete_consent():
    card = OptionCard(name="Бусиновский парк", price_min=12_400_000)
    state = ConversationState(
        params={"max_price": 10_000_000, "finance_preference": "family_mortgage"},
        selected_option_name=card.name,
        visible_options=(card,),
        active_topic="financing",
    )
    plan = SemanticPlan(
        operation="financing",
        intent="mortgage",
        selected_option_name=card.name,
        constraints_delta={"hard": {"down_payment": 10_000_000}},
        requested_facts=("mortgage_terms",),
        facts_needed=("mortgage_terms",),
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(ctx("это первоначальный взнос"), state)

    assert "Поняла: 10 млн рублей — это первоначальный взнос" in turn.response_text
    assert "семейной ипотеке" in turn.response_text
    assert "Бусиновский парк" in turn.response_text
    assert "первоначальным взносом 10 млн рублей" in turn.response_text
    assert "Проверить условия по этому ЖК?" not in turn.response_text
    assert turn.response_text.count("?") == 1


def test_operator_handoff_uses_safe_canonical_financing_summary_without_premature_claim():
    state = ConversationState(
        params={"down_payment": 10_000_000, "finance_preference": "family_mortgage"},
        selected_option_name="Бусиновский парк",
        visible_options=(OptionCard(name="Бусиновский парк"),),
        active_topic="financing",
        pending_followup="financing_consent",
    )

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="operator", followup_outcome="accept"))).process(ctx("да"), state)

    assert "семейной ипотеке" in turn.response_text
    assert "Бусиновский парк" in turn.response_text
    assert "первоначальным взносом 10 млн рублей" in turn.response_text
    assert "нужный вопрос" not in turn.response_text
    assert "Передам" not in turn.response_text
    assert "На какой номер вам удобно позвонить?" in turn.response_text


def test_provider_failure_is_atomic_and_preserves_state():
    state = ConversationState(params={"location": "Сокол"}, visible_options=(OptionCard(name="Сокол Парк"),))
    plan = SemanticPlan(operation="refine_search", constraints_delta={"max_price": 17_000_000})

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(fail=True)).process(ctx("обнови"), state)

    assert turn.execution.ok is False
    assert turn.state_delta.is_empty
    assert turn.state == state.to_dict()
    assert "Сейчас не могу надёжно проверить нужную информацию" in turn.response_text
    assert "Передать оператору запрос?" in turn.response_text


def test_provider_retry_success_is_one_logical_answer():
    search = RetryInsideSearch()
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))

    assert search.provider_attempts == 2
    assert turn.response_text.count("Retry Park") == 1
    assert turn.response_text.count("?") == 1


def test_runtime_uses_deterministic_renderer():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result)).process(ctx("найди"))

    assert "Лучи" in turn.response_text
    assert turn.trace["response_composer"] == {"used": False, "reason": "deterministic_renderer"}


def test_response_composer_off_does_not_call_configured_composer():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    composer = OneShotComposer()

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="off").process(ctx("найди"))

    assert composer.calls == []
    assert "Лучи" in turn.response_text
    assert turn.trace["response_composer"] == {"used": False, "reason": "deterministic_renderer"}


def test_response_composer_ineligible_reset_operator_offtopic_and_error_do_not_call_composer():
    cases = [
        (SemanticPlan(operation="reset"), None),
        (ExecutableTurn(goal=IntentGoal.OPERATOR, stage=Stage.OPERATOR_HANDOFF, action=TurnAction.OFFER_OPERATOR), None),
        (ExecutableTurn(goal=IntentGoal.OFF_TOPIC, stage=Stage.OFF_TOPIC, action=TurnAction.ANSWER_OFF_TOPIC), None),
        (SemanticPlan(operation="search", intent="life"), SearchService(fail=True)),
    ]
    for plan, search_service in cases:
        composer = OneShotComposer()
        turn = TurnProcessor(planner=Planner(plan), search_service=search_service, response_composer=composer, response_composer_mode="publish").process(ctx("текст"))

        assert composer.calls == []
        assert turn.trace["response_composer"]["mode"] == "publish"
        assert turn.trace["response_composer"]["published"] is False
        assert turn.trace["response_composer"]["reason"] == "ineligible_response_goal"


def test_response_composer_ineligible_open_question_goal_does_not_call_composer():
    card = OptionCard(name="Лучи", price_min=12_000_000)
    state = ConversationState(visible_options=(card,), selected_option_name=card.name)
    composer = OneShotComposer()

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="answer_open_question", intent="life", selected_option_name=card.name, requested_facts=("apartment_price",))),
        response_composer=composer,
        response_composer_mode="publish",
    ).process(ctx("сколько стоит?"), state)

    assert composer.calls == []
    assert "Модельный ответ" not in turn.response_text
    assert turn.trace["response_composer"]["reason"] == "ineligible_response_goal"


def test_response_composer_shadow_calls_once_but_publishes_deterministic_and_finalizes_state():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    composer = OneShotComposer()

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="shadow").process(ctx("найди"))

    assert len(composer.calls) == 1
    assert "Лучи" in turn.response_text
    assert "Модельный ответ" not in turn.response_text
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.trace["response_composer"]["mode"] == "shadow"
    assert turn.trace["response_composer"]["used"] is True
    assert turn.trace["response_composer"]["published"] is False
    assert turn.trace["response_composer"]["attempts"] == 1


def test_response_composer_publish_success_uses_model_text_and_finalizes_state():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    composer = OneShotComposer()

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="publish").process(ctx("найди"))

    assert len(composer.calls) == 1
    assert turn.response_text == "Модельный ответ.\n\nКакой вариант смотрим?"
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.trace["runtime_summary"]["question_count"] == 1
    assert turn.trace["response_composer"]["published"] is True


def test_response_composer_publish_failure_falls_back_without_turn_failure_and_state_uses_deterministic():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    failed = ComposerAttemptResult(status="fallback", text="детерминированный", errors=("schema_invalid_options",), error_category="schema", error_code="schema_invalid_options", attempts=1)
    composer = OneShotComposer(result=failed)

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="publish").process(ctx("найди"))

    assert turn.execution.ok is True
    assert len(composer.calls) == 1
    assert "Лучи" in turn.response_text
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.trace["response_composer"]["used"] is False
    assert turn.trace["response_composer"]["published"] is False
    assert turn.trace["response_composer"]["error_code"] == "schema_invalid_options"


def test_response_composer_exception_falls_back_without_turn_failure():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    composer = OneShotComposer(fail=True)

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="publish").process(ctx("найди"))

    assert turn.execution.ok is True
    assert "Лучи" in turn.response_text
    assert turn.trace["response_composer"]["reason"] == "composer_error"


def test_manager_rewriter_off_does_not_call_and_preserves_prepared_answer():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    rewriter = ManagerRewriter()

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=rewriter, manager_rewriter_mode="off").process(ctx("найди"))

    assert rewriter.calls == []
    assert "Лучи" in turn.response_text
    assert turn.trace["manager_rewriter"] == {"used": False, "reason": "off"}


def test_manager_rewriter_shadow_gets_full_transcript_brief_and_preserves_answer():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    state = ConversationState(dialogue_turns=({"user": "привет", "assistant": "Здравствуйте"},))
    rewriter = ManagerRewriter("Переписанный ответ")

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=rewriter, manager_rewriter_mode="shadow").process(ctx("найди"), state)

    assert len(rewriter.calls) == 1
    call = rewriter.calls[0]
    assert call["transcript"] == ({"user": "привет", "assistant": "Здравствуйте"}, {"user": "найди", "assistant": ""})
    assert call["current_question"] == "найди"
    assert "Лучи" in call["prepared_answer"]
    assert [card.name for card in call["brief"].canonical_cards] == ["Лучи"]
    assert "Переписанный" not in turn.response_text
    assert turn.trace["manager_rewriter"]["published"] is False


def test_manager_rewriter_publish_serves_rewrite_and_state_stores_published_text():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    rewriter = ManagerRewriter("Живой ответ менеджера. Что посмотрим дальше?")

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=rewriter, manager_rewriter_mode="publish").process(ctx("найди"))

    assert turn.response_text == "Живой ответ менеджера. Что посмотрим дальше?"
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.state["dialogue_turns"][-1]["assistant"] == turn.response_text
    assert turn.trace["manager_rewriter"]["published"] is True


def test_temporary_finance_unknown_sentence_filter_removes_single_exact_between_paragraphs():
    text = "Первый полезный абзац.\n\nУ меня нет информации о финансовой стороне этих предложений.\n\nВторой полезный абзац."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Первый полезный абзац.\n\nВторой полезный абзац."


def test_temporary_finance_unknown_sentence_filter_removes_repeated_exact_phrase():
    text = "Старт. У меня нет информации о финансовой стороне этих предложений. У меня нет информации о финансовой стороне этих предложений. Финиш."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Старт. Финиш."


def test_temporary_finance_unknown_sentence_filter_removes_full_user_example_tail():
    text = "По второму варианту: цена от 15 млн рублей, метро рядом, дом уже сдан. У меня нет информации по финансовым условиям. Что для вас важнее — цена или срок сдачи?"

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "По второму варианту: цена от 15 млн рублей, метро рядом, дом уже сдан. Что для вас важнее — цена или срок сдачи?"


def test_temporary_finance_unknown_sentence_filter_removes_about_financial_terms_variant():
    text = "Старт. У меня нет информации о финансовых условиях. Финиш."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Старт. Финиш."


def test_temporary_finance_unknown_sentence_filter_removes_standalone_user_reported_variant():
    text = "Старт. К сожалению, у меня нет информации о финансовой стороне вопроса. Финиш."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Старт. Финиш."


def test_temporary_finance_unknown_sentence_filter_removes_exact_finance_prefix_variant():
    text = "К сожалению, у меня нет информации о финансовой стороне этих предложений."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == ""


def test_temporary_finance_unknown_sentence_filter_removes_prefixed_variant_between_answer_and_cta():
    phrase = "К сожалению, у меня нет информации о финансовой стороне этих предложений."
    text = (
        "По второму варианту: цена от 15 млн рублей, метро рядом, дом уже сдан. "
        f"{phrase} "
        "Что для вас важнее — цена или срок сдачи?"
    )

    cleaned = runtime_module._temporary_strip_repeated_finance_unknown_sentence(text)

    assert phrase not in cleaned
    assert cleaned == "По второму варианту: цена от 15 млн рублей, метро рядом, дом уже сдан. Что для вас важнее — цена или срок сдачи?"


def test_temporary_finance_unknown_sentence_filter_removes_repeated_mixed_variants():
    text = "Старт. У меня нет информации о финансовой стороне этих предложений. У меня нет информации по финансовым условиям. У меня нет информации о финансовых условиях. Финиш."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Старт. Финиш."


def test_temporary_finance_unknown_sentence_filter_removes_variant_without_dot():
    text = "Старт.\n\n   у меня нет информации о финансовой стороне этих предложений   \n\nФиниш."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == "Старт.\n\nФиниш."


def test_temporary_finance_unknown_sentence_filter_keeps_longer_useful_sentence():
    text = "Полезно: у меня нет информации о финансовой стороне этих предложений, но могу сравнить цену, срок и метро."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == text


def test_temporary_finance_unknown_sentence_filter_keeps_longer_user_reported_variant():
    text = "К сожалению, у меня нет информации о финансовой стороне вопроса, но могу сравнить подтверждённые цены и сроки."

    assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == text


def test_temporary_finance_unknown_sentence_filter_keeps_longer_useful_dash_and_colon_sentence():
    for text in (
        "У меня нет информации по финансовым условиям — зато могу сравнить цену, срок и метро.",
        "У меня нет информации о финансовых условиях: могу показать подтверждённые цены и сроки.",
    ):
        assert runtime_module._temporary_strip_repeated_finance_unknown_sentence(text) == text


def test_temporary_finance_unknown_sentence_filter_state_stores_cleaned_text():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    composer = OneShotComposer(result=ComposerAttemptResult(status="primary", text="Полезный ответ.\n\nУ меня нет информации по финансовым условиям.\n\nКакой вариант смотрим?", attempts=1))

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), response_composer=composer, response_composer_mode="publish").process(ctx("найди"))

    assert turn.response_text == "Полезный ответ.\n\nКакой вариант смотрим?"
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.state["dialogue_turns"][-1]["assistant"] == turn.response_text


def test_temporary_finance_unknown_sentence_filter_cleans_manager_publish_path():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    rewriter = ManagerRewriter("Живой ответ.\n\nУ меня нет информации о финансовой стороне этих предложений\n\nЧто посмотрим дальше?")

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=rewriter, manager_rewriter_mode="publish").process(ctx("найди"))

    assert turn.response_text == "Живой ответ.\n\nЧто посмотрим дальше?"
    assert turn.state["recent_turns"][-1]["assistant"] == turn.response_text
    assert turn.trace["manager_rewriter"]["published"] is True


def test_manager_rewriter_prompt_provenance_only_when_invoked():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})

    off = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=ManagerRewriter(), manager_rewriter_mode="off").process(ctx("найди"))
    assert "prompt_provenance" not in off.trace["manager_rewriter"]

    invoked = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=ManagerRewriter(), manager_rewriter_mode="shadow").process(ctx("найди"))
    prompts = invoked.trace["manager_rewriter"]["prompt_provenance"]["prompts"]
    assert [item["stage"] for item in prompts] == ["manager_rewriter"]


def test_manager_rewriter_empty_and_exception_preserve_prepared_answer():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})
    for rewriter in (ManagerRewriter(""), ManagerRewriter(fail=True)):
        turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(result), manager_rewriter=rewriter, manager_rewriter_mode="publish").process(ctx("найди"))

        assert "Лучи" in turn.response_text
        assert turn.trace["manager_rewriter"]["published"] is False


def test_manager_rewriter_does_not_receive_pre_reset_dialogue():
    rewriter = ManagerRewriter()
    state = ConversationState(dialogue_turns=({"user": "старый вопрос", "assistant": "старый ответ"},))

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="reset")),
        manager_rewriter=rewriter,
        manager_rewriter_mode="publish",
    ).process(ctx("/start"), state)

    assert rewriter.calls == []
    assert turn.trace["manager_rewriter"]["reason"] == "reset_turn"
    assert turn.state["dialogue_turns"][-1]["user"] == "/start"
    assert "старый вопрос" not in str(turn.state["dialogue_turns"])


def test_dialogue_turns_persist_all_recent_turns_remain_capped_and_redacted():
    state = ConversationState.from_dict({"recent_turns": [{"user": "старый +7 999 123-45-67", "assistant": "mail a@b.ru"}]})
    assert state.dialogue_turns == ({"user": "старый [redacted-contact]", "assistant": "mail [redacted-email]"},)

    current = state
    for idx in range(7):
        current = ConversationState.from_dict(TurnProcessor(planner=Planner(SemanticPlan(operation="off_topic"))).process(ctx(f"u{idx} test{idx}@x.ru"), current).state)

    assert len(current.dialogue_turns) == 8
    assert len(current.recent_turns) == 6
    assert "[redacted-email]" in current.dialogue_turns[-1]["user"]
    assert "test6@x.ru" not in current.dialogue_turns[-1]["user"]


def test_response_composer_semantic_warning_alone_does_not_block_one_shot_publish():
    class WarningComposer:
        def __init__(self):
            self.calls = 0

        def compose(self, brief, *, repair_errors=(), model="google/gemini-2.5-flash"):
            self.calls += 1
            return '{"intro":"Это идеальный вариант для комфортной жизни.","options":[],"recommendation":"","missing_note":"","final_question":"Какой вариант смотрим?"}'

    composer = WarningComposer()
    result = asyncio.run(compose_response_one_shot_async(ResponseBrief(answer_goal="answer_open_question"), fallback_text="fallback", composer=composer))

    assert composer.calls == 1
    assert result.status == "primary"
    assert result.text.startswith("Это идеальный вариант")
    assert "unsupported_marketing_claim" in " ".join(result.warnings)


def test_deterministic_renderer_does_not_publish_untrusted_options():
    result = SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12_000_000}]})

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process(ctx("найди"))

    assert turn.trace["response_composer"] == {"used": False, "reason": "deterministic_renderer"}
    assert "Искала квартиры. Район и бюджет пока не ограничивала — нашла один вариант" in turn.response_text
    assert "Чужой" not in turn.response_text


def test_deterministic_renderer_keeps_atomic_state():
    state = ConversationState(params={"location": "Сокол"})
    result = SearchResult.from_dict({"facts": [{"name": "Сокол Парк", "location": "Сокол"}], "params": {"location": "Сокол"}})

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process(ctx("найди"), state)

    assert turn.execution.ok is True
    assert turn.trace["accepted_state"] is True
    assert turn.trace["response_composer"] == {"used": False, "reason": "deterministic_renderer"}
    assert turn.state["visible_options"][0]["name"] == "Сокол Парк"
    assert "Сокол Парк" in turn.response_text


def test_selected_compound_question_answers_price_and_readiness_with_one_fresh_cta():
    card = OptionCard(name="Скандинавия", price_min=10_800_000, ready="сдан")
    state = ConversationState(visible_options=(card,), selected_option_name=card.name)
    plan = SemanticPlan(
        operation="select_option",
        selected_option_name=card.name,
        requested_facts=("apartment_price", "readiness"),
    )

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService()).process(
        ctx("Сколько стоит и дом уже сдан?"), state
    )

    assert "10,8 млн" in turn.response_text
    assert "дом сдан" in turn.response_text
    assert "Хотите посмотреть цену" not in turn.response_text
    assert turn.response_text.count("?") == 1


def test_no_results_copy_uses_public_finishing_label_and_hides_internal_terms():
    plan = SemanticPlan(
        operation="search",
        constraints_delta={"hard": {"rooms": "studio", "max_price": 9_000_000, "finishing": "renovation"}},
    )
    result = SearchResult.from_dict({"facts": [], "near": [], "missing": ["finance", "details"]})

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("студию до 9 млн с отделкой"))

    lowered = turn.response_text.casefold()
    assert "renovation" not in lowered
    assert "mcp" not in lowered
    assert "с отделкой" in lowered
    assert "части уточняющих данных" not in lowered


def test_named_object_not_found_never_mentions_mcp():
    plan = SemanticPlan(operation="lookup_object", reference="Новый берег")
    result = SearchResult.from_dict({"facts": [], "near": [], "missing": []})

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("Что по Новому берегу?"))

    assert "MCP" not in turn.response_text
    assert "подтверждённой информации" in turn.response_text


def test_family_cards_do_not_receive_repeated_generic_benefit_fallbacks():
    result = SearchResult.from_dict({
        "facts": [
            {"name": "Первый", "price_min": 12_000_000, "ready": "сдан"},
            {"name": "Второй", "price_min": 13_000_000, "ready": "сдан"},
            {"name": "Третий", "price_min": 14_000_000, "ready": "сдан"},
        ]
    })

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search", intent="family")),
        search_service=SearchService(result),
    ).process(ctx("двушка для семьи"))

    assert "Здесь есть ориентиры" not in turn.response_text
    assert turn.response_text.count("Готовый дом удобен семье") == 1


def test_missing_caveat_does_not_repeat_fact_visible_in_cards():
    cards = (OptionCard(name="Первый", room_formats=("двухкомнатные",), ready="сдан"),)

    caveat = response_module._missing_caveat(
        ("rooms", "readiness", "finance"), cards, viewpoint="financing"
    )

    assert caveat == "Пока нет подтверждённой информации об условиях оплаты."


def test_missing_caveat_filters_categories_by_viewpoint_but_keeps_requested_fact():
    life = response_module._missing_caveat(
        ("family_infrastructure", "walk_infrastructure", "finance"),
        viewpoint="life",
    )
    requested_school = response_module._missing_caveat(
        ("family_infrastructure",),
        viewpoint="life",
        requested_facts=("schools",),
    )

    assert life is None
    assert requested_school == "Пока нет подтверждённой информации о семейной инфраструктуре."


def test_rental_shortlist_hides_unrequested_ads_and_sales_missing() -> None:
    unrequested = response_module._missing_caveat(
        ("ads", "sales"),
        viewpoint="rental",
    )
    requested_inventory = response_module._missing_caveat(
        ("ads", "sales"),
        viewpoint="rental",
        requested_facts=("apartment_inventory",),
    )
    requested_sales = response_module._missing_caveat(
        ("ads", "sales"),
        viewpoint="rental",
        requested_facts=("sales_count",),
    )

    assert unrequested is None
    assert requested_inventory == "Пока нет подтверждённой информации о количестве объявлений."
    assert requested_sales == "Пока нет подтверждённой информации о продажах по ЕГРН."


def test_rental_shortlist_gives_three_distinct_grounded_sales_angles() -> None:
    cards = (
        OptionCard(name="Левел Павелецкая Сити", location="Даниловский", price_min=18_500_000, ready="сдан"),
        OptionCard(name="Матвеевский парк", location="Очаково-Матвеевское", price_min=16_100_000, ready="сдан"),
        OptionCard(name="Кронштадтский 9", location="Головинский", price_min=15_200_000, ready="сдан"),
    )

    text = response_module.render_response(ResponsePlan(
        acknowledgement="Подобрала три варианта под аренду.",
        cards=cards,
        final_question="Какой вариант хотите рассмотреть подробнее?",
        viewpoint="rental",
    ))

    assert "Дом уже сдан" in text
    assert "средний вариант в текущей тройке" in text
    assert "самый доступный вход" in text
    assert "ЕГРН" not in text
    assert "спрос" not in text
    assert text.count("?") == 1


def test_near_results_explain_difference_and_ask_one_specific_relaxation() -> None:
    plan = SemanticPlan(
        operation="search",
        constraints_delta={"hard": {"rooms": "studio", "max_price": 9_000_000, "finishing": True}},
    )
    result = SearchResult.from_dict({
        "facts": [],
        "near": [{
            "name": "Ближайший",
            "price_min": 8_000_000,
            "room_formats": ["однокомнатные"],
            "is_near": True,
            "why_close": "другая комнатность; отделка не подтверждена",
        }],
        "missing": [],
    })

    turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("студию до 9 млн с отделкой"))

    assert "Отличие: другая комнатность; отделка не подтверждена" in turn.response_text
    assert "Рассмотреть другую комнатность?" in turn.response_text
    assert "Ослабим один параметр?" not in turn.response_text
    assert turn.response_text.count("?") == 1


def test_all_public_actions_finalize_through_process_once():
    actions = [
        SemanticPlan(operation="reset"),
        SemanticPlan(operation="search"),
        SemanticPlan(operation="current_options"),
        SemanticPlan(operation="select_option", selected_option_name="Лучи"),
        SemanticPlan(operation="financing"),
        SemanticPlan(operation="operator"),
        SemanticPlan(operation="operator", operator_consent=True),
        SemanticPlan(operation="operator", operator_consent=False),
        SemanticPlan(operation="freeform"),
        SemanticPlan(operation="bad_operation"),
    ]
    for plan in actions:
        journal = Journal()
        trace = Trace()
        search = SearchService(SearchResult.from_dict({"facts": [{"name": "Лучи"}]}))
        state = ConversationState(visible_options=(OptionCard(name="Лучи"),))
        TurnProcessor(planner=Planner(plan), search_service=search, journal=journal, trace=trace).process(ctx(str(plan.operation)), state)
        assert len(journal.rows) == 1
        assert [e["event"] for e in trace.events].count("finalized") == 1


def test_search_action_calls_search_exactly_once():
    search = SearchService(SearchResult.from_dict({"facts": [{"name": "Один вызов"}]}))

    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=search).process(ctx("найди"))

    assert turn.execution.ok is True
    assert search.calls == 1
    assert search.selected_calls == 0


def test_old_split_ports_are_not_public_definitions():
    assert hasattr(ports, "SearchServicePort")
    assert not hasattr(ports, "FramingPort")
    assert not hasattr(ports, "SearchPort")
    assert not hasattr(ports, "EnrichmentPort")


def test_journal_and_trace_sidecars_do_not_alter_business_state():
    class MutatingJournal:
        def append(self, result):
            result.state["params"] = {"location": "сломано"}

    class MutatingTrace:
        def record(self, event):
            event["event"] = "сломано"

    state = ConversationState(params={"location": "Сокол"})
    result = SearchResult.from_dict({"facts": [{"name": "Сокол Парк"}]})

    turn = TurnProcessor(
        planner=Planner(SemanticPlan(operation="search")),
        search_service=SearchService(result),
        journal=MutatingJournal(),
        trace=MutatingTrace(),
    ).process(ctx("найди"), state)

    assert turn.state["params"]["location"] == "Сокол"
    assert turn.stage.value == "refinement"


def test_async_turn_reports_stage_timings():
    async def scenario():
        result = SearchResult.from_dict({"facts": [{"name": "Тайминг Парк"}]})
        turn = await TurnProcessor(
            planner=Planner(SemanticPlan(operation="search")),
            search_service=SearchService(result),
        ).process_async(ctx("найди"), ConversationState())

        timing = turn.trace["timing_ms"]
        assert set(timing) == {"planner", "execution", "response", "total"}
        assert all(isinstance(value, int) and value >= 0 for value in timing.values())
        assert timing["total"] >= timing["planner"]
        assert timing["total"] >= timing["execution"]

    asyncio.run(scenario())


def test_sync_wrapper_and_async_core_are_equivalent_for_pure_sync_ports():
    result = SearchResult.from_dict({"facts": [{"name": "Одинаковый", "price_min": 12_000_000, "location": "Москва"}], "params": {"location": "Москва"}})
    plan = SemanticPlan(operation="search", intent="life", constraints_delta={"location": "Москва"})

    sync_turn = TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process(ctx("найди в Москве"), ConversationState())

    async def scenario():
        return await TurnProcessor(planner=Planner(plan), search_service=SearchService(result)).process_async(ctx("найди в Москве"), ConversationState())

    async_turn = asyncio.run(scenario())

    assert sync_turn.action == async_turn.action == TurnAction.SEARCH
    assert sync_turn.execution == async_turn.execution
    assert sync_turn.state_delta == async_turn.state_delta
    assert sync_turn.state == async_turn.state
    assert sync_turn.response_text == async_turn.response_text
    assert _stable_turn_shape(sync_turn) == _stable_turn_shape(async_turn)


def _stable_turn_shape(turn):
    data = to_jsonable(turn)
    timing = data["trace"].get("timing_ms")
    assert set(timing or {}) == {"planner", "execution", "response", "total"}
    data["trace"]["timing_ms"] = {key: "<timing>" for key in sorted(timing)}
    runtime_timing = data["trace"].get("runtime_summary", {}).get("timing_ms")
    assert set(runtime_timing or {}) == {"planner", "execution", "response", "total"}
    data["trace"]["runtime_summary"]["timing_ms"] = {key: "<timing>" for key in sorted(runtime_timing)}
    return data


def test_recipe_resolver_called_once_per_sync_turn(monkeypatch):
    calls = []
    original = response_module.scenario_recipes.resolve_recipe

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(response_module.scenario_recipes, "resolve_recipe", spy)
    result = SearchResult.from_dict({"facts": [{"name": "Один", "price_min": 10_000_000}]})
    turn = TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process(ctx("найди"))

    assert turn.response_plan.recipe_id
    assert len(calls) == 1


def test_recipe_resolver_called_once_per_async_turn(monkeypatch):
    async def scenario():
        calls = []
        original = response_module.scenario_recipes.resolve_recipe

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(response_module.scenario_recipes, "resolve_recipe", spy)
        result = SearchResult.from_dict({"facts": [{"name": "Асинк", "price_min": 10_000_000}]})
        turn = await TurnProcessor(planner=Planner(SemanticPlan(operation="search")), search_service=SearchService(result)).process_async(ctx("найди"), ConversationState())

        assert turn.response_plan.recipe_id
        assert len(calls) == 1

    asyncio.run(scenario())


def test_response_composer_has_no_parallel_recipe_resolver_source():
    composer_path = Path(__file__).resolve().parents[1] / "nmbot_v2" / "response_composer.py"
    source = composer_path.read_text(encoding="utf-8")
    assert "def _recipe_contract" not in source
    assert "resolve_recipe" not in source


def test_v2_release_chain_search_refine_select_then_operator_preserves_selected_context():
    first_result = SearchResult.from_dict({"facts": [{"name": "Первый", "price_min": 12_000_000}, {"name": "Второй", "price_min": 14_000_000}]})
    first = TurnProcessor(planner=Planner(SemanticPlan(operation="search", intent="life")), search_service=SearchService(first_result)).process(ctx("найди квартиру"))
    state = ConversationState.from_dict(first.state)

    refine_result = SearchResult.from_dict({"facts": [{"name": "Первый", "price_min": 12_000_000, "finishing": "с отделкой"}, {"name": "Второй", "price_min": 14_000_000}], "params": {"finishing": "с отделкой"}})
    refined = TurnProcessor(planner=Planner(SemanticPlan(operation="refine_search", intent="life", constraints_delta={"finishing": "с отделкой"})), search_service=SearchService(refine_result)).process(ctx("только с отделкой"), state)
    state = ConversationState.from_dict(refined.state)

    selected = TurnProcessor(planner=Planner(SemanticPlan(operation="select_option", selected_option_name="Первый")), search_service=SearchService(selected=OptionCard(name="Первый", price_min=12_000_000, finishing="с отделкой", developer="ПИК"))).process(ctx("первый"), state)
    state = ConversationState.from_dict(selected.state)

    operator = TurnProcessor(planner=Planner(SemanticPlan(operation="operator", explicit_operator_request=True, operator_reason="проверить актуальное наличие"))).process(ctx("позови оператора"), state)

    assert [item["name"] for item in first.state["visible_options"]] == ["Первый", "Второй"]
    assert refined.state["visible_options"][0]["name"] == "Первый"
    assert selected.state["selected_option_name"] == "Первый"
    assert "ЖК «Первый»" in operator.response_text
    assert "проверить актуальное наличие" not in operator.response_text
    assert operator.response_text.count("?") == 1


def test_operator_handoff_never_exposes_internal_planner_reason():
    state = ConversationState(
        selected_option_name="Бусиновский парк",
        visible_options=(OptionCard(name="Бусиновский парк"),),
    )
    plan = SemanticPlan(
        operation="operator",
        operator_consent=True,
        operator_reason=(
            "Клиент подтвердил согласие на проверку условий ипотеки для "
            "выбранного ЖК в рамках pending_scenario"
        ),
    )

    turn = TurnProcessor(planner=Planner(plan)).process(ctx("да"), state)

    assert "ЖК «Бусиновский парк»" in turn.response_text
    assert "На какой номер вам удобно позвонить?" in turn.response_text
    assert "pending_scenario" not in turn.response_text
    assert "Клиент подтвердил" not in turn.response_text
    assert turn.response_text.count("?") == 1


def test_clarification_never_exposes_internal_planner_metadata():
    plan = SemanticPlan(
        operation="clarify_financing",
        clarification="pending_scenario: dialog_action=ask_clarification",
    )
    turn = TurnProcessor(planner=Planner(plan)).process(ctx("уточни"), ConversationState())

    assert "pending_scenario" not in turn.response_text
    assert "dialog_action" not in turn.response_text
    assert turn.response_text.count("?") == 1
