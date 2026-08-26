#!/usr/bin/env python3
"""Immutable V6 release registry and atomic A/B route state.

The module is stdlib-only, stores no secrets or dialogue payloads, and performs
no network or process operations.  Runtime preparation/health checks belong to
``nmbot_release_control``; this module owns durable identity, route and journal
invariants.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit


REGISTRY_SCHEMA = "nmbot.release_registry.v1"
ROUTE_SCHEMA = "nmbot.active_route.v1"
JOURNAL_SCHEMA = "nmbot.release_journal.v1"
RUNTIME_VERSION = "V6"
PROFILES = ("TEST", "PROD")
SLOTS = ("A", "B")
QUALITY_VERDICTS = frozenset({"unreviewed", "approved", "rejected"})
CHECK_OUTCOMES = frozenset({"passed", "failed"})
SAFE_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_RECEIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


class ReleaseRegistryError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_profile(value: Any) -> str:
    profile = str(value or "").strip().upper()
    if profile not in PROFILES:
        raise ReleaseRegistryError("profile must be exactly TEST or PROD")
    return profile


def normalize_slot(value: Any) -> str:
    slot = str(value or "").strip().upper()
    if slot not in SLOTS:
        raise ReleaseRegistryError("slot must be exactly A or B")
    return slot


def validate_release_id(value: Any) -> str:
    release_id = str(value or "").strip()
    if not SAFE_RELEASE_RE.fullmatch(release_id) or release_id in {".", "..", "UNKNOWN"}:
        raise ReleaseRegistryError("unsafe release_id")
    return release_id


def validate_sha256(value: Any, *, field: str) -> str:
    digest = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(digest):
        raise ReleaseRegistryError(f"{field} must be a lowercase sha256")
    return digest


def validate_git_sha(value: Any) -> str:
    digest = str(value or "").strip().lower()
    if digest and not GIT_SHA_RE.fullmatch(digest):
        raise ReleaseRegistryError("source_git_sha must be a lowercase 40-64 character Git hash")
    return digest


def validate_receipt_ref(value: Any, *, required: bool = False) -> str:
    receipt = str(value or "").strip()
    if not receipt:
        if required:
            raise ReleaseRegistryError("receipt_ref is required")
        return ""
    if not SAFE_RECEIPT_RE.fullmatch(receipt):
        raise ReleaseRegistryError("unsafe receipt_ref")
    return receipt


def validate_reason_code(value: Any, *, default: str = "manual") -> str:
    code = str(value or default).strip().lower()
    if not SAFE_CODE_RE.fullmatch(code):
        raise ReleaseRegistryError("unsafe reason_code")
    return code


def validate_upstream(value: Any) -> str:
    """Allow only an explicit loopback HTTP origin, never a path or remote host."""
    text = str(value or "").strip()
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ReleaseRegistryError("upstream must be an explicit http://127.0.0.1:<port> origin") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or not 1024 <= port <= 65535
    ):
        raise ReleaseRegistryError("upstream must be an explicit http://127.0.0.1:<port> origin")
    return f"http://127.0.0.1:{port}"


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _event_digest(event_without_hash: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(event_without_hash)).hexdigest()


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_canonical_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_route_file(path: Path, *, expected_profile: str | None = None) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseRegistryError("active route is missing or malformed") from exc
    if not isinstance(raw, dict) or raw.get("schema") != ROUTE_SCHEMA:
        raise ReleaseRegistryError("active route schema mismatch")
    profile = normalize_profile(raw.get("profile"))
    if expected_profile is not None and profile != normalize_profile(expected_profile):
        raise ReleaseRegistryError("active route profile mismatch")
    try:
        revision = int(raw.get("revision"))
    except (TypeError, ValueError) as exc:
        raise ReleaseRegistryError("active route revision is invalid") from exc
    if revision < 1:
        raise ReleaseRegistryError("active route revision is invalid")
    active = _validate_route_target(raw.get("active"), field="active")
    previous_raw = raw.get("previous")
    previous = _validate_route_target(previous_raw, field="previous") if previous_raw is not None else None
    switched_at = str(raw.get("switched_at") or "")
    if not switched_at or len(switched_at) > 80:
        raise ReleaseRegistryError("active route timestamp is invalid")
    return {
        "schema": ROUTE_SCHEMA,
        "profile": profile,
        "revision": revision,
        "active": active,
        "previous": previous,
        "switched_at": switched_at,
    }


def _validate_route_target(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReleaseRegistryError(f"route {field} target is invalid")
    return {
        "slot": normalize_slot(value.get("slot")),
        "release_id": validate_release_id(value.get("release_id")),
        "upstream": validate_upstream(value.get("upstream")),
    }


class ReleaseRegistry:
    def __init__(self, root: Path, *, now: Callable[[], str] = utc_now) -> None:
        self.root = Path(root)
        self.state_path = self.root / "registry.json"
        self.journal_path = self.root / "journal.jsonl"
        self.routes_dir = self.root / "routes"
        self.lock_path = self.root / ".registry.lock"
        self._now = now

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _initial_state() -> dict[str, Any]:
        return {
            "schema": REGISTRY_SCHEMA,
            "revision": 0,
            "runtime_version": RUNTIME_VERSION,
            "releases": {},
            "profiles": {
                profile: {
                    "slots": {
                        slot: {"status": "empty", "release_id": None, "upstream": None, "prepared_at": None, "health_receipt_ref": ""}
                        for slot in SLOTS
                    }
                }
                for profile in PROFILES
            },
        }

    def _read_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._initial_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseRegistryError("release registry is malformed") from exc
        if not isinstance(state, dict) or state.get("schema") != REGISTRY_SCHEMA or state.get("runtime_version") != RUNTIME_VERSION:
            raise ReleaseRegistryError("release registry schema mismatch")
        if not isinstance(state.get("releases"), dict) or not isinstance(state.get("profiles"), dict):
            raise ReleaseRegistryError("release registry structure is invalid")
        return state

    def _write_state_unlocked(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision") or 0) + 1
        _atomic_write_json(self.state_path, state)

    def _read_events_unlocked(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        events: list[dict[str, Any]] = []
        previous = "0" * 64
        try:
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ReleaseRegistryError("release journal cannot be read") from exc
        for index, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReleaseRegistryError("release journal is malformed") from exc
            if not isinstance(event, dict) or event.get("schema") != JOURNAL_SCHEMA or event.get("sequence") != index:
                raise ReleaseRegistryError("release journal sequence is invalid")
            if event.get("previous_sha256") != previous:
                raise ReleaseRegistryError("release journal chain is invalid")
            claimed = str(event.get("event_sha256") or "")
            unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
            if not SHA256_RE.fullmatch(claimed) or _event_digest(unsigned) != claimed:
                raise ReleaseRegistryError("release journal hash is invalid")
            previous = claimed
            events.append(event)
        return events

    def _append_event_unlocked(self, event_type: str, *, release_id: str, profile: str | None = None, slot: str | None = None, **fields: Any) -> dict[str, Any]:
        event_code = validate_reason_code(event_type)
        events = self._read_events_unlocked()
        event: dict[str, Any] = {
            "schema": JOURNAL_SCHEMA,
            "sequence": len(events) + 1,
            "timestamp": self._now(),
            "previous_sha256": events[-1]["event_sha256"] if events else "0" * 64,
            "event_type": event_code,
            "release_id": validate_release_id(release_id),
        }
        if profile is not None:
            event["profile"] = normalize_profile(profile)
        if slot is not None:
            event["slot"] = normalize_slot(slot)
        allowed_fields = {"outcome", "reason_code", "receipt_ref", "quality_verdict", "from_release_id", "from_slot"}
        if set(fields) - allowed_fields:
            raise ReleaseRegistryError("unsupported release journal field")
        for key, value in fields.items():
            if value in (None, ""):
                continue
            if key in {"outcome", "reason_code"}:
                event[key] = validate_reason_code(value)
            elif key == "receipt_ref":
                event[key] = validate_receipt_ref(value)
            elif key == "quality_verdict":
                verdict = str(value).strip().lower()
                if verdict not in QUALITY_VERDICTS:
                    raise ReleaseRegistryError("invalid quality verdict")
                event[key] = verdict
            elif key == "from_release_id":
                event[key] = validate_release_id(value)
            elif key == "from_slot":
                event[key] = normalize_slot(value)
        event["event_sha256"] = _event_digest(event)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.journal_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, _canonical_bytes(event))
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(self.journal_path.parent)
        return copy.deepcopy(event)

    def register_release(
        self,
        *,
        release_id: str,
        artifact_sha256: str,
        manifest_sha256: str,
        source_git_sha: str = "",
        prompt_sha256: str = "",
    ) -> dict[str, Any]:
        rid = validate_release_id(release_id)
        identity = {
            "release_id": rid,
            "runtime_version": RUNTIME_VERSION,
            "artifact_sha256": validate_sha256(artifact_sha256, field="artifact_sha256"),
            "manifest_sha256": validate_sha256(manifest_sha256, field="manifest_sha256"),
            "source_git_sha": validate_git_sha(source_git_sha),
            "prompt_sha256": validate_sha256(prompt_sha256, field="prompt_sha256") if prompt_sha256 else "",
        }
        with self._locked():
            state = self._read_state_unlocked()
            existing = state["releases"].get(rid)
            if existing:
                existing_identity = {key: existing.get(key, "") for key in identity}
                if existing_identity != identity:
                    raise ReleaseRegistryError("release_id already exists with different immutable identity")
                return copy.deepcopy(existing)
            record = {
                **identity,
                "registered_at": self._now(),
                "quality": {"verdict": "unreviewed", "receipt_ref": ""},
                "last_check": None,
            }
            state["releases"][rid] = record
            self._write_state_unlocked(state)
            self._append_event_unlocked("release_registered", release_id=rid)
            return copy.deepcopy(record)

    def list_releases(self) -> list[dict[str, Any]]:
        with self._locked():
            state = self._read_state_unlocked()
            return [copy.deepcopy(state["releases"][rid]) for rid in sorted(state["releases"])]

    def show_release(self, release_id: str) -> dict[str, Any]:
        rid = validate_release_id(release_id)
        with self._locked():
            state = self._read_state_unlocked()
            record = state["releases"].get(rid)
            if not isinstance(record, dict):
                raise ReleaseRegistryError("release_id is not registered")
            active_in: list[dict[str, Any]] = []
            for profile in PROFILES:
                route = self._read_route_unlocked(profile, required=False)
                if route and route["active"]["release_id"] == rid:
                    active_in.append({"profile": profile, "slot": route["active"]["slot"]})
            return {**copy.deepcopy(record), "active_in": active_in}

    def set_quality(self, release_id: str, *, verdict: str, receipt_ref: str) -> dict[str, Any]:
        rid = validate_release_id(release_id)
        normalized = str(verdict or "").strip().lower()
        if normalized not in QUALITY_VERDICTS - {"unreviewed"}:
            raise ReleaseRegistryError("quality verdict must be approved or rejected")
        receipt = validate_receipt_ref(receipt_ref, required=True)
        with self._locked():
            state = self._read_state_unlocked()
            record = state["releases"].get(rid)
            if not isinstance(record, dict):
                raise ReleaseRegistryError("release_id is not registered")
            record["quality"] = {"verdict": normalized, "receipt_ref": receipt, "updated_at": self._now()}
            self._write_state_unlocked(state)
            self._append_event_unlocked("quality_recorded", release_id=rid, quality_verdict=normalized, receipt_ref=receipt)
            return copy.deepcopy(record["quality"])

    def record_check(self, release_id: str, *, profile: str, outcome: str, reason_code: str, receipt_ref: str) -> dict[str, Any]:
        rid = validate_release_id(release_id)
        normalized_profile = normalize_profile(profile)
        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in CHECK_OUTCOMES:
            raise ReleaseRegistryError("check outcome must be passed or failed")
        reason = validate_reason_code(reason_code)
        receipt = validate_receipt_ref(receipt_ref, required=True)
        with self._locked():
            state = self._read_state_unlocked()
            record = state["releases"].get(rid)
            if not isinstance(record, dict):
                raise ReleaseRegistryError("release_id is not registered")
            check = {"profile": normalized_profile, "outcome": normalized_outcome, "reason_code": reason, "receipt_ref": receipt, "checked_at": self._now()}
            record["last_check"] = check
            self._write_state_unlocked(state)
            self._append_event_unlocked("check_recorded", release_id=rid, profile=normalized_profile, outcome=normalized_outcome, reason_code=reason, receipt_ref=receipt)
            return copy.deepcopy(check)

    def prepare_slot(self, *, profile: str, slot: str, release_id: str, upstream: str, health_receipt_ref: str) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        rid = validate_release_id(release_id)
        target = validate_upstream(upstream)
        receipt = validate_receipt_ref(health_receipt_ref, required=True)
        with self._locked():
            state = self._read_state_unlocked()
            if rid not in state["releases"]:
                raise ReleaseRegistryError("release_id is not registered")
            slot_state = {
                "status": "ready",
                "release_id": rid,
                "upstream": target,
                "prepared_at": self._now(),
                "health_receipt_ref": receipt,
            }
            state["profiles"][normalized_profile]["slots"][normalized_slot] = slot_state
            self._write_state_unlocked(state)
            self._append_event_unlocked("slot_prepared", release_id=rid, profile=normalized_profile, slot=normalized_slot, outcome="passed", receipt_ref=receipt)
            return copy.deepcopy(slot_state)

    def begin_slot_prepare(self, *, profile: str, slot: str, release_id: str, upstream: str) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        rid = validate_release_id(release_id)
        target = validate_upstream(upstream)
        with self._locked():
            state = self._read_state_unlocked()
            if rid not in state["releases"]:
                raise ReleaseRegistryError("release_id is not registered")
            slot_state = {
                "status": "preparing",
                "release_id": rid,
                "upstream": target,
                "prepared_at": None,
                "health_receipt_ref": "",
            }
            state["profiles"][normalized_profile]["slots"][normalized_slot] = slot_state
            self._write_state_unlocked(state)
            self._append_event_unlocked("slot_prepare_started", release_id=rid, profile=normalized_profile, slot=normalized_slot)
            return copy.deepcopy(slot_state)

    def fail_slot_prepare(
        self,
        *,
        profile: str,
        slot: str,
        release_id: str,
        reason_code: str,
        receipt_ref: str,
    ) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        rid = validate_release_id(release_id)
        reason = validate_reason_code(reason_code)
        receipt = validate_receipt_ref(receipt_ref, required=True)
        with self._locked():
            state = self._read_state_unlocked()
            current = state["profiles"][normalized_profile]["slots"][normalized_slot]
            if current.get("release_id") != rid:
                raise ReleaseRegistryError("slot preparation identity changed")
            slot_state = {
                **current,
                "status": "failed",
                "prepared_at": None,
                "health_receipt_ref": receipt,
            }
            state["profiles"][normalized_profile]["slots"][normalized_slot] = slot_state
            self._write_state_unlocked(state)
            self._append_event_unlocked(
                "slot_prepare_failed",
                release_id=rid,
                profile=normalized_profile,
                slot=normalized_slot,
                outcome="failed",
                reason_code=reason,
                receipt_ref=receipt,
            )
            return copy.deepcopy(slot_state)

    def slot_state(self, *, profile: str, slot: str) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        with self._locked():
            state = self._read_state_unlocked()
            return copy.deepcopy(state["profiles"][normalized_profile]["slots"][normalized_slot])

    def route_path(self, profile: str) -> Path:
        return self.routes_dir / f"{normalize_profile(profile).lower()}.json"

    def _read_route_unlocked(self, profile: str, *, required: bool) -> dict[str, Any] | None:
        path = self.route_path(profile)
        if not path.exists() and not required:
            return None
        return read_route_file(path, expected_profile=profile)

    def read_route(self, profile: str, *, required: bool = True) -> dict[str, Any] | None:
        normalized_profile = normalize_profile(profile)
        with self._locked():
            return copy.deepcopy(self._read_route_unlocked(normalized_profile, required=required))

    def _restore_route_unlocked(self, profile: str, previous_route: dict[str, Any] | None) -> None:
        path = self.route_path(profile)
        if previous_route is None:
            if path.exists():
                path.unlink()
                _fsync_directory(path.parent)
            return
        _atomic_write_json(path, previous_route)

    def _switch_route_unlocked(
        self,
        *,
        profile: str,
        target: Mapping[str, str],
        event_type: str,
        reason_code: str,
        post_switch_check: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        previous_route = self._read_route_unlocked(profile, required=False)
        if previous_route and previous_route["active"] == dict(target):
            return previous_route
        previous_target = previous_route["active"] if previous_route else None
        next_route = {
            "schema": ROUTE_SCHEMA,
            "profile": profile,
            "revision": (previous_route["revision"] if previous_route else 0) + 1,
            "active": dict(target),
            "previous": copy.deepcopy(previous_target),
            "switched_at": self._now(),
        }
        self._append_event_unlocked(
            "activation_started" if event_type == "release_activated" else "rollback_started",
            release_id=target["release_id"],
            profile=profile,
            slot=target["slot"],
            reason_code=reason_code,
            from_release_id=previous_target["release_id"] if previous_target else None,
            from_slot=previous_target["slot"] if previous_target else None,
        )
        route_written = False
        try:
            _atomic_write_json(self.route_path(profile), next_route)
            route_written = True
            if post_switch_check is not None:
                post_switch_check(copy.deepcopy(next_route))
            self._append_event_unlocked(
                event_type,
                release_id=target["release_id"],
                profile=profile,
                slot=target["slot"],
                outcome="passed",
                reason_code=reason_code,
                from_release_id=previous_target["release_id"] if previous_target else None,
                from_slot=previous_target["slot"] if previous_target else None,
            )
        except Exception as exc:
            if route_written:
                try:
                    self._restore_route_unlocked(profile, previous_route)
                except Exception as restore_exc:
                    raise ReleaseRegistryError(f"route switch failed and route restore failed: {restore_exc}") from exc
            raise
        return copy.deepcopy(next_route)

    def activate(
        self,
        *,
        profile: str,
        slot: str,
        reason_code: str = "manual",
        post_switch_check: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        normalized_slot = normalize_slot(slot)
        reason = validate_reason_code(reason_code)
        with self._locked():
            state = self._read_state_unlocked()
            slot_state = state["profiles"][normalized_profile]["slots"][normalized_slot]
            if slot_state.get("status") != "ready":
                raise ReleaseRegistryError("slot is not ready")
            target = _validate_route_target(
                {"slot": normalized_slot, "release_id": slot_state.get("release_id"), "upstream": slot_state.get("upstream")},
                field="slot",
            )
            return self._switch_route_unlocked(
                profile=normalized_profile,
                target=target,
                event_type="release_activated",
                reason_code=reason,
                post_switch_check=post_switch_check,
            )

    def rollback(
        self,
        *,
        profile: str,
        reason_code: str = "manual_rollback",
        post_switch_check: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        normalized_profile = normalize_profile(profile)
        reason = validate_reason_code(reason_code)
        with self._locked():
            current = self._read_route_unlocked(normalized_profile, required=True)
            assert current is not None
            previous = current.get("previous")
            if not previous:
                raise ReleaseRegistryError("no previous release is available for rollback")
            state = self._read_state_unlocked()
            prepared = state["profiles"][normalized_profile]["slots"][previous["slot"]]
            if prepared.get("status") != "ready" or prepared.get("release_id") != previous["release_id"] or prepared.get("upstream") != previous["upstream"]:
                raise ReleaseRegistryError("previous release is no longer warm and ready")
            return self._switch_route_unlocked(
                profile=normalized_profile,
                target=previous,
                event_type="release_rolled_back",
                reason_code=reason,
                post_switch_check=post_switch_check,
            )

    def journal_events(self) -> list[dict[str, Any]]:
        with self._locked():
            return copy.deepcopy(self._read_events_unlocked())

    def sync_release_to(self, destination: "ReleaseRegistry", release_id: str) -> dict[str, Any]:
        source = self.show_release(release_id)
        return destination.register_release(
            release_id=source["release_id"],
            artifact_sha256=source["artifact_sha256"],
            manifest_sha256=source["manifest_sha256"],
            source_git_sha=source.get("source_git_sha", ""),
            prompt_sha256=source.get("prompt_sha256", ""),
        )
