from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from aiohttp import web
from nmbot_v2.semantic_planner import derive_runtime_decision, normalize_semantic_planner_result, semantic_to_dict


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

PLANNER_SPEC = importlib.util.spec_from_file_location("followup_semantic_transition", ROOT / "followup_intent_classifier.py")
assert PLANNER_SPEC and PLANNER_SPEC.loader
planner = importlib.util.module_from_spec(PLANNER_SPEC)
sys.modules[PLANNER_SPEC.name] = planner
PLANNER_SPEC.loader.exec_module(planner)

API_SPEC = importlib.util.spec_from_file_location("nmbot_api_semantic_transition", SCRIPT_DIR / "nmbot_api_server.py")
assert API_SPEC and API_SPEC.loader
api = importlib.util.module_from_spec(API_SPEC)
sys.modules[API_SPEC.name] = api
API_SPEC.loader.exec_module(api)


class FakeStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: str) -> dict[str, Any]:
        return self.states.setdefault(user_id, api._default_state())

    async def save(self, user_id: str, state: dict[str, Any]) -> None:
        self.states[user_id] = dict(state)


def make_app(client: Any, store: FakeStore | None = None) -> web.Application:
    app = web.Application()
    app["state_store"] = store or FakeStore()
    app["crm_callback_outbox"] = api.LocalCallbackOutbox(Path("/tmp/nmbot-semantic-test-outbox"))
    app["overmind_client"] = client
    return app


def semantic_search(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "operation": "search",
        "intent": "investment",
        "constraints_delta": {"hard": {}, "preferences": {"purpose": "investment"}, "unknown": {}},
        "reference": None,
        "scope": "unknown",
        "confidence": 0.92,
        "clarification": "",
        "facets": {},
        "operator_contact": {"requested": False, "consent": "none"},
        "missing_fields": [],
        "reason": "semantic test",
    }
    plan.update(overrides)
    return plan


def test_semantic_prompt_keeps_explicit_rental_separate_from_investment() -> None:
    prompt = planner.DIALOG_STATE_PLANNER_PROMPT

    assert '«под аренду»' in prompt
    assert 'response_viewpoint="rental"' in prompt
    assert "Не обобщай аренду до investment" in prompt
    assert "приоритет у rental" in prompt
    assert 'hard.location="ЦАО"' in prompt
    assert "hard.max_price=60000000" in prompt


def test_intent_plan_v3_prompt_canonicalizes_moscow_center_to_cao() -> None:
    prompt = planner.INTENT_PLAN_V3_PROMPT

    assert 'hard.location="ЦАО"' in prompt
    assert 'district="центр"' in prompt
    assert 'location="center"' in prompt


def test_h054_semantic_prompt_declares_additive_scenario_needs() -> None:
    prompt = planner.DIALOG_STATE_PLANNER_PROMPT

    assert '"scenario_needs": []' in prompt
    assert "для семьи, под сдачу и с ипотекой" in prompt
    assert '["family", "rental", "financing"]' in prompt
    assert "Это НЕ hard filters" in prompt


def test_h054_semantic_prompt_declares_contact_resume_signal() -> None:
    assert 'resolved_intent="resume_contact"' in planner.DIALOG_STATE_PLANNER_PROMPT


def test_h054_scenario_needs_normalizes_aliases_dedupes_and_reports_drops() -> None:
    semantic = normalize_semantic_planner_result({
        "scenario_needs": ["family", "mortgage", "finance", "rental", "unsupported", "life", "investment", "extra"],
        "requested_comparison": ["price"],
    })

    assert semantic.scenario_needs == ("family", "financing", "rental", "life", "investment")
    assert semantic.requested_comparison == ("price",)
    assert "unsupported_scenario_need:unsupported" in semantic.errors
    assert "unsupported_scenario_need:extra" in semantic.errors


def test_h054_semantic_to_dict_includes_scenario_needs() -> None:
    semantic = normalize_semantic_planner_result({"scenario_needs": "family,mortgage"})

    assert semantic_to_dict(semantic)["scenario_needs"] == ["family", "financing"]


def test_semantic_empty_investment_search_derives_new_search_set() -> None:
    result = planner._with_canonical_fields({}, semantic_search(constraints_delta={"hard": {"rooms": 2}, "preferences": {"purpose": "investment"}, "unknown": {}}), state={})

    assert result["action"] == "search"
    assert result["dialog_action"] == "new_search"
    assert result["target"] == "new_search"
    assert result["search_policy"] == "required"
    assert result["intent_policy"] == "set"
    assert result["scope"] == "unknown"
    assert result["constraints_patch"]["hard"] == {"rooms": 2}
    assert result["constraints_patch"]["preferences"] == {"purpose": "investment"}


def test_semantic_known_intent_delta_derives_update_keep() -> None:
    state = {"primary_intent": "investment", "params": {"purpose": "investment"}}
    result = planner._with_canonical_fields({}, semantic_search(constraints_delta={"hard": {"max_price": 40_000_000, "location": ["центр"]}, "preferences": {}, "unknown": {}}), state=state)

    assert result["action"] == "search"
    assert result["dialog_action"] == "update_search"
    assert result["intent_policy"] == "keep"
    assert result["constraints_patch"]["hard"]["max_price"] == 40_000_000


def test_operator_consent_semantic_payload_derives_canonical_operator_contact() -> None:
    result = planner._with_canonical_fields({}, {
        "goal": "operator",
        "operator_consent": True,
        "followup_outcome": "accept",
        "confidence": 1.0,
    }, state={"pending_followup": "contact_name", "contact_consent": False})

    assert result["canonical_valid"] is True
    assert result["action"] == "operator_contact"
    assert result["target"] == "operator"
    assert result["search_policy"] == "forbidden"
    assert result["operator_contact"] == {"requested": True, "consent": "granted"}


def test_operator_consent_without_pending_operator_flow_fails_closed() -> None:
    result = planner._with_canonical_fields({}, {
        "goal": "operator",
        "operator_consent": True,
        "followup_outcome": "accept",
        "confidence": 1.0,
    }, state={"pending_followup": None, "contact_consent": False})

    assert result["canonical_valid"] is True
    assert result["action"] == "recover_dialogue"
    assert "invalid_operator_consent_scope" in result["derived_decision"]["errors"]


def test_api_canonical_bridge_accepts_live_operator_consent_payload() -> None:
    state = {"pending_followup": "contact_name", "contact_consent": False}
    canonical = api._ensure_derived_canonical_plan({
        "goal": "operator",
        "operator_consent": True,
        "followup_outcome": "accept",
        "confidence": 1.0,
    }, state)

    decision = api._decision_from_planner(canonical, state)

    assert canonical["action"] == "operator_contact"
    assert canonical["operator_contact"] == {"requested": True, "consent": "granted"}
    assert decision.action == "operator_contact"
    assert decision.search_policy == "forbidden"


def test_canonical_operator_contact_round_trip_preserves_consent() -> None:
    semantic = normalize_semantic_planner_result({
        "action": "operator_contact",
        "dialog_action": "operator_live_check",
        "operator_contact": {"requested": True, "consent": "granted"},
        "confidence": 1.0,
    })

    decision = derive_runtime_decision(
        semantic,
        {"pending_followup": "contact_name", "contact_consent": False},
    )

    assert semantic.operator_consent is True
    assert decision.action == "operator_contact"


def test_semantic_chain_parking_followup_price_preserved() -> None:
    state = {
        "visible_options": [{"name": "Мичуринский парк"}],
        "selected_object": {"canonical_name": "Мичуринский парк", "present_fact_fields": ["parking"]},
        "dialog_focus": {"subject": "parking", "last_requested_facts": ["parking"], "last_answered_facts": ["parking"]},
    }
    result = planner._with_canonical_fields({}, {
        "user_goal": "узнать стоимость паркинга",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": "Мичуринский парк",
        "resolved_subject": "parking",
        "resolved_intent": "price",
        "requested_facts": ["parking_price"],
        "facts_needed": ["parking_price"],
        "requires_enrichment": True,
        "focus_action": "keep",
        "confidence": 0.95,
    }, state=state)

    assert result["action"] == "answer_current_options"
    assert result["dialog_action"] == "select_option"
    assert result["scope"] == "one"
    assert result["selected_option_name"] == "Мичуринский парк"
    assert result["requested_facts"] == ["parking_price"]
    assert result["facts_needed"] == ["parking_price"]
    assert result["requires_enrichment"] is True


def test_unknown_explicit_named_object_routes_to_mcp_lookup() -> None:
    semantic = normalize_semantic_planner_result({
        "user_goal": "узнать про паркинг",
        "refers_to_existing_objects": False,
        "requests_new_objects": False,
        "named_object_reference": "Северный берег",
        "resolved_subject": "parking",
        "requested_facts": ["parking"],
        "facts_needed": ["parking"],
        "confidence": 0.98,
    })
    decision = derive_runtime_decision(semantic, state={"visible_options": []})

    assert decision.action == "lookup_object"
    assert decision.dialog_action == "lookup_named_object"
    assert decision.search_policy == "required"
    assert decision.scope == "one"


def test_canonical_compatibility_keeps_named_object_reference() -> None:
    result = planner._with_canonical_fields({}, {
        "user_goal": "узнать цену в названном ЖК",
        "refers_to_existing_objects": False,
        "requests_new_objects": True,
        "selected_reference": None,
        "named_object_reference": "Северный берег",
        "requested_facts": ["apartment_price"],
        "facts_needed": ["apartment_price"],
        "constraints_delta": {
            "hard": {"max_price": 10_000_000},
            "preferences": {"mortgage": "family_mortgage"},
            "unknown": {},
        },
        "confidence": 0.98,
    }, state={"visible_options": []})

    assert result["named_object_reference"] == "Северный берег"
    assert result["constraints_delta"]["hard"]["max_price"] == 10_000_000
    assert result["constraints_delta"]["preferences"]["finance_preference"] == "family_mortgage"


def test_canonical_compatibility_keeps_explicit_fresh_search_signal() -> None:
    result = planner._with_canonical_fields({}, {
        "user_goal": "показать другие варианты без повторов",
        "refers_to_existing_objects": True,
        "requests_new_objects": True,
        "selected_reference": None,
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "confidence": 0.98,
    }, state={"visible_options": [{"name": "Первый ЖК"}]})

    assert result["requests_new_objects"] is True
    assert result["refers_to_existing_objects"] is True


def test_semantic_explicit_apartment_price_switches_subject() -> None:
    result = planner._with_canonical_fields({}, {
        "user_goal": "узнать стоимость квартиры",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": "Мичуринский парк",
        "resolved_subject": "apartment",
        "requested_facts": ["apartment_price"],
        "facts_needed": [],
        "focus_action": "switch",
        "confidence": 0.95,
    }, state={"visible_options": [{"name": "Мичуринский парк"}]})

    assert result["dialog_action"] == "select_option"
    assert result["resolved_subject"] == "apartment"
    assert result["requested_facts"] == ["apartment_price"]
    assert result["facts_needed"] == []


def test_semantic_ambiguous_price_without_focus_clarifies_without_enrichment() -> None:
    result = planner._with_canonical_fields({}, {
        "user_goal": "уточнить цену",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": None,
        "requested_facts": [],
        "facts_needed": [],
        "requires_enrichment": False,
        "focus_action": "clarify",
        "clarification": "Вы про цену квартиры или машиноместа?",
        "confidence": 0.8,
    }, state={"visible_options": [{"name": "Мичуринский парк"}]})

    assert result["action"] == "clarify"
    assert result["requires_enrichment"] is False
    assert result["facts_needed"] == []


def test_semantic_constraints_alias_budget_max_to_max_price_and_drop_unknown_sensitive() -> None:
    state = {"primary_intent": "investment", "params": {"purpose": "investment"}}
    observed = semantic_search(
        constraints_delta={
            "hard": {
                "budget_max": 40_000_000,
                "room_count": 2,
                "location_name": ["центр"],
                "client_id": "secret-client",
                "unsupported_field": "drop-me",
            },
            "preferences": {"budget": 35_000_000, "token": "secret-token", "purpose": "investment"},
            "unknown": {"price_max": 45_000_000, "raw_payload": "secret"},
        },
    )

    result = planner._with_canonical_fields({}, observed, state=state)

    assert result["constraints_patch"]["hard"] == {"max_price": 40_000_000, "rooms": 2, "location": ["центр"]}
    assert result["constraints_patch"]["preferences"] == {"max_price": 35_000_000, "purpose": "investment"}
    assert result["constraints_patch"]["unknown"] == {"max_price": 45_000_000}
    assert "unsupported_constraint:purpose" not in result["canonical_errors"]
    dumped = str(result["constraints_patch"])
    assert "budget_max" not in dumped
    assert "client_id" not in dumped
    assert "unsupported_field" not in dumped
    assert "secret" not in dumped


def test_observed_malformed_semantic_purpose_constraint_keeps_current_options_no_search() -> None:
    state = {"primary_intent": "investment", "visible_options": [{"name": "Первый ЖК"}, {"name": "Второй ЖК"}]}
    raw = {
        "user_goal": "оценить текущие варианты под аренду",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": None,
        "requested_comparison": None,
        "scenario_change": None,
        "constraints_delta": {"hard": {"purpose": "rental_investment"}, "preferences": {}, "unknown": {}},
        "requires_enrichment": False,
        "facts_needed": [],
        "clarification": None,
        "confidence": 0.92,
        "reason": "observed malformed semantic output",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "answer_current_options"
    assert result["target"] == "current_options"
    assert result["search_policy"] == "forbidden"
    assert result["constraints_patch"] == {"hard": {}, "preferences": {}, "unknown": {}}
    assert result["intent"] == "investment"
    assert result["intent_policy"] == "keep"
    assert "unsupported_constraint:purpose" in result["canonical_errors"]
    assert result["derived_decision"]["needs_search"] is False


def test_real_searchable_constraints_still_derive_new_search() -> None:
    state = {"primary_intent": "investment", "params": {"purpose": "investment"}}
    raw = {
        "user_goal": "обновить поиск по району бюджету и комнатности",
        "refers_to_existing_objects": False,
        "requests_new_objects": True,
        "scenario_change": None,
        "constraints_delta": {"hard": {"location": ["центр"], "max_price": 40_000_000, "rooms": 2}, "preferences": {}, "unknown": {}},
        "requires_enrichment": False,
        "facts_needed": [],
        "clarification": None,
        "confidence": 0.93,
        "reason": "real searchable filters",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "search"
    assert result["search_policy"] == "required"
    assert result["constraints_patch"]["hard"] == {"location": ["центр"], "max_price": 40_000_000, "rooms": 2}


def test_mortgage_constraint_is_canonical_finance_preference_for_rental_search() -> None:
    raw = {
        "user_goal": "найти недорогую квартиру для аренды без ипотеки",
        "refers_to_existing_objects": False,
        "requests_new_objects": True,
        "scenario_change": "rental",
        "constraints_delta": {
            "hard": {"max_price": 50_000_000, "mortgage": False},
            "preferences": {},
            "unknown": {},
        },
        "requires_enrichment": False,
        "facts_needed": [],
        "clarification": None,
        "confidence": 0.93,
        "reason": "new rental search with financing preference",
    }

    result = planner._with_canonical_fields({}, raw, state={})

    assert result["action"] == "search"
    assert result["search_policy"] == "required"
    assert result["constraints_patch"]["hard"] == {"max_price": 50_000_000}
    assert result["constraints_patch"]["preferences"] == {"finance_preference": False}
    assert "unsupported_constraint:mortgage" not in result["canonical_errors"]
    assert result["derived_decision"]["needs_search"] is True


def test_explicit_down_payment_is_preserved_for_current_financing_context() -> None:
    raw = {
        "user_goal": "уточнить первоначальный взнос",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": "Бусиновский парк",
        "response_viewpoint": "financing",
        "resolved_subject": "mortgage",
        "resolved_intent": "update_financial_constraints",
        "constraints_delta": {
            "hard": {"down_payment": 10_000_000},
            "preferences": {},
            "unknown": {},
        },
        "requested_facts": ["mortgage_terms"],
        "facts_needed": ["mortgage_terms"],
        "requires_enrichment": True,
        "focus_action": "switch",
        "confidence": 1.0,
    }

    result = planner._with_canonical_fields(
        {},
        raw,
        state={"visible_options": [{"name": "Бусиновский парк"}], "selected_option_name": "Бусиновский парк"},
    )

    assert result["constraints_patch"]["hard"] == {}
    assert result["constraints_delta"]["hard"] == {"down_payment": 10_000_000}
    assert "unsupported_constraint:down_payment" not in result["canonical_errors"]
    assert result["search_policy"] == "forbidden"


def test_legacy_contradictory_technical_fields_cannot_override_derived_route() -> None:
    raw = semantic_search() | {
        "action": "recover_dialogue",
        "dialog_action": "ask_clarification",
        "target": "none",
        "search_policy": "forbidden",
        "intent_policy": "change",
        "search_profile": "none",
        "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
        "selected_option_name": None,
        "clarification_fields": [],
    }

    result = planner._with_canonical_fields({}, raw, state={})

    assert result["action"] == "search"
    assert result["target"] == "new_search"
    assert result["search_policy"] == "required"
    assert "source_canonical_action_ignored" in result["source_canonical_errors"]


def test_semantic_numeric_reference_resolves_exact_current_option() -> None:
    state = {"visible_options": [{"name": "Первый ЖК"}, {"name": "Мичуринский парк"}]}
    result = planner._with_canonical_fields({}, semantic_search(operation="select_option", reference=2, scope="one"), state=state)

    assert result["action"] == "answer_current_options"
    assert result["dialog_action"] == "select_option"
    assert result["selected_option_name"] == "Мичуринский парк"


def test_semantic_malformed_operation_recovers_safely() -> None:
    result = planner._with_canonical_fields({}, semantic_search(operation="delete_everything", confidence=0.9), state={})

    assert result["action"] == "recover_dialogue"
    assert result["search_policy"] == "forbidden"
    assert "invalid_operation" in result["canonical_errors"]


def test_semantic_current_options_all_routes_all() -> None:
    state = {"primary_intent": "investment", "visible_options": [{"name": "Первый ЖК"}]}
    result = planner._with_canonical_fields({}, semantic_search(operation="current_options", scope="all", intent="rental"), state=state)

    assert result["action"] == "answer_current_options"
    assert result["target"] == "current_options"
    assert result["search_policy"] == "forbidden"
    assert result["scope"] == "all"
    assert result["selected_option_name"] is None
    assert result["intent_policy"] == "change"


def test_answer_planner_fixture_cases_derive_runtime_decisions_without_model() -> None:
    probe_spec = importlib.util.spec_from_file_location("nmbot_v2_answer_planner_probe_test", SCRIPT_DIR / "nmbot_v2_answer_planner_probe.py")
    assert probe_spec and probe_spec.loader
    probe = importlib.util.module_from_spec(probe_spec)
    sys.modules[probe_spec.name] = probe
    probe_spec.loader.exec_module(probe)

    cases = json.loads((ROOT / "tests" / "fixtures" / "nmbot_v2_answer_planner_hypothesis.json").read_text(encoding="utf-8"))
    assert len(cases) == 16
    failures: list[tuple[str, list[str]]] = []
    for case in cases:
        semantic_result, derived = probe.derive_for_case(case, case["semantic_mock"])
        passed, errors = probe.evaluate(case["expected"], semantic_result, derived)
        if not passed:
            failures.append((case["id"], errors))
    assert failures == []


def test_new_semantic_shape_without_operation_derives_current_options() -> None:
    state = {"primary_intent": "investment", "visible_options": [{"name": "Первый ЖК"}]}
    raw = {
        "user_goal": "сравнить текущие варианты по готовности",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": None,
        "requested_comparison": ["readiness"],
        "scenario_change": None,
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "requires_enrichment": True,
        "facts_needed": ["ready"],
        "clarification": None,
        "confidence": 0.9,
        "reason": "semantic-only shape",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "answer_current_options"
    assert result["search_policy"] == "forbidden"
    assert result["semantic_plan"]["facts_needed"] == ["ready"]
    assert "operation" not in result["semantic_plan"]
    assert "invalid_operation" not in result["canonical_errors"]


def test_response_viewpoint_financing_current_options_maps_to_mortgage_without_search() -> None:
    state = {"primary_intent": "investment", "visible_options": [{"name": "Первый ЖК"}, {"name": "Второй ЖК"}]}
    raw = {
        "user_goal": "уточнить финансирование по текущим вариантам",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "selected_reference": None,
        "requested_comparison": ["financing"],
        "response_viewpoint": "financing",
        "scenario_change": None,
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "requires_enrichment": False,
        "facts_needed": [],
        "clarification": None,
        "confidence": 0.91,
        "reason": "semantic viewpoint only",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "answer_current_options"
    assert result["search_policy"] == "forbidden"
    assert result["derived_decision"]["needs_search"] is False
    assert result["intent"] == "mortgage"
    assert result["semantic_plan"]["response_viewpoint"] == "financing"


def test_explicit_new_search_overrides_nonessential_clarification() -> None:
    semantic = normalize_semantic_planner_result({
        "user_goal": "подобрать новые варианты для семьи и аренды",
        "refers_to_existing_objects": False,
        "requests_new_objects": True,
        "scenario_needs": ["family", "rental", "financing"],
        "response_viewpoint": "family",
        "clarification": "Какой бюджет и район рассматриваете?",
        "confidence": 1.0,
    })

    decision = derive_runtime_decision(semantic, {})

    assert decision.action == "search"
    assert decision.search_policy == "required"
    assert decision.context_source == "new_search"
    assert decision.clarification == ""


def test_h054_canonical_output_preserves_scenario_needs_without_filters() -> None:
    state = {"primary_intent": "family", "visible_options": [{"name": "Первый ЖК"}]}
    raw = {
        "user_goal": "оценить варианты для семьи под аренду и ипотеку",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "requested_comparison": ["price"],
        "scenario_needs": ["family", "rental", "mortgage"],
        "response_viewpoint": "family",
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "requires_enrichment": False,
        "facts_needed": [],
        "confidence": 0.94,
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["semantic_plan"]["scenario_needs"] == ["family", "rental", "financing"]
    assert result["scenario_needs"] == ["family", "rental", "financing"]
    assert result["constraints_patch"] == {"hard": {}, "preferences": {}, "unknown": {}}
    assert result["search_policy"] == "forbidden"


def test_response_viewpoint_rental_existing_list_changes_answer_intent_without_search() -> None:
    state = {"primary_intent": "investment", "visible_options": [{"name": "Первый ЖК"}]}
    raw = {
        "user_goal": "оценить текущие варианты под аренду",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "response_viewpoint": "rental",
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "requires_enrichment": False,
        "facts_needed": [],
        "clarification": None,
        "confidence": 0.92,
        "reason": "semantic viewpoint only",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "answer_current_options"
    assert result["search_policy"] == "forbidden"
    assert result["intent"] == "rental"
    assert result["intent_policy"] == "change"
    assert result["derived_decision"]["needs_search"] is False


def test_response_viewpoint_unchanged_retains_current_intent() -> None:
    state = {"primary_intent": "family", "visible_options": [{"name": "Первый ЖК"}]}
    raw = {
        "user_goal": "сравнить текущие варианты по готовности",
        "refers_to_existing_objects": True,
        "requests_new_objects": False,
        "requested_comparison": ["readiness"],
        "response_viewpoint": "unchanged",
        "constraints_delta": {"hard": {}, "preferences": {}, "unknown": {}},
        "requires_enrichment": True,
        "facts_needed": ["ready"],
        "clarification": None,
        "confidence": 0.9,
        "reason": "same viewpoint",
    }

    result = planner._with_canonical_fields({}, raw, state=state)

    assert result["action"] == "answer_current_options"
    assert result["intent"] == "family"
    assert result["intent_policy"] == "keep"
    assert result["search_policy"] == "forbidden"
    forbidden = {"action", "target", "search_policy", "context_source", "needs_search", "scope"}
    assert forbidden.isdisjoint(result["semantic_plan"])


def test_repeated_unclear_turn_uses_one_concrete_missing_field(monkeypatch) -> None:
    async def scenario() -> None:
        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"operation": "recover", "intent": "unknown", "confidence": 0.9, "clarification": "", "reason": "unclear"}

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, *_args: Any, **_kwargs: Any):
                raise AssertionError("recover must not search")

        store = FakeStore()
        state = api._default_state()
        state["primary_intent"] = "investment"
        state["params"] = {"purpose": "investment"}
        state["recovery_count"] = 1
        state["last_bot_question"] = "Помню задачу. Уточните, пожалуйста, что именно продолжить: район, бюджет, комнатность или текущие варианты?"
        store.states["u-loop"] = state
        result = await api.run_chat(make_app(FakeClient(), store), user_id="u-loop", message="не понял", channel="jivo")
        assert result["intent"] == "freeform"
        assert result["turn_decision"] == {"stage": "freeform", "action": "freeform"}
        assert result["answer"].endswith("Какой бюджет ориентировочно держим?")
        assert result["answer"].count("?") == 1

    asyncio.run(scenario())


def test_stateful_semantic_delta_search_does_not_reask_purpose(monkeypatch) -> None:
    async def scenario() -> None:
        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return semantic_search(constraints_delta={"hard": {"max_price": 40_000_000, "location": ["центр"]}, "preferences": {}, "unknown": {}})

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        gateway_calls: list[dict[str, Any]] = []

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, request_data: dict[str, Any], _headers: dict[str, Any], _timeout: int):
                gateway_calls.append(request_data)
                return json.dumps({
                    "facts": [{"name": "Лучи", "location": "центр", "min_price": 20_000_000}],
                    "near": [],
                    "missing": [],
                    "params": {"purpose": "investment", "max_price": 40_000_000, "location": ["центр"]},
                    "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                }, ensure_ascii=False), {"ok": True}

        store = FakeStore()
        state = {"nmbot_v2": {"params": {"purpose": "investment"}, "active_topic": "investment"}}
        store.states["u-delta"] = state
        result = await api.run_chat(make_app(FakeClient(), store), user_id="u-delta", message="В центре желательно до 40 млн", channel="jivo")
        assert result["intent"] == "main_search"
        assert result["turn_decision"] == {"stage": "refinement", "action": "search"}
        assert len(gateway_calls) == 2
        assert '"count": 2' in gateway_calls[1]["query"]
        assert '"excluded_names": ["Лучи"]' in gateway_calls[1]["query"]
        saved = store.states["u-delta"]["nmbot_v2"]
        assert saved["params"]["purpose"] == "investment"
        assert saved["params"]["max_price"] == 40_000_000
        assert saved["active_topic"] == "investment"

    asyncio.run(scenario())


def test_observed_gemini_budget_max_reaches_api_search_params(monkeypatch) -> None:
    async def scenario() -> None:
        async def fake_plan(_session: Any, **_kwargs: Any) -> dict[str, Any]:
            return semantic_search(
                constraints_delta={
                    "hard": {
                        "budget_max": 40_000_000,
                        "location": ["центр"],
                        "phone": "+7 999 111-22-33",
                        "unknown_field": "drop-me",
                    },
                    "preferences": {},
                    "unknown": {},
                }
            )

        monkeypatch.setattr(api.followup_intent_classifier, "plan_dialog_state", fake_plan)

        gateway_calls: list[dict[str, Any]] = []

        class FakeClient:
            async def ensure_session(self) -> None:
                return None

            async def _run_gateway_request(self, request_data: dict[str, Any], _headers: dict[str, Any], _timeout: int):
                gateway_calls.append(request_data)
                return json.dumps({
                    "facts": [{"name": "Лучи", "location": "центр", "min_price": 20_000_000}],
                    "near": [],
                    "missing": [],
                    "params": {"purpose": "investment", "max_price": 40_000_000, "location": ["центр"]},
                    "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "requested_field_priorities": [], "relaxation_audit": []},
                }, ensure_ascii=False), {"ok": True}

        store = FakeStore()
        state = {"nmbot_v2": {"params": {"purpose": "investment"}, "active_topic": "investment"}}
        store.states["u-budget-alias"] = state
        result = await api.run_chat(make_app(FakeClient(), store), user_id="u-budget-alias", message="В цеетре желательно до 40 млн", channel="jivo")

        assert result["intent"] == "main_search"
        assert len(gateway_calls) == 2
        query = gateway_calls[0]["query"]
        assert '"count": 2' in gateway_calls[1]["query"]
        assert '"excluded_names": ["Лучи"]' in gateway_calls[1]["query"]
        assert '"max_price": 40000000' in query
        assert '"location": ["центр"]' in query
        assert "budget_max" not in query
        assert "unknown_field" not in query
        assert "+7 999" not in query
        saved = store.states["u-budget-alias"]["nmbot_v2"]["params"]
        assert saved["max_price"] == 40_000_000
        assert saved["location"] == ["центр"]
        assert "phone" not in saved and "unknown_field" not in saved and "budget_max" not in saved

    asyncio.run(scenario())
