#!/usr/bin/env python3
"""Privacy-safe append-only ledger for prompts sent to external gateways."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "logs" / "prompt_dispatches.jsonl"
_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:/-]")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def ledger_path() -> Path:
    return Path(os.getenv("NMBOT_PROMPT_LEDGER", str(DEFAULT_LEDGER))).expanduser()


def append_dispatch(
    *,
    request_data: dict[str, Any],
    status: str,
    started_at: datetime,
    accepted_at: datetime | None = None,
    task_id: Any = None,
    retry: int | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record one gateway attempt without storing prompt/query/payload contents."""
    prompt = str(request_data.get("system_prompt") or "")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None
    stage = _token(request_data.get("_payload_stage")) or "gateway"
    service = _token(request_data.get("service")) or "unknown"
    model = _token(request_data.get("model")) or "unknown"
    safe_status = str(status or "unknown").strip().lower()
    if safe_status not in {"accepted", "failed", "missing_task_id"}:
        safe_status = "unknown"
    row: dict[str, Any] = {
        "schema": "nmbot.prompt_dispatch.v1",
        "event": "prompt_dispatch",
        "request_id": "req_" + uuid.uuid4().hex[:16],
        "sent_at": _utc(started_at),
        "accepted_at": _utc(accepted_at) if accepted_at else None,
        "status": safe_status,
        "destination": {"gateway": "overmind", "agent": "gateway-agent", "endpoint": "/process"},
        "service": service,
        "stage": stage,
        "model": model,
        "prompt_sha256": digest,
        "prompt_id": "p_" + digest[:12] if digest else None,
        "prompt_chars": len(prompt),
        "query_chars": len(str(request_data.get("query") or "")),
        "has_mcp": bool(request_data.get("mcp_servers")),
    }
    safe_task = _token(task_id)
    if safe_task:
        row["gateway_task_id"] = safe_task
    if isinstance(retry, int) and retry >= 0:
        row["retry"] = min(retry, 5)
    prompt_source = _token(request_data.get("_prompt_source"))
    if prompt_source:
        row["prompt_source"] = prompt_source
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.chmod(target, 0o600)
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def sanitize_dispatch(value: Any) -> dict[str, Any] | None:
    """Return only the stable, non-content fields accepted by the ledger contract."""
    if not isinstance(value, dict) or value.get("schema") != "nmbot.prompt_dispatch.v1":
        return None
    out = dict(value)
    for forbidden in ("prompt", "prompt_body", "query", "payload", "response", "token", "secret"):
        out.pop(forbidden, None)
    digest = out.get("prompt_sha256")
    if digest is not None and not _SHA_RE.fullmatch(str(digest)):
        return None
    return out


def _utc(value: datetime) -> str:
    stamp = value.astimezone(timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _token(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _TOKEN_RE.sub("_", text)[:160] or None
