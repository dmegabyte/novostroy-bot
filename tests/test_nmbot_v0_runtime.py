from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from nmbot_v0 import V0State, V0TurnProcessor
from nmbot_v0.runtime import FINANCING_CHECK_ALL_QUESTION, OPERATOR_PHONE_QUESTION, SELECTED_OBJECT_PRESENTATION_QUESTION, V0_CONTACT_PHONE_DIGITS_REQUEST, _build_turn_context, _fallback, _search_cards_without_shown_options, merge_search_params
from nmbot_v0.contracts import SearchResult
from nmbot_v0.card_normalizer import normalize_card
from nmbot_v0.field_contract import v0_presentation_search_fields
from nmbot_v0.presentation import build_shortlist_comparison_context, render_grounded_card_block, shortlist_level_sparse_note
from nmbot_v0.contracts import OptionCard


RENTAL_SELECTED_CTA = "Проверить доступные квартиры для сдачи именно в этом ЖК?"
FAMILY_SELECTED_CTA = "Проверить подходящие семейные планировки в этом ЖК?"
V0_CLIENT_FORBIDDEN_RE = re.compile(r"карточк|проверенн.*данн|непроверенн.*данн|сохран[ёе]нн.*данн|бот настроен|диалогов.*состояни", re.IGNORECASE)


def assert_no_v0_client_forbidden(text: str) -> None:
    assert V0_CLIENT_FORBIDDEN_RE.search(text) is None, text


def test_v0_scenario_prompt_requires_priority_field_contract() -> None:
    text = (Path(__file__).resolve().parents[1] / "prompts" / "v0_scenario_search.txt").read_text(encoding="utf-8")

    assert "V0_RUNTIME_METADATA.search_field_contract.fields" in text
    assert "name`/`alias`" in text
    assert "new_building_class`/`building_type" in text
    assert "school" in text
    assert "не отбрасывай безопасную карточку" in text
    assert "расплывчатое субъективное впечатление" in text
    assert 'action="open_question"' in text
    assert 'response_policy="answer_directly"' in text
    assert "не отправляй к оператору" in text
    assert "без запроса конкретного измеримого факта" in text
    assert "Явная цель сдачи всегда приоритетнее" in text
    assert "на руках" in text
    assert 'viewpoint="rental"' in text
    assert "dynamic live check" in text
    assert "двухкомнатные сейчас есть" in text
    assert 'action="operator"' in text
    assert 'response_policy="operator_phone_request"' in text
    assert "apartment_inventory" in text
    assert 'followup_outcome="new_question"' in text
    assert "не replay project card" in text
    assert "до двух полных записей `ads`" in text
    assert "релевантные связи `house`" in text
    assert "не придумывай `lot_examples`" in text
    assert "state.previous_assistant_message" in text
    assert "не является MCP evidence" in text
    assert "только если `state.visible_options` непустой" in text
    assert "Если `state.visible_options` пустой" in text
    assert 'action="search"' in text
    assert "format_recovery.strict_json_only=true" in text
    assert 'previous_output_invalid_strict_json' in text
    assert "Не упоминай предыдущую ошибку форматирования" in text
    assert "сначала объедини ограничения" in text
    assert "decision.exclude_option_names" in text
    assert "новое пожелание к искомой квартире или ЖК" in text
    assert "не предлагай разобрать прежние варианты" in text
    assert "как только клиент уточнил условие следующим сообщением" in text
    assert "ответ «на воду» означает новый поиск" in text
    assert "не является вопросом о старых карточках" in text


def test_v0_architecture_is_isolated_from_v2_private_response_and_normalizer() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in (root / "nmbot_v0").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "nmbot_v2.response" not in text, path
        assert "nmbot_v2.card_normalizer" not in text, path

    for path in (root / "scripts" / "nmbot_runtime_adapter.py", root / "scripts" / "nmbot_api_server.py", root / "scripts" / "dialogue_journal.py"):
        text = path.read_text(encoding="utf-8")
        assert "V0_PRESENTATION_TRACE_FIELDS" not in text or "nmbot_v0.field_contract" in text, path

    fields = v0_presentation_search_fields()
    assert fields[:2] == ["name", "alias"]
    assert "ads" in fields
    assert "house" in fields
    assert "apartment_types" in fields
    assert "ads.fullprice" in fields
    assert "school" in fields


def test_v2_files_do_not_contain_v0_owned_implementation() -> None:
    root = Path(__file__).resolve().parents[1]
    response = (root / "nmbot_v2" / "response.py").read_text(encoding="utf-8")
    normalizer = (root / "nmbot_v2" / "card_normalizer.py").read_text(encoding="utf-8")
    search_contract = (root / "nmbot_v2" / "search_contract.py").read_text(encoding="utf-8")

    assert "render_grounded_card_block" not in response
    assert "selected_object_grounded_acknowledgement" not in response
    assert 'month = re.fullmatch(r"(20\\d{2})-(0[1-9]|1[0-2])"' not in response
    assert 'if key == "ready" and (' not in normalizer
    assert "_machine(text) in {_machine(label) for label in PROPERTY_CLASS.values()}" not in normalizer
    assert "V0_PRESENTATION_FIELD_GROUPS" not in search_contract
    assert "v0_presentation_search_fields" not in search_contract


def test_v0_ready_boolean_and_property_class_normalization_are_v0_owned() -> None:
    for value in (True, 1, 1.0, "1"):
        assert normalize_card({"name": "ЖК Готовый", "ready": value}).ready == "сдан"

    for value in (False, 0, 0.0, "0"):
        assert normalize_card({"name": "ЖК Не подтверждён", "ready": value}).ready is None

    assert normalize_card({"name": "ЖК Enum", "state": 1}).ready is None
    assert normalize_card({"name": "ЖК Enum", "status": "1"}).ready is None
    assert normalize_card({"name": "ЖК Enum", "developer": "comfort"}).developer is None
    assert normalize_card({"name": "ЖК Enum", "developer": "комфорт-класс"}).developer is None
    assert normalize_card({"name": "ЖК Настоящий", "developer": "ПИК"}).developer == "ПИК"


def test_v0_presentation_formats_iso_year_month_readiness() -> None:
    text = render_grounded_card_block(1, OptionCard(name="Январь", ready="2028-01"), viewpoint="life")

    assert "сдача в январе 2028 года" in text
    assert "2028-01" not in text


def test_v0_search_flow_validates_three_cards_with_scenario_port_only() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life", "params": {"budget": "до 12"}, "active_topic": "life"},
            "search": {
                "facts": [
                    {"name": "ЖК Первый", "location": "Москва", "min_price": 10000000},
                    {"name": "ЖК Второй", "location": "Новая Москва", "min_price": 11000000},
                    {"name": "ЖК Третий", "location": "Москва", "min_price": 12000000},
                    {"name": "ЖК Четвёртый", "location": "Москва", "min_price": 13000000},
                ],
                "near": [],
                "missing": [],
                "params": {},
            },
        }

    result = V0TurnProcessor(scenario_search=scenario_search).process("Подбери новостройку")

    assert result.ok is True
    assert result.error_code is None
    assert len(result.state.visible_options) == 3
    assert result.state.visible_options[0].name == "ЖК Первый"
    assert result.state.params == {"max_price": 12_000_000}


def test_v0_merge_search_params_keeps_previous_constraints_and_normalizes_aliases() -> None:
    assert merge_search_params(
        {"rooms": 2, "location_name": "центр", "finishing": "с отделкой"},
        {"budget": "до 100 млн", "rooms": None},
    ) == {
        "rooms": 2,
        "location": "ЦАО",
        "finishing": "с отделкой",
        "max_price": 100_000_000,
    }


def test_v0_search_merges_state_params_and_excludes_previously_shown_options() -> None:
    initial = V0State(
        params={"rooms": 2, "location_name": "центр"},
        visible_options=(OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")),
    )

    def scenario_search(context):
        assert context["state"]["params"] == {"rooms": 2, "location_name": "центр"}
        return {
            "decision": {
                "action": "search",
                "viewpoint": "life",
                "params": {"budget": "100 млн"},
                "exclude_option_names": ["ЖК Первый", "ЖК Второй", "ЖК Новый"],
            },
            "search": {
                "facts": [
                    {"name": "ЖК Первый", "location": "ЦАО", "rooms": 2, "min_price": 90_000_000},
                    {"name": "ЖК Новый", "location": "ЦАО", "rooms": 2, "min_price": 95_000_000},
                    {"name": "ЖК Ещё один", "location": "ЦАО", "rooms": 2, "min_price": 99_000_000},
                ],
                "near": [],
                "missing": [],
                "params": {},
            },
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Давай другие до 100 млн", state=initial)

    assert result.ok is True
    assert result.state.params == {"rooms": 2, "location": "ЦАО", "max_price": 100_000_000}
    assert [card.name for card in result.state.visible_options] == ["ЖК Новый", "ЖК Ещё один"]


def test_v0_excludes_shown_options_before_final_three_card_limit() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")))
    search_result = SearchResult(
        facts=(
            OptionCard(name="ЖК Первый"),
            OptionCard(name="ЖК Второй"),
            OptionCard(name="ЖК Третий"),
            OptionCard(name="ЖК Четвёртый"),
        )
    )

    cards = _search_cards_without_shown_options(
        search_result,
        {"exclude_option_names": ["ЖК Первый", "ЖК Второй", "ЖК Четвёртый"]},
        initial,
    )

    assert [card.name for card in cards] == ["ЖК Третий", "ЖК Четвёртый"]


def test_v0_search_cards_use_exact_facts_before_near_alternatives() -> None:
    search_result = SearchResult(
        facts=(OptionCard(name="ЖК Exact"),),
        near=(OptionCard(name="ЖК Near"),),
    )

    cards = _search_cards_without_shown_options(search_result, {}, V0State())

    assert [card.name for card in cards] == ["ЖК Exact"]


def test_v0_search_cards_use_near_only_when_facts_empty() -> None:
    search_result = SearchResult(near=(OptionCard(name="ЖК Near 1"), OptionCard(name="ЖК Near 2")))

    cards = _search_cards_without_shown_options(search_result, {}, V0State())

    assert [card.name for card in cards] == ["ЖК Near 1", "ЖК Near 2"]


def test_v0_empty_search_preserves_previous_visible_options() -> None:
    initial = V0State(
        params={"rooms": 2, "location": "ЦАО"},
        visible_options=(OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй")),
    )
    calls = []

    def scenario_search(context):
        calls.append(context)
        return {
            "decision": {"action": "search", "viewpoint": "life", "params": {"budget": "100 млн"}},
            "search": {"facts": [], "near": [], "missing": [], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("До 100 млн", state=initial)

    assert result.ok is True
    assert len(calls) == 2
    assert result.state.visible_options == initial.visible_options
    assert result.state.params == {"rooms": 2, "location": "ЦАО", "max_price": 100_000_000}
    assert result.answer.scope == "operator_phone"
    assert result.state.pending_action == "contact_phone"
    assert OPERATOR_PHONE_QUESTION in result.message
    assert "Какой вариант хотите разобрать подробнее?" not in result.message


def test_v0_empty_search_recovers_grounded_cards_on_second_call() -> None:
    calls = []

    def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return {
                "decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2, "budget": "до 14 млн", "district": "ЦАО"}},
                "search": {"facts": [], "near": [], "missing": [], "params": {}},
            }
        assert context["search_recovery"]["reason"] == "valid_empty_search"
        assert context["search_recovery"]["attempt"] == 1
        assert context["search_recovery"]["max_attempts"] == 1
        assert context["search_recovery"]["retained_hard_params"] == {"rooms": 2, "max_price": 14_000_000, "district": "ЦАО"}
        return {
            "decision": {"action": "operator", "viewpoint": "life"},
            "search": {"facts": [{"name": "ЖК Второй шанс", "district": "ЦАО", "rooms": 2, "min_price": 13_500_000}], "near": [], "missing": [], "params": {"rooms": 2, "district": "ЦАО"}},
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Нужна двушка в Москве до 14 млн")

    assert result.ok is True
    assert len(calls) == 2
    assert [card.name for card in result.state.visible_options] == ["ЖК Второй шанс"]
    assert result.answer.scope == "shortlist"
    assert "Какой вариант хотите разобрать подробнее?" in result.message
    assert result.diagnostics["decision"]["action"] == "search"
    assert result.diagnostics["search_validation"]["recovery"]["outcome"] == "recovered"
    trace = result.diagnostics["search_validation"]["field_trace"]["cards"][0]
    assert "district" in trace["raw_fields"]
    assert "rooms" in trace["raw_fields"]
    assert result.diagnostics["search_validation"]["initial_field_trace"] == {"cards": []}


def test_v0_empty_search_async_recovers_grounded_cards_on_second_call() -> None:
    calls = []

    async def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return {"decision": {"action": "search", "viewpoint": "life", "params": {"budget": "до 12 млн"}}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}
        return {"decision": {"action": "off_topic", "viewpoint": "life"}, "search": {"facts": [{"name": "ЖК Асинхронный", "location": "Москва", "min_price": 11_000_000}], "near": [], "missing": [], "params": {}}}

    result = asyncio.run(V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process_async("До 12 млн"))

    assert result.ok is True
    assert len(calls) == 2
    assert [card.name for card in result.state.visible_options] == ["ЖК Асинхронный"]
    assert result.diagnostics["decision"]["action"] == "search"
    assert result.diagnostics["search_validation"]["recovery"]["outcome"] == "recovered"


def test_v0_empty_search_both_empty_fails_closed_to_operator_phone() -> None:
    calls = []

    def scenario_search(context):
        calls.append(context)
        return {"decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Нужна двушка")

    assert result.ok is True
    assert len(calls) == 2
    assert result.answer.scope == "operator_phone"
    assert result.answer.final_question == OPERATOR_PHONE_QUESTION
    assert "подтверждённые варианты" in result.message
    assert "Не буду придумывать" in result.message
    assert "Оператор сможет проверить текущую доступность" in result.message
    assert "Какой вариант хотите разобрать подробнее?" not in result.message
    assert result.message.count("?") == 1
    assert result.state.pending_action == "contact_phone"
    assert result.diagnostics["decision"]["action"] == "search"
    assert result.diagnostics["search_validation"]["recovery"]["outcome"] == "empty"


def test_v0_malformed_first_then_valid_empty_second_skips_recovery_budget_exhausted_sync() -> None:
    calls = []

    def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return "not-json"
        return {"decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Нужна двушка")

    assert result.ok is True
    assert len(calls) == 2
    assert "search_recovery" not in calls[1]
    assert result.answer.scope == "operator_phone"
    assert result.state.pending_action == "contact_phone"
    assert result.diagnostics["search_validation"]["recovery"] == {"attempted": False, "outcome": "skipped_budget_exhausted"}


def test_v0_malformed_first_then_valid_empty_second_skips_recovery_budget_exhausted_async() -> None:
    calls = []

    async def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return "not-json"
        return {"decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    result = asyncio.run(V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process_async("Нужна двушка"))

    assert result.ok is True
    assert len(calls) == 2
    assert result.answer.scope == "operator_phone"
    assert result.state.pending_action == "contact_phone"
    assert result.diagnostics["search_validation"]["recovery"] == {"attempted": False, "outcome": "skipped_budget_exhausted"}


def test_v0_empty_search_recovery_malformed_or_invalid_never_third_call_and_fails_closed() -> None:
    recovery_payloads = (
        "not-json",
        {"decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}}, "search": {"facts": [{"name": "ЖК Без hard evidence"}], "near": [], "missing": [], "params": {}}},
    )
    for recovery_payload in recovery_payloads:
        calls = []

        def scenario_search(context):
            calls.append(context)
            if len(calls) == 1:
                return {"decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}
            return recovery_payload

        result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Нужна двушка")

        assert result.ok is True
        assert len(calls) == 2
        assert result.answer.scope == "operator_phone"
        assert result.state.pending_action == "contact_phone"
        assert result.diagnostics["search_validation"]["recovery"]["outcome"] == "invalid"


def test_v0_search_first_nonempty_does_not_recover() -> None:
    calls = []

    def scenario_search(context):
        calls.append(context)
        return {"decision": {"action": "search", "viewpoint": "life", "params": {"budget": "до 10 млн"}}, "search": {"facts": [{"name": "ЖК Сразу", "location": "Москва", "min_price": 9_000_000}], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("До 10 млн")

    assert result.ok is True
    assert len(calls) == 1
    assert [card.name for card in result.state.visible_options] == ["ЖК Сразу"]
    assert "recovery" not in result.diagnostics["search_validation"]


def test_v0_finance_empty_search_recovery_keeps_down_payment_unconfirmed() -> None:
    calls = []

    def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return {
                "decision": {"action": "search", "viewpoint": "financing", "active_topic": "financing", "params": {"down_payment": 0, "budget": "до 15 млн"}, "requested_facts": ["down_payment"]},
                "search": {"facts": [], "near": [], "missing": [], "params": {}},
            }
        assert context["search_recovery"]["relaxed_semantic_params"]["down_payment"] == 0
        return {
            "decision": {"action": "search", "viewpoint": "life"},
            "search": {"facts": [{"name": "ЖК Базовый", "location": "Москва", "min_price": 14_000_000}], "near": [], "missing": ["down_payment"], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Есть варианты без первоначального взноса до 15 млн?")

    assert result.ok is True
    assert len(calls) == 2
    assert [card.name for card in result.state.visible_options] == ["ЖК Базовый"]
    assert "без первоначального взноса" in result.message
    assert "не подтверждаю" in result.message
    assert "нет подтверждения" in result.message
    assert "доступны без первоначального взноса" in result.message
    assert "покупку без первоначального взноса подтверждаю" not in result.message
    assert result.answer.final_question == FINANCING_CHECK_ALL_QUESTION
    assert result.diagnostics["search_validation"]["recovery"]["outcome"] == "recovered"


def test_v0_scenario_malformed_once_retries_and_recovers_to_answer_once() -> None:
    calls = {"scenario": 0, "answer": 0}

    def scenario_search(_context):
        calls["scenario"] += 1
        if calls["scenario"] == 1:
            return "not-json"
        return {
            "decision": {"action": "search", "viewpoint": "life", "params": {"budget": "до 12"}, "active_topic": "life"},
            "search": {"facts": [{"name": "ЖК Первый", "location": "Москва", "min_price": 10_000_000}], "near": [], "missing": [], "params": {}},
        }

    def answer(brief):
        calls["answer"] += 1
        return {
            "answer_kind": brief["decision"]["expected_answer_kind"],
            "scope": brief["decision"]["expected_scope"],
            "intro": "runtime replaces this",
            "options": [{"name": card["name"]} for card in brief["allowed_cards"]],
            "recommendation": "",
            "missing_note": "",
            "final_question": brief["decision"]["cta_template"],
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Подбери новостройку")

    assert result.ok is True
    assert calls == {"scenario": 2, "answer": 0}
    assert [card.name for card in result.state.visible_options] == ["ЖК Первый"]
    assert "цены от 10 000 000 ₽" in result.message


def test_v0_scenario_malformed_twice_falls_back_without_answer_or_state_mutation() -> None:
    initial = V0State(params={"rooms": 2})
    calls = {"scenario": 0, "answer": 0}
    contexts = []

    def scenario_search(context):
        calls["scenario"] += 1
        contexts.append(context)
        return "not-json"

    def answer(_brief):
        calls["answer"] += 1
        raise AssertionError("answer prompt must not be called")

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Подбери", state=initial)

    assert result.ok is False
    assert result.error_code == "malformed_scenario_output"
    assert result.state is initial
    assert calls == {"scenario": 2, "answer": 0}
    assert result.diagnostics["errors"]
    assert "попробуйте" in result.message.lower()
    assert not re.search(r"телефон|оператор|менеджер", result.message, re.IGNORECASE)
    assert "format_recovery" not in contexts[0]
    assert contexts[1]["format_recovery"] == {"strict_json_only": True, "reason": "previous_output_invalid_strict_json"}
    assert "not-json" not in json.dumps(contexts[1], ensure_ascii=False)
    assert "format_recovery" not in contexts[0]


def test_v0_previous_assistant_message_is_state_context_and_successfully_persisted() -> None:
    previous = "Вот варианты, которые клиент видел.\nКакой вариант хотите разобрать подробнее?"
    initial = V0State(previous_assistant_message=previous, active_topic="life")
    calls = []

    def scenario_search(context):
        calls.append(context)
        assert context["user_text"] == "А второй?"
        assert "previous_assistant_message" not in context
        assert context["state"]["previous_assistant_message"] == previous
        assert context["state"]["active_topic"] == "life"
        return {
            "decision": {"action": "search", "viewpoint": "life", "active_topic": "life"},
            "search": {"facts": [{"name": "ЖК Второй", "location": "Москва", "min_price": 11_000_000}], "near": [], "missing": [], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("А второй?", state=initial)

    assert result.ok is True
    assert len(calls) == 1
    assert result.state.previous_assistant_message == result.message


def test_v0_previous_assistant_message_is_bounded_in_turn_context() -> None:
    long_previous = "п" * 2100
    long_user = "у" * 2100

    context = _build_turn_context(long_user, V0State(previous_assistant_message=long_previous), conversation_ref="local")

    assert len(context["user_text"]) == 2000
    assert len(context["state"]["previous_assistant_message"]) == 2000


def test_v0_previous_assistant_message_not_mutated_on_malformed_scenario_fallback() -> None:
    initial = V0State(previous_assistant_message="старый видимый ответ")

    def scenario_search(_context):
        return "not-json"

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Подбери", state=initial)

    assert result.ok is False
    assert result.state is initial
    assert result.state.previous_assistant_message == "старый видимый ответ"


def test_v0_search_field_trace_and_normalizer_keep_family_life_fields() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "family", "params": {"budget": "до 15"}, "active_topic": "family"},
            "search": {
                "facts": [
                    {
                        "name": "ЖК Семейный",
                        "alias": "Семейный",
                        "location": "Москва",
                        "rooms": 2,
                        "square_min": 54,
                        "finishing": "с отделкой",
                        "delivered": True,
                        "property_metro": "Парк",
                        "developer": "Девелопер",
                        "school": True,
                        "kindergarten": True,
                        "park_near": True,
                        "min_price": 12_000_000,
                        "client_text": "секретный запрос клиента",
                    }
                ],
                "near": [],
                "missing": [],
                "params": {},
            },
        }

    def answer(brief):
        card = brief["allowed_cards"][0]
        assert card["location"] == "Москва"
        assert card["rooms"] == "2"
        assert card["finishing"] == "с отделкой"
        assert card["ready"] == "сдан"
        assert card["metro"] == "Парк"
        assert card["developer"] == "Девелопер"
        assert "школа" in card["infrastructure"]
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "runtime replaces this",
            "options": [{"name": "ЖК Семейный"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("нужнва кавртира для семьи пв 10 млн")

    assert result.ok is True
    card = result.state.visible_options[0]
    assert card.location == "Москва"
    assert card.rooms == "2"
    assert card.finishing == "с отделкой"
    assert card.ready == "сдан"
    assert card.metro == "Парк"
    assert card.developer == "Девелопер"
    assert "школа" in card.infrastructure
    trace = result.diagnostics["search_validation"]["field_trace"]["cards"][0]
    assert {"name", "location", "rooms", "finishing", "delivered", "property_metro", "developer", "school"}.issubset(set(trace["raw_fields"]))
    assert {"name", "location", "rooms", "finishing", "ready", "metro", "developer", "infrastructure"}.issubset(set(trace["normalized_fields"]))
    dumped = json.dumps(trace, ensure_ascii=False)
    assert "ЖК Семейный" not in dumped
    assert "секретный запрос" not in dumped


def test_v0_client_messages_do_not_expose_internal_wording_representative_paths() -> None:
    def search_scenario(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life", "params": {"max_price": 12_000_000}, "active_topic": "life"},
            "search": {"facts": [{"name": "ЖК Первый", "location": "Москва", "min_price": 10_000_000}], "near": [], "missing": [], "params": {}},
        }

    def names_answer(brief):
        return {
            "answer_kind": brief["decision"]["expected_answer_kind"],
            "scope": brief["decision"]["expected_scope"],
            "intro": "runtime replaces",
            "options": [{"name": item["name"]} for item in brief["allowed_cards"]],
            "recommendation": "",
            "missing_note": "",
            "final_question": brief["decision"]["cta_template"],
        }

    successful = V0TurnProcessor(scenario_search=search_scenario, answer=names_answer).process("Подбери")
    assert successful.ok is True
    assert_no_v0_client_forbidden(successful.message)

    sparse_state = V0State(visible_options=(OptionCard(name="ЖК Сухой"),), selected_option_name="ЖК Сухой", active_topic="life")

    def selected_scenario(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Сухой", "active_topic": "life"}, "search": {}}

    sparse = V0TurnProcessor(scenario_search=selected_scenario, answer=names_answer).process("Расскажи", state=sparse_state)
    assert sparse.ok is True
    assert "по этому варианту пока мало" in sparse.message
    assert_no_v0_client_forbidden(sparse.message)

    fallback = V0TurnProcessor(scenario_search=lambda _context: "not-json", answer=names_answer).process("Сломайся")
    assert fallback.ok is False
    assert_no_v0_client_forbidden(fallback.message)


def test_v0_search_validation_invalid_hard_fails_closed_without_persisting_cards() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life", "params": {"rooms": 2}, "active_topic": "life"},
            "search": {"facts": [{"name": "ЖК Нестрогий", "rooms": [3], "location": "Москва"}], "near": [], "missing": [], "params": {}},
        }

    def answer(_brief):
        raise AssertionError("invalid search must not reach answer rendering")

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Нужна двушка")

    assert result.ok is False
    assert result.error_code == "invalid_search_output"
    assert result.state == V0State()
    assert "ЖК Нестрогий" not in result.message
    validation = result.diagnostics["search_validation"]
    assert validation["status"] == "invalid"
    assert "fact_0_violates_hard:rooms" in validation["errors"]


def test_v0_missing_open_fact_uses_operator_phone_policy() -> None:
    def scenario_search(_context):
        return {
            "decision": {
                "action": "operator",
                "viewpoint": "life",
                "client_question": "Сколько стоит паркинг?",
                "requested_facts": ["parking_price"],
                "response_policy": "operator_phone_request",
            },
            "search": {"facts": [], "near": [], "missing": ["parking_price"], "params": {}},
        }

    def answer(brief):
        assert brief["decision"]["response_policy"] == "operator_phone_request"
        assert brief["decision"]["operator_handoff_template"] == OPERATOR_PHONE_QUESTION
        return {
            "answer_kind": "operator",
            "scope": "operator_phone",
            "intro": "Цену паркинга наугад не назову.",
            "options": [],
            "recommendation": "",
            "missing_note": "Лучше проверим это через оператора.",
            "final_question": OPERATOR_PHONE_QUESTION,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Сколько стоит паркинг?")

    assert result.ok is True
    assert result.answer is not None
    assert result.answer.final_question == OPERATOR_PHONE_QUESTION
    assert result.state.pending_action == "contact_phone"
    assert result.message.startswith("Не буду отвечать наугад.")
    assert "Лучше проверим это через оператора." not in result.message
    assert "Нужно проверить это у оператора по актуальным данным." in result.message


def test_v0_answer_port_mismatched_card_is_not_called_and_current_options_stays_deterministic() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Свой", location="Москва"),), active_topic="life")
    calls = {"answer": 0}

    def scenario_search(_context):
        return {"decision": {"action": "current_options", "viewpoint": "life"}, "search": {}}

    def answer(_brief):
        calls["answer"] += 1
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "Вот вариант.",
            "options": [{"name": "ЖК Чужой"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("А что по ним?", state=initial)

    assert result.ok is True
    assert result.error_code is None
    assert calls["answer"] == 0
    assert [option["name"] for option in result.answer.options] == ["ЖК Свой"]
    assert "ЖК «Свой»" in result.message
    assert result.state.visible_options == initial.visible_options


def test_v0_malformed_scenario_output_skips_answer_and_preserves_state() -> None:
    initial = V0State(params={"rooms": 2})
    calls = {"answer": 0}

    def scenario_search(_context):
        return "not json"

    def answer(_brief):
        calls["answer"] += 1
        raise AssertionError("answer prompt must not be called")

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Привет", state=initial)

    assert result.ok is False
    assert result.error_code == "malformed_scenario_output"
    assert result.state is initial
    assert calls["answer"] == 0
    assert "попробуйте" in result.message.lower()
    assert not re.search(r"телефон|оператор|менеджер", result.message, re.IGNORECASE)


def test_v0_scenario_format_recovery_context_is_copy_and_can_succeed() -> None:
    calls: list[dict] = []

    def scenario_search(context):
        calls.append(context)
        if len(calls) == 1:
            return "not json"
        assert context["format_recovery"] == {"strict_json_only": True, "reason": "previous_output_invalid_strict_json"}
        return {"decision": {"action": "off_topic", "viewpoint": "life", "active_topic": "life"}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario_search, answer=_names_only_answer).process("Привет")

    assert result.ok is True
    assert len(calls) == 2
    assert "format_recovery" not in calls[0]
    assert "not json" not in json.dumps(calls[1], ensure_ascii=False)


def test_v0_deterministic_off_topic_fallback_stays_in_valeria_role() -> None:
    def scenario_search(_context):
        return {"decision": {"action": "off_topic", "viewpoint": "life", "active_topic": "life"}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    def answer(_brief):
        raise AssertionError("deterministic off-topic fallback must not need answer provider")

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("а как сварить пельмени?")

    assert result.ok is True
    assert result.answer.answer_kind == "off_topic"
    assert "Валерия" in result.message
    assert "подбором новостройки" in result.message
    assert result.message.endswith("Вернёмся к подбору новостройки?")


def test_v0_scenario_format_recovery_context_is_copy_async() -> None:
    initial = V0State(params={"rooms": 1})
    contexts: list[dict] = []
    answer_calls = 0

    async def scenario_search(context):
        contexts.append(context)
        return "not json"

    async def answer(_brief):
        nonlocal answer_calls
        answer_calls += 1
        raise AssertionError("answer prompt must not be called")

    async def run_case():
        return await V0TurnProcessor(scenario_search=scenario_search, answer=answer).process_async("Подбери", state=initial)

    result = asyncio.run(run_case())

    assert result.ok is False
    assert result.error_code == "malformed_scenario_output"
    assert result.state is initial
    assert answer_calls == 0
    assert len(contexts) == 2
    assert "format_recovery" not in contexts[0]
    assert contexts[1]["format_recovery"] == {"strict_json_only": True, "reason": "previous_output_invalid_strict_json"}
    assert "not json" not in json.dumps(contexts[1], ensure_ascii=False)
    assert "попробуйте" in result.message.lower()
    assert not re.search(r"телефон|оператор|менеджер", result.message, re.IGNORECASE)


def test_v0_continuing_selected_object_ignores_answer_port_greeting_and_uses_canonical_answer() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый", location="Москва", price_min=10_000_000),), selected_option_name="ЖК Первый", has_greeted=True)
    calls = {"answer": 0}

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Первый"}}

    def answer(_brief):
        calls["answer"] += 1
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "Здравствуйте, по ЖК Первый есть данные.",
            "options": [{"name": "ЖК Первый"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": FAMILY_SELECTED_CTA,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Расскажи про первый", state=initial)

    assert result.ok is True
    assert calls["answer"] == 0
    assert "Здравствуйте" not in result.message
    assert result.answer.final_question == SELECTED_OBJECT_PRESENTATION_QUESTION


def test_v0_selected_object_ignores_model_cta_and_uses_runtime_cta() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый", location="Москва", price_min=10_000_000),), selected_option_name="ЖК Первый", has_greeted=True)

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Первый"}}

    def answer(_brief):
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "По ЖК Первый есть подтверждённые данные.",
            "options": [{"name": "ЖК Первый"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Расскажи про первый", state=initial)

    assert result.ok is True
    assert result.answer is not None
    assert result.answer.final_question == SELECTED_OBJECT_PRESENTATION_QUESTION
    assert result.message.endswith(SELECTED_OBJECT_PRESENTATION_QUESTION)


def test_v0_selected_object_renders_structured_card_without_marketing_description() -> None:
    initial = V0State(
        params={"budget": "до 12 млн"},
        visible_options=(
            OptionCard(
                name="ЖК Первый",
                location="Москва",
                district="ЮАО",
                price_min=10_000_000,
                rooms=2,
                area="54 м²",
                finishing="с отделкой",
                ready="сдан",
                metro="Павелецкая",
                developer="Девелопер",
                infrastructure=("школа", "парк"),
            ),
        ),
        selected_option_name="ЖК Первый",
        has_greeted=True,
    )

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "family", "selected_option_name": "ЖК Первый", "requested_facts": ["price_min", "finishing"]}}

    def answer(_brief):
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "По ЖК Первый в сохранённой подборке есть такие факты.",
            "options": [{"name": "ЖК Первый"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": FAMILY_SELECTED_CTA,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Расскажи про первый", state=initial)

    assert result.ok is True
    assert "1. ЖК «Первый» — Москва, цены от 10 000 000 ₽" in result.message
    assert "с отделкой" in result.message
    assert "рядом: школа, парк" in result.message
    assert "Рядом есть школа" in result.message
    assert "вписывается в указанный бюджет" in result.message
    assert "самый понятный" not in result.message.casefold()
    assert result.answer is not None
    assert result.answer.final_question == FAMILY_SELECTED_CTA
    assert result.state.last_answer_kind == "selected_object"
    assert result.state.last_assistant_question == FAMILY_SELECTED_CTA
    assert "price_min" in result.state.answered_facts


def test_v0_model_marketing_intro_and_recommendation_do_not_enter_search_output() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life"},
            "search": {"facts": [{"name": "ЖК Точный", "location": "Москва", "min_price": 7_483_188}], "near": [], "missing": [], "params": {}},
        }

    def answer(_brief):
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "Лучший инвестиционный хит с гарантированным ростом цены.",
            "options": [{"name": "ЖК Точный"}],
            "recommendation": "лучший по цене и качеству",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Подбери ЖК")

    assert result.ok is True
    assert "Лучший инвестиционный хит" not in result.message
    assert "лучший по цене и качеству" not in result.message
    assert result.message.startswith("По вашему запросу подходит один вариант.")
    assert "Нашла один подходящий вариант" not in result.message
    assert "цены от 7 483 188 ₽" in result.message


def test_v0_model_marketing_intro_and_recommendation_do_not_enter_selected_output() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый", location="Москва", price_min=7_483_188),), selected_option_name="ЖК Первый", has_greeted=True)

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Первый"}}

    def answer(_brief):
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "Лучший вариант для счастливой жизни и инвестиций.",
            "options": [{"name": "ЖК Первый"}],
            "recommendation": "лучший по цене и качеству",
            "missing_note": "",
            "final_question": SELECTED_OBJECT_PRESENTATION_QUESTION,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Расскажи про него", state=initial)

    assert result.ok is True
    assert result.message.startswith("По ЖК «Первый» могу рассказать вот что.")
    assert "Лучший вариант" not in result.message
    assert "лучший по цене и качеству" not in result.message
    assert "цены от 7 483 188 ₽" in result.message


def test_v0_selected_one_price_fact_says_other_characteristics_not_confirmed_without_operator() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Цена", price_min=7_483_188),), selected_option_name="ЖК Цена", has_greeted=True)

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Цена", "requested_facts": ["price_min"]}}

    def answer(_brief):
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "Маркетинговый текст не должен попасть в ответ.",
            "options": [{"name": "ЖК Цена"}],
            "recommendation": "лучший по цене и качеству",
            "missing_note": "",
            "final_question": SELECTED_OBJECT_PRESENTATION_QUESTION,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Какая цена?", state=initial)

    assert result.ok is True
    assert "цены от 7 483 188 ₽" in result.message
    assert "по этому варианту пока мало подтверждённых деталей" in result.message
    assert OPERATOR_PHONE_QUESTION not in result.message
    assert result.state.last_answer_kind == "selected_object"


def test_v0_live_typed_option_echo_port_is_not_called_and_canonical_facts_render() -> None:
    calls = {"answer": 0}

    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life"},
            "search": {
                "facts": [
                    {"name": "ЖК Первый", "location": "Москва", "min_price": 7_483_188},
                    {"name": "ЖК Второй", "location": "Новая Москва", "min_price": 8_100_000},
                    {"name": "ЖК Третий", "location": "Москва", "min_price": 9_200_000},
                ],
                "near": [],
                "missing": [],
                "params": {},
            },
        }

    typed_payload = {
        "answer_kind": "search_many",
        "scope": "shortlist",
        "intro": "runtime заменит",
        "options": [
            {"name": "ЖК Первый", "location": "г. Москва", "price_min": "от 7.4 млн", "benefit_fact": "price_min"},
            {"name": "ЖК Второй", "location": "Новая Москва", "price_min": "от 8.1 млн", "benefit_fact": "price_min"},
            {"name": "ЖК Третий", "location": "Москва", "price_min": "от 9.2 млн", "benefit_fact": "price_min"},
        ],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите разобрать подробнее?",
    }

    def answer(_brief):
        calls["answer"] += 1
        return typed_payload

    accepted = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Подбери")
    assert accepted.ok is True
    assert calls["answer"] == 0
    assert "Москва" in accepted.message
    assert "цены от 7 483 188 ₽" in accepted.message
    assert "г. Москва" not in accepted.message
    assert "от 7.4 млн" not in accepted.message


def test_v0_search_uses_canonical_card_order_when_answer_port_would_reorder() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый"), OptionCard(name="ЖК Второй"), OptionCard(name="ЖК Третий")), active_topic="life")
    calls = {"answer": 0}

    def scenario_search(_context):
        return {"decision": {"action": "current_options", "viewpoint": "life"}}

    def answer(_brief):
        calls["answer"] += 1
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "runtime заменит",
            "options": [{"name": "ЖК Второй"}, {"name": "ЖК Первый"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Что по ним?", state=initial)
    assert result.ok is True
    assert calls["answer"] == 0
    assert [option["name"] for option in result.answer.options] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]


def test_v0_family_shortlist_with_fact_above_hard_budget_fails_closed() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "family", "active_topic": "family", "params": {"max_price": 10_000_000}},
            "search": {
                "facts": [
                    {"name": "ЖК Школа", "location": "Москва", "min_price": 9_700_000, "school": True, "kindergarten": True},
                    {"name": "ЖК Готовый", "location": "Москва", "min_price": 10_200_000, "delivered": True},
                    {"name": "ЖК Парк", "location": "Москва", "min_price": 11_000_000, "park_near": True},
                ],
                "near": [],
                "missing": [],
                "params": {"max_price": 10_000_000},
            },
        }

    def answer(_brief):
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "runtime replaces this",
            "options": [{"name": "ЖК Школа"}, {"name": "ЖК Готовый"}, {"name": "ЖК Парк"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("нужнва кавртира для семьи пв 10 млн")

    assert result.ok is False
    assert result.error_code == "invalid_search_output"
    assert result.state == V0State()
    assert "ЖК «Школа»" not in result.message
    assert "ЖК «Готовый»" not in result.message
    assert "ЖК «Парк»" not in result.message
    assert "fact_1_violates_hard:max_price" in result.diagnostics["search_validation"]["errors"]
    assert "fact_2_violates_hard:max_price" in result.diagnostics["search_validation"]["errors"]


def test_v0_family_ready_cards_fall_back_to_distinct_life_location_benefits() -> None:
    cards = [
        {"name": "ЖК Первый", "location": "Котельники", "min_price": 8_000_000, "ready": 1},
        {"name": "ЖК Второй", "location": "Коммунарка", "min_price": 9_000_000, "ready": 1},
        {"name": "ЖК Третий", "location": "Внуково", "min_price": 9_500_000, "ready": 1},
    ]

    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "family", "params": {"max_price": 10_000_000}},
            "search": {"facts": cards, "near": [], "missing": [], "params": {"max_price": 10_000_000}},
        }

    def answer(_brief):
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "runtime replaces this",
            "options": [{"name": card["name"]} for card in cards],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("для семьи до 10 млн")

    assert result.ok is True
    assert result.message.count("дом сдан") == 1
    assert "самый низкий подтверждённый старт" in result.message
    assert "ближайший вариант отличается на 500 000 ₽" in result.message
    assert "локация Внуково" in result.message


def test_v0_shortlist_states_shared_family_facts_once_and_uses_price_roles() -> None:
    cards = [
        {"name": "ЖК Первый", "location": "Север", "min_price": 9_000_000, "ready": 1, "school": True},
        {"name": "ЖК Второй", "location": "Юг", "min_price": 9_500_000, "ready": 1, "school": True},
        {"name": "ЖК Третий", "location": "Запад", "min_price": 10_400_000, "ready": 1, "school": True},
    ]

    def scenario(_context):
        return {"decision": {"action": "search", "viewpoint": "family"}, "search": {"facts": cards, "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Подбери для семьи")

    assert result.ok is True
    assert result.message.count("рядом указаны школа") == 1
    assert result.message.count("дом сдан") == 1
    assert "самый низкий подтверждённый старт" in result.message
    assert "ближайший вариант отличается на 500 000 ₽" in result.message
    assert "Ценового преимущества здесь нет" in result.message


def test_v0_equal_price_location_choice_has_no_invented_winner() -> None:
    cards = [
        {"name": "ЖК Восток", "location": "Восточный район", "min_price": 9_000_000},
        {"name": "ЖК Запад", "location": "Западный район", "min_price": 9_000_000},
    ]

    def scenario(_context):
        return {"decision": {"action": "search", "viewpoint": "life"}, "search": {"facts": cards, "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Сравни")

    assert result.ok is True
    assert result.message.count("одинаковая стартовая цена — 9 000 000 ₽") == 1
    assert result.message.count("Отдельного преимущества по подтверждённым фактам нет") == 2
    assert "лучший" not in result.message.casefold()


def test_v0_homogeneous_sparse_shortlist_keeps_each_card_readable_and_honest() -> None:
    cards = [
        {"name": "ЖК Первый", "location": "Зеленоград", "ready": 1},
        {"name": "ЖК Второй", "location": "Зеленоград", "ready": 1},
        {"name": "ЖК Третий", "location": "Зеленоград", "ready": 1},
    ]

    def scenario(_context):
        return {"decision": {"action": "search", "viewpoint": "life"}, "search": {"facts": cards, "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("В зеленогрде есть что?")

    assert result.ok is True
    honest_note = "По этим ЖК пока подтверждены только общие характеристики; цены и планировки лучше сравнить отдельно."
    assert result.message.count("Зеленоград, дом сдан") == 1
    assert "По известным характеристикам этот вариант пока не отличается от остальных в подборке" not in result.message
    assert result.message.count(honest_note) == 1
    assert result.message.count("Общее для всех вариантов:") == 1
    assert result.message.count("Какой вариант хотите разобрать подробнее?") == 1
    assert all(f"{index}. ЖК «{name}»." in result.message for index, name in enumerate(("Первый", "Второй", "Третий"), start=1))


def test_v0_runtime_keeps_new_building_class_and_building_type_in_final_cards() -> None:
    cards = [
        {"name": "ЖК Первый", "location": "Зеленоград", "ready": 1, "new_building_class": "comfort"},
        {"name": "ЖК Второй", "location": "Зеленоград", "ready": 1, "building_type": "economy"},
        {"name": "ЖК Третий", "location": "Зеленоград", "ready": 1},
    ]

    def scenario(_context):
        return {"decision": {"action": "search", "viewpoint": "life"}, "search": {"facts": cards, "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("В зеленогрде есть что?")

    assert result.ok is True
    assert "ЖК «Первый» — комфорт-класс" in result.message
    assert "ЖК «Второй» — эконом-класс" in result.message
    assert "По этим ЖК пока подтверждены только общие характеристики" not in result.message
    assert "По известным характеристикам этот вариант пока не отличается от остальных в подборке" not in result.message


def test_v0_sparse_card_remains_honest_without_inventing_details() -> None:
    def scenario_search(_context):
        return {
            "decision": {"action": "search", "viewpoint": "family", "active_topic": "family"},
            "search": {"facts": [{"name": "ЖК Только имя"}], "near": [], "missing": [], "params": {}},
        }

    def answer(_brief):
        return {
            "answer_kind": "search_many",
            "scope": "shortlist",
            "intro": "runtime replaces this",
            "options": [{"name": "ЖК Только имя"}],
            "recommendation": "",
            "missing_note": "",
            "final_question": "Какой вариант хотите разобрать подробнее?",
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("подбери для семьи")

    assert result.ok is True
    assert "1. ЖК «Только имя»." in result.message
    assert "по этому варианту пока мало полезных деталей" in result.message
    assert "школ" not in result.message.casefold()
    assert "парк" not in result.message.casefold()


def test_v0_presentation_renders_grounded_card_blocks_directly() -> None:
    cards = (
        OptionCard(name="ЖК Семейный", location="Котельники", price_min=11_900_000, infrastructure=("школа", "детский сад"), finishing="с отделкой"),
        OptionCard(name="ЖК Парк", location="Котельники", price_min=12_600_000, infrastructure=("парк",), ready="сдан"),
    )
    used: set[str] = set()
    rendered = "\n\n".join(render_grounded_card_block(index, card, viewpoint="family", used_benefits=used, cards=cards) for index, card in enumerate(cards, 1))

    assert "1. ЖК «Семейный»" in rendered
    assert "Рядом есть школа, детский сад" in rendered
    assert "2. ЖК «Парк»" in rendered
    assert "Парк добавляет семье" in rendered


def test_v0_presentation_unique_property_class_prevents_sparse_note() -> None:
    cards = (
        OptionCard(name="ЖК Первый", location="Зеленоград", ready="сдан", property_class="business"),
        OptionCard(name="ЖК Второй", location="Зеленоград", ready="сдан"),
        OptionCard(name="ЖК Третий", location="Зеленоград", ready="сдан"),
    )
    context = build_shortlist_comparison_context(cards, "life")
    rendered = render_grounded_card_block(1, cards[0], viewpoint="life", cards=cards, comparison_context=context)

    assert "ЖК «Первый» — бизнес-класс" in rendered
    assert shortlist_level_sparse_note(cards, context) == ""


def test_v0_answer_port_unsupported_description_key_is_not_called() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Первый", location="Москва"),), selected_option_name="ЖК Первый")
    calls = {"answer": 0}

    def scenario_search(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Первый"}}

    def answer(_brief):
        calls["answer"] += 1
        return {
            "answer_kind": "selected_object",
            "scope": "one_card",
            "intro": "По ЖК Первый есть факт.",
            "options": [{"name": "ЖК Первый", "description": "Лучший ЖК для жизни и инвестиций."}],
            "recommendation": "",
            "missing_note": "",
            "final_question": SELECTED_OBJECT_PRESENTATION_QUESTION,
        }

    result = V0TurnProcessor(scenario_search=scenario_search, answer=answer).process("Расскажи", state=initial)

    assert result.ok is True
    assert calls["answer"] == 0
    assert "Лучший ЖК для жизни и инвестиций" not in result.message
    assert [option["name"] for option in result.answer.options] == ["ЖК Первый"]


def test_v0_serialized_state_loads_safe_defaults() -> None:
    state = V0State.from_dict({
        "params": {"budget": "до 12 млн"},
        "visible_options": [{"name": "ЖК Первый", "location": "Москва"}],
        "selected_option_name": "ЖК Первый",
        "active_topic": "life",
    })
    assert state.has_greeted is False
    assert state.last_answer_kind is None
    assert state.previous_assistant_message is None
    assert state.answered_facts == ()
    assert state.pending_action is None


def test_v0_serialized_state_round_trips_previous_assistant_message_with_bound() -> None:
    previous = "Показываю клиенту ровно это." + (" хвост" * 500)
    state = V0State(previous_assistant_message=previous)

    serialized = state.to_dict()
    restored = V0State.from_dict(serialized)
    restored_from_legacy_value = V0State.from_dict({"previous_assistant_message": previous})

    assert serialized["previous_assistant_message"] == previous[:2000]
    assert restored.previous_assistant_message == previous[:2000]
    assert restored_from_legacy_value.previous_assistant_message == previous[:2000]


def _names_only_answer(brief):
    scope = brief["decision"]["expected_scope"]
    return {
        "answer_kind": brief["decision"]["expected_answer_kind"],
        "scope": scope,
        "intro": "runtime replaces this",
        "options": [{"name": card["name"]} for card in brief["allowed_cards"]] if scope in {"shortlist", "one_card"} else [],
        "recommendation": "",
        "missing_note": "",
        "final_question": brief["decision"]["cta_template"],
    }


def test_v0_selected_rental_typo_accept_routes_to_operator_without_replaying_list() -> None:
    calls = []

    def scenario(context):
        calls.append(context)
        if len(calls) == 1:
            return {
                "decision": {"action": "search", "viewpoint": "rental", "active_topic": "rental", "params": {"budget": "30 млн"}},
                "search": {"facts": [
                    {"name": "ЖК Первый", "location": "Москва", "min_price": 25_000_000},
                    {"name": "ЖК Второй", "location": "Москва", "min_price": 27_000_000},
                    {"name": "ЖК Третий", "location": "Москва", "min_price": 29_000_000},
                ], "near": [], "missing": [], "params": {"budget": "30 млн"}},
            }
        if len(calls) == 2:
            return {"decision": {"action": "selected_object", "viewpoint": "rental", "selected_option_name": "ЖК Третий", "active_topic": "rental"}, "search": {}}
        assert calls[-1]["state"]["pending_action"] == "check_selected_availability"
        return {"decision": {"action": "current_options", "viewpoint": "rental", "followup_outcome": "accept", "confirmed_action": "check_selected_availability", "confirmed_subject": "ЖК Третий"}, "search": {}}

    processor = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer)
    first = processor.process("под сдачу что то есть, у меня на руках 30 млн")
    second = processor.process("третий", state=first.state)
    third = processor.process("хчоу", state=second.state)

    assert first.ok and second.ok and third.ok
    assert second.answer.final_question == RENTAL_SELECTED_CTA
    assert second.state.pending_action == "check_selected_availability"
    assert second.state.pending_subject == "ЖК Третий"
    assert third.answer.answer_kind == "operator"
    assert third.answer.final_question == OPERATOR_PHONE_QUESTION
    assert "ЖК «Третий» для последующей сдачи" in third.message
    assert "Оператор сможет проверить наличие, площади, отделку и точную цену" in third.message
    assert "Нашла три варианта" not in third.message
    assert third.message.count("?") == 1
    assert third.state.pending_action == "contact_phone"


def test_v0_operator_accept_brief_has_no_card_options() -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Третий"),),
        selected_option_name="ЖК Третий",
        active_topic="rental",
        pending_action="check_selected_availability",
        pending_subject="ЖК Третий",
        pending_topic="rental",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "rental", "followup_outcome": "accept", "confirmed_action": "check_selected_availability", "confirmed_subject": "ЖК Третий"}, "search": {}}

    def answer(brief):
        assert brief["decision"]["expected_scope"] == "operator_phone"
        assert brief["allowed_cards"] == []
        return _names_only_answer(brief)

    result = V0TurnProcessor(scenario_search=scenario, answer=answer).process("хчоу", state=state)
    assert result.ok is True
    assert result.answer.options == ()
    assert result.state.pending_action == "contact_phone"


def test_v0_accept_semantic_outcome_for_da_and_hochu_uses_same_runtime_path() -> None:
    base = V0State(visible_options=(OptionCard(name="ЖК Третий"),), selected_option_name="ЖК Третий", active_topic="rental", pending_action="check_selected_availability", pending_subject="ЖК Третий", pending_topic="rental")

    def make_scenario(text):
        return lambda _context: {"decision": {"action": "current_options", "viewpoint": "rental", "followup_outcome": "accept", "confirmed_action": "check_selected_availability", "confirmed_subject": "ЖК Третий", "client_question": text}, "search": {}}

    for text in ("да", "хочу"):
        result = V0TurnProcessor(scenario_search=make_scenario(text), answer=_names_only_answer).process(text, state=base)
        assert result.ok is True
        assert result.answer.final_question == OPERATOR_PHONE_QUESTION
        assert "для последующей сдачи" in result.message


def test_v0_decline_clears_pending_without_phone_or_list() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Третий"),), selected_option_name="ЖК Третий", active_topic="rental", pending_action="check_selected_availability", pending_subject="ЖК Третий")
    scenario = lambda _context: {"decision": {"action": "current_options", "viewpoint": "rental", "followup_outcome": "decline"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("нет", state=state)

    assert result.ok is True
    assert result.state.pending_action is None
    assert OPERATOR_PHONE_QUESTION not in result.message
    assert "Нашла" not in result.message
    assert result.message.count("?") == 1


def test_v0_new_substantive_question_is_not_treated_as_consent() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Третий", location="Москва"),), selected_option_name="ЖК Третий", active_topic="rental", pending_action="check_selected_availability", pending_subject="ЖК Третий")
    scenario = lambda _context: {"decision": {"action": "selected_object", "viewpoint": "rental", "selected_option_name": "ЖК Третий", "followup_outcome": "new_question"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("а какая отделка?", state=state)

    assert result.ok is True
    assert result.answer.answer_kind == "selected_object"
    assert OPERATOR_PHONE_QUESTION not in result.message
    assert result.state.pending_action == "check_selected_availability"


def test_v0_accept_mismatched_action_or_subject_fails_closed_without_mutation() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Свой"),), selected_option_name="ЖК Свой", pending_action="check_selected_availability", pending_subject="ЖК Свой")

    bad_subject = lambda _context: {"decision": {"action": "current_options", "followup_outcome": "accept", "confirmed_action": "check_selected_availability", "confirmed_subject": "ЖК Чужой"}, "search": {}}
    result = V0TurnProcessor(scenario_search=bad_subject, answer=_names_only_answer).process("да", state=state)

    assert result.ok is False
    assert result.error_code == "invalid_pending_followup"
    assert result.state is state

    missing_subject = lambda _context: {"decision": {"action": "current_options", "followup_outcome": "accept", "confirmed_action": "check_selected_availability"}, "search": {}}
    missing = V0TurnProcessor(scenario_search=missing_subject, answer=_names_only_answer).process("да", state=state)
    assert missing.ok is False
    assert "pending_confirmed_subject_required" in missing.diagnostics["errors"]
    assert missing.state is state


def test_v0_selected_cta_by_rental_family_default() -> None:
    card = OptionCard(name="ЖК Свой")
    for viewpoint, expected in (("rental", RENTAL_SELECTED_CTA), ("family", FAMILY_SELECTED_CTA), ("life", SELECTED_OBJECT_PRESENTATION_QUESTION)):
        state = V0State(visible_options=(card,), selected_option_name="ЖК Свой", active_topic=viewpoint)
        scenario = lambda _context, viewpoint=viewpoint: {"decision": {"action": "selected_object", "viewpoint": viewpoint, "selected_option_name": "ЖК Свой"}, "search": {}}
        result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("первый", state=state)
        assert result.ok is True
        assert result.answer.final_question == expected
        assert result.state.pending_action == "check_selected_availability"


def test_v0_short_selection_keeps_sticky_rental_topic_when_model_returns_life() -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000),),
        selected_option_name="ЖК Третий",
        active_topic="rental",
    )
    scenario = lambda _context: {
        "decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Третий"},
        "search": {},
    }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("третий", state=state)

    assert result.ok is True
    assert result.state.active_topic == "rental"
    assert result.answer.final_question == RENTAL_SELECTED_CTA
    assert result.state.pending_topic == "rental"


def test_v0_selected_enrichment_exact_name_merges_and_mismatch_is_rejected() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000), OptionCard(name="ЖК Другой")), selected_option_name="ЖК Третий", active_topic="rental")

    exact = lambda _context: {"decision": {"action": "selected_object", "viewpoint": "rental", "selected_option_name": "ЖК Третий"}, "search": {"facts": [{"name": "ЖК Третий", "location": "Москва", "finishing": "с отделкой", "square_min": 42, "min_price": 28_500_000}], "near": [], "missing": [], "params": {}}}
    result = V0TurnProcessor(scenario_search=exact, answer=_names_only_answer).process("третий", state=state)
    assert result.ok is True
    assert result.state.visible_options[0].location == "Москва"
    assert result.state.visible_options[0].finishing == "с отделкой"
    assert result.state.visible_options[1].name == "ЖК Другой"
    assert len(result.answer.options) == 1

    mismatch = lambda _context: {"decision": {"action": "selected_object", "viewpoint": "rental", "selected_option_name": "ЖК Третий"}, "search": {"facts": [{"name": "ЖК Третий Парк", "location": "Москва"}], "near": [], "missing": [], "params": {}}}
    rejected = V0TurnProcessor(scenario_search=mismatch, answer=_names_only_answer).process("третий", state=state)
    assert rejected.ok is True
    assert rejected.state.visible_options[0].location is None
    assert rejected.diagnostics["search_validation"].get("selected_enrichment_rejected") == "name_mismatch"


def test_v0_invalid_selected_enrichment_preserves_existing_card(monkeypatch) -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000),),
        selected_option_name="ЖК Третий",
        active_topic="life",
    )
    scenario = lambda _context: {
        "decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Третий"},
        "search": {"facts": [{"name": "ЖК Третий", "location": "Москва"}], "near": [], "missing": [], "params": {}},
    }
    monkeypatch.setattr("nmbot_v0.runtime.validate_search_output", lambda *_args: {"ok": False, "status": "invalid", "errors": ["fact_0_violates_hard:name"]})

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("третий", state=state)

    assert result.ok is True
    assert result.state.visible_options == state.visible_options
    assert result.state.visible_options[0].location is None
    assert result.diagnostics["search_validation"]["safe_code"] == "search_validation_error"
    assert result.diagnostics["search_validation"]["selected_enrichment_rejected"] == "search_validation_error"
    assert "Москва" not in result.message


def test_v0_invalid_selected_bootstrap_preserves_empty_prior_state(monkeypatch) -> None:
    scenario = lambda _context: {
        "decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Новый"},
        "search": {"facts": [{"name": "ЖК Новый", "location": "Москва"}], "near": [], "missing": [], "params": {}},
    }
    monkeypatch.setattr("nmbot_v0.runtime.validate_search_output", lambda *_args: {"ok": False, "status": "invalid", "errors": ["fact_0_violates_hard:name"]})

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("расскажи про новый")

    assert result.ok is True
    assert result.state.visible_options == ()
    assert result.state.selected_option_name is None
    assert result.diagnostics["search_validation"]["safe_code"] == "search_validation_error"
    assert result.diagnostics["search_validation"]["selected_bootstrap_rejected"] == "search_validation_error"
    assert "Москва" not in result.message


def test_v0_selected_exact_enrichment_merges_and_renders_two_confirmed_lots() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000),), selected_option_name="ЖК Третий", active_topic="life")

    def scenario(_context):
        return {
            "decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Третий"},
            "search": {
                "facts": [
                    {
                        "name": "ЖК Третий",
                        "location": "Москва",
                        "min_price": 28_500_000,
                        "ads": [
                            {"id": 1, "rooms": 2, "area": 54.5, "floor": 7, "floors_total": 24, "fullprice": 12_300_000, "renovation": "с отделкой", "house_id": "h1"},
                            {"id": 2, "rooms": 3, "area": 68, "floor": 12, "floors_total": 24, "fullprice": 14_000_000, "renovation": "white_box", "house_id": "h2"},
                            {"id": 3, "rooms": 1, "area": 38, "floor": 3, "floors_total": 24, "fullprice": 9_000_000, "renovation": "без отделки", "house_id": "h3"},
                        ],
                        "house": [{"id": "h1", "name": "корпус 1"}, {"id": "h2", "name": "дом 2"}, {"id": "h3", "name": "корпус 3"}],
                        "apartment_types": ["2", "3"],
                    }
                ],
                "near": [],
                "missing": [],
                "params": {},
            },
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("третий", state=state)

    assert result.ok is True
    card = result.state.visible_options[0]
    assert len(card.lot_examples) == 2
    assert card.lot_examples[0].house_name == "корпус 1"
    assert "1. ЖК «Третий» — Москва, цены от 28 500 000 ₽" in result.message
    assert "По объявлениям вижу такие подтверждённые квартиры:" in result.message
    assert "Квартира 1: двухкомнатная квартира, 54,5 м², 7 этаж из 24, полная цена 12 300 000 ₽, отделка — с отделкой, корпус/дом — корпус 1." in result.message
    assert "Квартира 2: трёхкомнатная квартира, 68 м², 12 этаж из 24, полная цена 14 000 000 ₽, отделка — предчистовая отделка, корпус/дом — дом 2." in result.message
    assert "Квартира 3" not in result.message
    assert "первая дешевле на 1 700 000 ₽" in result.message
    assert "вторая больше на 13,5 м²" in result.message
    assert result.message.count(SELECTED_OBJECT_PRESENTATION_QUESTION) == 1


def test_v0_selected_without_ads_keeps_project_only_and_honest_boundary() -> None:
    state = V0State(visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000, location="Москва"),), selected_option_name="ЖК Третий", active_topic="life")
    scenario = lambda _context: {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Третий"}, "search": {"facts": [{"name": "ЖК Третий", "location": "Москва", "min_price": 28_500_000}], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("третий", state=state)

    assert result.ok is True
    assert "1. ЖК «Третий» — Москва, цены от 28 500 000 ₽" in result.message
    assert "По объявлениям вижу" not in result.message
    assert "Подтверждённых квартир из объявлений по этому ЖК сейчас не вижу" in result.message
    assert "конкретные квартиры доступны" in result.message


def test_v0_selected_name_mismatch_does_not_render_first_visible_card() -> None:
    state = V0State(
        visible_options=(
            OptionCard(name="ЖК Первый", location="Москва"),
            OptionCard(name="ЖК Второй", location="Москва"),
        ),
        selected_option_name="ЖК Первый",
        active_topic="life",
    )
    scenario = lambda _context: {
        "decision": {
            "action": "selected_object",
            "viewpoint": "life",
            "selected_option_name": "ЖК Неизвестный",
        }
    }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Расскажи про неизвестный", state=state)

    assert result.ok is True
    assert "ЖК «Первый»" not in result.message
    assert "ЖК «Второй»" not in result.message
    assert result.state.selected_option_name == "ЖК Первый"
    assert result.state.pending_subject != "ЖК Неизвестный"


def test_v0_rejected_selected_bootstrap_does_not_persist_unverified_name() -> None:
    scenario = lambda _context: {
        "decision": {
            "action": "selected_object",
            "viewpoint": "life",
            "selected_option_name": "ЖК Неизвестный",
        },
        "search": {
            "facts": [{"name": "ЖК Другой", "location": "Москва"}],
            "near": [],
            "missing": [],
            "params": {},
        },
    }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Расскажи про неизвестный")

    assert result.ok is True
    assert result.state.selected_option_name is None
    assert result.state.pending_subject is None
    assert result.diagnostics["search_validation"]["selected_bootstrap_rejected"] == "name_mismatch"


def test_v0_unrenderable_lot_example_uses_honest_no_lot_boundary() -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Третий", location="Москва"),),
        selected_option_name="ЖК Третий",
        active_topic="life",
    )
    scenario = lambda _context: {
        "decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Третий"},
        "search": {
            "facts": [{"name": "ЖК Третий", "location": "Москва", "ads": [{"id": 1, "status": "active"}]}],
            "near": [],
            "missing": [],
            "params": {},
        },
    }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("третий", state=state)

    assert result.ok is True
    assert "По объявлениям вижу" not in result.message
    assert "Подтверждённых квартир из объявлений по этому ЖК сейчас не вижу" in result.message


def test_v0_selected_enrichment_sync_async_answer_port_not_called_and_results_match() -> None:
    initial = V0State(visible_options=(OptionCard(name="ЖК Третий", price_min=29_000_000),), selected_option_name="ЖК Третий", active_topic="rental")
    calls = {"sync_answer": 0, "async_answer": 0}

    def scenario(_context):
        return {
            "decision": {"action": "selected_object", "viewpoint": "rental", "selected_option_name": "ЖК Третий"},
            "search": {"facts": [{"name": "ЖК Третий", "location": "Москва", "finishing": "с отделкой"}], "near": [], "missing": [], "params": {}},
        }

    def malformed_answer(_brief):
        calls["sync_answer"] += 1
        return "not-json"

    async def async_scenario(context):
        return scenario(context)

    async def invalid_answer(_brief):
        calls["async_answer"] += 1
        return {"options": [{"name": "ЖК Третий", "description": "лишнее поле"}]}

    async def run_case():
        return await V0TurnProcessor(scenario_search=async_scenario, answer=invalid_answer).process_async("третий", state=initial)

    sync_result = V0TurnProcessor(scenario_search=scenario, answer=malformed_answer).process("третий", state=initial)
    async_result = asyncio.run(run_case())

    assert sync_result.ok is True
    assert async_result.ok is True
    assert calls == {"sync_answer": 0, "async_answer": 0}
    assert sync_result.message == async_result.message
    assert sync_result.answer == async_result.answer
    assert sync_result.state.visible_options[0].location == "Москва"
    assert async_result.state.visible_options[0].finishing == "с отделкой"


def test_v0_ready_delivered_future_card_fails_closed() -> None:
    def scenario(_context):
        return {
            "decision": {"action": "search", "viewpoint": "life", "active_topic": "life", "params": {"delivered": True}},
            "search": {
                "facts": [{"name": "ЖК Будущий", "location": "Москва", "min_price": 9_000_000, "ready": "2028"}],
                "near": [{"name": "ЖК Альтернатива", "location": "Москва", "min_price": 9_500_000, "ready": "2027", "is_near": True, "why_close": "срок другой"}],
                "missing": [],
                "params": {"delivered": True},
            },
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Нужен сданный дом")

    assert result.ok is False
    assert result.error_code == "invalid_search_output"
    assert result.state == V0State()
    assert "ЖК «Альтернатива»" not in result.message
    assert "ЖК «Будущий»" not in result.message
    validation = result.diagnostics["search_validation"]
    assert validation["status"] == "invalid"
    assert "fact_0_violates_hard:ready" in validation["errors"]


def test_v0_financing_typed_params_override_life_and_create_check_all_pending() -> None:
    def scenario(_context):
        return {
            "decision": {
                "action": "search",
                "viewpoint": "life",
                "active_topic": "life",
                "params": {"max_price": 12_000_000, "financing": True, "down_payment": 0},
                "requested_facts": ["mortgage_terms"],
            },
            "search": {"facts": [{"name": "ЖК Первый", "location": "Москва", "min_price": 10_000_000}], "near": [], "missing": [], "params": {"max_price": 12_000_000}},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Нужна ипотека без первоначального взноса")

    assert result.ok is True
    assert result.state.active_topic == "financing"
    assert result.state.pending_action == "check_current_options_financing"
    assert result.state.pending_subject == "all_current_options"
    assert result.answer.final_question == "Проверить условия оплаты по всем этим вариантам?"
    assert "без первоначального взноса" in result.message
    assert "не подтверждаю" in result.message


def test_v0_financing_short_accept_routes_to_operator_for_all_current_options() -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Первый", price_min=10_000_000), OptionCard(name="ЖК Второй", price_min=11_000_000)),
        active_topic="financing",
        pending_action="check_current_options_financing",
        pending_subject="all_current_options",
        pending_topic="financing",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "financing", "followup_outcome": "accept", "confirmed_action": "check_current_options_financing", "confirmed_subject": "all_current_options"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("проверьте по всем вариантам", state=state)

    assert result.ok is True
    assert result.answer.answer_kind == "operator"
    assert result.answer.final_question == OPERATOR_PHONE_QUESTION
    assert "по всем текущим вариантам" in result.message
    assert "без первоначального взноса" in result.message
    assert "Нашла" not in result.message
    assert result.state.pending_action == "contact_phone"


def test_v0_financing_decline_clears_pending_without_selected_availability_text() -> None:
    state = V0State(
        visible_options=(OptionCard(name="ЖК Первый", price_min=10_000_000), OptionCard(name="ЖК Второй", price_min=11_000_000)),
        active_topic="financing",
        pending_action="check_current_options_financing",
        pending_subject="all_current_options",
        pending_topic="financing",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "financing", "followup_outcome": "decline"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("не надо", state=state)

    assert result.ok is True
    assert result.answer.answer_kind == "open_question"
    assert result.state.pending_action is None
    assert result.state.pending_subject is None
    assert result.state.pending_topic is None
    assert "условий оплаты по всем текущим вариантам" in result.message
    assert "не будем сейчас проверять наличие по выбранному ЖК" not in result.message
    assert OPERATOR_PHONE_QUESTION not in result.message
    assert result.message.count("?") == 1


def test_v0_current_options_school_metro_followup_keeps_priced_cards_and_only_grounded_answered_facts() -> None:
    initial = V0State(
        visible_options=(
            OptionCard(name="ЖК Первый", price_min=10_000_000),
            OptionCard(name="ЖК Второй", price_min=11_000_000),
        ),
        active_topic="family",
        answered_facts=("price_min",),
    )
    answer_calls = 0

    def scenario(_context):
        return {
            "decision": {
                "action": "current_options",
                "viewpoint": "family",
                "active_topic": "family",
                "requested_facts": ["schools", "metro"],
            },
            "search": {"facts": [{"name": "ЖК Sparse", "location": "не должно попасть в state"}], "near": [], "missing": ["schools", "metro"], "params": {}},
        }

    def answer(brief):
        nonlocal answer_calls
        answer_calls += 1
        return _names_only_answer(brief)

    result = V0TurnProcessor(scenario_search=scenario, answer=answer).process("А что со школой и метро? Вы про это ничего не сказали", state=initial)

    assert result.ok is True
    assert result.state.visible_options == initial.visible_options
    assert result.state.active_topic == "family"
    assert result.state.answered_facts == ("price_min",)
    assert "schools" not in result.state.answered_facts
    assert "metro" not in result.state.answered_facts
    assert result.message.startswith("В текущих карточках нет подтверждённой информации по школам и метро.")
    assert "цены от 10 000 000 ₽" in result.message
    assert "цены от 11 000 000 ₽" in result.message
    assert "семейные детали" in result.message
    assert answer_calls == 0
    assert result.diagnostics["decision"]["requested_facts"] == ["schools", "metro"]


def test_v0_requested_school_present_but_metro_missing_only_mentions_metro_boundary() -> None:
    initial = V0State(
        visible_options=(
            OptionCard(name="ЖК Школьный", price_min=10_000_000, infrastructure=("школа",)),
            OptionCard(name="ЖК Семейный", price_min=11_000_000, infrastructure=("детский сад",)),
        ),
        active_topic="family",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "family", "active_topic": "family", "requested_facts": ["schools", "property_metro"]}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("А что со школой и метро?", state=initial)

    assert result.ok is True
    assert result.message.startswith("В текущих карточках нет подтверждённой информации по метро.")
    assert "по школ" not in result.message.split("\n", 1)[0]
    assert "цены от 10 000 000 ₽" in result.message
    assert result.state.visible_options == initial.visible_options


def test_v0_current_options_without_requested_missing_fact_keeps_normal_intro() -> None:
    initial = V0State(
        visible_options=(OptionCard(name="ЖК Первый", price_min=10_000_000), OptionCard(name="ЖК Второй", price_min=11_000_000)),
        active_topic="family",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "family", "active_topic": "family", "requested_facts": []}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Покажите варианты ещё раз", state=initial)

    assert result.ok is True
    assert result.message.startswith("По вашему запросу подходят два варианта.")
    assert "Нашла два подходящих варианта" not in result.message
    assert "нет подтверждённой информации" not in result.message
    assert result.state.visible_options == initial.visible_options


def test_v0_initial_family_school_metro_request_is_search_but_followup_uses_current_options() -> None:
    calls = []

    def scenario(context):
        calls.append(context)
        if not context["state"]["visible_options"]:
            return {
                "decision": {
                    "action": "search",
                    "viewpoint": "family",
                    "active_topic": "family",
                    "params": {"rooms": 2, "max_price": 18_000_000},
                    "requested_facts": ["schools", "metro"],
                },
                "search": {
                    "facts": [
                        {"name": "ЖК Семейный", "location": "Москва", "min_price": 17_500_000, "rooms": 2, "property_metro": "Озёрная", "school": True},
                        {"name": "ЖК Второй", "location": "Москва", "min_price": 18_000_000, "rooms": 2},
                    ],
                    "near": [],
                    "missing": [],
                    "params": {"rooms": 2, "max_price": 18_000_000},
                },
            }
        return {
            "decision": {
                "action": "current_options",
                "viewpoint": "family",
                "active_topic": "family",
                "requested_facts": ["schools", "metro"],
            },
            "search": {"facts": [{"name": "ЖК Не должен заменить текущие"}], "near": [], "missing": ["schools", "metro"], "params": {}},
        }

    processor = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer)
    first = processor.process("Ищу двушку для себя и ребёнка, до 18 миллионов. Желательно не слишком далеко от метро и чтобы рядом была школа.")
    second = processor.process("А что со школой и метро? Вы про это ничего не сказали", state=first.state)

    assert first.ok is True
    assert first.diagnostics["decision"]["action"] == "search"
    assert len(calls[0]["state"]["visible_options"]) == 0
    assert first.state.active_topic == "family"
    assert [card.name for card in first.state.visible_options] == ["ЖК Семейный", "ЖК Второй"]
    assert [card.price_min for card in first.state.visible_options] == [17_500_000, 18_000_000]
    assert second.ok is True
    assert second.diagnostics["decision"]["action"] == "current_options"
    assert len(calls[1]["state"]["visible_options"]) == 2
    assert second.state.visible_options == first.state.visible_options
    assert [card.price_min for card in second.state.visible_options] == [17_500_000, 18_000_000]


def test_v0_contact_phone_decline_overrides_operator_and_preserves_selected_context() -> None:
    initial = V0State(
        visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
        selected_option_name="Мичуринский парк",
        active_topic="family",
        pending_action="contact_phone",
        pending_subject="Мичуринский парк",
        pending_topic="family",
    )

    def scenario(_context):
        return {
            "decision": {
                "action": "operator",
                "viewpoint": "family",
                "active_topic": "family",
                "selected_option_name": "Мичуринский парк",
                "response_policy": "operator_phone_request",
                "followup_outcome": "decline",
            },
            "search": {},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("номер не оставлю", state=initial)

    assert result.ok is True
    assert result.diagnostics["decision"]["action"] == "open_question"
    assert result.diagnostics["decision"]["response_policy"] == "answer_directly"
    assert result.answer.scope != "operator_phone"
    assert not re.search(r"телефон|номер|оператор|менеджер", result.message, re.IGNORECASE)
    assert result.state.selected_option_name == "Мичуринский парк"
    assert result.state.active_topic == "family"
    assert result.state.visible_options == initial.visible_options
    assert result.state.pending_action is None
    assert result.state.pending_subject is None
    assert result.state.pending_topic is None


def test_v0_contact_phone_semantic_accept_asks_for_digits_and_preserves_pending_context() -> None:
    initial = V0State(
        visible_options=(OptionCard(name="Мичуринский парк", price_min=18_000_000),),
        selected_option_name="Мичуринский парк",
        active_topic="family",
        pending_action="contact_phone",
        pending_subject="Мичуринский парк",
        pending_topic="family",
    )

    def scenario(_context):
        return {
            "decision": {
                "action": "current_options",
                "viewpoint": "family",
                "active_topic": "family",
                "selected_option_name": "Мичуринский парк",
                "followup_outcome": "accept",
            },
            "search": {},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("да, всё верно", state=initial)

    assert result.ok is True
    assert result.diagnostics["decision"]["action"] == "operator"
    assert result.diagnostics["decision"]["response_policy"] == "operator_phone_request"
    assert result.diagnostics["decision"]["_pending_resolution"] == "accept_contact_phone"
    assert result.message == V0_CONTACT_PHONE_DIGITS_REQUEST
    assert result.answer.final_question == V0_CONTACT_PHONE_DIGITS_REQUEST
    assert OPERATOR_PHONE_QUESTION not in result.message
    assert result.state.pending_action == "contact_phone"
    assert result.state.pending_subject == "Мичуринский парк"
    assert result.state.pending_topic == "family"
    assert result.state.selected_option_name == "Мичуринский парк"
    assert result.state.active_topic == "family"
    assert result.state.visible_options == initial.visible_options


def test_v0_named_first_turn_exact_lookup_bootstraps_selected_card() -> None:
    def scenario(_context):
        return {
            "decision": {"action": "selected_object", "viewpoint": "life", "active_topic": "life", "selected_option_name": "ЖК Точный", "requested_facts": ["apartment_price", "finishing", "readiness"]},
            "search": {"facts": [{"name": "ЖК Точный", "location": "Москва", "min_price": 8_000_000, "finishing": "с отделкой", "delivered": True}], "near": [], "missing": [], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Расскажите про ЖК Точный")

    assert result.ok is True
    assert result.state.selected_option_name == "ЖК Точный"
    assert [card.name for card in result.state.visible_options] == ["ЖК Точный"]
    assert "цены от 8 000 000 ₽" in result.message
    assert "с отделкой" in result.message
    assert "дом сдан" in result.message
    assert "Готовность дома по этому ЖК пока не подтверждена" not in result.message


def test_v0_named_first_turn_missing_requested_readiness_is_stated() -> None:
    def scenario(_context):
        return {
            "decision": {"action": "selected_object", "viewpoint": "life", "active_topic": "life", "selected_option_name": "ЖК Без срока", "requested_facts": ["apartment_price", "finishing", "readiness"]},
            "search": {"facts": [{"name": "ЖК Без срока", "location": "Москва", "min_price": 8_000_000, "finishing": "с отделкой"}], "near": [], "missing": ["ready"], "params": {}},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Расскажите про ЖК Без срока, цена, отделка и готовность")

    assert result.ok is True
    assert "цены от 8 000 000 ₽" in result.message
    assert "с отделкой" in result.message
    assert "Готовность дома по этому ЖК пока не подтверждена." in result.message
    assert result.message.count("?") == 1


def test_v0_selected_project_followup_current_two_room_availability_routes_operator_without_replay() -> None:
    calls = []

    def scenario(context):
        calls.append(context)
        if len(calls) == 1:
            return {
                "decision": {"action": "selected_object", "viewpoint": "life", "active_topic": "life", "selected_option_name": "ЖК Точный", "requested_facts": ["apartment_price", "finishing"]},
                "search": {"facts": [{"name": "ЖК Точный", "location": "Москва", "min_price": 8_000_000, "finishing": "с отделкой"}], "near": [], "missing": [], "params": {}},
            }
        assert calls[-1]["state"]["pending_action"] == "check_selected_availability"
        assert calls[-1]["state"]["selected_option_name"] == "ЖК Точный"
        return {
            "decision": {
                "action": "operator",
                "viewpoint": "life",
                "active_topic": "life",
                "selected_option_name": "ЖК Точный",
                "params": {"rooms": 2},
                "requested_facts": ["apartment_inventory", "rooms"],
                "response_policy": "operator_phone_request",
                "followup_outcome": "new_question",
            },
            "search": {"facts": [], "near": [], "missing": ["apartment_inventory"], "params": {}},
        }

    processor = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer)
    first = processor.process("Расскажите про ЖК Точный")
    second = processor.process("А двухкомнатные сейчас есть?", state=first.state)

    assert first.ok is True
    assert first.state.selected_option_name == "ЖК Точный"
    assert first.state.pending_action == "check_selected_availability"
    assert second.ok is True
    assert second.answer.answer_kind == "operator"
    assert second.answer.final_question == OPERATOR_PHONE_QUESTION
    assert second.state.selected_option_name == "ЖК Точный"
    assert second.state.params["rooms"] == 2
    assert second.state.pending_action == "contact_phone"
    assert "ЖК «Точный»" not in second.message
    assert "цены от 8 000 000 ₽" not in second.message
    assert OPERATOR_PHONE_QUESTION in second.message


def test_v0_named_first_turn_without_exact_card_never_claims_empty_details() -> None:
    def scenario(_context):
        return {"decision": {"action": "selected_object", "viewpoint": "life", "selected_option_name": "ЖК Неточный"}, "search": {"facts": [], "near": [], "missing": [], "params": {}}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Расскажите про ЖК Неточный")

    assert result.ok is True
    assert "вижу такие детали" not in result.message
    assert "не вижу подтверждённой карточки" in result.message
    assert result.state.selected_option_name == "ЖК Неточный"


def test_v0_current_options_price_min_comparison_returns_one_card_and_preserves_state() -> None:
    state = V0State(
        visible_options=(
            OptionCard(name="ЖК Дорогой", location="Москва", price_min=15_000_000),
            OptionCard(name="ЖК Дешёвый", location="Москва", price_min=9_000_000),
            OptionCard(name="ЖК Средний", location="Москва", price_min=12_000_000),
        ),
        active_topic="life",
    )

    def scenario(_context):
        return {"decision": {"action": "current_options", "viewpoint": "life", "comparison_metric": "price_min"}, "search": {}}

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Какой из них самый дешёвый?", state=state)

    assert result.ok is True
    assert len(result.answer.options) == 1
    assert result.answer.options[0]["name"] == "ЖК Дешёвый"
    assert "ЖК «Дешёвый»" in result.message
    assert "ЖК «Дорогой»" not in result.message
    assert "ЖК «Средний»" not in result.message
    assert result.state.visible_options == state.visible_options


def test_v0_sparse_family_shortlist_does_not_claim_broad_family_suitability() -> None:
    def scenario(_context):
        return {
            "decision": {"action": "search", "viewpoint": "family", "active_topic": "family", "params": {"max_price": 12_000_000, "rooms": 2}, "requested_facts": ["schools", "parks"]},
            "search": {"facts": [{"name": "ЖК База", "location": "Москва", "rooms": 2, "min_price": 11_000_000}], "near": [], "missing": ["school", "park_near"], "params": {"max_price": 12_000_000, "rooms": 2}},
        }

    result = V0TurnProcessor(scenario_search=scenario, answer=_names_only_answer).process("Для семьи нужна двушка рядом со школой и парком")

    assert result.ok is True
    assert "Точного совпадения" in result.message or "семейные детали" in result.message or "нет подтверждённой информации" in result.message
    assert "подходящ" not in result.message.casefold().split("\n", 1)[0]
    assert "Рядом есть школа" not in result.message
    assert "Парк добавляет" not in result.message
