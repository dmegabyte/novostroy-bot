from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_release_module():
    scripts_dir = ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location("nmbot_release_legacy_under_test", scripts_dir / "nmbot_release.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(scripts_dir))
        except ValueError:
            pass


def test_legacy_deploy_fails_closed_before_ssh_upload_or_restart(monkeypatch) -> None:
    module = _load_release_module()
    calls: list[object] = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("legacy deploy must fail before any command/upload/write")

    monkeypatch.setattr(module, "run", forbidden)
    monkeypatch.setattr(module, "ssh", forbidden)
    monkeypatch.setattr(module, "write_identity_manifest", forbidden)

    try:
        module.deploy(("scripts/nmbot_api_server.py",), release_id="legacy-disabled")
    except RuntimeError as exc:
        text = str(exc)
        assert "legacy partial production deploy is disabled" in text
        assert "snapshot-vps-source" in text and "build-from-worktree" in text
    else:  # pragma: no cover
        raise AssertionError("legacy deploy must fail closed")
    assert calls == []


def test_legacy_deploy_cli_fails_closed_before_remote_calls(monkeypatch, capsys) -> None:
    module = _load_release_module()
    monkeypatch.setattr(module, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("run called")))
    monkeypatch.setattr(module, "ssh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ssh called")))

    code = module.main(["deploy", "--release-id", "legacy-disabled", "scripts/nmbot_api_server.py"])

    captured = capsys.readouterr()
    assert code == 1
    assert "legacy partial production deploy is disabled" in captured.err
    assert "snapshot-vps-source" in captured.err
