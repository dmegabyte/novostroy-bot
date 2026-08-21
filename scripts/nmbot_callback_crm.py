#!/usr/bin/env python3
"""Privacy-bounded CRM callback adapter using only urllib."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CRMResult:
    status: str
    retryable: bool = False
    uncertain: bool = False
    error_class: str = ""
    receipt: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "retryable": self.retryable,
            "uncertain": self.uncertain,
            "error_class": self.error_class,
            "receipt": self.receipt[:120],
        }


Transport = Callable[[str, bytes, float], tuple[int, bytes]]


def _urllib_transport(endpoint: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(4096)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(4096)


class CallbackCRMAdapter:
    def __init__(self, *, endpoint: str | None = None, transport: Transport | None = None, timeout: float = 10.0) -> None:
        self._endpoint = str(endpoint if endpoint is not None else os.getenv("NMBOT_CALLBACK_CRM_ENDPOINT") or "").strip()
        self._transport = transport or _urllib_transport
        self._timeout = max(0.1, min(float(timeout), 30.0))

    def send_callback(self, *, phone: str, name: str, summary: str) -> CRMResult:
        if not self._endpoint:
            return CRMResult(status="failed", error_class="crm_configuration_error")
        payload = {
            "phone": str(phone or "")[:40],
            "name": str(name or "")[:100],
            "request": str(summary or "")[:1000],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            status, raw = self._transport(self._endpoint, body, self._timeout)
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            return CRMResult(status="uncertain", uncertain=True, error_class="crm_transport_uncertain")
        if status == 429 or 500 <= status <= 599:
            return CRMResult(status="retryable_error", retryable=True, error_class=f"crm_http_{status}")
        if status in {401, 403, 404}:
            return CRMResult(status="failed", error_class=f"crm_http_{status}")
        if not 200 <= status <= 299:
            return CRMResult(status="failed", error_class="crm_http_terminal")
        try:
            response = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return CRMResult(status="failed", error_class="crm_invalid_response")
        if not isinstance(response, dict) or response.get("ok") is not True:
            return CRMResult(status="failed", error_class="crm_invalid_response")
        receipt = response.get("receipt")
        safe_receipt = str(receipt)[:120] if isinstance(receipt, (str, int)) else ""
        return CRMResult(status="ok", receipt=safe_receipt)
