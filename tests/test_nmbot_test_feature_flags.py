from __future__ import annotations

import pytest
from scripts import nmbot_test_feature_flags as flags_module

from scripts.nmbot_test_feature_flags import (
    ALLOWED_KEYS,
    FeatureFlagError,
    build_set_command,
    build_restore_command,
    _backup_name,
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


def test_set_command_requires_explicit_non_production_test_identity(monkeypatch) -> None:
    assignments = [(key, "1") for key in sorted(ALLOWED_KEYS)]
    with pytest.raises(FeatureFlagError):
        build_set_command(assignments, "backup.env")
    monkeypatch.setattr(flags_module, "TEST_HOST", "test@example.invalid")
    monkeypatch.setattr(flags_module, "TEST_PORT", "2222")
    monkeypatch.setattr(flags_module, "TEST_API_PORT", "8088")
    monkeypatch.setattr(flags_module, "TEST_ROOT", "/srv/nmbot-test")
    monkeypatch.setattr(flags_module, "TEST_SERVICE", "nmbot-test-api.service")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "test-release")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "test")
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", "/srv/nmbot-test/test-identity.json")
    command = build_set_command(assignments, "backup.env")

    assert "/srv/nmbot-test" in command
    assert "nmbot_env_secrets.py" in command
    assert "nmbot-test-api.service" in command
    assert "novostroy-bot-n8n-bridge.service" not in command
    assert "client-production" not in command
    assert "flock -n 9" in command
    assert ".test-feature-flags.lock" in command
    assert "test ! -e" in command
    for key in ALLOWED_KEYS:
        assert key in command


def test_backup_names_are_unique_and_restore_uses_only_test_api_service(monkeypatch) -> None:
    monkeypatch.setattr(flags_module, "TEST_HOST", "test@example.invalid")
    monkeypatch.setattr(flags_module, "TEST_PORT", "2222")
    monkeypatch.setattr(flags_module, "TEST_API_PORT", "8088")
    monkeypatch.setattr(flags_module, "TEST_ROOT", "/srv/nmbot-test")
    monkeypatch.setattr(flags_module, "TEST_SERVICE", "nmbot-test-api.service")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "test-release")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "test")
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", "/srv/nmbot-test/test-identity.json")
    monkeypatch.setattr(flags_module.uuid, "uuid4", lambda: type("UUID", (), {"hex": "a"})())
    first = _backup_name()
    monkeypatch.setattr(flags_module.uuid, "uuid4", lambda: type("UUID", (), {"hex": "b"})())
    second = _backup_name()
    restore = build_restore_command(first)

    assert first != second
    assert first.endswith("-a.env")
    assert second.endswith("-b.env")
    assert "test-feature-flags" in restore
    assert "nmbot-test-api.service" in restore
    assert "n8n-bridge" not in restore


def test_remote_status_script_checks_identity_file_without_rendering_identity_values(monkeypatch) -> None:
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", "/srv/nmbot-test/test-identity.json")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "hidden-profile")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "hidden-marker")

    script = flags_module._remote_status_script()

    assert 'identity_file.read_text' in script
    assert '"test_identity_ok": test_identity_ok' in script
    assert '"profile"' not in script.split("print(json.dumps", 1)[1]
    assert '"release_marker"' not in script.split("print(json.dumps", 1)[1]


def test_missing_or_mismatched_test_identity_fails_before_remote_command(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(flags_module, "TEST_HOST", "")
    monkeypatch.setattr(flags_module, "TEST_PORT", "2222")
    monkeypatch.setattr(flags_module, "TEST_API_PORT", "8088")
    monkeypatch.setattr(flags_module, "TEST_ROOT", "/srv/nmbot-test")
    monkeypatch.setattr(flags_module, "TEST_SERVICE", "nmbot-test-api.service")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "test-release")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "test")
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", "/srv/nmbot-test/test-identity.json")
    monkeypatch.setattr(flags_module.subprocess, "run", lambda *args, **kwargs: calls.append("remote"))

    with pytest.raises(FeatureFlagError, match="not fully configured"):
        flags_module.read_status()

    assert calls == []


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


def test_render_status_uses_runtime_effective_defaults_for_missing_flags() -> None:
    rendered = render_status({"feature_flags": {}})

    assert rendered["feature_flags"] == {
        "NMBOT_BROAD_INVENTORY_GATE_ENABLED": "enabled",
        "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED": "enabled",
        "NMBOT_OPENROUTER_EXCLUDE_REASONING": "disabled",
    }
