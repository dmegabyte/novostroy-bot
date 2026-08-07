import asyncio
import json
import subprocess
import sys
from pathlib import Path

from nmbot_v2.card_normalizer import normalize_card
from nmbot_v2.contracts import OptionCard, SearchResult, SemanticPlan
from nmbot_v2.inventory_gate import project_broad_inventory
from nmbot_v2.search_contract import V2SearchRequest, build_query, lot_matches_hard_constraints
from nmbot_v2.search_enrichment import build_option_enrichment_request
from scripts import nmbot_runtime_adapter as adapter_module


def _card(name, *ads):
    return normalize_card({"name": name, "ads": list(ads)})


def test_exact_enrichment_keeps_facts_and_uses_named_object_mode():
    request = build_option_enrichment_request(
        OptionCard(name="ЖК Тест"), "life", facts_needed=("lot_examples", "unknown", "parking")
    )

    assert request.count == 1
    assert request.search_mode == "named_object"
    assert request.facts_needed == ("lot_examples", "parking")

    query = build_query(request)
    assert "ads.id" in query
    assert "ads.state" in query
    assert "ads.status" in query


def test_normalizer_preserves_lot_state_at_end_of_compatible_contract():
    card = _card("Активный", {"id": 1, "state": 2, "status": 2})

    assert card.lot_examples[0].state == 2
    assert card.lot_examples[0].status == 2


def test_normalized_lot_preserves_supported_ready_delivered_and_renovation_for_gate():
    card = _card("Активный", {
        "id": 1, "state": 2, "status": 2, "area": 54, "renovation": "с отделкой", "ready": "сдан", "delivered": True,
    })
    lot = card.lot_examples[0]

    assert lot.ready == "сдан"
    assert lot.delivered is True
    assert lot.renovation == "с отделкой"
    assert lot_matches_hard_constraints(
        {"ads": [lot.__dict__]},
        {"area_min_m2": 50, "ready": "delivered", "finishing": True},
    )


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_inventory_gate.py"


def test_inventory_projection_requires_active_in_sale_lot_with_valid_id():
    active = _card("Активный", {"id": 1, "state": 2, "status": 2})
    booked = _card("Бронь", {"id": 2, "state": 2, "status": 1})
    inactive = _card("Неактивный", {"id": 3, "state": 1, "status": 2})
    missing = _card("Без id", {"id": "", "state": 2, "status": 2})

    projected, trace = project_broad_inventory(
        SearchResult(facts=(active, booked, inactive, missing), missing=("ads",))
    )

    assert projected.facts == (active,)
    assert projected.missing == ("ads",)
    assert trace == {"source_count": 4, "visible_count": 1, "excluded_unqualified_count": 3}


def test_inventory_projection_applies_lot_hard_to_the_same_active_lot():
    matching = _card("Подходит", {"id": 1, "state": 2, "status": 2, "rooms": 2, "fullprice": 10_000_000})
    split_evidence = _card(
        "Разные лоты",
        {"id": 2, "state": 2, "status": 2, "rooms": 2, "fullprice": 12_000_000},
        {"id": 3, "state": 2, "status": 2, "rooms": 1, "fullprice": 9_000_000},
    )

    projected, trace = project_broad_inventory(
        SearchResult(facts=(matching, split_evidence)),
        lot_hard={"rooms": 2, "max_price": 10_000_000},
    )

    assert projected.facts == (matching,)
    assert trace == {"source_count": 2, "visible_count": 1, "excluded_unqualified_count": 1}


def test_runtime_inventory_gate_honors_env_and_emits_aggregate_only_trace(monkeypatch):
    active = _card("Секретное имя", {"id": 1, "state": 2, "status": 2})
    booked = _card("Ещё одно имя", {"id": 2, "state": 2, "status": 1})
    source = SearchResult(facts=(active, booked))

    monkeypatch.setenv("NMBOT_BROAD_INVENTORY_GATE_ENABLED", "off")
    unchanged, disabled = adapter_module._apply_broad_inventory_gate(source)
    assert unchanged == source
    assert disabled == {
        "stage": "broad_inventory_gate",
        "enabled": False,
        "status": "disabled",
        "source_count": 2,
        "visible_count": 2,
        "excluded_unqualified_count": 0,
    }

    monkeypatch.setenv("NMBOT_BROAD_INVENTORY_GATE_ENABLED", "yes")
    filtered, trace = adapter_module._apply_broad_inventory_gate(source)
    assert filtered.facts == (active,)
    assert trace == {
        "stage": "broad_inventory_gate",
        "enabled": True,
        "status": "filtered",
        "source_count": 2,
        "visible_count": 1,
        "excluded_unqualified_count": 1,
    }
    assert "Секретное имя" not in json.dumps(trace, ensure_ascii=False)
    assert "Ещё одно имя" not in json.dumps(trace, ensure_ascii=False)


def test_inventory_gate_cli_status_enable_disable_and_dry_run(tmp_path):
    env_file = tmp_path / ".env"
    command = [sys.executable, str(SCRIPT), "--env-file", str(env_file)]

    default = subprocess.run([*command, "status"], check=True, capture_output=True, text=True)
    assert default.stdout.strip() == "default"

    dry = subprocess.run([*command, "--dry-run", "enable"], check=True, capture_output=True, text=True)
    assert dry.stdout.strip() == "dry-run enabled"
    assert not env_file.exists()

    env_file.write_text("OTHER_KEY=keep\n", encoding="utf-8")
    enabled = subprocess.run([*command, "enable"], check=True, capture_output=True, text=True)
    assert enabled.stdout.strip() == "added enabled"
    assert env_file.read_text(encoding="utf-8") == "OTHER_KEY=keep\n\nNMBOT_BROAD_INVENTORY_GATE_ENABLED=1\n"
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8") == "OTHER_KEY=keep\n"
    assert subprocess.run([*command, "status"], check=True, capture_output=True, text=True).stdout.strip() == "enabled"

    disabled = subprocess.run([*command, "disable"], check=True, capture_output=True, text=True)
    assert disabled.stdout.strip() == "updated disabled"
    assert "OTHER_KEY=keep" in env_file.read_text(encoding="utf-8")
    assert subprocess.run([*command, "status"], check=True, capture_output=True, text=True).stdout.strip() == "disabled"


def test_broad_timeout_drops_unverified_base_card(monkeypatch):
    source = SearchResult(facts=(_card("Базовый", {"id": 1, "state": 2, "status": 2}),))

    async def unenriched(*args, **kwargs):
        return source, {"applied": False, "count": 1, "applied_count": 0, "items": [{"idx": 1, "applied": False, "skipped": "TimeoutError"}]}

    instance = adapter_module._OvermindSearchAdapter({"overmind_client": object()})
    monkeypatch.setattr(adapter_module, "enrich_search_result_top_options", unenriched)

    result = asyncio.run(
        instance._enrich_shortlist_top_options(
            object(), source, V2SearchRequest(search_goal={"entity_type": "new_building_flat"})
        )
    )

    assert result.facts == ()
    assert result.missing == ()


def test_broad_backfill_enriches_new_candidates_and_gates_them(monkeypatch):
    contract = V2SearchRequest(search_goal={"entity_type": "new_building_flat"}, count=2)
    calls = []

    async def fake_search_once(client, retrieval_contract, *, prompt, validation_contract=None):
        calls.append(retrieval_contract)
        return SearchResult(facts=(OptionCard(name=f"ЖК {len(calls)}"),)), ()

    async def fake_enrichment(client, result, current_contract, *, facts_needed=None):
        assert facts_needed == ("lot_examples",)
        if len(calls) == 1:
            return SearchResult()
        return SearchResult(facts=(_card("ЖК 2", {"id": 2, "state": 2, "status": 2}),))

    monkeypatch.setattr(adapter_module, "build_search_request", lambda *args: contract)
    monkeypatch.setattr(adapter_module, "build_candidate_retrieval_request", lambda value: value)
    instance = adapter_module._OvermindSearchAdapter({"overmind_client": object()})
    instance._search_once = fake_search_once
    instance._enrich_shortlist_top_options = fake_enrichment

    result = asyncio.run(instance.search(SemanticPlan(operation="search"), adapter_module.ConversationState()))

    assert len(calls) == 2
    assert result.facts == (_card("ЖК 2", {"id": 2, "state": 2, "status": 2}),)
