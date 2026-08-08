from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_v6_jivo_smoke.py"
spec = importlib.util.spec_from_file_location("nmbot_v6_jivo_smoke", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def _bot(**overrides):
    value = {
        "role": "bot",
        "response_model": {"status": "valid", "published": True},
        "response_composer": {"fallback_reason": None},
        "error_summary": {"status": "ok"},
        "runtime_summary": {"quality_blockers": []},
    }
    value.update(overrides)
    return value


def test_release_smoke_rejects_terminal_fallback_delivery() -> None:
    accepted, failures = module.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot(response_model={"status": "fallback", "published": False})],
    )
    assert not accepted
    assert failures == ["runtime_not_accepted"]


def test_release_smoke_rejects_validation_or_quality_failures() -> None:
    accepted, failures = module.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot(response_composer={"fallback_reason": "validation_failed"}, runtime_summary={"quality_blockers": ["search_without_cards"]})],
    )
    assert not accepted
    assert failures == ["composer_fallback", "quality_blocker"]


def test_release_smoke_accepts_published_result_only() -> None:
    accepted, failures = module.evaluate_release_smoke(
        query_result={"ok": True, "http_status": 200},
        events=[_bot()],
    )
    assert accepted
    assert failures == []


def test_chat_ref_is_one_way_and_journal_reader_is_shape_only(tmp_path: Path) -> None:
    chat_id = "test-chat"
    path = tmp_path / "dialogue_journal.jsonl"
    path.write_text(json.dumps({"chat_id_ref": module._chat_ref(chat_id), "role": "bot"}) + "\n", encoding="utf-8")
    assert module._read_chat_events(chat_id, journal=path) == [{"chat_id_ref": module._chat_ref(chat_id), "role": "bot"}]
    assert chat_id not in module._chat_ref(chat_id)
