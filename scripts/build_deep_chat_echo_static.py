#!/usr/bin/env python3
"""Build the isolated, dependency-pinned Deep Chat echo static artifact.

The output deliberately contains no NMBot endpoint, credentials, or runtime
configuration.  It is only a browser-side echo smoke page.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path


PACKAGE = "deep-chat"
VERSION = "2.5.0"
REGISTRY_URL = f"https://registry.npmjs.org/{PACKAGE}/{VERSION}"
SAFE_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class BuildError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fetch_package() -> tuple[bytes, str, str]:
    """Fetch the fixed registry version and verify npm's published SRI hash."""
    with urllib.request.urlopen(REGISTRY_URL, timeout=30) as response:
        metadata = json.load(response)
    dist = metadata.get("dist") if isinstance(metadata, dict) else None
    if not isinstance(dist, dict) or not isinstance(dist.get("tarball"), str) or not isinstance(dist.get("integrity"), str):
        raise BuildError("Deep Chat registry metadata is missing tarball integrity")
    if metadata.get("name") != PACKAGE or metadata.get("version") != VERSION:
        raise BuildError("Deep Chat registry returned an unexpected package identity")
    with urllib.request.urlopen(dist["tarball"], timeout=60) as response:
        archive = response.read()
    algorithm, encoded = dist["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(archive).digest()).decode() != encoded:
        raise BuildError("Deep Chat tarball does not match npm integrity")
    return archive, dist["integrity"], dist["tarball"]


def clean_git_commit(root: Path) -> str:
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], text=True, capture_output=True, check=False)
    if status.returncode != 0 or status.stdout:
        raise BuildError("static artifact must be built from a clean Git worktree")
    revision = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    commit = revision.stdout.strip()
    if revision.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BuildError("static artifact source commit is unavailable")
    return commit


def asset_from_tarball(archive: bytes) -> tuple[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = set(tar.getnames())
        candidates = ("package/dist/deepChat.bundle.js", "package/dist/deepChat.js")
        name = next((item for item in candidates if item in names), None)
        if name is None:
            raise BuildError("Deep Chat package lacks an approved browser asset")
        member = tar.getmember(name)
        if not member.isfile() or member.size > 10 * 1024 * 1024:
            raise BuildError("Deep Chat browser asset is unsafe")
        handle = tar.extractfile(member)
        if handle is None:
            raise BuildError("Deep Chat browser asset cannot be extracted")
        return Path(name).name, handle.read()


def page() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ирина — echo test</title><style>body{margin:0;background:#f4f7fb;color:#13213a;font:16px system-ui,sans-serif}.wrap{max-width:720px;margin:32px auto;padding:0 16px}.card{background:#fff;border:1px solid #dce4ef;border-radius:20px;padding:20px;box-shadow:0 12px 28px #10213a12}.status{color:#087f3e;font-weight:700}.dot{display:inline-block;width:9px;height:9px;background:#20b565;border-radius:50%;margin-right:7px}deep-chat{height:min(70vh,620px)}</style></head>
<body><main class="wrap"><section class="card"><h1>Ирина</h1><p class="status"><span class="dot"></span>Ирина онлайн</p><p>Тест виджета: сообщение остаётся только в браузере и возвращается как эхо.</p><deep-chat aria-label="Тестовый чат Ирины"></deep-chat></section></main>
<script type="module">import './vendor/deep-chat.js';const chat=document.querySelector('deep-chat');chat.connect={handler:(body,signals)=>{const messages=Array.isArray(body.messages)?body.messages:[];const last=messages.at(-1)||{};const text=typeof last.text==='string'?last.text.trim():'';signals.onResponse({text:text?`Эхо: ${text}`:'Напишите сообщение для проверки.'});}};</script></body></html>"""


def write_artifact(out_dir: Path, release_id: str, archive: bytes, integrity: str, tarball_url: str, source_commit: str) -> Path:
    if not SAFE_RELEASE_ID.fullmatch(release_id):
        raise BuildError("unsafe release id")
    destination = out_dir / release_id
    if destination.exists() or destination.is_symlink():
        raise BuildError("artifact destination already exists")
    asset_name, asset = asset_from_tarball(archive)
    with tempfile.TemporaryDirectory(prefix=".deep-chat-echo-", dir=out_dir) as temp:
        stage = Path(temp)
        vendor = stage / "vendor"
        vendor.mkdir()
        (vendor / "deep-chat.js").write_bytes(asset)
        (stage / "index.html").write_text(page(), encoding="utf-8")
        files = []
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                files.append({"path": str(path.relative_to(stage)), "sha256": sha256_bytes(path.read_bytes()), "size": path.stat().st_size})
        manifest = {"schema":"nmbot.static_echo.v1", "release_id":release_id, "source_provenance":{"git_commit":source_commit}, "deep_chat":{"package":PACKAGE,"version":VERSION,"integrity":integrity,"tarball_url":tarball_url,"source_asset":asset_name}, "files":files}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a pinned local Deep Chat echo static artifact")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    archive, integrity, tarball_url = fetch_package()
    output = write_artifact(args.out_dir, args.release_id, archive, integrity, tarball_url, clean_git_commit(Path(__file__).resolve().parents[1]))
    print(json.dumps({"build":"ok", "artifact":str(output), "manifest":str(output / "manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
