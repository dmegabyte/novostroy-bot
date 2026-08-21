from __future__ import annotations

import importlib.util
import json
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


def test_recon_command_is_readonly_and_binds_selected_contour() -> None:
    module = load_module()
    spec = module.load_registry()["primary"]

    command = module.build_remote_command(contour="primary", spec=spec)

    assert '"contour":"primary"' in command
    assert "systemctl\", \"--user\", \"show\"" in command
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
