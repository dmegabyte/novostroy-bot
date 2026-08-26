from __future__ import annotations

import json
import subprocess

from scripts import nmbot_static_public_release as release


def test_validates_hashed_static_artifact(tmp_path) -> None:
    page = tmp_path / "index.html"
    page.write_text("ok", encoding="utf-8")
    manifest = {"schema":"nmbot.static_echo.v1", "release_id":"deep-chat-echo-test.1", "files":[{"path":"index.html", "sha256":release.sha256(page), "size":2}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert release.validate_artifact(tmp_path)["release_id"] == "deep-chat-echo-test.1"


def test_refuses_deploy_without_confirm(tmp_path) -> None:
    try:
        release.deploy(tmp_path, confirm=False)
    except release.ReleaseError as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected confirmation gate")


def test_rejects_untracked_static_file(tmp_path) -> None:
    page = tmp_path / "index.html"
    page.write_text("ok", encoding="utf-8")
    manifest = {"schema":"nmbot.static_echo.v1", "release_id":"deep-chat-echo-test.1", "files":[{"path":"index.html", "sha256":release.sha256(page), "size":2}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "extra.js").write_text("unexpected", encoding="utf-8")
    try:
        release.validate_artifact(tmp_path)
    except release.ReleaseError as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("expected extra-file rejection")


def test_recon_requires_ready_static_http(monkeypatch) -> None:
    monkeypatch.setattr(release, "run_ssh", lambda command: subprocess.CompletedProcess(command, 0, '{"static_root":true,"http_ready":true}', ""))
    assert release.recon() == {"static_root": True, "http_ready": True}
    monkeypatch.setattr(release, "run_ssh", lambda command: subprocess.CompletedProcess(command, 0, '{"static_root":true,"http_ready":false}', ""))
    try:
        release.recon()
    except release.ReleaseError as exc:
        assert "HTTP-ready" in str(exc)
    else:
        raise AssertionError("expected HTTP readiness rejection")
