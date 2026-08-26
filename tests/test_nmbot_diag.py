from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.nmbot_diag import DiagnosticError, inspect_local


def _identity(path: Path, release_id: str = "v6-clean-r1") -> None:
    path.write_text(json.dumps({"schema": "nmbot.release_identity.v1", "release_id": release_id}), encoding="utf-8")


def _route(path: Path, release_id: str = "v6-clean-r1") -> None:
    path.write_text(json.dumps({
        "schema": "nmbot.active_route.v1",
        "profile": "TEST",
        "revision": 1,
        "active": {"slot": "A", "release_id": release_id, "upstream": "http://127.0.0.1:18088"},
        "previous": None,
        "switched_at": "2026-08-26T00:00:00Z",
    }), encoding="utf-8")


def test_local_diagnostic_matches_identity_health_and_route(tmp_path: Path) -> None:
    identity = tmp_path / "identity.json"
    health = tmp_path / "health.json"
    route = tmp_path / "route.json"
    _identity(identity)
    _route(route)
    health.write_text(json.dumps({"ok": True, "runtime": "V6", "profile": "TEST", "release_id": "v6-clean-r1"}), encoding="utf-8")

    result = inspect_local(identity_path=identity, profile="TEST", health_path=health, route_path=route)
    assert result["health"] == "matched"
    assert result["route"]["slot"] == "A"
    assert len(result["route"]["upstream_ref"]) == 16


def test_local_diagnostic_fails_closed_on_identity_desync(tmp_path: Path) -> None:
    identity = tmp_path / "identity.json"
    route = tmp_path / "route.json"
    _identity(identity)
    _route(route, release_id="v6-clean-other")
    with pytest.raises(DiagnosticError, match="route snapshot identity mismatch"):
        inspect_local(identity_path=identity, profile="TEST", route_path=route)


def test_diagnostic_source_has_no_network_client() -> None:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "nmbot_diag.py").read_text(encoding="utf-8")
    assert "urlopen(" not in source
    assert "ClientSession" not in source
    assert "requests." not in source
