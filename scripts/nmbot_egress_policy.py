#!/usr/bin/env python3
"""Client-visible egress guard for isolated nmbot contours."""

from __future__ import annotations

import os
import re
import json
import ipaddress
from dataclasses import dataclass
from typing import Any


CLIENT_PRODUCTION_PROFILE = "client_production"
SAFE_CLIENT_FALLBACK_TEXT = "Извините, я не могу сейчас корректно показать ответ. Давайте продолжим подбор квартиры обычным сообщением."

_VERSION_DECORATION_RE = re.compile(r"(?:^|\n)\s*Сейчас\s+активна\s+версия:\s*V[0-4]\.\s*(?=\n|$)", re.I)
_CODE_FENCE_RE = re.compile(r"```")
_VERSION_MARKER_RE = re.compile(r"(?<![\wА-Яа-яЁё])V[0-4](?![\wА-Яа-яЁё])", re.I)
_START_VERSION_RE = re.compile(r"(?<!\S)/start_[01234](?!\S)", re.I)
_TECH_MARKERS = (
    "MCP", "OpenRouter", "Overmind", "gateway-agent", "response_composer", "manager_rewriter",
    "OptionCard", "ResponseBrief", "ResponsePlan", "IntentPlanV3", "runtime_version", "raw_payload", "payload",
    "traceback", "task_id", "trace_id", "provider_token", "api_token", "bridge_token", "NMBOT_API_TOKEN",
    "NMBOT_N8N_BRIDGE_TOKEN", "JIVO_PROVIDER_TOKEN", "JIVO_PROVIDER_ID",
)
def _marker_pattern(marker: str) -> str:
    return r"[_\-\s]+".join(re.escape(part) for part in re.split(r"[_\-\s]+", marker) if part)


_TECH_MARKER_RE = re.compile(
    r"(?<![\wА-Яа-яЁё])(?:" + "|".join(_marker_pattern(marker) for marker in _TECH_MARKERS) + r")(?![\wА-Яа-яЁё])",
    re.I,
)
_INFRA_RE = re.compile(r"(?:localhost|/home/\S*|/tmp/\S*|/var/\S*|\bsystemd\b|[\w.-]+\.service\b)", re.I)
_URL_RE = re.compile(r"https?://[^\s)>'\"]+", re.I)
_IP_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<![\w:])::1(?![\w:])")
_DIAG_JSON_KEYS = {"ok", "error", "meta", "trace", "runtime_version", "payload", "raw_payload", "task_id", "trace_id", "stack", "exception"}


@dataclass(frozen=True)
class EgressGuardResult:
    text: str
    blocked: bool = False
    blocker_code: str | None = None


def contour_profile(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get("NMBOT_CONTOUR_PROFILE") or "test").strip().lower() or "test"


def is_client_production(environ: dict[str, str] | None = None) -> bool:
    return contour_profile(environ) == CLIENT_PRODUCTION_PROFILE


def sanitize_client_text(text: Any, *, profile: str | None = None) -> EgressGuardResult:
    selected_profile = (profile or contour_profile()).strip().lower()
    value = str(text or "")
    if selected_profile != CLIENT_PRODUCTION_PROFILE:
        return EgressGuardResult(value)

    value = _VERSION_DECORATION_RE.sub("\n", value).strip()
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    blocker = _blocker_code(value)
    if blocker:
        return EgressGuardResult(SAFE_CLIENT_FALLBACK_TEXT, blocked=True, blocker_code=blocker)
    return EgressGuardResult(value)


def guard_jivo_event(event: dict[str, Any], *, profile: str | None = None) -> tuple[dict[str, Any], EgressGuardResult | None]:
    if not isinstance(event, dict) or event.get("event") != "BOT_MESSAGE":
        return event, None
    guarded = dict(event)
    message = guarded.get("message") if isinstance(guarded.get("message"), dict) else {}
    guarded_message = dict(message)
    result = sanitize_client_text(guarded_message.get("text"), profile=profile)
    guarded_message["text"] = result.text
    guarded["message"] = guarded_message
    return guarded, result


def _blocker_code(text: str) -> str | None:
    if not text:
        return None
    if _CODE_FENCE_RE.search(text):
        return "code_fence"
    if _START_VERSION_RE.search(text):
        return "start_version_marker"
    if _VERSION_MARKER_RE.search(text):
        return "runtime_version_marker"
    if _TECH_MARKER_RE.search(text):
        return "technical_marker"
    if _INFRA_RE.search(text):
        return "infrastructure_marker"
    if _has_private_ip_or_internal_url(text):
        return "internal_network_marker"
    if _looks_like_diagnostic_json(text):
        return "json_diagnostic"
    return None


def _has_private_ip_or_internal_url(text: str) -> bool:
    for match in _IP_RE.finditer(text):
        try:
            ip = ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return True
    for match in _URL_RE.finditer(text):
        url = match.group(0)
        host = re.sub(r"^https?://", "", url, flags=re.I).split("/", 1)[0].split(":", 1)[0].strip("[]")
        if host.lower() in {"localhost"}:
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return True
    return False


def _contains_diag_keys(value: Any, *, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        if keys & _DIAG_JSON_KEYS:
            return True
        return any(_contains_diag_keys(item, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(_contains_diag_keys(item, depth=depth + 1) for item in value[:20])
    return False


def _looks_like_diagnostic_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    try:
        parsed = json.loads(stripped)
    except Exception:
        return False
    return _contains_diag_keys(parsed)
