#!/usr/bin/env python3
"""V6-only client-visible egress guard for exact TEST/PROD profiles."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping


PROFILES = frozenset({"TEST", "PROD"})
SAFE_CLIENT_FALLBACK_TEXT = "Извините, я не могу сейчас корректно показать ответ. Давайте продолжим подбор квартиры обычным сообщением."
_CODE_FENCE_RE = re.compile(r"```")
_RUNTIME_MARKER_RE = re.compile(r"(?<![\wА-Яа-яЁё])V(\d+)(?![\wА-Яа-яЁё])", re.I)
_START_MARKER_RE = re.compile(r"(?<!\S)/start_(\d+)(?!\S)", re.I)
_TECH_MARKERS = (
    "MCP", "OpenRouter", "Overmind", "gateway-agent", "runtime_version", "raw_payload", "payload",
    "traceback", "task_id", "trace_id", "provider_token", "api_token", "bridge_token",
    "NMBOT_API_TOKEN", "NMBOT_N8N_BRIDGE_TOKEN", "JIVO_PROVIDER_TOKEN", "JIVO_PROVIDER_ID",
)
_TECH_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])(?:" + "|".join(re.escape(item).replace(r"\_", r"[_\-\s]+") for item in _TECH_MARKERS) + r")(?![\wА-Яа-яЁё])",
    re.I,
)
_INFRA_RE = re.compile(r"(?:localhost|/(?:home|tmp|var)/\S*|\bsystemd\b|[\w.-]+\.service\b)", re.I)
_URL_RE = re.compile(r"https?://[^\s)>'\"]+", re.I)
_IP_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<![\w:])::1(?![\w:])")
_DIAG_KEYS = {"ok", "error", "meta", "trace", "runtime_version", "payload", "raw_payload", "task_id", "trace_id", "stack", "exception"}


@dataclass(frozen=True)
class EgressGuardResult:
    text: str
    blocked: bool = False
    blocker_code: str | None = None


def contour_profile(environ: Mapping[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    raw = str(env.get("NMBOT_CONTOUR_PROFILE") or "TEST").strip().upper()
    if raw not in PROFILES:
        raise ValueError("NMBOT_CONTOUR_PROFILE must be exactly TEST or PROD")
    return raw


def is_production(environ: Mapping[str, str] | None = None) -> bool:
    return contour_profile(environ) == "PROD"


def sanitize_client_text(text: Any, *, profile: str | None = None) -> EgressGuardResult:
    selected = contour_profile({"NMBOT_CONTOUR_PROFILE": profile}) if profile is not None else contour_profile()
    value = str(text or "")
    if selected != "PROD":
        return EgressGuardResult(value)
    blocker = _blocker_code(value)
    if blocker:
        return EgressGuardResult(SAFE_CLIENT_FALLBACK_TEXT, blocked=True, blocker_code=blocker)
    return EgressGuardResult(value)


def guard_jivo_event(event: dict[str, Any], *, profile: str | None = None) -> tuple[dict[str, Any], EgressGuardResult | None]:
    if not isinstance(event, dict) or event.get("event") != "BOT_MESSAGE":
        return event, None
    guarded = dict(event)
    message = dict(guarded.get("message")) if isinstance(guarded.get("message"), dict) else {}
    result = sanitize_client_text(message.get("text"), profile=profile)
    message["text"] = result.text
    guarded["message"] = message
    return guarded, result


def _blocker_code(text: str) -> str | None:
    if not text:
        return None
    if _CODE_FENCE_RE.search(text):
        return "code_fence"
    if any(int(match.group(1)) != 6 for match in _START_MARKER_RE.finditer(text)):
        return "start_version_marker"
    if any(int(match.group(1)) != 6 for match in _RUNTIME_MARKER_RE.finditer(text)):
        return "runtime_version_marker"
    if _TECH_RE.search(text):
        return "technical_marker"
    if _INFRA_RE.search(text):
        return "infrastructure_marker"
    if _has_private_ip_or_internal_url(text):
        return "internal_network_marker"
    if _looks_like_diagnostic_json(text):
        return "json_diagnostic"
    return None


def _has_private_ip_or_internal_url(text: str) -> bool:
    candidates = [match.group(0) for match in _IP_RE.finditer(text)]
    for match in _URL_RE.finditer(text):
        host = re.sub(r"^https?://", "", match.group(0), flags=re.I).split("/", 1)[0].split(":", 1)[0].strip("[]")
        if host.lower() == "localhost":
            return True
        candidates.append(host)
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
            return True
    return False


def _contains_diag_keys(value: Any, *, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, dict):
        return bool({str(key) for key in value} & _DIAG_KEYS) or any(_contains_diag_keys(item, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_diag_keys(item, depth=depth + 1) for item in value[:20])
    return False


def _looks_like_diagnostic_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        return _contains_diag_keys(json.loads(stripped))
    except json.JSONDecodeError:
        return False
