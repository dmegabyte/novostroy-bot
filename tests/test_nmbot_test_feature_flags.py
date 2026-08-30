from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import nmbot_test_feature_flags as flags_module

from scripts.nmbot_test_feature_flags import (
    ALLOWED_KEYS,
    FeatureFlagError,
    build_set_command,
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
    assert ": test-identity-check;" in command
    assert "rollback() { trap - ERR;" in command
    assert "test ! -e" in command
    for key in ALLOWED_KEYS:
        assert key in command


def test_backup_names_are_unique_and_set_uses_only_test_api_service(monkeypatch) -> None:
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
    assignments = [("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")]
    command = build_set_command(assignments, first)

    assert first != second
    assert first.endswith("-a.env")
    assert second.endswith("-b.env")
    assert "test-feature-flags" in command
    assert "nmbot-test-api.service" in command
    assert "n8n-bridge" not in command
    assert "flock -n 9" in command
    assert ".test-feature-flags.lock" in command
    assert command.count(": test-identity-check;") >= 3
    assert "sha256sum" in command


def test_locked_remote_guards_fail_closed_without_printing_identity_values(monkeypatch) -> None:
    monkeypatch.setattr(flags_module, "TEST_ROOT", "/srv/nmbot-test")
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", "/srv/nmbot-test/test-identity.json")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "hidden-profile")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "hidden-marker")

    identity_script = flags_module._remote_identity_check_script()
    post_change_script = flags_module._remote_post_change_check_script([
        ("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "1"),
    ])

    assert 'raise SystemExit("TEST identity verification failed")' in identity_script
    assert "print(" not in identity_script
    assert 'identity.get("profile")' in identity_script
    assert 'identity.get("release_marker")' in identity_script
    assert 'raise SystemExit("TEST post-change validation failed")' in post_change_script


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

    monkeypatch.setattr(flags_module, "TEST_HOST", flags_module.PRODUCTION_HOST)
    with pytest.raises(FeatureFlagError, match="matches production contour"):
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
        "runtime_v6": True,
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
        "runtime": "V6",
    }
    assert secret not in str(rendered)


def test_render_status_uses_runtime_effective_defaults_for_missing_flags() -> None:
    rendered = render_status({"feature_flags": {}})

    assert rendered["feature_flags"] == {
        "NMBOT_BROAD_INVENTORY_GATE_ENABLED": "enabled",
        "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED": "enabled",
        "NMBOT_OPENROUTER_EXCLUDE_REASONING": "disabled",
    }


def _configure_local_transaction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, post_check: str) -> tuple[Path, Path]:
    """Build a local, deterministic stand-in for the remote locked shell command."""
    root = tmp_path / "test-root"
    scripts = root / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()
    (root / ".env").write_text("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED=1\nSECRET=not-printed\n", encoding="utf-8")
    (root / "test-identity.json").write_text(json.dumps({"profile": "test", "release_marker": "marker"}), encoding="utf-8")
    (scripts / "nmbot_env_secrets.py").write_text(
        """import os, sys
from pathlib import Path
env = Path(sys.argv[sys.argv.index('--env') + 1])
key = sys.argv[sys.argv.index('--key') + 1]
value = sys.argv[sys.argv.index('--value') + 1]
mode = os.environ.get('TX_MODE', '')
lines = [line for line in env.read_text().splitlines() if not line.startswith(key + '=')]
lines.append(key + '=' + value)
env.write_text('\\n'.join(lines) + '\\n')
if mode == 'identity_after_backup':
    (env.parent / 'test-identity.json').write_text('{}')
if mode == 'update_fail':
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    (fake_bin / "systemctl").write_text(
        """#!/bin/sh
echo "$2" >> "$TX_LOG"
if [ "$2" = restart ]; then
  count=$(test -f "$TX_COUNT" && cat "$TX_COUNT" || printf 0)
  count=$((count + 1)); printf '%s' "$count" > "$TX_COUNT"
  if [ "$TX_MODE" = concurrent_fail ] && [ "$count" = 1 ]; then printf 'OTHER=concurrent\\n' >> "$TX_ENV"; exit 1; fi
  if [ "$TX_MODE" = restart_fail ] && [ "$count" = 1 ]; then exit 1; fi
fi
if [ "$2" = is-active ]; then printf 'active\\n'; fi
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "systemctl", 0o755)
    for name, value in {
        "TEST_HOST": "test@example.invalid",
        "TEST_PORT": "2222",
        "TEST_API_PORT": "8088",
        "TEST_ROOT": str(root),
        "TEST_SERVICE": "nmbot-test-api.service",
        "TEST_RELEASE_MARKER": "marker",
        "TEST_PROFILE": "test",
        "TEST_IDENTITY_FILE": str(root / "test-identity.json"),
    }.items():
        monkeypatch.setattr(flags_module, f"NMBOT_{name}", value, raising=False)
    monkeypatch.setattr(flags_module, "TEST_HOST", "test@example.invalid")
    monkeypatch.setattr(flags_module, "TEST_PORT", "2222")
    monkeypatch.setattr(flags_module, "TEST_API_PORT", "8088")
    monkeypatch.setattr(flags_module, "TEST_ROOT", str(root))
    monkeypatch.setattr(flags_module, "TEST_SERVICE", "nmbot-test-api.service")
    monkeypatch.setattr(flags_module, "TEST_RELEASE_MARKER", "marker")
    monkeypatch.setattr(flags_module, "TEST_PROFILE", "test")
    monkeypatch.setattr(flags_module, "TEST_IDENTITY_FILE", str(root / "test-identity.json"))
    monkeypatch.setattr(flags_module, "_remote_post_change_check_script", lambda assignments: post_check)
    return root, fake_bin


def _run_local_transaction(command: str, root: Path, fake_bin: Path, tmp_path: Path, mode: str = "") -> subprocess.CompletedProcess[str]:
    log = tmp_path / "systemctl.log"
    count = tmp_path / "systemctl.count"
    return subprocess.run(
        ["bash", "-c", command], text=True, capture_output=True, check=False,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "TX_MODE": mode,
             "TX_LOG": str(log), "TX_COUNT": str(count), "TX_ENV": str(root / ".env")},
    )


def test_locked_transaction_success_and_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    post_check = "import json; print(json.dumps({'runtime_v5': True, 'health_ok': True, 'service_active': True, 'test_identity_ok': True, 'feature_flags': {'NMBOT_MAIN_SEARCH_FALLBACK_ENABLED': False}}))"
    root, fake_bin = _configure_local_transaction(monkeypatch, tmp_path, post_check=post_check)
    result = _run_local_transaction(build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")], "one.env"), root, fake_bin, tmp_path)

    assert result.returncode == 0
    assert "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED=0" in (root / ".env").read_text()
    assert (root / "backups" / "one.env").exists()
    assert result.stdout.strip().startswith('{')
    command = build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")], "order.env")
    assert command.index("flock -n 9") < command.index("cp -p") < command.index("apply_updates;")
    update_at = command.index("apply_updates;")
    restart_at = command.index("systemctl --user restart", update_at)
    assert update_at < restart_at < command.index("trap - ERR", restart_at)
    assert command.count("trap rollback ERR") == 1


@pytest.mark.parametrize("mode, post_check, expected_restarts", [
    ("update_fail", "import sys; raise SystemExit(1)", 1),
    ("restart_fail", "import sys; raise SystemExit(1)", 2),
    ("", "import sys; raise SystemExit(1)", 2),
])
def test_transaction_failure_rolls_back_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, post_check: str, expected_restarts: int) -> None:
    root, fake_bin = _configure_local_transaction(monkeypatch, tmp_path, post_check=post_check)
    result = _run_local_transaction(build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")], "failure.env"), root, fake_bin, tmp_path, mode)

    assert result.returncode != 0
    assert result.stderr.count("TEST_FEATURE_FLAGS_ROLLED_BACK") == 1
    assert "TEST_FEATURE_FLAGS_ROLLBACK_FAILED" not in result.stderr
    assert (root / ".env").read_text() == "NMBOT_MAIN_SEARCH_FALLBACK_ENABLED=1\nSECRET=not-printed\n"
    assert (tmp_path / "systemctl.count").read_text() == str(expected_restarts)


def test_transaction_refuses_stale_full_file_fingerprint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, fake_bin = _configure_local_transaction(monkeypatch, tmp_path, post_check="import sys; raise SystemExit(1)")
    result = _run_local_transaction(build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "1")], "stale.env"), root, fake_bin, tmp_path, "concurrent_fail")

    assert result.returncode != 0
    assert "TEST_FEATURE_FLAGS_ROLLBACK_FAILED" in result.stderr
    assert "OTHER=concurrent" in (root / ".env").read_text()
    assert "SECRET=not-printed" not in result.stderr


def test_identity_mismatch_refuses_write_and_restore(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root, fake_bin = _configure_local_transaction(monkeypatch, tmp_path, post_check="import sys; raise SystemExit(1)")
    (root / "test-identity.json").write_text("{}", encoding="utf-8")
    before = (root / ".env").read_text()
    refused_before_write = _run_local_transaction(build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")], "identity-a.env"), root, fake_bin, tmp_path)
    assert refused_before_write.returncode != 0
    assert (root / ".env").read_text() == before

    (root / "test-identity.json").write_text(json.dumps({"profile": "test", "release_marker": "marker"}), encoding="utf-8")
    refused_restore = _run_local_transaction(build_set_command([("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "0")], "identity-b.env"), root, fake_bin, tmp_path, "identity_after_backup")
    assert refused_restore.returncode != 0
    assert "TEST_FEATURE_FLAGS_ROLLBACK_FAILED" in refused_restore.stderr
