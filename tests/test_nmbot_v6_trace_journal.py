import importlib.util
import json
from pathlib import Path


def _journal_module():
    path = Path(__file__).parents[1] / "scripts" / "dialogue_journal.py"
    spec = importlib.util.spec_from_file_location("dialogue_journal_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_journal_preserves_prompt2_context_without_secrets():
    journal = _journal_module()
    trace = {
        "schema_version": 1,
        "stages": [
            {"stage": "user", "status": "received"},
            {"stage": "prompt1", "status": "accepted", "model": "google/gemini-3.1-flash-lite-preview"},
            {"stage": "mcp", "status": "accepted", "server": "novostroym", "tool": "get_flat_info", "call_count": 1, "safe_projection": {"facts": []}},
            {"stage": "prompt2", "status": "accepted", "model": "google/gemini-3.1-flash-lite-preview"},
            {"stage": "bot_message", "status": "returned"},
        ],
        "prompt2_context": {
            "search_result": {"action": "search", "target": "new_search", "search_policy": "required", "requested_claims": ["installment_terms"]},
            "trusted_mcp": {"facts": [{"name": "Янила Форест", "price_range": "от 4.5 млн"}]},
            "answer_contract": {"requested_claims": ["installment_terms"], "missing_claims": ["installment_terms"], "allowed_claims": ["project_price"]},
        },
    }
    safe = journal._safe_v6_trace(trace)
    assert safe is not None
    assert safe["prompt2_context"]["answer_contract"]["missing_claims"] == ["installment_terms"]
    rendered = json.dumps(safe, ensure_ascii=False)
    assert "prompt2_context" in rendered
    assert "secret" not in rendered.lower()
