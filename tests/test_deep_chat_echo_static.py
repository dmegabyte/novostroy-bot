from __future__ import annotations

import io
import tarfile

from scripts import build_deep_chat_echo_static as builder


def package_with_asset() -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        payload = b"customElements.define('deep-chat', class extends HTMLElement {});"
        info = tarfile.TarInfo("package/dist/deepChat.bundle.js")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def test_builds_local_echo_page_with_pinned_vendor_asset(tmp_path) -> None:
    artifact = builder.write_artifact(tmp_path, "deep-chat-echo-test.1", package_with_asset(), "sha512-test", "https://registry.npmjs.org/deep-chat/-/deep-chat-2.5.0.tgz")
    page = (artifact / "index.html").read_text(encoding="utf-8")
    manifest = (artifact / "manifest.json").read_text(encoding="utf-8")
    assert "Ирина онлайн" in page
    assert "Эхо:" in page
    assert "vendor/deep-chat.js" in page
    assert "https://" not in page
    assert '"version": "2.5.0"' in manifest


def test_rejects_artifact_reuse_and_missing_browser_asset(tmp_path) -> None:
    archive = package_with_asset()
    builder.write_artifact(tmp_path, "deep-chat-echo-test.1", archive, "sha512-test", "url")
    try:
        builder.write_artifact(tmp_path, "deep-chat-echo-test.1", archive, "sha512-test", "url")
    except builder.BuildError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected no-clobber rejection")
