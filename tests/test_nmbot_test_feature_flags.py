from __future__ import annotations

import pytest

from scripts.nmbot_test_feature_flags import (
    ALLOWED_KEYS,
    FeatureFlagError,
    build_set_command,
    render_status,
    validate_assignment,
    validate_assignments,
)


def test_validate_assignment_accepts_only_allowlisted_on_off_values() -> None:
    key = "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED"
    assert validate_assignment(f"{key}=on") == (key, "1")
    assert validate_assignment(f"{key}=off") == (key, "0")

    with pytest.raises(FeatureFlagError):
        validate_assignment("NMBOT_API_TOKEN=on")
    with pytest.raises(FeatureFlagError):
        validate_assignment(f"{key}=true")
    with pytest.raises(FeatureFlagError):
        validate_assignment(f"{key}=ON")
    with pytest.raises(FeatureFlagError):
        validate_assignment(f"{key}=on=extra")
    with pytest.raises(FeatureFlagError):
        validate_assignments([f"{key}=on", f"{key}=off"])


def test_set_command_is_fixed_to_test_api_and_helper() -> None:
    assignments = [(key, "1") for key in sorted(ALLOWED_KEYS)]
    command = build_set_command(assignments, "backup.env")

    assert "/home/neiro/novostroy-bot" in command
    assert "nmbot_env_secrets.py" in command
    assert "novostroy-bot-api.service" in command
    assert "novostroy-bot-n8n-bridge.service" not in command
    assert "client-production" not in command
    for key in ALLOWED_KEYS:
        assert key in command


def test_render_status_is_bounded_and_never_includes_dotenv_values() -> None:
    secret = "must-not-be-rendered"
    rendered = render_status({
        "feature_flags": {
            "NMBOT_BROAD_INVENTORY_GATE_ENABLED": True,
            "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED": False,
            "unexpected": secret,
        },
        "health_ok": True,
        "service_active": False,
        "runtime_v5": True,
        "dotenv_value": secret,
    })

    assert rendered == {
        "feature_flags": {
            "NMBOT_BROAD_INVENTORY_GATE_ENABLED": "enabled",
            "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED": "disabled",
            "NMBOT_OPENROUTER_EXCLUDE_REASONING": "disabled",
        },
        "health": "healthy",
        "service": "inactive",
        "runtime": "V5",
    }
    assert secret not in str(rendered)
