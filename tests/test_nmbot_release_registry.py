from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.nmbot_release_registry import (
    ReleaseRegistry,
    ReleaseRegistryError,
    read_route_file,
    validate_upstream,
)


ARTIFACT_A = "a" * 64
ARTIFACT_B = "b" * 64
MANIFEST_A = "c" * 64
MANIFEST_B = "d" * 64
GIT_A = "1" * 40


def _register(registry: ReleaseRegistry, release_id: str, *, second: bool = False) -> dict:
    return registry.register_release(
        release_id=release_id,
        artifact_sha256=ARTIFACT_B if second else ARTIFACT_A,
        manifest_sha256=MANIFEST_B if second else MANIFEST_A,
        source_git_sha=GIT_A,
    )


def test_register_is_immutable_idempotent_and_privacy_bounded(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)

    first = _register(registry, "v6-r41")
    second = _register(registry, "v6-r41")

    assert first == second
    assert registry.show_release("v6-r41")["runtime_version"] == "V6"
    assert len(registry.list_releases()) == 1
    with pytest.raises(ReleaseRegistryError, match="different immutable identity"):
        _register(registry, "v6-r41", second=True)
    with pytest.raises(ReleaseRegistryError, match="unsafe release_id"):
        _register(registry, "../prod")


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8088",
        "http://localhost:8088",
        "http://10.0.0.1:8088",
        "http://127.0.0.1:8088/private",
        "http://user@127.0.0.1:8088",
        "http://127.0.0.1:80",
        "http://127.0.0.1:99999",
    ],
)
def test_upstream_rejects_non_loopback_origin_or_unsafe_shape(value: str) -> None:
    with pytest.raises(ReleaseRegistryError, match="upstream"):
        validate_upstream(value)


def test_test_and_prod_routes_are_independent(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    _register(registry, "v6-r42", second=True)
    registry.prepare_slot(
        profile="TEST",
        slot="A",
        release_id="v6-r42",
        upstream="http://127.0.0.1:18088",
        health_receipt_ref="local:test-r42",
    )
    registry.prepare_slot(
        profile="PROD",
        slot="B",
        release_id="v6-r41",
        upstream="http://127.0.0.1:28088",
        health_receipt_ref="local:prod-r41",
    )

    test_route = registry.activate(profile="TEST", slot="A", reason_code="test_acceptance")
    prod_route = registry.activate(profile="PROD", slot="B", reason_code="prod_activation")

    assert test_route["active"] == {
        "slot": "A",
        "release_id": "v6-r42",
        "upstream": "http://127.0.0.1:18088",
    }
    assert prod_route["active"]["release_id"] == "v6-r41"
    assert registry.read_route("TEST")["active"]["release_id"] == "v6-r42"
    assert registry.read_route("PROD")["active"]["release_id"] == "v6-r41"


def test_activate_then_rollback_uses_warm_previous_slot_without_rebuild(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    _register(registry, "v6-r42", second=True)
    registry.prepare_slot(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088", health_receipt_ref="health:r41"
    )
    registry.prepare_slot(
        profile="TEST", slot="B", release_id="v6-r42", upstream="http://127.0.0.1:18089", health_receipt_ref="health:r42"
    )
    registry.activate(profile="TEST", slot="A")
    registry.activate(profile="TEST", slot="B")

    rolled_back = registry.rollback(profile="TEST", reason_code="quality_regression")

    assert rolled_back["active"]["release_id"] == "v6-r41"
    assert rolled_back["active"]["slot"] == "A"
    assert rolled_back["previous"]["release_id"] == "v6-r42"
    assert registry.slot_state(profile="TEST", slot="A")["status"] == "ready"
    assert registry.slot_state(profile="TEST", slot="B")["status"] == "ready"


def test_activation_requires_ready_slot_and_rollback_requires_previous(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")

    with pytest.raises(ReleaseRegistryError, match="slot is not ready"):
        registry.activate(profile="TEST", slot="A")

    registry.prepare_slot(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088", health_receipt_ref="health:r41"
    )
    registry.activate(profile="TEST", slot="A")
    with pytest.raises(ReleaseRegistryError, match="no previous release"):
        registry.rollback(profile="TEST")


def test_failed_slot_prepare_cannot_leave_a_stale_ready_slot(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    registry.prepare_slot(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088", health_receipt_ref="health:old"
    )

    preparing = registry.begin_slot_prepare(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088"
    )
    failed = registry.fail_slot_prepare(
        profile="TEST",
        slot="A",
        release_id="v6-r41",
        reason_code="health_failed",
        receipt_ref="health:failed-42",
    )

    assert preparing["status"] == "preparing"
    assert failed["status"] == "failed"
    with pytest.raises(ReleaseRegistryError, match="slot is not ready"):
        registry.activate(profile="TEST", slot="A")


def test_quality_checks_and_journal_are_hash_chained(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    registry.record_check(
        "v6-r41",
        profile="TEST",
        outcome="passed",
        reason_code="v6_contracts",
        receipt_ref="pytest:131-passed",
    )
    registry.set_quality("v6-r41", verdict="approved", receipt_ref="review:owner-42")

    events = registry.journal_events()
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[0]["previous_sha256"] == "0" * 64
    assert events[1]["previous_sha256"] == events[0]["event_sha256"]
    assert registry.show_release("v6-r41")["quality"]["verdict"] == "approved"
    assert "dialogue" not in json.dumps(events).lower()

    lines = registry.journal_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["release_id"] = "v6-r99"
    lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    registry.journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ReleaseRegistryError, match="journal hash"):
        registry.journal_events()
    with pytest.raises(ReleaseRegistryError, match="journal hash"):
        registry.set_quality("v6-r41", verdict="rejected", receipt_ref="review:tampered")


def test_route_is_restored_when_completion_journal_write_fails(tmp_path: Path, monkeypatch) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    _register(registry, "v6-r42", second=True)
    registry.prepare_slot(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088", health_receipt_ref="health:r41"
    )
    registry.prepare_slot(
        profile="TEST", slot="B", release_id="v6-r42", upstream="http://127.0.0.1:18089", health_receipt_ref="health:r42"
    )
    original = registry.activate(profile="TEST", slot="A")
    append = registry._append_event_unlocked

    def fail_completion(event_type: str, **kwargs):
        if event_type == "release_activated":
            raise OSError("simulated journal failure")
        return append(event_type, **kwargs)

    monkeypatch.setattr(registry, "_append_event_unlocked", fail_completion)
    with pytest.raises(OSError, match="simulated journal failure"):
        registry.activate(profile="TEST", slot="B")

    assert read_route_file(registry.route_path("TEST")) == original


def test_route_is_restored_when_post_switch_check_fails(tmp_path: Path) -> None:
    registry = ReleaseRegistry(tmp_path)
    _register(registry, "v6-r41")
    _register(registry, "v6-r42", second=True)
    registry.prepare_slot(
        profile="TEST", slot="A", release_id="v6-r41", upstream="http://127.0.0.1:18088", health_receipt_ref="health:r41"
    )
    registry.prepare_slot(
        profile="TEST", slot="B", release_id="v6-r42", upstream="http://127.0.0.1:18089", health_receipt_ref="health:r42"
    )
    original = registry.activate(profile="TEST", slot="A")

    def fail_check(_route: dict) -> None:
        raise ReleaseRegistryError("bridge route post-check failed")

    with pytest.raises(ReleaseRegistryError, match="post-check failed"):
        registry.activate(profile="TEST", slot="B", post_switch_check=fail_check)

    assert registry.read_route("TEST") == original


def test_sync_copies_identity_only_and_preserves_destination_routes(tmp_path: Path) -> None:
    source = ReleaseRegistry(tmp_path / "source")
    destination = ReleaseRegistry(tmp_path / "destination")
    _register(source, "v6-r41")

    synced = source.sync_release_to(destination, "v6-r41")

    assert synced["release_id"] == "v6-r41"
    assert destination.show_release("v6-r41")["artifact_sha256"] == ARTIFACT_A
    assert destination.read_route("PROD", required=False) is None
