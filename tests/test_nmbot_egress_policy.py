from __future__ import annotations

import pytest

from scripts.nmbot_egress_policy import SAFE_CLIENT_FALLBACK_TEXT, contour_profile, guard_jivo_event, sanitize_client_text


def test_test_is_passthrough_and_missing_profile_defaults_to_test(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_CONTOUR_PROFILE", raising=False)
    text = "Технический черновик ```json```"
    assert contour_profile() == "TEST"
    assert sanitize_client_text(text).text == text


def test_prod_allows_normal_v6_text_and_blocks_unsafe_material() -> None:
    normal = sanitize_client_text("Подбор V6 готов, какой вариант обсудим?", profile="PROD")
    assert normal.blocked is False

    foreign = sanitize_client_text(f"Активна V{6 + 1}", profile="PROD")
    assert foreign.blocked and foreign.blocker_code == "runtime_version_marker"
    assert foreign.text == SAFE_CLIENT_FALLBACK_TEXT
    assert sanitize_client_text(f"Команда /start_{6 + 1}", profile="PROD").blocker_code == "start_version_marker"
    assert sanitize_client_text("Адрес http://127.0.0.1:8088", profile="PROD").blocker_code in {"infrastructure_marker", "internal_network_marker"}
    assert sanitize_client_text('{"ok":false,"trace":"x"}', profile="PROD").blocker_code == "json_diagnostic"


def test_jivo_guard_changes_only_prod_bot_message() -> None:
    event = {"event": "BOT_MESSAGE", "message": {"text": f"Активна V{6 + 1}"}}
    guarded, result = guard_jivo_event(event, profile="PROD")
    assert result and result.blocked
    assert guarded["message"]["text"] == SAFE_CLIENT_FALLBACK_TEXT
    assert event["message"]["text"] != SAFE_CLIENT_FALLBACK_TEXT

    inbound = {"event": "CLIENT_MESSAGE", "message": {"text": "unchanged"}}
    assert guard_jivo_event(inbound, profile="PROD") == (inbound, None)


def test_invalid_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="exactly TEST or PROD"):
        sanitize_client_text("answer", profile="prod-like")
