from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_contour_recon.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nmbot_contour_recon_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_requires_explicit_unverified_traffic_role() -> None:
    module = load_module()
    registry = module.load_registry()

    assert set(registry) == {"primary", "client-production"}
    assert {spec["traffic_role"] for spec in registry.values()} == {"unverified"}
    assert all(set(spec["services"]) == {"api", "bridge"} for spec in registry.values())


def test_recon_command_is_readonly_and_binds_selected_contour() -> None:
    module = load_module()
    spec = module.load_registry()["primary"]

    command = module.build_remote_command(contour="primary", spec=spec)
    argv = shlex.split(command)

    assert '"contour":"primary"' in command
    assert argv[:2] == ["python3", "-c"]
    compile(argv[2], "<nmbot-contour-recon-remote>", "exec")
    assert "systemctl\", \"--user\", \"show\"" in command
    assert "nmbot-dialogue-sheet-export.service" in command
    assert "nmbot-dialogue-sheet-export.timer" in command
    assert "dialogue_journal.jsonl" in command
    assert 'root / "logs" / "dialogue_journal.jsonl"' in command
    assert 'current / "logs" / "dialogue_journal.jsonl"' in command
    assert 'root / "bridge-current" / "logs" / "dialogue_journal.jsonl"' in command
    assert "journal_target" in command and "default_split" in command
    assert "sheet_target" in command and "tab_target" in command
    assert "journalctl" in command and "last_write" in command and "known_error_codes" in command
    assert "valid_conversation_ref" in command
    assert "2026-08-27" in command and "2026-08-28" in command
    assert "urlopen" in command
    assert not any(token in command for token in (" restart", " start", " stop", " deploy", "scp", "rsync"))


def test_run_recon_rejects_receipt_for_other_contour(monkeypatch) -> None:
    module = load_module()
    spec = module.load_registry()["primary"]
    payload = {"schema_version": module.SCHEMA_VERSION, "contour": "client-production"}
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, json.dumps(payload), ""))

    try:
        module.run_recon(contour="primary", spec=spec)
    except module.ReconError as exc:
        assert "does not match" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("mismatched contour receipt must fail")
