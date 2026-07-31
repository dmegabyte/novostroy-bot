from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY_SCRIPT = ROOT / "scripts" / "project_memory_notebook_inventory.py"


def load_inventory_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_inventory_test", INVENTORY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_storage(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge-mcp"
    base = root / "workspaces" / "default" / "notebooks"
    write_json(
        base / "beta" / "sources" / "s1.json",
        {
            "source_id": "source-1",
            "title": "Source title",
            "created_at": "2026-07-02T00:00:00Z",
            "updated_at": "2026-07-03T00:00:00Z",
            "content": "SOURCE_BODY_SECRET_456",
        },
    )
    write_json(
        base / "alpha" / "notes" / "n1.json",
        {
            "note_id": "note-1",
            "title": "Note title",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T01:00:00Z",
            "note": "NOTE_BODY_SECRET_123",
        },
    )
    return root


def test_inventory_safe_metadata_hashes_and_stable_sort(tmp_path: Path) -> None:
    root = make_storage(tmp_path)
    mod = load_inventory_module()

    payload = mod.inventory_notebook_storage(root, "default")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["schema"] == "project_memory_notebook_inventory.v1"
    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["write_performed"] is False
    assert payload["content_printed"] is False
    assert payload["migration_performed"] is False
    assert payload["automatic_routing_changed"] is False
    assert payload["requires_owner_confirmation"] is True
    assert payload["record_count"] == 2
    assert [(item["notebook"], item["kind"], item["id"]) for item in payload["records"]] == [
        ("alpha", "note", "note-1"),
        ("beta", "source", "source-1"),
    ]
    assert payload["records"][0]["content_sha256"] == hashlib.sha256("NOTE_BODY_SECRET_123".encode()).hexdigest()
    assert payload["records"][0]["body_bytes"] == len("NOTE_BODY_SECRET_123".encode())
    assert payload["records"][1]["content_sha256"] == hashlib.sha256("SOURCE_BODY_SECRET_456".encode()).hexdigest()
    assert "NOTE_BODY_SECRET_123" not in serialized
    assert "SOURCE_BODY_SECRET_456" not in serialized
    assert "Note title" not in serialized
    assert "Source title" not in serialized
    assert str(root) not in serialized
    assert payload["storage_scope"] == "local_notebook_store"
    assert "storage_root" not in payload
    assert "title" not in payload["records"][0]
    assert '"note":' not in serialized
    assert '"content":' not in serialized


def test_inventory_cli_filter_and_no_writes(tmp_path: Path) -> None:
    root = make_storage(tmp_path)
    before = {path: path.stat().st_mtime_ns for path in root.rglob("*.json")}

    run = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT), "--storage-root", str(root), "--workspace", "default", "--notebook", "alpha", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["record_count"] == 1
    assert payload["records"][0]["notebook"] == "alpha"
    assert payload["write_performed"] is False
    assert before == {path: path.stat().st_mtime_ns for path in root.rglob("*.json")}
    assert "NOTE_BODY_SECRET_123" not in run.stdout
    assert "Note title" not in run.stdout
    assert str(root) not in run.stdout


def test_inventory_missing_root_fails_closed(tmp_path: Path) -> None:
    mod = load_inventory_module()
    payload = mod.inventory_notebook_storage(tmp_path / "missing", "default")

    assert payload["ok"] is False
    assert payload["denied_reason"] == "storage_root_missing"
    assert payload["record_count"] == 0
    assert payload["write_performed"] is False
    assert str(tmp_path / "missing") not in json.dumps(payload)
    assert payload["storage_scope"] == "local_notebook_store"
    assert "storage_root" not in payload


def test_inventory_malformed_json_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "knowledge-mcp"
    bad = root / "workspaces" / "default" / "notebooks" / "alpha" / "notes" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json SECRET_SHOULD_NOT_PRINT", encoding="utf-8")
    mod = load_inventory_module()

    payload = mod.inventory_notebook_storage(root, "default")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "malformed_json"
    assert payload["write_performed"] is False
    assert "SECRET_SHOULD_NOT_PRINT" not in serialized


def test_inventory_rejects_escaping_workspace_or_notebook(tmp_path: Path) -> None:
    root = make_storage(tmp_path)
    mod = load_inventory_module()

    workspace_payload = mod.inventory_notebook_storage(root, "../default")
    notebook_payload = mod.inventory_notebook_storage(root, "default", ["../alpha"])

    assert workspace_payload["ok"] is False
    assert workspace_payload["denied_reason"] == "invalid_workspace"
    assert notebook_payload["ok"] is False
    assert notebook_payload["denied_reason"] == "invalid_notebook"


def test_inventory_source_has_no_external_runtime_imports() -> None:
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
    ]
    source = INVENTORY_SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
