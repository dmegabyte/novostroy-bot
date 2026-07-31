import pytest

from nmbot_v1.contracts import V1AnswerKind, V1Error
from nmbot_v1.response import V1ResponsePlan, render_response
from nmbot_v1.search import parse_search_provider_result
from nmbot_v1.search_contract import project_public_card


def card(ref, name, evidence):
    return {"ref": ref, "name": name, "facts": {"price": evidence.get("max_price", 10), "location": evidence.get("location", "?")}, "evidence": evidence}


def test_search_parser_exact_near_missing_empty_and_malformed():
    hard = {"location": "Москва", "max_price": 12, "rooms": 2}
    raw = {"schema_version": 1, "cards": [
        card("a", "Точный", {"location": "Москва", "max_price": 11, "rooms": 2}),
        card("b", "Дорого", {"location": "Москва", "max_price": 13, "rooms": 2}),
        card("c", "Нет evidence", {"location": "Москва", "max_price": 10}),
    ], "attempts": [{"status": "ok"}]}
    result = parse_search_provider_result(raw, hard)
    assert [c.ref for c in result.exact] == ["a"]
    assert [c.ref for c in result.near] == ["b", "c"]
    assert result.missing == ("rooms",)

    empty = parse_search_provider_result({"schema_version": 1, "cards": [], "attempts": []}, hard)
    assert empty.exact == () and empty.near == () and empty.error_code is None
    with pytest.raises(V1Error):
        parse_search_provider_result({"schema_version": 1, "cards": [], "unknown": 1}, hard)
    with pytest.raises(V1Error):
        parse_search_provider_result(["bad"], hard)
    with pytest.raises(V1Error):
        parse_search_provider_result({"schema_version": 1, "cards": [{"ref": 1, "name": "Bad", "facts": {}, "evidence": {}}], "attempts": []}, hard)
    with pytest.raises(V1Error):
        parse_search_provider_result({"schema_version": 1, "cards": [{"ref": "x", "name": "Bad", "facts": [], "evidence": {}}], "attempts": []}, hard)
    with pytest.raises(V1Error):
        parse_search_provider_result({"schema_version": 1, "cards": [], "attempts": ["bad"]}, hard)
    with pytest.raises(V1Error):
        parse_search_provider_result({"schema_version": 1, "cards": [], "attempts": []}, {"unsupported": "x"})


def test_public_projection_drops_raw_provider_facts_and_keeps_grounded_allowlist():
    raw = {"schema_version": 1, "cards": [{
        "ref": "p1", "name": "ЖК Безопасный",
        "facts": {"token": "SECRET", "comment": "evil@example.com", "location": "Не доверять"},
        "evidence": {"location": "Москва", "max_price": 10, "rooms": 2, "token": "SECRET", "phone": "+7 999 111-22-33", "raw_payload": {"x": 1}},
    }], "attempts": [{"status": "ok", "token": "SECRET"}]}
    result = parse_search_provider_result(raw, {"location": "Москва"})
    public = project_public_card(result.exact[0])
    assert public == {"ref": "p1", "name": "ЖК Безопасный", "facts": {"location": "Москва", "price": 10, "rooms": 2}}
    assert "evidence" not in public
    assert "SECRET" not in str(public)
    assert "+7" not in str(public)
    assert "evil@" not in str(public)


def test_public_projection_drops_internal_dsl_but_keeps_valid_public_fields():
    raw = {"schema_version": 1, "cards": [{
        "ref": "p1", "name": "ЖК Семейный",
        "facts": {},
        "evidence": {"location": "Москва", "max_price": 10, "rooms": "novos.rooms contains '2'"},
    }], "attempts": [{"status": "ok"}]}

    result = parse_search_provider_result(raw, {"rooms": 2})
    assert result.exact == ()
    assert [c.ref for c in result.near] == ["p1"]
    public = project_public_card(result.near[0])

    assert public == {"ref": "p1", "name": "ЖК Семейный", "facts": {"location": "Москва", "price": 10}}
    assert "novos.rooms" not in str(public)
    assert "contains" not in str(public)


def test_public_projection_keeps_ordinary_rooms_location_and_price():
    raw = {"schema_version": 1, "cards": [{
        "ref": "p1", "name": "ЖК Обычный",
        "facts": {},
        "evidence": {"location": "Москва", "max_price": 10, "rooms": "2-комнатные"},
    }], "attempts": [{"status": "ok"}]}

    result = parse_search_provider_result(raw, {"location": "Москва"})

    assert project_public_card(result.exact[0]) == {"ref": "p1", "name": "ЖК Обычный", "facts": {"location": "Москва", "price": 10, "rooms": "2-комнатные"}}


def test_response_caps_near_not_mixed_and_one_question():
    exact = tuple({"ref": str(i), "name": f"ЖК {i}", "facts": {"price": i}, "evidence": {}} for i in range(5))
    near = tuple({"ref": f"n{i}", "name": f"Рядом {i}", "facts": {}, "evidence": {}} for i in range(5))
    plan = V1ResponsePlan(V1AnswerKind.SEARCH_RESULTS, exact, near, cta="Хотите выбрать один из этих вариантов?")
    assert len(plan.exact_cards) == 3
    assert len(plan.near_cards) == 0
    text = render_response(plan)
    assert "Рядом" not in text
    assert text.count("?") == 1
    near_plan = V1ResponsePlan(V1AnswerKind.SEARCH_RESULTS, (), near, cta="Уточним бюджет?")
    assert len(near_plan.near_cards) == 2
