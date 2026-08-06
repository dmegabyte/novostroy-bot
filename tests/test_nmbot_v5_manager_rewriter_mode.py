from __future__ import annotations

from scripts.nmbot_v5_manager_rewriter_mode import build_set_command, validate_mode


def test_validate_mode_is_fail_closed() -> None:
    assert validate_mode("SHADOW") == "shadow"
    assert validate_mode("publish") == "publish"
    try:
        validate_mode("client-production")
    except Exception as exc:
        assert "off, shadow, publish" in str(exc)
    else:
        raise AssertionError("invalid mode was accepted")


def test_set_command_is_fixed_to_test_api_and_helper() -> None:
    command = build_set_command("shadow", "backup.env")
    assert "/home/neiro/novostroy-bot" in command
    assert "nmbot_env_secrets.py" in command
    assert "NMBOT_MANAGER_REWRITER_MODE" in command
    assert "novostroy-bot-api.service" in command
    assert "client-production" not in command
    assert "novostroy-bot-n8n-bridge.service" not in command
