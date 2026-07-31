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
spec = importlib.util.spec_from_file_location("chat_tester_bot", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["chat_tester_bot"] = mod
spec.loader.exec_module(mod)


def _search_payload() -> str:
    return json.dumps(
        {
            "action": "search",
            "target": "new_search",
            "search_policy": "required",
            "facts": [
                {"name": "ЖК Точный", "location": "Сокол", "price_range": "до 18 млн", "rooms": 2},
                {"name": "ЖК Точный 2", "location": "Сокол", "price_range": "до 18 млн", "rooms": 2},
            ],
            "near": [
                {"name": "ЖК Почти", "location": "Динамо", "price_range": "до 18 млн", "why_close": "отличие: другая локация"},
            ],
            "missing": [],
            "params": {"location": ["Сокол"], "max_price": 18_000_000},
        },
        ensure_ascii=False,
    )


def test_extract_search_result_options_keeps_facts_and_near_separate() -> None:
    separated = mod._extract_search_result_options(_search_payload())

    assert [item["name"] for item in separated["facts"]] == ["ЖК Точный", "ЖК Точный 2"]
    assert [item["name"] for item in separated["near"]] == ["ЖК Почти"]
    assert all(item["source"] == "facts" for item in separated["facts"])
    assert all(item["source"] == "near" for item in separated["near"])
    assert [item["name"] for item in mod._extract_options(_search_payload(), include_near=False)] == ["ЖК Точный", "ЖК Точный 2"]
    assert [item["name"] for item in mod._extract_options(_search_payload())] == ["ЖК Точный", "ЖК Точный 2", "ЖК Почти"]


def test_stage_presenter_uses_only_exact_facts_when_near_exists(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        monkeypatch.setattr(mod, "STAGE_PRESENTER_ENABLED", True)

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            return _search_payload(), {"task_id": "task-search"}

        async def fake_chat(*args: Any, **kwargs: Any):
            return "LLM tried to answer", {}, 0, {}

        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)
        monkeypatch.setattr(client, "_chat_with_retry", fake_chat)

        response_text, _params, _search_meta, chat_meta = await client.ask("двушка на Соколе до 18")

        assert "ЖК Точный" in response_text
        assert "ЖК Точный 2" in response_text
        assert "ЖК Почти" not in response_text
        assert [item["name"] for item in chat_meta["_visible_options"]] == ["ЖК Точный", "ЖК Точный 2"]

    asyncio.run(scenario())


def test_overmind_ask_query_contains_hard_constraints_envelope_without_sensitive_values(monkeypatch) -> None:
    async def scenario() -> None:
        client = mod.OvermindClient()
        seen_queries: list[str] = []
        payload = {
            "action": "operator_contact",
            "clarification_question": "Оставьте номер телефона, и специалист уточнит детали.",
            "facts": [],
            "near": [],
            "missing": [],
            "params": {},
        }

        async def fake_ensure_session() -> None:
            return None

        async def fake_gateway(request_data: dict[str, Any], headers: dict[str, Any], timeout: int):
            seen_queries.append(request_data["query"])
            return json.dumps(payload, ensure_ascii=False), {"task_id": "task-hard"}

        monkeypatch.setattr(client, "ensure_session", fake_ensure_session)
        monkeypatch.setattr(client, "_run_gateway_request", fake_gateway)

        await client.ask(
            "на Соколе до 18",
            hard_constraints={
                "hard": {"location": ["Сокол"], "max_price": 18_000_000, "phone": "+7 999 123-45-67"},
                "preferences": {"purpose": "family", "token": "secret-token"},
                "raw_payload": {"client_id": "client-secret"},
            },
            params={"district": "newmsk", "max_price": 30_000_000, "rooms": ["1", "2"]},
        )

        query = seen_queries[0]
        assert "SEARCH_CONTRACT_ENVELOPE=" in query
        assert "search_hard_constraints_v1" in query
        assert "Сокол" in query
        assert "18000000" in query
        assert "+7 999" not in query
        assert "secret-token" not in query
        assert "client-secret" not in query
        assert "raw_payload" not in query
        assert '"location": ["Новая Москва"]' in query
        assert '"district": "newmsk"' not in query

        asyncio.run(scenario())


def test_newmsk_is_canonicalized_to_mcp_location_without_budget_or_rooms_loss() -> None:
    normalized = mod._normalize_hard_constraints(
        {
            "hard": {
                "district": "newmsk",
                "max_price": 30_000_000,
                "rooms": ["1", "2"],
            }
        }
    )

    hard = normalized["hard"]
    assert hard["location"] == ["Новая Москва"]
    assert "district" not in hard
    assert "locations" not in hard
    assert hard["max_price"] == 30_000_000
    assert hard["rooms"] == ["1", "2"]


def test_explicit_location_wins_and_district_is_not_sent() -> None:
    normalized = mod._normalize_hard_constraints(
        {
            "hard": {
                "district": "newmsk",
                "location": ["Новая Москва"],
                "max_price": 30_000_000,
                "rooms": ["1", "2"],
            }
        }
    )

    hard = normalized["hard"]
    assert hard["location"] == ["Новая Москва"]
    assert "district" not in hard
    assert "districts" not in hard
    assert hard["max_price"] == 30_000_000
    assert hard["rooms"] == ["1", "2"]

def test_compact_payload_declares_presentation_policy() -> None:
    payload = mod._compact_answer_facts_payload(_search_payload(), params={}, new_params={})

    assert payload["presentation_policy"]["facts"] == "primary exact matches only"
    assert "alternatives only" in payload["presentation_policy"]["near"]
    assert [item["name"] for item in payload["facts"]] == ["ЖК Точный", "ЖК Точный 2"]
    assert [item["name"] for item in payload["near"]] == ["ЖК Почти"]


def test_refresh_search_state_keeps_near_outside_primary_options() -> None:
    state: dict[str, Any] = {}

    mod._refresh_search_state(state, {"_response_text": _search_payload()})

    assert [item["name"] for item in state["last_options"]] == ["ЖК Точный", "ЖК Точный 2"]
    assert [item["name"] for item in state["last_near_options"]] == ["ЖК Почти"]
