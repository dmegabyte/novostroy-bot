from pathlib import Path

from nmbot_v1.contracts import V1IntentPlan
from nmbot_v1.execution_path import sanitize_execution_path
from nmbot_v1.runtime import run_turn_sync
from nmbot_v1.state import V1ConversationState


def plan(goal, **kw):
    data = {"schema_version": 1, "goal": goal, "viewpoint": "buyer", "constraints_delta": {"hard": {}, "preferences": {}}, "selected_option_ref": None, "selected_lot_ref": None, "requested_facts": [], "operator_intent": "none", "clarification": None, "confidence": 1}
    data.update(kw)
    return V1IntentPlan.from_dict(data)


class Planner:
    def __init__(self, *plans):
        self.plans = list(plans)
        self.inputs = []
    def plan(self, planner_input):
        self.inputs.append(planner_input)
        return self.plans.pop(0)


class Search:
    def __init__(self, exc=False):
        self.requests = []
        self.exc = exc
    def search(self, request):
        self.requests.append(request)
        if self.exc:
            raise RuntimeError("SECRET provider exploded")
        return {"schema_version": 1, "cards": [
            {"ref": "p1", "name": "ЖК Первый", "facts": {"price": 10, "location": "Москва"}, "evidence": {"location": "Москва", "max_price": 10}},
            {"ref": "p2", "name": "ЖК Второй", "facts": {"price": 11, "location": "Москва"}, "evidence": {"location": "Москва", "max_price": 11}},
        ], "attempts": [{"status": "ok", "path": "fake"}]}


class EmptySearch:
    def search(self, request):
        return {"schema_version": 1, "cards": [], "attempts": [{"status": "ok"}]}


class MaliciousSearch:
    def search(self, request):
        return {"schema_version": 1, "cards": [{
            "ref": "p1", "name": "ЖК Чистый",
            "facts": {"token": "SECRET_FACT", "phone": "+7 999 111-22-33", "email": "bad@example.com", "price": 999},
            "evidence": {"location": "Москва", "max_price": 10, "rooms": 2, "token": "SECRET_EVIDENCE", "raw_payload": {"secret": 1}},
        }], "attempts": [{"status": "ok", "token": "SECRET_ATTEMPT"}]}


class DslRoomsSearch:
    def search(self, request):
        return {"schema_version": 1, "cards": [{
            "ref": "p1", "name": "ЖК Чистый",
            "facts": {"rooms": "novos.rooms contains '2'"},
            "evidence": {"location": "Москва", "max_price": 10, "rooms": "novos.rooms contains '2'"},
        }], "attempts": [{"status": "ok"}]}


class PresenterFail:
    def present(self, *_):
        raise RuntimeError("SECRET presenter")


class PresenterCount:
    def __init__(self, text):
        self.calls = 0
        self.text = text
    def present(self, *_):
        self.calls += 1
        return self.text


class ResponseModel:
    model = "openai/gpt-5.5"

    def __init__(self, candidate=None, exc=False):
        self.candidate = candidate or {"response": "Модельный ответ. Выбрать?", "visible_options": [], "next_action": "none"}
        self.exc = exc
        self.calls = []

    def present(self, model_input):
        self.calls.append(model_input)
        if self.exc:
            raise RuntimeError("SECRET model failed")
        return self.candidate


class Sink:
    def __init__(self):
        self.events = []
    def write(self, event):
        self.events.append(event)


class ExplodingPlanner:
    def plan(self, _planner_input):
        raise RuntimeError("SECRET planner exploded")


def _stages(result):
    path = sanitize_execution_path(result.trace["execution_path"])
    assert path is not None
    return {item["stage_id"]: item for item in path["stages"]}


def test_runtime_first_search_refinement_trace_journal_and_presenter_fallback():
    planner = Planner(plan("search", constraints_delta={"hard": {"location": "Москва", "max_price": 12}, "preferences": {}}))
    search = Search()
    journal = Sink(); trace = Sink()
    result = run_turn_sync("ищу +7 999 111-22-33 SECRET", None, planner, search, PresenterFail(), journal, trace, presenter_mode="shadow")
    assert result.runtime_version == "V1"
    assert result.stage == "first_search"
    assert result.action == "search"
    assert result.answer_kind == "search_results"
    assert result.state["revision"] == 1
    assert result.state["hard_constraints"] == {"location": "Москва", "max_price": 12}
    assert len(result.state["visible_options"]) == 2
    assert "ЖК Первый" in result.response_text
    assert "SECRET" not in str(result.trace)
    assert result.trace["runtime_version"] == "V1"
    assert result.trace["execution_path"]["path_id"] == "v1.turn.v1"
    stages = _stages(result)
    assert stages["v1.planner"]["status"] == "completed"
    assert stages["v1.transition"]["status"] == "completed"
    assert stages["v1.search"]["status"] == "completed"
    assert stages["v1.response_plan"]["status"] == "completed"
    assert stages["v1.deterministic_render"]["status"] == "completed"
    assert stages["v1.presenter"]["status"] == "fallback"
    assert stages["v1.runtime_finalize"]["status"] == "completed"
    assert trace.events[0]["runtime_version"] == "V1"
    assert journal.events[0]["runtime_version"] == "V1"

    planner2 = Planner(plan("search", constraints_delta={"hard": {"rooms": 2}, "preferences": {}}))
    search2 = Search()
    refined = run_turn_sync("теперь две комнаты", result.state, planner2, search2)
    assert refined.stage == "refine_search"
    assert search2.requests[0].hard_constraints == {"location": "Москва", "max_price": 12, "rooms": 2}


def test_invalid_selection_provider_exception_and_low_confidence_operator_do_not_mutate():
    state = V1ConversationState.clean()
    bad_select = run_turn_sync("выбери", state.to_dict(), Planner(plan("select_project", selected_option_ref="x")))
    assert bad_select.stage == "safe_error"
    assert bad_select.state == state.to_dict()

    provider_error = run_turn_sync("поиск", state.to_dict(), Planner(plan("search")), Search(exc=True))
    assert provider_error.stage == "safe_error"
    assert provider_error.state == state.to_dict()
    assert "SECRET" not in provider_error.response_text

    low = run_turn_sync("да оператор", state.to_dict(), Planner(plan("search", operator_intent="accept", confidence=0.2)))
    assert low.stage == "safe_error"
    assert low.state == state.to_dict()
    assert low.answer_kind == "safe_error"

    no_offer_accept = run_turn_sync("да оператор", state.to_dict(), Planner(plan("search", operator_intent="accept", confidence=0.95)))
    assert no_offer_accept.stage == "safe_error"
    assert no_offer_accept.state == state.to_dict()

    unsupported = run_turn_sync("ищу", state.to_dict(), Planner(plan("search", constraints_delta={"hard": {"school_nearby": True}, "preferences": {}})), Search())
    assert unsupported.stage == "safe_error"
    assert unsupported.state == state.to_dict()
    assert unsupported.trace["safe_code"] == "search_validation_error"


def test_select_lot_operator_accept_after_offer_expand_and_missing_search_port():
    base = V1ConversationState.from_dict({**V1ConversationState.clean().to_dict(), "selected_project": {"ref": "p1", "name": "ЖК Первый"}, "visible_options": [{"ref": "l1", "name": "Лот 1", "facts": {"price": 10}, "evidence": {}}]})
    lot = run_turn_sync("этот лот", base.to_dict(), Planner(plan("select_lot", selected_lot_ref="l1")))
    assert lot.stage == "selected_lot"
    assert lot.action == "select_lot"
    assert lot.state["selected_lot"]["ref"] == "l1"
    assert "Выбрала Лот 1" in lot.response_text
    assert "Что уточнить по этому лоту" in lot.response_text
    assert lot.response_text.count("?") == 1

    bad_lot = run_turn_sync("другой лот", base.to_dict(), Planner(plan("select_lot", selected_lot_ref="missing")))
    assert bad_lot.stage == "safe_error"
    assert bad_lot.state == base.to_dict()

    offered = run_turn_sync("позови оператора", None, Planner(plan("offer_operator", operator_intent="request")))
    accepted = run_turn_sync("да", offered.state, Planner(plan("search", operator_intent="accept", confidence=0.95)))
    assert accepted.stage == "contact_name"
    assert accepted.action == "accept_operator"
    assert accepted.state["contact_consent"] is True

    expanded = run_turn_sync("расширь", None, Planner(plan("expand_search")), Search())
    assert expanded.stage == "expand_search"
    missing = run_turn_sync("ищу", None, Planner(plan("search")), search_port=None)
    assert missing.stage == "safe_error"
    assert "Сорян" not in missing.response_text
    assert missing.state == V1ConversationState.clean().to_dict()
    assert missing.trace["safe_code"] == "missing_search_port"
    missing_stages = _stages(missing)
    assert missing_stages["v1.search"] == {"stage_id": "v1.search", "status": "failed", "error_code": "missing_search_port"}
    assert missing_stages["v1.response_plan"]["status"] == "completed"
    assert missing_stages["v1.runtime_finalize"]["status"] == "completed"


def test_runtime_exception_has_failed_planner_path_without_secret_leak():
    result = run_turn_sync("ищу SECRET", None, ExplodingPlanner(), Search())

    assert result.stage == "safe_error"
    assert "Сорян" not in result.response_text
    stages = _stages(result)
    assert stages["v1.planner"] == {"stage_id": "v1.planner", "status": "failed", "error_code": "runtime_safe_error"}
    assert stages["v1.transition"]["status"] == "skipped"
    assert stages["v1.runtime_finalize"]["status"] == "completed"
    assert "SECRET" not in str(result.trace)


def test_empty_inventory_current_options_and_presenter_modes():
    empty = run_turn_sync("ничего", None, Planner(plan("search")), EmptySearch())
    assert empty.stage == "first_search"
    assert empty.answer_kind == "search_results"
    assert empty.state["revision"] == 1
    assert "не нашла" in empty.response_text
    assert "техничес" not in empty.response_text.lower()
    assert empty.response_text.count("?") == 1

    state = V1ConversationState.from_dict({**V1ConversationState.clean().to_dict(), "visible_options": [{"ref": "p1", "name": "ЖК Первый", "facts": {"price": 10}, "evidence": {}}]})
    current = run_turn_sync("что было?", state.to_dict(), Planner(plan("answer_current")), search_port=None)
    assert current.stage == "current_options"
    assert "ЖК Первый" in current.response_text
    assert current.response_text.count("?") == 1

    off_presenter = PresenterCount("CUSTOM")
    off = run_turn_sync("ищу", None, Planner(plan("search")), Search(), off_presenter, presenter_mode="off")
    assert off_presenter.calls == 0
    assert "CUSTOM" not in off.response_text
    assert off.trace["presenter_requested_mode"] == "off"
    assert off.trace["presenter_effective_mode"] == "off"

    valid_text = "ЖК Первый 10 и ЖК Второй 11. Хотите выбрать один из этих вариантов?"
    shadow_presenter = PresenterCount(valid_text)
    shadow = run_turn_sync("ищу", None, Planner(plan("search")), Search(), shadow_presenter, presenter_mode="shadow")
    assert shadow_presenter.calls == 1
    assert shadow.response_text != valid_text
    assert shadow.trace["presenter_requested_mode"] == "shadow"
    assert shadow.trace["presenter_effective_mode"] == "shadow"

    publish_presenter = PresenterCount("ЖК Первый 10 и ЖК Второй 11. Новая модель добавила ипотеку 0% и скидку миллион. Хотите выбрать один из этих вариантов?")
    publish = run_turn_sync("ищу", None, Planner(plan("search")), Search(), publish_presenter, presenter_mode="publish")
    assert publish_presenter.calls == 1
    assert publish.response_text != publish_presenter.text
    assert "ипотеку 0%" not in publish.response_text
    assert "скидку миллион" not in publish.response_text
    assert publish.trace["presenter_requested_mode"] == "publish"
    assert publish.trace["presenter_effective_mode"] == "shadow"
    assert publish.trace["presenter_mode_reason"] == "presenter_publish_not_enabled_stage_a"
    assert "SECRET" not in str(publish.trace)
    assert publish_presenter.text not in str(publish.trace)


def test_one_model_response_mode_off_shadow_publish_fallback_and_contact_bypass():
    candidate = {"response": "Есть ЖК Первый. Посмотреть подробнее?", "visible_options": [{"name": "ЖК Первый"}], "next_action": "inspect_option"}
    off_model = ResponseModel(candidate)
    off = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=off_model, response_model_mode="off")
    assert off_model.calls == []
    assert off.trace["response_model"] == {"mode": "off", "status": "off", "published": False}
    assert off.response_text != candidate["response"]

    shadow_model = ResponseModel(candidate)
    shadow = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=shadow_model, response_model_mode="shadow")
    assert shadow_model.calls
    assert shadow_model.calls[0]["previous_assistant_message"] == ""
    assert shadow.trace["response_model"]["status"] == "valid"
    assert shadow.trace["response_model"]["published"] is False
    assert shadow.response_text != candidate["response"]
    assert candidate["response"] not in str(shadow.trace)

    publish_model = ResponseModel(candidate)
    published = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=publish_model, response_model_mode="publish")
    assert published.response_text == candidate["response"]
    assert published.trace["response_model"]["published"] is True

    bad_model = ResponseModel({"response": "ЖК Несуществующий. Берём?", "visible_options": [{"name": "ЖК Несуществующий"}], "next_action": "inspect_option"})
    fallback = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=bad_model, response_model_mode="publish")
    assert fallback.response_text != bad_model.candidate["response"]
    assert fallback.trace["response_model"]["status"] == "fallback"
    assert fallback.trace["response_model"]["reason"] == "one_model_validation_failed:visible_option_0_not_in_evidence"
    assert "SECRET" not in str(fallback.trace)

    invalid_json_model = ResponseModel("not json")
    invalid_json = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=invalid_json_model, response_model_mode="publish")
    assert invalid_json.trace["response_model"] == {"mode": "publish", "status": "fallback", "published": False, "reason": "invalid_json"}

    wrong_keys_model = ResponseModel({"response": "Есть ЖК Первый?", "raw_payload": "SECRET"})
    wrong_keys = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=wrong_keys_model, response_model_mode="publish")
    assert wrong_keys.trace["response_model"] == {"mode": "publish", "status": "fallback", "published": False, "reason": "wrong_keys"}
    assert "SECRET" not in str(wrong_keys.trace)

    provider_error_model = ResponseModel(exc=True)
    provider_error = run_turn_sync("ищу", None, Planner(plan("search")), Search(), response_model_port=provider_error_model, response_model_mode="publish")
    assert provider_error.trace["response_model"] == {"mode": "publish", "status": "fallback", "published": False, "reason": "provider_or_validation_failed"}

    offered = run_turn_sync("оператор", None, Planner(plan("offer_operator", operator_intent="request")))
    accepted_model = ResponseModel(candidate)
    accepted = run_turn_sync("да", offered.state, Planner(plan("search", operator_intent="accept", confidence=0.95)), response_model_port=accepted_model, response_model_mode="publish")
    assert accepted_model.calls == []
    assert accepted.trace["response_model"]["reason"] == "terminal_contact_flow_bypass"
    assert accepted.response_text != candidate["response"]


def test_one_model_input_uses_bounded_redacted_previous_context():
    state = V1ConversationState.from_dict({
        **V1ConversationState.clean().to_dict(),
        "recent_safe_turns": ["Ассистент: вот варианты, звонить по +7 999 111-22-33 не буду"],
    })
    model = ResponseModel({"response": "Есть ЖК Первый. Посмотреть подробнее?", "visible_options": [{"name": "ЖК Первый"}], "next_action": "inspect_option"})

    result = run_turn_sync("ещё варианты", state.to_dict(), Planner(plan("search")), Search(), response_model_port=model, response_model_mode="shadow")

    previous = model.calls[0]["previous_assistant_message"]
    assert result.trace["response_model"]["status"] == "valid"
    assert "Ассистент: вот варианты" in previous
    assert "***2233" in previous
    assert "+7 999" not in previous
    assert len(previous) <= 700


def test_provider_secrets_never_reach_state_response_trace_or_journal():
    journal = Sink(); trace = Sink()
    result = run_turn_sync("ищу", None, Planner(plan("search", constraints_delta={"hard": {"location": "Москва"}, "preferences": {}})), MaliciousSearch(), journal_port=journal, trace_port=trace)
    blob = str({"state": result.state, "response": result.response_text, "trace": result.trace, "journal": journal.events, "trace_events": trace.events})
    assert result.state["visible_options"] == [{"ref": "p1", "name": "ЖК Чистый", "facts": {"location": "Москва", "price": 10, "rooms": 2}}]
    assert "evidence" not in blob
    assert "attempt" not in blob.lower()
    assert "SECRET" not in blob
    assert "+7 999" not in blob
    assert "bad@example" not in blob
    assert "999" not in result.response_text
    assert "10" in result.response_text


def test_internal_dsl_never_reaches_response_state_or_one_model_evidence():
    model = ResponseModel({"response": "Есть ЖК Чистый. Посмотреть подробнее?", "visible_options": [{"name": "ЖК Чистый"}], "next_action": "inspect_option"})

    result = run_turn_sync(
        "двушка для семьи",
        None,
        Planner(plan("search", constraints_delta={"hard": {"rooms": 2}, "preferences": {}})),
        DslRoomsSearch(),
        response_model_port=model,
        response_model_mode="shadow",
    )

    blob = str({"state": result.state, "response": result.response_text, "model_input": model.calls[0]})
    assert result.stage == "first_search"
    assert result.state["visible_options"] == [{"ref": "p1", "name": "ЖК Чистый", "facts": {"location": "Москва", "price": 10}}]
    assert model.calls[0]["evidence"] == {"facts": [], "near": [{"name": "ЖК Чистый", "location": "Москва", "price": 10}], "missing": ["rooms"], "params": {}}
    assert "novos.rooms" not in blob
    assert "contains" not in blob
    assert "2-комнат" not in blob


def test_contact_name_phone_flow_and_privacy_boundaries():
    offered = run_turn_sync("оператор", None, Planner(plan("offer_operator", operator_intent="request")))
    consent = run_turn_sync("да", offered.state, Planner(plan("search", operator_intent="accept", confidence=0.95)))
    named = run_turn_sync("Ирина", consent.state, Planner(plan("capture_name", contact_name="Ирина")))
    assert named.stage == "contact_phone"
    assert named.state["contact_name"] == "Ирина"
    phoned = run_turn_sync("+7 999 111-22-33", named.state, Planner(plan("capture_phone", contact_phone="+7 999 111-22-33")))
    assert phoned.stage == "contact_phone"
    assert phoned.state["contact_phone_redacted"] == "***2233"
    privacy_blob = str({"state": phoned.state, "response": phoned.response_text, "trace": phoned.trace})
    assert "+7 999" not in privacy_blob
    assert "111-22-33" not in privacy_blob
    assert "callback" not in phoned.response_text.lower()
    assert "локально" not in phoned.response_text.lower()
    assert "отправ" not in phoned.response_text.lower()

    clean = V1ConversationState.clean()
    wrong_name = run_turn_sync("Ирина", clean.to_dict(), Planner(plan("capture_name", contact_name="Ирина")))
    assert wrong_name.stage == "safe_error"
    assert wrong_name.state == clean.to_dict()
    bad_phone = run_turn_sync("bad", named.state, Planner(plan("capture_phone", contact_phone="not-a-phone")))
    assert bad_phone.stage == "safe_error"
    assert bad_phone.state == named.state


def test_action_aware_manual_language_repairs():
    state = V1ConversationState.from_dict({**V1ConversationState.clean().to_dict(), "visible_options": [{"ref": "p1", "name": "ЖК Первый", "facts": {"location": "Москва"}}]})
    selected = run_turn_sync("выбираю первый", state.to_dict(), Planner(plan("select_project", selected_option_ref="p1")))
    assert selected.response_text == "Выбрала ЖК Первый. Показать квартиры и лоты в этом ЖК?"

    fact = run_turn_sync("проверь срок", None, Planner(plan("fact_check", requested_facts=["completion"])))
    assert "нет подтверждённых актуальных данных" in fact.response_text
    assert "безопасном контексте" not in fact.response_text
    assert "проверить" not in fact.response_text.lower()
    assert fact.response_text.count("?") == 1

    declined = run_turn_sync("не надо оператора", None, Planner(plan("search", operator_intent="decline")))
    assert "без оператора" in declined.response_text.lower()
    assert "Позвать оператора" not in declined.response_text

    off_topic = run_turn_sync("рецепт пирога", None, Planner(plan("off_topic")))
    assert "новостройками" in off_topic.response_text
    assert "рецепт" not in off_topic.response_text.lower()


def test_no_v0_v2_imports_in_package_source():
    root = Path(__file__).resolve().parents[1] / "nmbot_v1"
    source = "\n".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
    assert "nmbot_v0" not in source
    assert "nmbot_v2" not in source
