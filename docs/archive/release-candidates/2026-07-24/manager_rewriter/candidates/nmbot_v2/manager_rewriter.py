from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .contracts import ResponseBrief, to_jsonable
from .prompt_provenance import build_prompt_provenance, identity_from_path
from .response_composer import _model_facing_brief_payload


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v2_manager_rewriter.txt"
DEFAULT_MODEL = "google/gemini-2.5-flash"


@dataclass(frozen=True)
class ManagerRewriteResult:
    text: str = ""
    used: bool = False
    status: str = "fallback"
    reason: str | None = None
    error_code: str | None = None
    attempts: int = 1
    prompt_provenance: dict[str, Any] | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "used": bool(self.used),
            "status": str(self.status or "fallback")[:40],
            "reason": str(self.reason or "")[:80] or None,
            "error_code": str(self.error_code or "")[:80] or None,
            "attempts": max(0, min(int(self.attempts or 0), 10)),
        }
        if self.prompt_provenance:
            out["prompt_provenance"] = self.prompt_provenance
        if self.raw_meta:
            safe_meta = {str(k)[:40]: v for k, v in self.raw_meta.items() if str(k).startswith("_") is False and k in {"ok", "status", "error_code"}}
            if safe_meta:
                out["provider_meta"] = safe_meta
        return out


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def configured_manager_rewriter_prompt_provenance(*, usage: str = "configured", coverage: str = "configured_only") -> dict[str, Any]:
    return build_prompt_provenance(
        [identity_from_path("manager_rewriter", "prompts/v2_manager_rewriter.txt", PROMPT_PATH, usage=usage)],
        coverage=coverage,
    )


def manager_rewriter_request_payload(
    *,
    transcript: tuple[dict[str, str], ...],
    current_question: str,
    prepared_answer: str,
    brief: ResponseBrief,
    prompt: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    payload = {
        "full_sanitized_transcript": list(transcript),
        "current_question": str(current_question or ""),
        "prepared_answer": str(prepared_answer or ""),
        "evidence_brief": _safe_brief_payload(brief),
    }
    request = {
        "_payload_stage": "conversation_answer_manager_rewriter",
        "query": "V2_MANAGER_REWRITER_INPUT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": model or os.getenv("NMBOT_MANAGER_REWRITER_MODEL") or DEFAULT_MODEL,
        "system_prompt": prompt if prompt is not None else load_prompt(),
        "parameters": {"temperature": 0.35, "max_tokens": 1800},
    }
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        request["external_api_key"] = api_key
    return request


def parse_manager_rewriter_text(raw: Any) -> str:
    if isinstance(raw, Mapping):
        for key in ("text", "answer", "content", "response"):
            value = raw.get(key)
            if str(value or "").strip():
                return str(value).strip()
        return ""
    return str(raw or "").strip()


async def rewrite_manager_answer_async(
    *,
    transcript: tuple[dict[str, str], ...],
    current_question: str,
    prepared_answer: str,
    brief: ResponseBrief,
    rewriter: Any,
) -> ManagerRewriteResult:
    provenance = configured_manager_rewriter_prompt_provenance(usage="invoked", coverage="complete")
    try:
        raw = await _maybe_await(
            rewriter.rewrite_manager_answer(
                transcript=transcript,
                current_question=current_question,
                prepared_answer=prepared_answer,
                brief=brief,
            )
        )
        text = parse_manager_rewriter_text(getattr(raw, "text", raw))
        meta = raw.to_meta() if hasattr(raw, "to_meta") else {}
        if not text:
            return ManagerRewriteResult(used=False, status="fallback", reason="empty_response", error_code="empty_response", prompt_provenance=provenance)
        return ManagerRewriteResult(text=text, used=True, status=str(meta.get("status") or "primary"), attempts=int(meta.get("attempts") or 1), prompt_provenance=provenance)
    except Exception as exc:
        return ManagerRewriteResult(used=False, status="fallback", reason="rewriter_error", error_code=exc.__class__.__name__, prompt_provenance=provenance)


def _safe_brief_payload(brief: ResponseBrief) -> dict[str, Any]:
    try:
        payload = _model_facing_brief_payload(brief)
    except Exception:
        payload = to_jsonable(brief)
    return payload if isinstance(payload, dict) else {}


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
