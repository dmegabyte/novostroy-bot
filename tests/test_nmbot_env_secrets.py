from __future__ import annotations

import importlib.util
from pathlib import Path
import os
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_env_secrets.py"
spec = importlib.util.spec_from_file_location("nmbot_env_secrets", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_bridge_status_configuration_keys_are_supported() -> None:
    assert {
        "NMBOT_BRIDGE_STATUS_UPDATES_ENABLED",
        "NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS",
        "NMBOT_BRIDGE_STATUS_TEMPLATES",
        "NMBOT_BRIDGE_TIMEOUT_SECONDS",
        "NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS",
        "NMBOT_BRIDGE_FALLBACK_TEXT",
    } <= mod.KNOWN_KEYS


def test_v0_model_keys_are_supported_without_unknown_key_escape() -> None:
    assert {
        "NMBOT_V0_MODEL",
        "NMBOT_V0_SEARCH_MODEL",
        "NMBOT_V0_ANSWER_MODEL",
    } <= mod.KNOWN_KEYS


def test_v1_one_model_gpt55_mode_key_is_supported() -> None:
    assert "NMBOT_V1_ONE_MODEL_GPT55_MODE" in mod.KNOWN_KEYS


def test_gateway_forensic_log_keys_are_supported() -> None:
    assert {
        "NMBOT_GATEWAY_FORENSIC_LOG_ENABLED",
        "NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS",
        "NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES",
    } <= mod.KNOWN_KEYS


def test_gateway_forensic_log_values_are_strictly_validated(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    for value in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        status = mod.set_key(env_path, "NMBOT_GATEWAY_FORENSIC_LOG_ENABLED", value)
        assert status in {"added", "updated"}

    for key, value in (
        ("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "1"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "31"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "1048576"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "104857600"),
    ):
        status = mod.set_key(env_path, key, value)
        assert status in {"added", "updated"}


def test_gateway_forensic_log_rejects_invalid_values_before_write(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=value\n", encoding="utf-8")

    for key, value in (
        ("NMBOT_GATEWAY_FORENSIC_LOG_ENABLED", "enabled"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "0"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "32"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", "1.5"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "1048575"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "104857601"),
        ("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", "ten-megabytes"),
    ):
        before = env_path.read_text(encoding="utf-8")
        try:
            mod.set_key(env_path, key, value)
        except ValueError as exc:
            assert key in str(exc)
        else:
            raise AssertionError(f"{key}={value} must be rejected")
        assert env_path.read_text(encoding="utf-8") == before


def test_set_key_quotes_status_templates_and_preserves_other_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=value\n", encoding="utf-8")

    status = mod.set_key(
        env_path,
        "NMBOT_BRIDGE_STATUS_TEMPLATES",
        "Первый статус.|Второй статус.",
    )

    assert status == "added"
    rendered = env_path.read_text(encoding="utf-8")
    assert "EXISTING=value" in rendered
    assert 'NMBOT_BRIDGE_STATUS_TEMPLATES="Первый статус.|Второй статус."' in rendered


def test_set_key_removes_duplicates_and_sets_mode_0600(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("NMBOT_API_TOKEN=old\nOTHER=value\nNMBOT_API_TOKEN=older\n", encoding="utf-8")
    os.chmod(env_path, 0o644)

    status = mod.set_key(env_path, "NMBOT_API_TOKEN", "new-secret")

    assert status == "updated"
    rendered = env_path.read_text(encoding="utf-8")
    assert rendered.count("NMBOT_API_TOKEN=") == 1
    assert "OTHER=value" in rendered
    assert oct(env_path.stat().st_mode & 0o777) == "0o600"


def test_set_key_rejects_crlf_in_secret(tmp_path: Path) -> None:
    try:
        mod.set_key(tmp_path / ".env", "NMBOT_API_TOKEN", "line1\nline2")
    except ValueError as exc:
        assert "CR/LF" in str(exc)
    else:
        raise AssertionError("CR/LF value must be rejected")


def test_v1_one_model_gpt55_mode_accepts_only_known_modes(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    for mode in ("off", "shadow", "publish"):
        status = mod.set_key(env_path, "NMBOT_V1_ONE_MODEL_GPT55_MODE", mode)
        assert status in {"added", "updated"}
        assert f"NMBOT_V1_ONE_MODEL_GPT55_MODE={mode}" in env_path.read_text(encoding="utf-8")

    before = env_path.read_text(encoding="utf-8")
    try:
        mod.set_key(env_path, "NMBOT_V1_ONE_MODEL_GPT55_MODE", "enabled")
    except ValueError as exc:
        assert "NMBOT_V1_ONE_MODEL_GPT55_MODE" in str(exc)
        assert "off|publish|shadow" in str(exc)
    else:
        raise AssertionError("arbitrary V1 one-model modes must be rejected")
    assert env_path.read_text(encoding="utf-8") == before


def test_v1_one_model_gpt55_cli_rejects_invalid_mode_before_write(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--env",
            str(env_path),
            "--key",
            "NMBOT_V1_ONE_MODEL_GPT55_MODE",
            "--value",
            "enabled",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "NMBOT_V1_ONE_MODEL_GPT55_MODE" in result.stderr
    assert not env_path.exists()


def test_response_composer_and_manager_rewriter_modes_accept_only_known_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    for key in mod.RESPONSE_COMPOSER_MANAGER_REWRITER_MODE_KEYS:
        for mode in ("off", "shadow", "publish"):
            status = mod.set_key(env_path, key, mode)
            assert status in {"added", "updated"}
            assert f"{key}={mode}" in env_path.read_text(encoding="utf-8")

        before = env_path.read_text(encoding="utf-8")
        try:
            mod.set_key(env_path, key, "enabled")
        except ValueError as exc:
            assert key in str(exc)
            assert "off|publish|shadow" in str(exc)
        else:
            raise AssertionError(f"{key}=enabled must be rejected")
        assert env_path.read_text(encoding="utf-8") == before


def test_generic_known_key_values_remain_unrestricted(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    status = mod.set_key(env_path, "NMBOT_V0_MODEL", "enabled")

    assert status == "added"
    assert "NMBOT_V0_MODEL=enabled" in env_path.read_text(encoding="utf-8")


def test_v0_resolution_independent_and_legacy_fallback() -> None:
    values = {
        "NMBOT_V0_MODEL": "legacy/model",
        "NMBOT_V0_SEARCH_MODEL": "search/model",
    }

    assert mod.resolve_v0_model(values, "NMBOT_V0_SEARCH_MODEL") == ("NMBOT_V0_SEARCH_MODEL", "search/model")
    assert mod.resolve_v0_model(values, "NMBOT_V0_ANSWER_MODEL") == ("NMBOT_V0_MODEL", "legacy/model")
    assert mod.resolve_v0_model({}, "NMBOT_V0_ANSWER_MODEL") == ("code-default", None)


def test_v0_set_search_leaves_answer_untouched(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("NMBOT_V0_ANSWER_MODEL=answer/model\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0-models", "--env", str(env_path), "set-search", "search/model"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = env_path.read_text(encoding="utf-8")
    assert "NMBOT_V0_SEARCH_MODEL=search/model" in rendered
    assert "NMBOT_V0_ANSWER_MODEL=answer/model" in rendered
    assert "search/model" not in result.stdout


def test_v0_cli_rejects_invalid_command_and_unknown_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    invalid_command = subprocess.run(
        [sys.executable, str(SCRIPT), "v0-models", "--env", str(env_path), "set-both", "model"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert invalid_command.returncode != 0

    unknown_key = subprocess.run(
        [sys.executable, str(SCRIPT), "--env", str(env_path), "--key", "NMBOT_UNKNOWN", "--value", "x"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert unknown_key.returncode != 0
    assert "unknown key" in unknown_key.stderr


def test_v0_status_hides_secret_looking_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "NMBOT_V0_SEARCH_MODEL=google/gemini-3.5-flash\n"
        "NMBOT_V0_ANSWER_MODEL=sk-secret-looking-value\n"
        "NMBOT_API_TOKEN=super-secret-token\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0-models", "--env", str(env_path), "status"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "google/gemini-3.5-flash" in result.stdout
    assert "sk-secret-looking-value" not in result.stdout
    assert "super-secret-token" not in result.stdout
    assert "NMBOT_API_TOKEN" not in result.stdout
