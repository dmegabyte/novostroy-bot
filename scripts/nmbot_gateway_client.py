from __future__ import annotations

import json
import logging
import os
import re
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import aiohttp


REPO_ROOT = Path(__file__).resolve().parent.parent
OVERMIND_URL = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
OVERMIND_TOKEN = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN", "")
OPENROUTER_EXCLUDE_REASONING = os.getenv("NMBOT_OPENROUTER_EXCLUDE_REASONING", "0").strip().lower() in {"1", "true", "yes", "on"}
PROVIDER_ERROR_RETRY_MODEL: Final[str] = "deepseek/deepseek-v4-flash"
MAIN_SEARCH_FALLBACK_MODELS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(
        model.strip()
        for model in os.getenv(
            "NMBOT_MAIN_SEARCH_FALLBACK_MODELS",
            "google/gemini-3.5-flash,openai/gpt-5.5",
        ).split(",")
        if model.strip()
    )
)
MAIN_SEARCH_FALLBACK_ENABLED = os.getenv("NMBOT_MAIN_SEARCH_FALLBACK_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}

SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
CHAT_MODEL = "google/gemini-2.5-flash"
SAFE_UPSTREAM_ERROR_TEXT = (
    "По запросу не удалось найти информацию. Точный ответ уточнит оператор. В текущих данных это не подтверждено. Передать оператору запрос?"
)

LOGS_DIR: Final[Path] = REPO_ROOT / "logs"
GATEWAY_FORENSIC_LOG_DIR: Final[Path] = LOGS_DIR / "forensic"
LOGGER = logging.getLogger(__name__)


GATEWAY_ERROR_SHAPE_KEYS: Final[set[str]] = {
    "code",
    "error",
    "id",
    "metadata",
    "message",
    "response",
    "result",
    "status",
    "type",
}


def _provider_error_text(error: Any, *, limit: int = 4000) -> str:
    try:
        if isinstance(error, (dict, list)):
            text = json.dumps(error, ensure_ascii=False, default=str)
        else:
            text = str(error or "")
    except Exception:
        text = str(type(error).__name__)
    return text[:limit]


def _provider_error_code(error: Any) -> str | None:
    """Allowlisted provider/model failure classifier for gateway retry.

    Keep this strict: ordinary empty/no-results search payloads must not trigger
    a provider retry.  This mirrors the canonical legacy gateway classifier.
    """
    raw = _provider_error_text(error, limit=4000)
    text = raw.strip().lower()
    if not text:
        return None
    if "corrupted thought signature" in text:
        return "corrupted_thought_signature"
    if "invalid_argument" in text and "provider" in text:
        return "provider_invalid_argument"
    choices_markers = ("'choices'", '"choices"', " choices", "choices ")
    if any(marker in text for marker in choices_markers) and any(
        marker in text
        for marker in (
            "missing",
            "keyerror",
            "key error",
            "parse",
            "parser",
            "response",
            "no choices",
            "without choices",
        )
    ):
        return "choices_response_parse"
    if "response parse" in text or "parse response" in text or "response parsing" in text:
        return "response_parse"
    return None


def _with_provider_retry_metadata(meta: dict[str, Any], *, code: str, attempted: bool) -> dict[str, Any]:
    result = dict(meta)
    if code:
        result["_provider_error_code"] = code
    result["_provider_retry_attempted"] = attempted
    result["_provider_retry_model"] = PROVIDER_ERROR_RETRY_MODEL
    return result


def _provider_retry_request_data(request_data: dict[str, Any]) -> dict[str, Any]:
    retry = dict(request_data)
    retry["model"] = PROVIDER_ERROR_RETRY_MODEL
    retry.pop("reasoning", None)
    retry["reasoning"] = {"exclude": True}
    return retry


def _response_payload_to_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("response", "text", "content", "answer"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""
    return ""


def _safe_gateway_task_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", text):
        return None
    return text


def _safe_gateway_status(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"completed", "failed", "cancelled", "running", "queued", "pending", "created"}:
        return text
    return None


def _gateway_payload_shape(value: Any) -> dict[str, Any]:
    shape: dict[str, Any] = {"payload_type": type(value).__name__}
    if isinstance(value, dict):
        keys = [str(key) for key in value.keys()]
        safe_keys = sorted(key for key in keys if key in GATEWAY_ERROR_SHAPE_KEYS)
        shape["payload_key_count"] = len(keys)
        if safe_keys:
            shape["payload_keys"] = safe_keys[:12]
    elif isinstance(value, list):
        shape["payload_item_count"] = min(len(value), 1000)
    return shape


def _gateway_error_event(
    *,
    error_type: str,
    stage: str,
    payload_stage: str | None = None,
    task_id: Any = None,
    task_status: Any = None,
    status: Any = None,
    duration_ms: Any = None,
    parse_status: Any = None,
    error: Any = None,
    payload: Any = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "error_type": error_type,
        "severity": "error",
        "stage": stage,
    }
    if payload_stage:
        event["payload_stage"] = str(payload_stage)[:80]
    safe_task_id = _safe_gateway_task_id(task_id)
    if safe_task_id:
        event["task_id"] = safe_task_id
    safe_status = _safe_gateway_status(task_status or status)
    if safe_status:
        event["status"] = safe_status
    if duration_ms is not None:
        event["duration_ms"] = _bounded_duration_ms(duration_ms)
    if str(parse_status or "") in {"ok", "invalid_json", "missing"}:
        event["parse_status"] = str(parse_status)
    if error is not None:
        event["exception_type"] = type(error).__name__
        code = _provider_error_code(error)
        if code:
            event["error_code"] = code
    if payload is not None:
        event.update(_gateway_payload_shape(payload))
    if extra:
        for key in ("http_status", "payload_type"):
            value = extra.get(key)
            if key == "http_status":
                try:
                    event[key] = int(value)
                except (TypeError, ValueError):
                    pass
            elif key == "payload_type" and isinstance(value, str):
                event[key] = value[:80]
    return event


def _bounded_duration_ms(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, 10 * 60 * 1000))


def _main_search_parse_status(text: Any) -> str:
    raw = str(text or "").strip()
    if not raw or _is_safe_upstream_fallback(raw):
        return "missing"
    return "ok" if _main_search_payload(raw) is not None else "invalid_json"


def _main_search_attempt_detail(*, model: Any, text: Any, meta: dict[str, Any] | None, duration_ms: Any, task_id: Any = None, ok: bool | None = None) -> dict[str, Any]:
    safe_meta = meta if isinstance(meta, dict) else {}
    parse_status = _main_search_parse_status(text)
    detail: dict[str, Any] = {
        "stage": "gateway_attempt",
        "model": str(model or "")[:80],
        "ok": bool(_is_usable_main_search_result(text, safe_meta) if ok is None else ok),
        "empty": bool(_is_empty_main_search_result(text)),
        "safe": bool(safe_meta.get("_safe_fallback")),
        "duration_ms": _bounded_duration_ms(duration_ms),
        "parse_status": parse_status,
    }
    safe_task_id = _safe_gateway_task_id(task_id or safe_meta.get("_gateway_task_id"))
    if safe_task_id:
        detail["gateway_task_id"] = safe_task_id
    return detail


def _with_main_search_attempt_meta(meta: dict[str, Any], *, payload_stage: str, model: str, text: Any, started: float, task_id: Any = None, ok: bool | None = None) -> dict[str, Any]:
    if payload_stage != "main_search":
        return meta
    detail = _main_search_attempt_detail(
        model=model,
        text=text,
        meta=meta,
        duration_ms=round((time.monotonic() - started) * 1000),
        task_id=task_id,
        ok=ok,
    )
    return {**meta, "_main_search_attempt": detail}


def _is_safe_upstream_fallback(text: Any) -> bool:
    normalized = str(text or "").strip()
    return normalized == SAFE_UPSTREAM_ERROR_TEXT or normalized.lower() in {"", "none", "null"}


def _main_search_payload(text: Any) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_malformed_main_search_result(text: Any) -> bool:
    raw = str(text or "").strip()
    if not raw or _is_safe_upstream_fallback(raw):
        return False
    return _main_search_payload(raw) is None


def _is_empty_main_search_result(text: Any) -> bool:
    payload = _main_search_payload(text)
    if payload is None:
        return False
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    near = payload.get("near") if isinstance(payload.get("near"), list) else []
    return not facts and not near


def _is_usable_main_search_result(text: Any, meta: dict[str, Any] | None = None) -> bool:
    if (meta or {}).get("_safe_fallback") or _is_safe_upstream_fallback(text):
        return False
    payload = _main_search_payload(text)
    if payload is None:
        return False
    facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    near = payload.get("near") if isinstance(payload.get("near"), list) else []
    return bool(facts or near)


def _strip_markdown(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        first_nl = value.find("\n")
        if first_nl > 0:
            value = value[first_nl + 1 :]
        if value.endswith("```"):
            value = value[:-3].rstrip()
    return value


def _format_numbered_list_spacing(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    out: list[str] = []
    seen_list_item = False
    for line in lines:
        stripped = line.strip()
        is_item = bool(re.match(r"^\s*(?:\d+\.|[-•*])\s+", line))
        is_question = stripped.endswith("?")
        if is_item and out and out[-1] != "":
            out.append("")
        if is_question and seen_list_item and out and out[-1] != "":
            out.append("")
        out.append(line)
        seen_list_item = seen_list_item or is_item
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _prepare_response_text(text: str) -> str:
    raw = _strip_markdown(str(text or "")).strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            if isinstance(data, dict) and isinstance(data.get("response"), str):
                raw = data["response"]
    except json.JSONDecodeError:
        pass
    raw = re.sub(r"ЖК\s+«(ЖК|ГК)\s+«([^»]+)»»", r"\1 «\2»", raw)
    raw = re.sub(r"ЖК\s+«([^»]+)»»", r"ЖК «\1»", raw)
    return _format_numbered_list_spacing(raw)


def _log_error_event(event: dict[str, Any]) -> None:
    try:
        row = dict(event)
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"bot_error_events-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
    except Exception as exc:
        LOGGER.warning("error event log failed: %s", exc)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _gateway_forensic_log_enabled() -> bool:
    """Return whether high-sensitivity raw gateway-result capture is enabled."""
    return _env_flag("NMBOT_GATEWAY_FORENSIC_LOG_ENABLED")


def _gateway_forensic_file_path(directory: Path, day: str, record_size: int, max_bytes: int) -> Path:
    """Select a daily JSONL file that can hold the whole record unmodified."""
    for index in range(10_000):
        suffix = "" if index == 0 else f"-{index:03d}"
        path = directory / f"gateway-result-{day}{suffix}.jsonl"
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return path
        if size == 0 or size + record_size <= max_bytes:
            return path
    raise RuntimeError("forensic log rotation exhausted")


def _cleanup_gateway_forensic_logs(directory: Path, *, now: datetime, retention_days: int) -> None:
    cutoff = now.date().toordinal() - retention_days
    for path in directory.glob("gateway-result-????-??-??*.jsonl"):
        match = re.fullmatch(r"gateway-result-(\d{4}-\d{2}-\d{2})(?:-\d{3})?\.jsonl", path.name)
        if not match:
            continue
        try:
            file_day = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            if file_day.toordinal() < cutoff:
                path.unlink()
        except (OSError, ValueError):
            continue


def _log_gateway_forensic_result(*, payload_stage: str, model: str, task_id: Any, task_status: Any, result: Any) -> None:
    """Append a complete raw gateway result to the isolated opt-in forensic log.

    This path intentionally stores model output and must never feed ordinary
    diagnostics or request metadata. Failures are best-effort by design.
    """
    if not _gateway_forensic_log_enabled():
        return
    try:
        now = datetime.now(timezone.utc)
        row = {
            "ts": now.isoformat(),
            "payload_stage": str(payload_stage)[:80],
            "model": str(model)[:200],
            "task_id": _safe_gateway_task_id(task_id),
            "status": _safe_gateway_status(task_status),
            "raw_gateway_result": result,
        }
        encoded = (json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n").encode("utf-8")
        directory = GATEWAY_FORENSIC_LOG_DIR
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        _cleanup_gateway_forensic_logs(
            directory,
            now=now,
            retention_days=_positive_env_int("NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS", 7),
        )
        path = _gateway_forensic_file_path(
            directory,
            now.date().isoformat(),
            len(encoded),
            _positive_env_int("NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES", 10 * 1024 * 1024),
        )
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.chmod(path, 0o600)
            offset = 0
            while offset < len(encoded):
                written = os.write(fd, encoded[offset:])
                if written <= 0:
                    raise OSError("forensic log write made no progress")
                offset += written
        finally:
            os.close(fd)
    except Exception:
        LOGGER.warning("gateway forensic log failed")


def _log_model_payload_metrics(stage: str, request_data: dict[str, Any], *, retry: int | None = None) -> None:
    try:
        parameters = request_data.get("parameters") if isinstance(request_data.get("parameters"), dict) else {}
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "model_payload_metrics",
            "stage": str(stage or "unknown"),
            "model": str(request_data.get("model") or ""),
            "service": str(request_data.get("service") or ""),
            "query_chars": len(str(request_data.get("query") or "")),
            "system_prompt_chars": len(str(request_data.get("system_prompt") or "")),
            "max_tokens": parameters.get("max_tokens"),
            "temperature": parameters.get("temperature"),
            "has_mcp": bool(request_data.get("mcp_servers")),
        }
        if retry is not None:
            row["retry"] = retry
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"model_payload_metrics-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        LOGGER.warning("model payload metrics log failed: %s", exc)


class OvermindClient:
    """Channel-neutral Overmind gateway client for API/Jivo V2 runtime.

    This intentionally contains only session management and low-level task
    transport semantics shared with the historical Telegram client.
    """

    def __init__(self) -> None:
        self.session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def ensure_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    def _gateway_client_impl(self) -> str:
        return f"{self.__class__.__module__}.{self.__class__.__name__}"

    def _with_gateway_client_impl(self, meta: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(meta or {})
        result["_gateway_client_impl"] = self._gateway_client_impl()
        return result

    async def _run_gateway_request(self, request_data: dict[str, Any], headers: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
        original_request_data = dict(request_data)
        first_text, first_meta = await self._run_gateway_request_once(original_request_data, headers, timeout)
        provider_error_code = first_meta.get("_provider_error_code") if isinstance(first_meta, dict) else None
        requested_model = str(original_request_data.get("model") or "")
        if provider_error_code and requested_model != PROVIDER_ERROR_RETRY_MODEL:
            retry_text, retry_meta = await self._run_gateway_request_once(_provider_retry_request_data(original_request_data), headers, timeout, metrics_retry=1)
            retry_provider_code = retry_meta.get("_provider_error_code") if isinstance(retry_meta, dict) else None
            if not (retry_meta.get("_safe_fallback") or retry_provider_code or _is_safe_upstream_fallback(retry_text)):
                first_text = retry_text
                first_meta = {**retry_meta, "_provider_retry_attempted": True, "_provider_retry_model": PROVIDER_ERROR_RETRY_MODEL, "_provider_error_code": provider_error_code, "_provider_retry_success": True}
            else:
                first_text = SAFE_UPSTREAM_ERROR_TEXT
                first_meta = {**retry_meta, "_provider_retry_attempted": True, "_provider_retry_model": PROVIDER_ERROR_RETRY_MODEL, "_provider_error_code": provider_error_code, "_provider_retry_failed": True, "_provider_retry_error_code": retry_provider_code or retry_meta.get("_provider_error_code"), "_first_provider_error_code": provider_error_code, "_safe_fallback": True, "_upstream_error": True}
        if provider_error_code and not first_meta.get("_provider_retry_attempted"):
            first_meta = {**first_meta, "_provider_retry_attempted": False, "_provider_retry_model": PROVIDER_ERROR_RETRY_MODEL}
        if (
            MAIN_SEARCH_FALLBACK_ENABLED
            and str(original_request_data.get("_payload_stage") or "") == "main_search"
            and (_is_empty_main_search_result(first_text) or _is_malformed_main_search_result(first_text) or first_meta.get("_safe_fallback"))
        ):
            return await self._run_main_search_fallback_race(original_request_data, headers, timeout, first_text, first_meta)
        return first_text, self._with_gateway_client_impl(first_meta)

    async def _run_main_search_fallback_race(
        self,
        request_data: dict[str, Any],
        headers: dict[str, Any],
        timeout: int,
        first_text: str,
        first_meta: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        race_models = list(
            dict.fromkeys(
                model
                for model in [str(request_data.get("model") or "").strip(), *MAIN_SEARCH_FALLBACK_MODELS]
                if model
            )
        )

        async def run(model: str) -> tuple[str, str, dict[str, Any]]:
            retry = {**request_data, "model": model}
            text, meta = await self._run_gateway_request_once(retry, headers, timeout, metrics_retry=1)
            return model, text, meta

        tasks = [asyncio.create_task(run(model)) for model in race_models]
        attempts: list[dict[str, Any]] = []
        pending: set[asyncio.Task[tuple[str, str, dict[str, Any]]]] = set(tasks)
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        model, text, meta = task.result()
                    except Exception as exc:
                        attempts.append({"model": "unknown", "ok": False, "error": exc.__class__.__name__})
                        continue
                    usable = _is_usable_main_search_result(text, meta)
                    detail = meta.get("_main_search_attempt") if isinstance(meta.get("_main_search_attempt"), dict) else {}
                    attempts.append({
                        "model": model,
                        "ok": usable,
                        "empty": _is_empty_main_search_result(text),
                        "safe": bool(meta.get("_safe_fallback")),
                        **({"gateway_task_id": detail.get("gateway_task_id")} if detail.get("gateway_task_id") else {}),
                        **({"duration_ms": detail.get("duration_ms")} if isinstance(detail.get("duration_ms"), int) else {}),
                        **({"parse_status": detail.get("parse_status")} if detail.get("parse_status") in {"ok", "invalid_json", "missing"} else {}),
                    })
                    if usable:
                        for remaining in pending:
                            remaining.cancel()
                        return text, {
                            **self._with_gateway_client_impl(meta),
                            "_search_fallback_race": True,
                            "_search_fallback_model": model,
                            "_search_fallback_models": race_models,
                            "_search_fallback_attempts": attempts,
                            "_first_main_search_attempt": first_meta.get("_main_search_attempt"),
                            "_first_attempt_empty_search": _is_empty_main_search_result(first_text),
                            "_first_attempt_malformed_search": _is_malformed_main_search_result(first_text),
                            "_first_attempt_safe": bool(first_meta.get("_safe_fallback")),
                        }
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        safe_attempts = [
            {
                "model": str(attempt.get("model") or ""),
                "ok": bool(attempt.get("ok")),
                "empty": bool(attempt.get("empty")),
                "safe": bool(attempt.get("safe")),
                **({"gateway_task_id": str(attempt.get("gateway_task_id"))[:80]} if attempt.get("gateway_task_id") else {}),
                **({"duration_ms": _bounded_duration_ms(attempt.get("duration_ms"))} if "duration_ms" in attempt else {}),
                **({"parse_status": str(attempt.get("parse_status"))} if attempt.get("parse_status") in {"ok", "invalid_json", "missing"} else {}),
                **({"error": str(attempt.get("error"))[:120]} if attempt.get("error") else {}),
            }
            for attempt in attempts
        ]
        _log_error_event(
            {
                "error_type": "main_search_fallback_exhausted",
                "severity": "error",
                "stage": "main_search_fallback",
                "gateway_client_impl": self._gateway_client_impl(),
                "attempted_models": race_models,
                "attempts": safe_attempts,
                "first_attempt_empty_search": _is_empty_main_search_result(first_text),
                "first_attempt_malformed_search": _is_malformed_main_search_result(first_text),
                "first_attempt_safe": bool(first_meta.get("_safe_fallback")),
            }
        )
        return SAFE_UPSTREAM_ERROR_TEXT, self._with_gateway_client_impl({
            "_safe_fallback": True,
            "_fallback_race_no_usable": True,
            "_search_fallback_race": True,
            "_search_fallback_models": race_models,
                "_search_fallback_attempts": safe_attempts,
                "_first_main_search_attempt": first_meta.get("_main_search_attempt"),
                "_first_attempt_empty_search": _is_empty_main_search_result(first_text),
            "_first_attempt_malformed_search": _is_malformed_main_search_result(first_text),
            "_first_attempt_safe": bool(first_meta.get("_safe_fallback")),
        })

    async def _run_gateway_request_once(
        self,
        request_data: dict[str, Any],
        headers: dict[str, Any],
        timeout: int,
        *,
        metrics_retry: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        session = await self.ensure_session()
        request_data = dict(request_data)
        model = str(request_data.get("model") or "")
        attempt_started = time.monotonic()
        if OPENROUTER_EXCLUDE_REASONING and model.startswith("google/gemini"):
            request_data.setdefault("reasoning", {"exclude": True})
        payload_stage = str(request_data.pop("_payload_stage", "gateway"))
        poll_interval_seconds = 1 if payload_stage == "conversation_answer_formatter" else 3
        _log_model_payload_metrics(payload_stage, request_data, retry=metrics_retry)
        payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": timeout,
            "max_retries": 0,
        }
        base = OVERMIND_URL.rstrip("/")
        url = f"{base}/api/v1/tasks/api"
        async with session.post(url, json=payload, headers=headers) as resp:
            task = await resp.json()
            if resp.status not in (200, 201):
                _log_error_event(_gateway_error_event(error_type="gateway_create_failed", stage="gateway_create_task", payload_stage=payload_stage, status="failed", duration_ms=round((time.monotonic() - attempt_started) * 1000), error=task, payload=task, extra={"http_status": resp.status}))
                code = _provider_error_code(task)
                meta = {"_upstream_error": True, "_safe_fallback": True}
                meta = _with_main_search_attempt_meta(meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, ok=False)
                return SAFE_UPSTREAM_ERROR_TEXT, _with_provider_retry_metadata(meta, code=code, attempted=False) if code else meta

        task_id = task.get("id")
        if not task_id:
            _log_error_event(_gateway_error_event(error_type="gateway_missing_task_id", stage="gateway_create_task", payload_stage=payload_stage, status="failed", duration_ms=round((time.monotonic() - attempt_started) * 1000), error=task, payload=task))
            code = _provider_error_code(task)
            meta = {"_upstream_error": True, "_safe_fallback": True}
            meta = _with_main_search_attempt_meta(meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, ok=False)
            return SAFE_UPSTREAM_ERROR_TEXT, _with_provider_retry_metadata(meta, code=code, attempted=False) if code else meta

        start = time.time()
        while time.time() - start < timeout:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
                status_data = await resp.json()
            status = status_data.get("status")
            if status in ("completed", "failed", "cancelled"):
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()
                _log_gateway_forensic_result(
                    payload_stage=payload_stage,
                    model=model,
                    task_id=task_id,
                    task_status=status,
                    result=result,
                )
                result_obj = result.get("result") or result
                if isinstance(result_obj, dict):
                    response_payload = result_obj.get("response", "")
                    error = result_obj.get("error", "")
                    metadata = result_obj.get("metadata", {}) or {}
                    if error:
                        code = _provider_error_code(error)
                        safe_meta = {"_payload_stage": payload_stage, "_upstream_error": True, "_safe_fallback": True}
                        safe_meta = _with_main_search_attempt_meta(safe_meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, task_id=task_id, ok=False)
                        _log_error_event(_gateway_error_event(error_type="gateway_task_error", stage="gateway_result", payload_stage=payload_stage, task_id=task_id, task_status=status, duration_ms=round((time.monotonic() - attempt_started) * 1000), parse_status=safe_meta.get("_main_search_attempt", {}).get("parse_status") if isinstance(safe_meta.get("_main_search_attempt"), dict) else None, error=error, payload=result_obj))
                        return SAFE_UPSTREAM_ERROR_TEXT, _with_provider_retry_metadata(safe_meta, code=code, attempted=False) if code else safe_meta
                    response_text = _response_payload_to_text(response_payload)
                    if response_text:
                        safe_meta = dict(metadata) if isinstance(metadata, dict) else {}
                        safe_task_id = _safe_gateway_task_id(task_id)
                        if safe_task_id:
                            safe_meta["_gateway_task_id"] = safe_task_id
                        safe_meta = _with_main_search_attempt_meta(safe_meta, payload_stage=payload_stage, model=model, text=response_text, started=attempt_started, task_id=task_id)
                        return response_text, safe_meta
                    if response_payload:
                        code = _provider_error_code(response_payload)
                        safe_meta = {"_upstream_error": True, "_safe_fallback": True}
                        safe_meta = _with_main_search_attempt_meta(safe_meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, task_id=task_id, ok=False)
                        _log_error_event(_gateway_error_event(error_type="gateway_non_text_response", stage="gateway_result", payload_stage=payload_stage, task_id=task_id, task_status=status, duration_ms=round((time.monotonic() - attempt_started) * 1000), parse_status=safe_meta.get("_main_search_attempt", {}).get("parse_status") if isinstance(safe_meta.get("_main_search_attempt"), dict) else None, error=response_payload, payload=response_payload, extra={"payload_type": type(response_payload).__name__}))
                        return SAFE_UPSTREAM_ERROR_TEXT, _with_provider_retry_metadata(safe_meta, code=code, attempted=False) if code else safe_meta
                    _log_error_event(_gateway_error_event(error_type="gateway_empty_response", stage="gateway_result", payload_stage=payload_stage, task_id=task_id, task_status=status, duration_ms=round((time.monotonic() - attempt_started) * 1000), error=result_obj, payload=result_obj))
                    code = _provider_error_code(result_obj)
                    meta = {"_upstream_error": True, "_safe_fallback": True}
                    meta = _with_main_search_attempt_meta(meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, task_id=task_id, ok=False)
                    return SAFE_UPSTREAM_ERROR_TEXT, _with_provider_retry_metadata(meta, code=code, attempted=False) if code else meta
            await asyncio_sleep(poll_interval_seconds)

        _log_error_event(
            _gateway_error_event(
                error_type="gateway_timeout",
                stage="gateway_status_poll",
                payload_stage=payload_stage,
                task_id=task_id,
                task_status="timeout",
                duration_ms=round((time.monotonic() - attempt_started) * 1000),
                parse_status="missing" if payload_stage == "main_search" else None,
            )
        )
        timeout_meta = {
            "_payload_stage": payload_stage,
            "_upstream_error": True,
            "_safe_fallback": True,
            "_gateway_timeout": True,
            "_gateway_task_id": _safe_gateway_task_id(task_id),
        }
        return SAFE_UPSTREAM_ERROR_TEXT, _with_main_search_attempt_meta(timeout_meta, payload_stage=payload_stage, model=model, text=SAFE_UPSTREAM_ERROR_TEXT, started=attempt_started, task_id=task_id, ok=False)


async def asyncio_sleep(delay: float) -> None:
    import asyncio

    await asyncio.sleep(delay)
