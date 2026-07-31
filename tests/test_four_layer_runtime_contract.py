from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "chat_tester_bot.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("chat_tester_bot_four_layer", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["chat_tester_bot_four_layer"] = mod
spec.loader.exec_module(mod)


def _payload(*, facts: list[dict[str, Any]], near: list[dict[str, Any]] | None = None) -> str:
    return json.dumps({"action": "search", "facts": facts, "near": near or [], "params": {}}, ensure_ascii=False)


def test_underfilled_is_checked_after_hard_constraint_validation():
    payload = _payload(
        facts=[
            {"name": "Подходит", "location": "Москва", "price_min": 45_000_000},
            {"name": "Не подходит", "location": "Москва", "price_min": 70_000_000},
        ]
    )
    params = {"purpose": "life", "rooms": "1,2", "max_price": 60_000_000}
    hard_constraints = {"hard": {"max_price": 60_000_000}}

    assert not mod._is_underfilled_broad_search_result(payload, params=params)
    assert mod._is_underfilled_validated_search_result(
        payload,
        params=params,
        hard_constraints=hard_constraints,
    )


async def _run_client(
    monkeypatch,
    search_payload: str,
    *,
    runtime: bool,
    enforce: bool = False,
    chat_response: dict[str, Any] | None = None,
    query: str = "ищу +7 999 123-45-67 на Соколе до 18 млн token=secret-token",
    hard_constraints: dict[str, Any] | None = None,
):
    client = mod.OvermindClient()
    calls: list[dict[str, Any]] = []

    async def fake_ensure_session() -> None:
        return None

    async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
        return search_payload, {"task_id": "task-search"}

    async def fake_chat(request_data: dict[str, Any], headers: dict[str, Any], timeout: int, uid: int):
        calls.append(request_data)
        response = chat_response or {
            "response": "Подходит ЖК Точный.",
            "params": {"ok": True},
            "visible_options": [{"name": "ЖК Точный", "option_id": "fact_1"}],
            "final_question": "Какой вариант хотите обсудить?",
        }
        text, params, _buttons, visible = client._parse_chat_json(json.dumps(response, ensure_ascii=False))
        return text, params, 0, {"_visible_options": visible}

    monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENABLED", runtime)
    monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENFORCE", enforce)
    monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
    monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
    monkeypatch.setattr(client, "_chat_with_retry", fake_chat)
    result = await client.ask(
        query,
        hard_constraints=hard_constraints or {"hard": {"location": ["Сокол"], "max_price": 18_000_000}},
    )
    return result, calls


def test_flag_off_uses_legacy_main_answer(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[{"name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000}]),
            runtime=False,
        )
        assert calls and calls[0]["_payload_stage"] == "main_answer"
        assert "four_layer_status" not in result[2]

    asyncio.run(scenario())


def test_router_profiles_flag_off_keeps_search_prompt_unchanged(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        search_calls: list[dict[str, Any]] = []
        chat_calls: list[dict[str, Any]] = []

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            search_calls.append(request_data)
            return _payload(facts=[{"name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000}]), {"task_id": "task-search"}

        async def fake_chat(request_data: dict[str, Any], headers: dict[str, Any], timeout: int, uid: int):
            chat_calls.append(request_data)
            return "Ответ", {}, 0, {}

        monkeypatch.setattr(mod, "ROUTER_PROFILES_ENABLED", False)
        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        await client.ask("семейная ипотека", search_profile={"profile": "family", "overlays": ["family", "mortgage"]})

        assert search_calls[0]["system_prompt"] == mod.SEARCH_SYSTEM_PROMPT
        assert chat_calls[0]["_payload_stage"] == "main_answer"

    asyncio.run(scenario())


def test_router_profiles_flag_on_composes_family_and_mortgage_overlays(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        search_calls: list[dict[str, Any]] = []

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            search_calls.append(request_data)
            return _payload(facts=[{"name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000}]), {"task_id": "task-search"}

        async def fake_chat(request_data: dict[str, Any], headers: dict[str, Any], timeout: int, uid: int):
            return "Ответ", {}, 0, {}

        monkeypatch.setattr(mod, "ROUTER_PROFILES_ENABLED", True)
        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        await client.ask("семейная ипотека", search_profile={"profile": "family", "overlays": ["family", "mortgage", "bad/inject"]})

        prompt = search_calls[0]["system_prompt"]
        assert "Профиль MCP-поиска: family" in prompt
        assert "Профиль MCP-поиска: mortgage" in prompt
        assert "bad/inject" not in prompt

    asyncio.run(scenario())


def test_shadow_valid_facts_uses_legacy_and_records_safe_diagnostics(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"id": "raw-secret-1", "name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000},
                    {"id": "raw-secret-2", "name": "ЖК Дорогой", "location": "Сокол", "price_min": 25_000_000},
                ]
            ),
            runtime=True,
            enforce=False,
        )
        _response, _params, search_meta, _chat_meta = result
        assert len(calls) == 1
        assert calls[0]["_payload_stage"] == "main_answer"
        assert search_meta["four_layer_enabled"] is True
        assert search_meta["four_layer_mode"] == "shadow"
        assert search_meta["four_layer_status"] == "ok"
        assert search_meta["four_layer_facts_count"] == 2
        assert search_meta["four_layer_near_count"] == 0
        assert search_meta["four_layer_matched_count"] == 1
        assert search_meta["four_layer_rejected_count"] == 1
        assert search_meta["four_layer_unknown_count"] == 0
        assert search_meta["resolved_action"] == "search"
        safe_meta_text = json.dumps(search_meta, ensure_ascii=False)
        assert "ЖК Точный" not in safe_meta_text
        assert "ЖК Дорогой" not in safe_meta_text
        assert "raw-secret" not in safe_meta_text
        assert "+7 999" not in safe_meta_text
        assert "secret-token" not in safe_meta_text

    asyncio.run(scenario())


def test_shadow_all_known_rejected_uses_legacy_and_logs_rejected_count(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[{"name": "ЖК Дорогой", "location": "Сокол", "price_min": 25_000_000}]),
            runtime=True,
            enforce=False,
        )
        assert calls[0]["_payload_stage"] == "main_answer"
        assert result[2]["four_layer_mode"] == "shadow"
        assert result[2]["four_layer_status"] == "no_exact_matches"
        assert result[2]["four_layer_matched_count"] == 0
        assert result[2]["four_layer_rejected_count"] == 1
        assert "ЖК Дорогой" not in json.dumps(result[2], ensure_ascii=False)

    asyncio.run(scenario())


def _question_count(text: str) -> int:
    return text.count("?")


def test_valid_structured_facts_render_deterministically_and_hide_rejected(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"id": "raw-secret-1", "name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000},
                    {"id": "raw-secret-2", "name": "ЖК Дорогой", "location": "Сокол", "price_min": 25_000_000},
                ]
            ),
            runtime=True,
            enforce=True,
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert "1. ЖК Точный" in response
        assert "ЖК Дорогой" not in response
        assert _question_count(response) == 1
        assert search_meta["four_layer_status"] == "ok"
        assert search_meta["four_layer_mode"] == "enforce"
        assert search_meta["four_layer_matched_count"] == 1
        assert search_meta["four_layer_rejected_count"] == 1
        assert "_response_text" not in search_meta
        assert chat_meta["_four_layer_presenter"]["llm_called"] is False
        assert [item["name"] for item in chat_meta["_visible_options"]] == ["ЖК Точный"]

    asyncio.run(scenario())


def test_family_payload_renders_three_fact_based_cards_without_llm(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {
                        "name": "ЖК Семейный",
                        "location": "Сокол",
                        "price_min": 15_000_000,
                        "finishing": "есть отделка",
                        "ready": "сдан",
                        "parks": "парк Берёзовая роща",
                        "schools": "школа во дворе",
                    },
                    {
                        "name": "ЖК Школьный",
                        "location": "Сокол",
                        "price_min": 16_000_000,
                        "kindergartens": "детский сад рядом",
                        "infrastructure": "магазины и кружки на первых этажах",
                    },
                    {
                        "name": "ЖК Парковый",
                        "location": "Сокол",
                        "price_min": 17_000_000,
                        "parks": "лесопарк через дорогу",
                        "playgrounds": "детские площадки",
                    },
                ]
            ),
            runtime=True,
            enforce=True,
            query="подбери для семьи с детьми на Соколе",
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert response.count("\n1. ЖК Семейный") == 1
        assert response.count("\n2. ЖК Школьный") == 1
        assert response.count("\n3. ЖК Парковый") == 1
        assert "школа во дворе" in response
        assert "детский сад рядом" in response
        assert "магазины и кружки" in response
        assert "лесопарк через дорогу" in response
        assert "поликлиник" not in response.lower()
        assert _question_count(response) == 1
        assert search_meta["four_layer_matched_count"] == 3
        assert chat_meta["_four_layer_presenter"]["llm_called"] is False

    asyncio.run(scenario())


def test_room_request_accepts_nested_apartment_types_and_ads_evidence(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {
                        "name": "ЖК Апарт",
                        "location": "Сокол",
                        "price_min": 16_000_000,
                        "apartment_types": [{"rooms": [1, 2], "area": "42-63"}],
                    },
                    {
                        "name": "ЖК Ads",
                        "location": "Сокол",
                        "price_min": 17_000_000,
                        "ads": [{"lot": {"rooms": "2-к"}, "price": 17_000_000}],
                    },
                    {
                        "name": "ЖК Только студии",
                        "location": "Сокол",
                        "price_min": 12_000_000,
                        "rooms": "студия",
                    },
                ]
            ),
            runtime=True,
            enforce=True,
            query="ищу 1-2 комнатные на Соколе до 18 млн",
            hard_constraints={"hard": {"location": ["Сокол"], "max_price": 18_000_000, "rooms": "1,2"}},
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert "ЖК Апарт" in response
        assert "ЖК Ads" in response
        assert "3. ЖК Только студии" not in response
        assert search_meta["four_layer_matched_count"] == 2
        assert search_meta["four_layer_room_unconfirmed_count"] == 1
        assert [item["name"] for item in chat_meta["_visible_options"]] == ["ЖК Апарт", "ЖК Ads"]

    asyncio.run(scenario())


def test_room_request_excludes_missing_evidence_and_adds_concise_note(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "ЖК Подтверждённый", "location": "Сокол", "price_min": 16_000_000, "room_types": "2"},
                    {"name": "ЖК Без комнат", "location": "Сокол", "price_min": 15_000_000, "description": "есть двушки"},
                ]
            ),
            runtime=True,
            enforce=True,
            query="нужна двушка на Соколе до 18 млн",
            hard_constraints={"hard": {"location": ["Сокол"], "max_price": 18_000_000, "rooms": 2}},
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert "1. ЖК Подтверждённый" in response
        assert "ЖК Без комнат" in response
        assert "не показываю" in response
        assert "передать запрос менеджеру?" in response
        assert "телефон" not in response.lower()
        for forbidden in ("mcp", "структурн", "карточ", "evidence", "комнатност"):
            assert forbidden not in response.lower()
        assert _question_count(response) == 1
        assert search_meta["four_layer_matched_count"] == 1
        assert search_meta["four_layer_room_unconfirmed_count"] == 1
        assert [item["name"] for item in chat_meta["_visible_options"]] == ["ЖК Подтверждённый"]

    asyncio.run(scenario())


def test_room_request_zero_evidence_offers_manager_without_phone_collection(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "ЖК Проза", "location": "Сокол", "price_min": 15_000_000, "description": "в продаже есть 2-комнатные"},
                    {"name": "ЖК Текст", "location": "Сокол", "price_min": 16_000_000, "why_close": "модель написала про двушки"},
                ]
            ),
            runtime=True,
            enforce=True,
            query="нужна двушка на Соколе до 18 млн",
            hard_constraints={"hard": {"location": ["Сокол"], "max_price": 18_000_000, "rooms": "2"}},
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert "не смогла надёжно подтвердить наличие и цену" in response
        assert "не значит, что таких квартир точно нет" in response
        assert "передать запрос менеджеру?" in response
        assert "телефон" not in response.lower()
        for forbidden in ("mcp", "структурн", "карточ", "evidence", "комнатност"):
            assert forbidden not in response.lower()
        assert _question_count(response) == 1
        assert search_meta["four_layer_matched_count"] == 0
        assert search_meta["four_layer_room_unconfirmed_count"] == 2
        assert chat_meta["_visible_options"] == []

    asyncio.run(scenario())


def test_room_empty_hard_search_recovers_with_broad_structured_evidence(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        search_calls: list[dict[str, Any]] = []
        chat_calls: list[dict[str, Any]] = []
        first_empty = json.dumps({"action": "search", "facts": [], "near": [], "params": {"rooms": "2"}}, ensure_ascii=False)
        broad_payload = _payload(
            facts=[
                {"name": "ЖК Двушка", "location": "Москва", "district": "msk", "price_min": 15_000_000, "rooms": "1, 2, 3"},
                {"name": "ЖК Студии", "location": "Москва", "district": "msk", "price_min": 13_000_000, "rooms": "студии"},
                {"name": "ЖК Проза", "location": "Москва", "district": "msk", "price_min": 14_000_000, "description": "есть двушки"},
            ]
        )

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            search_calls.append(request_data)
            if len(search_calls) == 1:
                return first_empty, {"task_id": "task-hard"}
            return broad_payload, {"task_id": "task-broad"}

        async def fake_chat(request_data: dict[str, Any], headers: dict[str, Any], timeout: int, uid: int):
            chat_calls.append(request_data)
            raise AssertionError("enforced room recovery must use deterministic presenter")

        monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENABLED", True)
        monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENFORCE", True)
        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        response, _params, search_meta, chat_meta = await client.ask(
            "Двушка в москве под инвестицию",
            params={"purpose": "investment", "rooms": "2", "location": "Москва"},
            hard_constraints={"hard": {"location": ["Москва"], "rooms": "2", "purpose": "investment"}},
        )

        recovery_calls = [call for call in search_calls if call.get("_payload_stage") == "main_search_room_evidence_recovery"]
        assert len(recovery_calls) == 1
        recovery_call = recovery_calls[0]
        assert '"rooms"' not in recovery_call["query"].split("SEARCH_CONTRACT_ENVELOPE=", 1)[1].split("\n", 1)[0]
        assert '"rooms"' not in recovery_call["query"]
        assert "Двушка в москве под инвестицию" not in recovery_call["query"]
        assert "не применяй нужный формат как фильтр" in recovery_call["query"].lower()
        assert "до 5 ЖК" in recovery_call["query"]
        assert any(call.get("_payload_stage") == "option_enrichment_search" for call in search_calls)
        assert "ЖК Двушка" in response
        assert "2. ЖК Студии" not in response
        assert "2. ЖК Проза" not in response
        assert "ЖК Проза" in response
        assert "не показываю" in response
        assert chat_calls == []
        assert search_meta["_room_evidence_recovery"] is True
        assert search_meta["four_layer_matched_count"] == 1
        assert [item["name"] for item in chat_meta["_visible_options"]] == ["ЖК Двушка"]

    asyncio.run(scenario())


def test_room_broad_recovery_does_not_treat_prose_as_exact(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        search_calls: list[dict[str, Any]] = []
        first_empty = json.dumps({"action": "search", "facts": [], "near": [], "params": {"rooms": "2"}}, ensure_ascii=False)
        broad_payload = _payload(
            facts=[
                {"name": "ЖК Только текст", "location": "Москва", "district": "msk", "price_min": 14_000_000, "description": "есть 2-комнатные"},
            ]
        )

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            search_calls.append(request_data)
            return (first_empty, {"task_id": "task-hard"}) if len(search_calls) == 1 else (broad_payload, {"task_id": "task-broad"})

        monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENABLED", True)
        monkeypatch.setattr(mod, "FOUR_LAYER_RUNTIME_ENFORCE", True)
        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)

        response, _params, search_meta, chat_meta = await client.ask(
            "Двушка в москве",
            params={"purpose": "search", "rooms": "2", "location": "Москва"},
            hard_constraints={"hard": {"location": ["Москва"], "rooms": "2"}},
        )

        assert "ЖК Только текст" not in response.split("\n\n", 1)[0]
        assert "не смогла надёжно подтвердить наличие и цену" in response
        for forbidden in ("mcp", "структурн", "карточ", "evidence", "комнатност"):
            assert forbidden not in response.lower()
        assert search_meta["four_layer_matched_count"] == 0
        assert chat_meta["_visible_options"] == []

    asyncio.run(scenario())


def test_no_room_constraint_preserves_normal_h4_list(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "ЖК Первый", "location": "Сокол", "price_min": 15_000_000},
                    {"name": "ЖК Второй", "location": "Сокол", "price_min": 16_000_000},
                    {"name": "ЖК Третий", "location": "Сокол", "price_min": 17_000_000},
                ]
            ),
            runtime=True,
            enforce=True,
            query="ищу на Соколе до 18 млн",
            hard_constraints={"hard": {"location": ["Сокол"], "max_price": 18_000_000}},
        )
        response, _params, search_meta, chat_meta = result
        assert calls == []
        assert "ЖК Первый" in response and "ЖК Второй" in response and "ЖК Третий" in response
        assert "передать запрос менеджеру?" not in response
        assert search_meta["four_layer_matched_count"] == 3
        assert search_meta["four_layer_room_unconfirmed_count"] == 0
        assert len(chat_meta["_visible_options"]) == 3

    asyncio.run(scenario())


def test_investment_renderer_uses_natural_confirmed_facts_without_profit_claims(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "Кронштадтский 9", "location": "Головинский", "metro": "Водный стадион", "price_min": 18_000_000, "finishing": "есть отделка", "ready": "сдан"},
                    {"name": "ЖК Второй", "location": "Сокол", "price_min": 19_000_000, "finishing": "есть отделка"},
                ]
            ),
            runtime=True,
            enforce=True,
            query="что взять под инвестиции",
            hard_constraints={"hard": {"purpose": "investment"}},
        )
        response, _params, _search_meta, _chat_meta = result
        low = response.lower().replace("ё", "е")
        assert calls == []
        assert "проверяемые опоры" not in low
        assert not any(word in low for word in ("доходност", "окупаем", "ликвид", "рост цен", "прибыл"))
        assert "отдел" in low or "готов" in low or "метро" in low

    asyncio.run(scenario())


def test_prepare_response_strips_unsupported_growth_income_claims() -> None:
    response = mod._prepare_response_text(
        "Этот ЖК ликвидный и даст рост цены. Цена от 18 млн. Хотите разобрать подробнее?"
    )
    low = response.lower().replace("ё", "е")
    assert "ликвид" not in low
    assert "рост цен" not in low
    assert "18 млн" in response


def test_sparse_facts_render_only_present_fields_without_inventing_family_claims(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "ЖК Лаконичный", "location": "Сокол", "price_min": 14_000_000},
                    {"name": "ЖК Минимум", "location": "Сокол", "price_min": 15_000_000},
                    {"name": "ЖК Только Цена", "location": "Сокол", "price_min": 16_000_000},
                ]
            ),
            runtime=True,
            enforce=True,
            query="подбери для семьи с детьми на Соколе",
        )
        response, _params, _search_meta, chat_meta = result
        assert calls == []
        assert response.count("\n1. ЖК Лаконичный") == 1
        assert response.count("\n2. ЖК Минимум") == 1
        assert response.count("\n3. ЖК Только Цена") == 1
        absent_words = "школ детский сад парк поликлиник клиник двор площадк инфраструктур отделк сдан".split()
        low = response.lower().replace("ё", "е")
        assert not any(word in low for word in absent_words)
        assert "14 млн" in response
        assert _question_count(response) == 1
        assert chat_meta["_four_layer_presenter"]["llm_called"] is False

    asyncio.run(scenario())


def test_all_known_rejected_gets_zero_exact_presenter_without_rejected_values(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[{"name": "ЖК Дорогой", "location": "Сокол", "price_min": 25_000_000}]),
            runtime=True,
            enforce=True,
            chat_response={"response": "Точных совпадений нет.", "params": {}, "visible_options": [], "final_question": "Что смягчим: локацию или бюджет?"},
        )
        assert calls[0]["_payload_stage"] == "four_layer_presenter"
        assert "ЖК Дорогой" not in calls[0]["query"]
        assert '"matched":[]' in calls[0]["query"]
        assert result[2]["four_layer_status"] == "no_exact_matches"
        assert result[2]["four_layer_matched_count"] == 0
        assert result[2]["four_layer_rejected_count"] == 1
        assert result[3]["_visible_options"] == []

    asyncio.run(scenario())


def test_unknown_hard_fact_shadow_is_safe_and_legacy_continues(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[{"name": "ЖК Без цены", "location": "Сокол", "id": "raw-secret-unknown"}]),
            runtime=True,
            enforce=False,
        )
        assert calls[0]["_payload_stage"] == "main_answer"
        assert result[2]["four_layer_status"] == "fallback:unknown_hard_fact"
        assert result[2]["four_layer_mode"] == "shadow"
        assert result[2]["four_layer_unknown_count"] == 1
        assert "ЖК Без цены" not in json.dumps(result[2], ensure_ascii=False)
        assert "raw-secret-unknown" not in json.dumps(result[2], ensure_ascii=False)

    asyncio.run(scenario())


def test_near_only_shadow_is_safe_and_legacy_continues(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[], near=[{"name": "ЖК Почти", "location": "Динамо", "price_min": 17_000_000}]),
            runtime=True,
            enforce=False,
        )
        assert calls[0]["_payload_stage"] == "main_answer"
        assert result[2]["four_layer_status"] == "no_exact_matches"
        assert result[2]["four_layer_mode"] == "shadow"
        assert result[2]["four_layer_near_count"] == 1
        assert "ЖК Почти" not in json.dumps(result[2], ensure_ascii=False)

    asyncio.run(scenario())


def test_near_only_enforce_uses_restricted_no_match_presenter(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(facts=[], near=[{"name": "ЖК Почти", "location": "Динамо", "price_min": 17_000_000}]),
            runtime=True,
            enforce=True,
            chat_response={"response": "Точных совпадений нет.", "params": {}, "visible_options": [], "final_question": "Какое условие можно смягчить?"},
        )
        assert calls[0]["_payload_stage"] == "four_layer_presenter"
        assert result[2]["four_layer_status"] == "no_exact_matches"
        assert result[3]["_visible_options"] == []
        assert "ЖК Почти" not in json.dumps(calls[0], ensure_ascii=False)

    asyncio.run(scenario())


def test_enforced_matched_list_does_not_call_presenter_even_if_chat_stub_would_invent(monkeypatch) -> None:
    async def scenario() -> None:
        result, calls = await _run_client(
            monkeypatch,
            _payload(
                facts=[
                    {"name": "ЖК Точный", "location": "Сокол", "price_min": 17_000_000},
                    {"name": "ЖК Дорогой", "location": "Сокол", "price_min": 25_000_000},
                ]
            ),
            runtime=True,
            enforce=True,
            chat_response={"response": "Попробуйте ЖК Дорогой.", "params": {}, "visible_options": [{"name": "ЖК Дорогой"}], "final_question": "Что выберете?"},
        )
        assert calls == []
        assert [item["name"] for item in result[3]["_visible_options"]] == ["ЖК Точный"]

    asyncio.run(scenario())


def test_enforced_runtime_loads_presenter_v2() -> None:
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert '_load_prompt("four_layer_presenter_v2")' in source
    assert '_load_prompt("four_layer_presenter_v1")' not in source


def test_presenter_semantic_gate_requires_matched_label_and_has_safe_fallback() -> None:
    context = {
        "matched": [
            {"option_id": "fact_1", "label": "ЖК Точный", "facts": {"location": "Сокол", "price_min": 17_000_000}}
        ]
    }
    assert mod._four_layer_presenter_mentions_matched("Подходит ЖК Точный. Что разобрать?", context) is True
    assert mod._four_layer_presenter_mentions_matched("Есть один вариант:. Что разобрать?", context) is False
    rendered = mod._four_layer_deterministic_presenter(context)
    assert "ЖК Точный" in rendered
    assert "Сокол" in rendered
    assert "17 млн" in rendered


def test_broad_moscow_hard_location_matches_only_structured_msk_district() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Гольяново", "location": "Гольяново", "district": "msk", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Москва"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_status"] == "ok"
    assert diag["four_layer_matched_count"] == 1
    assert visible[0]["location"] == "Гольяново"
    assert decision_context["matched"][0]["facts"]["location"] == "Гольяново"
    assert "geo_evidence" not in decision_context["matched"][0]


def test_broad_moscow_hard_location_logs_structured_mo_mismatch_without_drop() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Химки", "location": "Химки", "district": "mo", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Москва"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_status"] == "ok"
    assert diag["four_layer_matched_count"] == 1
    assert diag["four_layer_rejected_count"] == 0
    assert diag["four_layer_geo_mismatch_count"] == 1
    assert diag["four_layer_geo_diagnostics"][0]["status"] == "broad_scope_mismatch"
    assert visible[0]["location"] == "Химки"


def test_specific_metro_hard_location_matches_structured_metro() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Войковская", "location": "Головинский", "district": "msk", "metro": "Войковская", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Войковская"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_status"] == "ok"
    assert diag["four_layer_matched_count"] == 1
    assert visible[0]["location"] == "Головинский"


def test_specific_area_hard_location_logs_wrong_area_without_drop() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Хорошевский", "location": "Хорошевский", "district": "msk", "metro": "Полежаевская", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Сокол"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_status"] == "ok"
    assert diag["four_layer_matched_count"] == 1
    assert diag["four_layer_rejected_count"] == 0
    assert diag["four_layer_geo_mismatch_count"] == 1
    assert diag["four_layer_geo_diagnostics"][0]["status"] == "specific_mismatch"
    assert visible[0]["location"] == "Хорошевский"


def test_geo_post_filter_keeps_lefortovo_for_center_cao_zao_requests() -> None:
    for expected in (["центр"], ["ЦАО"], ["ЗАО"]):
        decision_context, diag, visible = mod._four_layer_decision_context(
            _payload(facts=[{"name": "ЖК Символ", "location": "Лефортово", "district": "msk", "price_min": 17_000_000}]),
            hard_constraints={"hard": {"location": expected}},
        )

        assert decision_context is not None
        assert diag["four_layer_status"] == "ok"
        assert diag["four_layer_matched_count"] == 1
        assert diag["four_layer_rejected_count"] == 0
        assert visible[0]["location"] == "Лефортово"
        assert decision_context["matched"][0]["label"] == "ЖК Символ"


def test_explicit_geo_mismatch_is_retained_with_safe_diagnostic_metadata() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Химки", "location": "Химки", "district": "mo", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Москва"]}},
    )

    assert decision_context is not None
    assert [item["label"] for item in decision_context["matched"]] == ["ЖК Химки"]
    assert [item["name"] for item in visible] == ["ЖК Химки"]
    assert diag["four_layer_geo_mismatch_count"] == 1
    assert diag["four_layer_geo_diagnostics"][0]["status"] == "broad_scope_mismatch"
    assert diag["four_layer_geo_diagnostics"][0]["source_ref"] == "search:facts:1"
    assert "ЖК Химки" not in json.dumps(diag["four_layer_geo_diagnostics"], ensure_ascii=False)


def test_geo_diagnostics_preserve_facts_and_near_counts_and_fact_ordering() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(
            facts=[
                {"name": "ЖК Первый", "location": "Химки", "district": "mo", "price_min": 12_000_000},
                {"name": "ЖК Второй", "location": "Лефортово", "district": "msk", "price_min": 13_000_000},
            ],
            near=[{"name": "ЖК Альтернатива", "location": "Динамо", "price_min": 11_000_000}],
        ),
        hard_constraints={"hard": {"location": ["Москва"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_facts_count"] == 2
    assert diag["four_layer_near_count"] == 1
    assert diag["four_layer_matched_count"] == 2
    assert [item["label"] for item in decision_context["matched"]] == ["ЖК Первый", "ЖК Второй"]
    assert [item["name"] for item in visible] == ["ЖК Первый", "ЖК Второй"]
    assert diag["four_layer_geo_mismatch_count"] == 1


def test_broad_moscow_without_structured_district_trusts_exact_fact_area() -> None:
    decision_context, diag, visible = mod._four_layer_decision_context(
        _payload(facts=[{"name": "ЖК Без округа", "location": "Гольяново", "price_min": 12_000_000}]),
        hard_constraints={"hard": {"location": ["Москва"]}},
    )

    assert decision_context is not None
    assert diag["four_layer_status"] == "ok"
    assert diag["four_layer_matched_count"] == 1
    assert diag["four_layer_rejected_count"] == 0
    assert visible[0]["location"] == "Гольяново"


def test_search_prompt_requires_region_code_separate_from_location() -> None:
    prompt = (Path(mod.__file__).resolve().parents[1] / "prompts" / "search_v1.txt").read_text(encoding="utf-8")

    assert "name, location, district, price_range" in prompt
    assert "обязательный региональный код MCP `msk|mo|newmsk`" in prompt
    assert "Не заменяй `district` названием района" in prompt
