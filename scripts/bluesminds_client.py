"""Reusable Python client for the Bluesminds API.

The module intentionally depends only on the Python standard library so it can
be used by the CLI and imported by small scripts without extra installation
steps.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional


DEFAULT_BASE_URL = "https://api.bluesminds.com"
DEFAULT_TIMEOUT = 60.0


class BluesmindsError(Exception):
    """User-facing Bluesminds API/client error.

    Attributes:
        status_code: HTTP status code for API errors, otherwise ``None``.
        response_body: Decoded error response body where available.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _env_timeout() -> float:
    raw = os.environ.get("BLUESMINDS_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError as exc:
        raise BluesmindsError("BLUESMINDS_TIMEOUT должен быть числом секунд") from exc
    if value <= 0:
        raise BluesmindsError("BLUESMINDS_TIMEOUT должен быть больше нуля")
    return value


def _decode_body(body: bytes, content_type: str = "") -> Any:
    text = body.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _build_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return base_url.rstrip("/") + path


def _short_error_body(body: Any) -> str:
    if isinstance(body, (dict, list)):
        text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    else:
        text = str(body or "")
    text = text.strip()
    if len(text) > 1000:
        return text[:1000] + "..."
    return text


class BluesmindsClient:
    """Small urllib-based Bluesminds API client.

    Args:
        api_key: API key. Defaults to ``BLUESMINDS_API_KEY``.
        base_url: API base URL. Defaults to ``BLUESMINDS_BASE_URL`` or the
            production endpoint.
        timeout: Request timeout in seconds. Defaults to ``BLUESMINDS_TIMEOUT``
            or 60 seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BLUESMINDS_API_KEY")
        if not self.api_key:
            raise BluesmindsError(
                "Не найден API-ключ. Укажите api_key или переменную BLUESMINDS_API_KEY."
            )
        self.base_url = (base_url or os.environ.get("BLUESMINDS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout) if timeout is not None else _env_timeout()
        if self.timeout <= 0:
            raise BluesmindsError("timeout должен быть больше нуля")

    def request(
        self,
        method: str,
        path: str,
        data: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Send an HTTP request and return a decoded JSON or text response."""

        request_headers: Dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "bluesminds-python/0.1",
        }
        body: Optional[bytes] = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        for name, value in (headers or {}).items():
            request_headers[name] = value

        req = urllib.request.Request(
            _build_url(self.base_url, path),
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "") if response.headers else ""
                return _decode_body(response.read(), content_type)
        except urllib.error.HTTPError as exc:
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            response_body = _decode_body(exc.read(), content_type)
            message = _short_error_body(response_body) or exc.reason
            raise BluesmindsError(
                f"HTTP {exc.code}: {message}",
                status_code=exc.code,
                response_body=response_body,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise BluesmindsError("Запрос превысил таймаут") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
                raise BluesmindsError("Запрос превысил таймаут") from exc
            raise BluesmindsError(f"Ошибка соединения: {reason}") from exc

    def chat(
        self,
        model: str,
        messages: Any,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict:
        """Create a chat completion via ``POST /v1/chat/completions``."""

        payload: Dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = self.request("POST", "/v1/chat/completions", data=payload)
        if not isinstance(response, dict):
            raise BluesmindsError("API вернул не JSON-объект для chat")
        return response

    def models(self) -> dict:
        """Return the model catalog via ``GET /v1/models``."""

        response = self.request("GET", "/v1/models")
        if not isinstance(response, dict):
            raise BluesmindsError("API вернул не JSON-объект для models")
        return response
