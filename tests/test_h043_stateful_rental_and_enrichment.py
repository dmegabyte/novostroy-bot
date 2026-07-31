from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

API_SPEC = importlib.util.spec_from_file_location("nmbot_api_server_h043", SCRIPT_DIR / "nmbot_api_server.py")
assert API_SPEC and API_SPEC.loader
api = importlib.util.module_from_spec(API_SPEC)
sys.modules[API_SPEC.name] = api
API_SPEC.loader.exec_module(api)

BOT_SPEC = importlib.util.spec_from_file_location("chat_tester_bot_h043", SCRIPT_DIR / "chat_tester_bot.py")
assert BOT_SPEC and BOT_SPEC.loader
bot = importlib.util.module_from_spec(BOT_SPEC)
sys.modules[BOT_SPEC.name] = bot
BOT_SPEC.loader.exec_module(bot)

PLANNER_SPEC = importlib.util.spec_from_file_location("followup_intent_classifier_h043", ROOT / "followup_intent_classifier.py")
assert PLANNER_SPEC and PLANNER_SPEC.loader
planner = importlib.util.module_from_spec(PLANNER_SPEC)
sys.modules[PLANNER_SPEC.name] = planner
PLANNER_SPEC.loader.exec_module(planner)


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, api._default_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)


class FakeOutbox:
    pass


class FakeCurrentOptionsClient:
    def __init__(self) -> None:
        self.ask_calls: list[str] = []
        self.explain_calls: list[dict[str, Any]] = []
        self.enrich_calls: list[str] = []

    async def ensure_session(self) -> object:
        return object()

    async def ask(self, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
        self.ask_calls.append(str(args[0]) if args else str(kwargs.get("query") or ""))
        raise AssertionError("new search must not be called")

    async def explain_consultation_followup(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.explain_calls.append(kwargs)
        state = kwargs["state"]
        selected = state.get("selected_option") or {}
        if state.get("scope") == "one" and selected.get("name"):
            return f"Расскажу подробнее про {selected['name']} с точки зрения сдачи в аренду.", {"seen_state": state}
        names = [item["name"] for item in state["visible_options"]]
        return "Сравню все текущие ЖК под сдачу: " + ", ".join(names), {"seen_state": state}

    async def enrich_option_search(self, query: str, **_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        self.enrich_calls.append(query)
        return {
            "facts": [{
                "name": "Мичуринский парк",
                "developer": "Донстрой",
                "new_building_class": "бизнес-класс",
                "rooms": "1, 2, 3",
                "sales_stats": "381 объявление",
            }]
        }, {"payload_stage": "option_enrichment_search", "test": True}


class FakeMortgageCurrentOptionsClient(FakeCurrentOptionsClient):
    async def explain_consultation_followup(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.explain_calls.append(kwargs)
        return "По ипотеке точные условия лучше проверить по выбранному ЖК. Какой ЖК смотрим первым?", {"seen_state": kwargs["state"]}


def make_app(client: Any) -> web.Application:
    app = web.Application()
    app["state_store"] = FakeStore()
    app["crm_callback_outbox"] = FakeOutbox()
    app["overmind_client"] = client
    return app


def canonical_plan(**overrides: Any) -> dict[str, Any]:
    plan = {
        "dialog_action": "recommend_options",
        "mode": "conversation",
        "confidence": 0.92,
        "params_delta": {},
        "selected_option_action": "keep",
        "selected_option_name": None,
        "rejected_options_add": [],
        "visible_options_policy": "keep",
        "numeric_choice_policy": "reject",
        "mcp_request_patch": None,
        "clarification": "",
        "clarification_question": "",
        "reason": "semantic current options",
        "fallback_used": False,
        "action": "answer_current_options",
        "intent": "rental",
        "intent_policy": "change",
        "target": "current_options",
        "search_policy": "forbidden",
        "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
        "facets": {},
        "operator_contact": {"requested": False, "consent": "none"},
        "search_profile": "none",
        "missing_fields": [],
        "clarification_fields": [],
        "scope": "all",
        "canonical_valid": True,
        "canonical_errors": [],
    }
    plan.update(overrides)
    return plan


def seed_options_state() -> dict[str, Any]:
    state = api._default_state()
    state["primary_intent"] = "investment"
    state["params"] = {"purpose": "investment", "primary_intent": "investment"}
    state["visible_options"] = [
        {"name": "Бусиновский парк", "ready": "строится", "metro": "Ховрино"},
        {"name": "Мичуринский парк", "ready": "сдан", "metro": "Мичуринский проспект"},
        {"name": "Жилой район «Скандинавия»", "ready": "строится", "metro": "Коммунарка"},
    ]
    state["last_options"] = list(state["visible_options"])
    state["last_bot_question"] = "Рассказать подробнее или сравнить варианты?"
    state["pending_followup"] = {"type": "visible_options", "count": 3}
    return state


def seed_sparse_rental_options_state() -> dict[str, Any]:
    state = api._default_state()
    state["primary_intent"] = "rental"
    state["params"] = {"purpose": "rental", "primary_intent": "rental"}
    state["visible_options"] = [
        {
            "name": "Бусиновский парк",
            "location": "Москва, район Западное Дегунино",
            "price_min": 12_400_000,
            "ready": "строится",
            "finishing": "с отделкой",
            "metro": "Ховрино",
            "apartment_types": "студии и 1-комнатные",
        },
        {
            "name": "Мичуринский парк",
            "location": "Москва, Очаково-Матвеевское",
            "price_min": 14_200_000,
            "ready": "сдан",
            "finishing": "предчистовая",
            "metro": "Мичуринский проспект",
            "apartment_types": "1-комнатные",
        },
        {
            "name": "Жилой район «Скандинавия»",
            "location": "Новая Москва",
            "price_min": 10_900_000,
            "ready": "строится",
            "finishing": "без отделки",
            "metro": "Коммунарка",
            "apartment_types": "студии",
        },
    ]
    state["last_options"] = list(state["visible_options"])
    state["last_bot_question"] = "Рассказать подробнее или сравнить варианты?"
    state["pending_followup"] = {"type": "visible_options", "count": 3}
    return state


def assert_safe_deterministic_rental_answer(text: str) -> None:
    lowered = text.lower().replace("ё", "е")
    for forbidden in (
        r"спрос",
        r"привлекател",
        r"арендатор",
        r"быстро\s+найти",
        r"влия.*аренд",
        r"12400000",
        r"mcp|json|база|подтвержденные данные|visible_options|selected_option|current_options",
    ):
        assert not re.search(forbidden, lowered), forbidden
    assert text.count("?") == 1


def test_scope_all_rental_current_options_uses_deterministic_renderer_not_llm(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        app["state_store"].states["u"] = seed_sparse_rental_options_state()

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(intent="rental", intent_policy="keep", scope="all")

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="а если я хочу сдать в аренду", channel="api")

        answer = result["answer"]
        assert result["intent"] == "answer_current_options"
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert result["meta"]["runtime"] == "v2"
        for name in ["Бусиновский парк", "Мичуринский парк", "Жилой район Скандинавия"]:
            assert name in answer
        assert "12,4 млн" in answer
        assert "14,2 млн" in answer
        assert "10,9 млн" in answer
        assert_safe_deterministic_rental_answer(answer)

    asyncio.run(scenario())


def test_scope_one_fuzzy_selected_option_uses_only_selected_card_not_llm(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        app["state_store"].states["u"] = seed_sparse_rental_options_state()

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                operation="select_option",
                dialog_action="select_option",
                action="answer_current_options",
                target="current_options",
                search_policy="forbidden",
                intent="rental",
                intent_policy="keep",
                scope="one",
                selected_option_name="Мичуринский парк",
                clarification_question="",
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="Мичурискний подробнее", channel="api")

        answer = result["answer"]
        assert result["selected_option"] == "Мичуринский парк"
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert "Мичуринский парк" in answer
        assert "Бусиновский парк" not in answer
        assert "Жилой район «Скандинавия»" not in answer
        assert_safe_deterministic_rental_answer(answer)

    asyncio.run(scenario())


def test_mortgage_current_options_still_uses_consultation_llm_path(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeMortgageCurrentOptionsClient()
        app = make_app(client)
        state = seed_sparse_rental_options_state()
        state["primary_intent"] = "mortgage"
        state["params"] = {"purpose": "mortgage", "primary_intent": "mortgage"}
        app["state_store"].states["u"] = state

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(intent="mortgage", intent_policy="keep", scope="all")

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="а по ипотеке все подходят?", channel="api")

        assert result["intent"] == "answer_current_options"
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert "ипотек" in result["answer"].lower()

    asyncio.run(scenario())


def test_investment_to_rental_scope_all_preserves_visible_options_and_avoids_search(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        app["state_store"].states["u"] = seed_options_state()

        async def fake_plan(_session: Any, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["state"]["primary_intent"] == "investment"
            return canonical_plan()

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="а есл ия хочу сдать в арнеду", channel="api")

        saved = app["state_store"].states["u"]
        assert result["intent"] == "answer_current_options"
        assert result["turn_decision"] == {"stage": "current_options", "action": "answer_from_current_options"}
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["active_topic"] == "rental"
        assert [item["name"] for item in saved["nmbot_v2"]["visible_options"]] == ["Бусиновский парк", "Мичуринский парк", "Жилой район «Скандинавия»"]
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert result["meta"]["runtime"] == "v2"

    asyncio.run(scenario())


def test_h046_typo_rental_switch_repairs_before_persisting_intent(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        app["state_store"].states["u"] = seed_options_state()
        repair_calls: list[dict[str, Any]] = []

        async def bad_first_plan(_session: Any, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["state"]["primary_intent"] == "investment"
            return canonical_plan(
                action="answer_current_options",
                dialog_action="recommend_options",
                target="none",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="unknown",
                search_profile="none",
                canonical_valid=False,
                canonical_errors=["current_options_requires_current_forbidden", "current_options_scope_must_be_one_or_all"],
            )

        async def repair_plan(_session: Any, **kwargs: Any) -> dict[str, Any]:
            repair_calls.append(kwargs)
            assert kwargs["state"]["primary_intent"] == "investment"
            assert set(kwargs["allowed_error_codes"]) == {"current_options_requires_current_forbidden", "current_options_scope_must_be_one_or_all", "invalid_action_target_search_policy"}
            serialized = json.dumps(kwargs["original_plan"], ensure_ascii=False)
            assert "а есл ия хочу" not in serialized
            return canonical_plan(
                action="answer_current_options",
                dialog_action="recommend_options",
                target="current_options",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="all",
                selected_option_name=None,
                search_profile="none",
                constraints_patch={"hard": {}, "preferences": {}, "unknown": {}},
                canonical_valid=True,
                canonical_errors=[],
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", bad_first_plan)
        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", repair_plan)
        result = await api.run_chat(app, user_id="u", message="а есл ия хочу сдать в арнеду", channel="api")

        saved = app["state_store"].states["u"]
        assert repair_calls == []
        assert result["intent"] == "answer_current_options"
        assert result["turn_decision"] == {"stage": "current_options", "action": "answer_from_current_options"}
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["active_topic"] == "rental"
        assert saved["nmbot_v2"]["params"]["primary_intent"] == "investment"
        assert saved["nmbot_v2"]["params"]["purpose"] == "investment"
        assert [item["name"] for item in saved["nmbot_v2"]["visible_options"]] == ["Бусиновский парк", "Мичуринский парк", "Жилой район «Скандинавия»"]
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert result["meta"]["runtime"] == "v2"

    asyncio.run(scenario())


def test_h046_non_repairable_invalid_plan_leaves_intent_and_visible_options_unchanged(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        app["state_store"].states["u"] = seed_options_state()
        repair_calls = 0

        async def invalid_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                action="answer_current_options",
                dialog_action="select_option",
                target="current_options",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="one",
                selected_option_name="Несуществующий ЖК",
                canonical_valid=True,
                canonical_errors=[],
            )

        async def repair_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("non-repairable selection errors must not be repaired")

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", invalid_plan)
        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", repair_plan)
        result = await api.run_chat(app, user_id="u", message="покажи этот", channel="api")

        saved = app["state_store"].states["u"]
        assert result["intent"] == "safe_upstream_fallback"
        assert repair_calls == 0
        assert set(saved) == {"nmbot_v2"}
        assert saved["nmbot_v2"]["params"] == {"purpose": "investment", "primary_intent": "investment"}
        assert [item["name"] for item in saved["nmbot_v2"]["visible_options"]] == ["Бусиновский парк", "Мичуринский парк", "Жилой район «Скандинавия»"]

    asyncio.run(scenario())


def test_h046_repair_never_runs_for_fallback_or_low_confidence(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        repair_calls = 0

        async def repair_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("repair must not run")

        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", repair_plan)

        async def fallback_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"fallback_used": True, "confidence": 0.0}

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fallback_plan)
        app["state_store"].states["fallback"] = seed_options_state()
        await api.run_chat(app, user_id="fallback", message="непонятно", channel="api")

        async def low_confidence_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                action="answer_current_options",
                target="none",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="unknown",
                confidence=0.2,
                canonical_valid=False,
                canonical_errors=["current_options_requires_current_forbidden"],
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", low_confidence_plan)
        app["state_store"].states["low"] = seed_options_state()
        await api.run_chat(app, user_id="low", message="а есл ия хочу сдать в арнеду", channel="api")

        assert repair_calls == 0
        assert app["state_store"].states["fallback"]["nmbot_v2"]["params"]["primary_intent"] == "investment"
        assert app["state_store"].states["low"]["nmbot_v2"]["params"]["primary_intent"] == "investment"

    asyncio.run(scenario())


def test_h046_public_planner_diagnostics_have_codes_without_raw_payload(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        state = seed_options_state()
        state["contact_phone_draft_meta"] = {"digits_len": 11, "captured": True}
        app["state_store"].states["u"] = state

        async def invalid_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                action="answer_current_options",
                dialog_action="recommend_options",
                target="none",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="unknown",
                canonical_valid=False,
                canonical_errors=["current_options_requires_current_forbidden", "current_options_scope_must_be_one_or_all"],
            )

        async def rejected_repair(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                action="answer_current_options",
                target="none",
                search_policy="forbidden",
                intent="rental",
                intent_policy="change",
                scope="unknown",
                confidence=0.9,
                canonical_valid=False,
                canonical_errors=["current_options_requires_current_forbidden"],
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", invalid_plan)
        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", rejected_repair)
        result = await api.run_chat(app, user_id="u", message="а есл ия хочу сдать в арнеду", channel="api")

        assert result["meta"]["runtime"] == "v2"
        assert result["turn_decision"] == {"stage": "current_options", "action": "answer_from_current_options"}
        public = result["meta"]["trace"]
        saved_state = app["state_store"].states["u"]
        assert set(saved_state) == {"nmbot_v2"}
        serialized = json.dumps({"public": public}, ensure_ascii=False).lower()
        for forbidden in ("а есл", "арнеду", "79991234567", "бусиновский", "мичуринский", "скандинавия", "client_id", "chat_id", "phone"):
            assert forbidden not in serialized

    asyncio.run(scenario())


def test_semantic_scope_all_plan_executes_all_three_without_new_search(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        state = seed_options_state()
        state["primary_intent"] = "rental"
        app["state_store"].states["u"] = state

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(intent="rental", intent_policy="keep", scope="all")

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="да все адвай", channel="api")

        assert result["intent"] == "answer_current_options"
        assert "какой ЖК" not in result["answer"]
        assert client.ask_calls == []
        assert client.explain_calls == []
        assert all(name in result["answer"] for name in ["Бусиновский парк", "Мичуринский парк", "Жилой район Скандинавия"])

    asyncio.run(scenario())


def test_typo_selected_option_is_owned_by_semantic_planner(monkeypatch) -> None:
    async def scenario() -> None:
        client = FakeCurrentOptionsClient()
        app = make_app(client)
        state = seed_options_state()
        state["primary_intent"] = "rental"
        app["state_store"].states["u"] = state

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return canonical_plan(
                operation="select_option",
                dialog_action="select_option",
                action="answer_current_options",
                target="current_options",
                search_policy="forbidden",
                intent="rental",
                intent_policy="keep",
                scope="one",
                selected_option_name="Мичуринский парк",
                clarification_question="",
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        result = await api.run_chat(app, user_id="u", message="Мичурискний подробнее", channel="api")

        assert result["intent"] == "answer_current_options"
        assert result["selected_option"] == "Мичуринский парк"
        assert result["turn_decision"] == {"stage": "selected_object", "action": "answer_selected_option"}
        assert "Мичуринский парк" in result["answer"]
        assert "Бусиновский парк" not in result["answer"]
        assert "Жилой район «Скандинавия»" not in result["answer"]
        assert client.explain_calls == []
        assert client.ask_calls == []

    asyncio.run(scenario())


def test_canonical_planner_contract_accepts_rental_and_scope_all() -> None:
    raw = canonical_plan()
    result = planner._with_canonical_fields({"dialog_action": "recommend_options", "confidence": 0.9}, raw)

    assert result["canonical_valid"] is True
    assert result["intent"] == "rental"
    assert result["scope"] == "all"


def test_stateful_canonical_dataset_decisions_and_exact_selection() -> None:
    rows = json.loads((ROOT / "tests" / "fixtures" / "h045_canonical_planner_stateful_regression.json").read_text(encoding="utf-8"))
    for row in rows:
        state = seed_options_state()
        state["visible_options"].insert(0, {"name": "ЖК Семейный", "ready": "строится"})
        state["last_options"] = list(state["visible_options"])
        normalized = planner._with_canonical_fields({}, row["plan"], state=api._safe_planner_state(row["user_text"], state))
        valid, errors = api._validate_canonical_plan(normalized, state)
        assert valid, (row["id"], errors)
        decision = api._decision_from_planner(normalized, state)
        assert decision.public() == row["expected_decision"], row["id"]
        api._apply_planner_selection(state, normalized)
        selected = (state.get("selected_option") or {}).get("name") if isinstance(state.get("selected_option"), dict) else None
        assert selected == row["expected_selected"], row["id"]


def test_observed_family_mortgage_bad_scenario_switch_is_advisory_runtime_current_options() -> None:
    state = seed_options_state()
    state["primary_intent"] = "investment"
    state["visible_options"].insert(0, {"name": "ЖК Семейный", "ready": "строится"})
    state["last_options"] = list(state["visible_options"])
    bad = canonical_plan(
        action="answer_current_options",
        dialog_action="consultation_answer",
        target="current_options",
        search_policy="forbidden",
        intent="family",
        intent_policy="change",
        scope="all",
        selected_option_name=None,
        facets={},
    )

    valid, errors = api._validate_canonical_plan(bad, state)
    decision = api._decision_from_planner(bad, state)

    assert valid is False
    assert "current_options_change_only_rental_switch" in errors
    assert decision.public() == {"action": "answer_current_options", "target": "current_options", "search_policy": "forbidden"}


def test_desired_family_mortgage_current_options_plan_passes() -> None:
    state = seed_options_state()
    state["primary_intent"] = "investment"
    state["visible_options"].insert(0, {"name": "ЖК Семейный", "ready": "строится"})
    state["last_options"] = list(state["visible_options"])
    desired = canonical_plan(
        action="answer_current_options",
        dialog_action="consultation_answer",
        target="current_options",
        search_policy="forbidden",
        intent="mortgage",
        intent_policy="keep",
        scope="all",
        selected_option_name=None,
        facets={"mortgage": True, "family_mortgage": True},
    )

    valid, errors = api._validate_canonical_plan(desired, state)
    decision = api._decision_from_planner(desired, state)
    api._apply_planner_selection(state, desired)

    assert valid, errors
    assert decision.public() == {"action": "answer_current_options", "target": "current_options", "search_policy": "forbidden"}
    assert state.get("selected_option") in (None, {})


def test_observed_new_rental_search_bad_scope_is_advisory_at_runtime() -> None:
    state = seed_options_state()
    bad = canonical_plan(
        action="search",
        dialog_action="new_search",
        target="new_search",
        search_policy="required",
        intent="rental",
        intent_policy="change",
        scope="all",
        selected_option_name=None,
        search_profile="investment",
        constraints_patch={"hard": {"max_price": 15_000_000, "location": ["Сокол"]}, "preferences": {"purpose": "rental"}, "unknown": {}},
    )

    valid, errors = api._validate_canonical_plan(bad, state)
    decision = api._decision_from_planner(bad, state)

    assert valid is False
    assert "search_scope_must_be_unknown" in errors
    assert decision.public() == {"action": "search", "target": "new_search", "search_policy": "required"}


def test_advisory_search_scope_error_executes_without_repair(monkeypatch) -> None:
    async def scenario() -> None:
        app = make_app(None)
        app["state_store"].states["u"] = seed_options_state()
        gateway_calls: list[dict[str, Any]] = []
        repair_calls = 0

        advisory_plan = canonical_plan(
            action="search",
            dialog_action="new_search",
            target="new_search",
            search_policy="required",
            intent="investment",
            intent_policy="set",
            scope="all",
            confidence=1.0,
            selected_option_name=None,
            search_profile="investment",
            constraints_patch={"hard": {"location": ["Сокол"]}, "preferences": {}, "unknown": {}},
            planner_raw_response='{"action":"search","scope":"all"}',
            canonical_valid=True,
            canonical_errors=[],
        )

        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return dict(advisory_plan)

        async def repair_plan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("advisory validation errors must not trigger repair")

        class SearchClient:
            async def ensure_session(self) -> object:
                return object()

            async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
                gateway_calls.append(request_data)
                return json.dumps(
                    {
                        "facts": [{"name": "ЖК Сокол", "location": "Сокол", "price_min": 12_000_000}],
                        "near": [],
                        "missing": [],
                        "params": {"purpose": "investment", "location": ["Сокол"]},
                        "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                    },
                    ensure_ascii=False,
                ), {"ok": True}

        app["overmind_client"] = SearchClient()
        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)
        monkeypatch.setattr(api.followup_intent_classifier, "repair_canonical_plan", repair_plan)

        result = await api.run_chat(app, user_id="u", message="подбери для инвестиций в Соколе", channel="api")

        assert result["intent"] == "main_search"
        assert result["turn_decision"] == {"stage": "refinement", "action": "search"}
        assert repair_calls == 0
        assert len(gateway_calls) == 2
        assert gateway_calls[0]["_payload_stage"] == "main_search"
        assert '"requested_hard": {"location": ["Сокол"]}' in gateway_calls[0]["query"]
        assert gateway_calls[1]["_payload_stage"] == "main_search"
        assert '"count": 2' in gateway_calls[1]["query"]
        assert '"excluded_names": ["ЖК Сокол"]' in gateway_calls[1]["query"]

    asyncio.run(scenario())


def test_desired_new_rental_search_plan_passes() -> None:
    state = seed_options_state()
    desired = canonical_plan(
        action="search",
        dialog_action="new_search",
        target="new_search",
        search_policy="required",
        intent="rental",
        intent_policy="change",
        scope="unknown",
        selected_option_name=None,
        search_profile="investment",
        constraints_patch={"hard": {"max_price": 15_000_000, "location": ["Сокол"]}, "preferences": {"purpose": "rental"}, "unknown": {}},
    )

    valid, errors = api._validate_canonical_plan(desired, state)
    decision = api._decision_from_planner(desired, state)

    assert valid, errors
    assert decision.public() == {"action": "search", "target": "new_search", "search_policy": "required"}


def test_new_search_without_known_intent_requires_set_policy() -> None:
    state = api._default_state()
    plan = canonical_plan(
        action="search",
        dialog_action="new_search",
        target="new_search",
        search_policy="required",
        intent="rental",
        intent_policy="change",
        scope="unknown",
        selected_option_name=None,
        search_profile="investment",
        constraints_patch={"hard": {"max_price": 15_000_000}, "preferences": {"purpose": "rental"}, "unknown": {}},
    )

    valid, errors = api._validate_canonical_plan(plan, state)

    assert valid is False
    assert "search_new_intent_requires_set" in errors


def test_new_search_same_known_intent_allows_keep_or_set() -> None:
    state = seed_options_state()
    state["primary_intent"] = "rental"
    for policy in ("keep", "set"):
        plan = canonical_plan(
            action="search",
            dialog_action="new_search",
            target="new_search",
            search_policy="required",
            intent="rental",
            intent_policy=policy,
            scope="unknown",
            selected_option_name=None,
            search_profile="investment",
            constraints_patch={"hard": {"max_price": 15_000_000}, "preferences": {"purpose": "rental"}, "unknown": {}},
        )
        valid, errors = api._validate_canonical_plan(plan, state)
        assert valid, (policy, errors)


def test_planner_selected_option_must_be_exact_current_allowlist_member() -> None:
    state = seed_options_state()
    plan = canonical_plan(
        dialog_action="select_option",
        action="answer_current_options",
        target="current_options",
        search_policy="forbidden",
        scope="one",
        selected_option_name="Мичурискний парк",
    )

    valid, errors = api._validate_canonical_plan(plan, state)

    assert valid is False
    assert "selected_option_not_in_current_options" in errors


def test_generic_family_mortgage_words_do_not_select_family_complex_in_shadow_fallback() -> None:
    state = seed_options_state()
    state["visible_options"].insert(0, {"name": "ЖК Семейный", "ready": "строится"})
    state["last_options"] = list(state["visible_options"])

    resolved = api._resolve_conservative_current_option_fallback(state, "семейная ипотека")

    assert resolved is False
    assert state.get("selected_option") in (None, {})


def test_shadow_fallback_unique_typo_is_only_migration_path_after_recover() -> None:
    state = seed_options_state()

    resolved = api._resolve_conservative_current_option_fallback(state, "Мичурискний подробнее")

    assert resolved is True
    assert state["selected_option"]["name"] == "Мичуринский парк"
    assert state["pending_followup"]["match"] == "shadow_fallback"


def test_scope_one_without_selected_option_fails_closed() -> None:
    state = seed_options_state()
    plan = canonical_plan(
        action="answer_current_options",
        dialog_action="consultation_answer",
        target="current_options",
        search_policy="forbidden",
        scope="one",
        selected_option_name=None,
    )

    valid, errors = api._validate_canonical_plan(plan, state)

    assert valid is False
    assert "selected_option_required_for_scope_one" in errors


def test_clarify_requires_question_and_does_not_reask_known_fields() -> None:
    state = seed_options_state()
    state["params"] = {"purpose": "investment"}
    plan = canonical_plan(
        action="clarify",
        dialog_action="ask_clarification",
        target="none",
        search_policy="forbidden",
        intent="investment",
        scope="unknown",
        clarification_question="",
        clarification_fields=["purpose"],
    )

    valid, errors = api._validate_canonical_plan(plan, state)

    assert valid is False
    assert "clarification_required" in errors
    assert any(error.startswith("known_field_reasked:purpose") for error in errors)


def test_unsupported_future_rental_income_claim_is_removed() -> None:
    text = bot._prepare_response_text(
        "ЖК готов. Можно сразу сдавать в аренду и начать получать доход от аренды. Метро рядом — 6 минут пешком."
    )

    lowered = text.lower().replace("ё", "е")
    assert "сразу сдавать" not in lowered
    assert "доход от аренды" not in lowered
    assert "Метро рядом" in text


class FakeRoomEnrichmentClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def enrich_option_search(self, query: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls.append(query)
        name = query.split("Раскрой подробно ", 1)[1].split(" для сценария", 1)[0]
        rooms = [{"rooms": "2-комнатные"}] if name in {"ЖК Первый", "ЖК Второй", "ЖК Третий"} else []
        return {"facts": [{"name": name, "location": "Москва", "price_min": 12_000_000, "apartment_types": rooms}]}, {"ok": True}


def test_room_query_filters_after_full_card_enrichment_and_returns_max_three() -> None:
    async def scenario() -> None:
        search_text = json.dumps(
            {
                "facts": [
                    {"name": "ЖК Первый", "location": "Москва", "price_min": 12_000_000},
                    {"name": "ЖК Второй", "location": "Москва", "price_min": 13_000_000},
                    {"name": "ЖК Третий", "location": "Москва", "price_min": 14_000_000},
                    {"name": "ЖК Четвёртый", "location": "Москва", "price_min": 15_000_000},
                    {"name": "ЖК Пятый", "location": "Москва", "price_min": 16_000_000},
                ]
            },
            ensure_ascii=False,
        )
        hard = {"hard": {"rooms": 2, "location": ["Москва"]}}
        before_context, before_diag, before_visible = bot._four_layer_decision_context(search_text, hard_constraints=hard)
        assert before_context is not None
        assert before_diag["four_layer_matched_count"] == 0
        assert before_visible == []

        client = FakeRoomEnrichmentClient()
        options = bot._extract_search_result_options(search_text)["facts"][:5]
        enriched, meta = await bot._enrich_top_options_for_first_list(client, {}, options, "rental", max_options=5, total_timeout=0.5)
        enriched_text = bot._search_result_with_enriched_facts(search_text, enriched)
        after_context, after_diag, after_visible = bot._four_layer_decision_context(enriched_text, hard_constraints=hard)

        assert len(client.calls) == 5
        assert meta["count"] == 5
        assert after_context is not None
        assert after_diag["four_layer_matched_count"] == 3
        assert [item["name"] for item in after_visible] == ["ЖК Первый", "ЖК Второй", "ЖК Третий"]
        assert len(after_context["matched"]) == 3

    asyncio.run(scenario())
