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
V5_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "v5_manager_rewriter.txt"
DEFAULT_MODEL = "google/gemini-2.5-flash"
V5_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
_SAFE_PROVIDER_META_KEYS = ("provider", "fallback", "reason")
_SAFE_PROVIDER_VALUES = {"bluesminds", "gateway"}
_SAFE_PROVIDER_REASONS = {"disabled", "empty", "exception", "none"}


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
            safe_meta = _safe_provider_meta(self.raw_meta)
            if safe_meta:
                out["provider_meta"] = safe_meta
        return out


def _prompt_path(runtime_version: str = "v2") -> Path:
    return V5_PROMPT_PATH if str(runtime_version or "v2").strip().lower() == "v5" else PROMPT_PATH


def load_prompt(runtime_version: str = "v2") -> str:
    return _prompt_path(runtime_version).read_text(encoding="utf-8")


def configured_manager_rewriter_prompt_provenance(*, runtime_version: str = "v2", usage: str = "configured", coverage: str = "configured_only") -> dict[str, Any]:
    prompt_path = _prompt_path(runtime_version)
    prompt_name = "manager_rewriter_v5" if str(runtime_version or "v2").strip().lower() == "v5" else "manager_rewriter"
    return build_prompt_provenance(
        [identity_from_path(prompt_name, str(prompt_path.relative_to(prompt_path.parents[1])), prompt_path, usage=usage)],
        coverage=coverage,
    )


def _v5_dialogue_history(transcript: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for turn in transcript:
        if not isinstance(turn, Mapping):
            continue
        user_text = str(turn.get("user") or "").strip()
        assistant_text = str(turn.get("assistant") or "").strip()
        if user_text:
            history.append({"role": "user", "authority": "user_source", "text": user_text})
        if assistant_text:
            history.append({"role": "assistant", "authority": "context_only", "text": assistant_text})
    return history


def _v5_card_payload(card: Any, *, rank: int, requested_rooms: Any) -> dict[str, Any]:
    raw = to_jsonable(card)
    raw = raw if isinstance(raw, Mapping) else {}
    facts: list[dict[str, Any]] = []
    for field in ("location", "district", "ready", "metro", "developer", "property_class", "infrastructure", "finishing", "area"):
        value = raw.get(field)
        if value not in (None, "", [], ()):
            facts.append({"field": field, "value": value, "scope": "project", "authoritative": True})

    room_prices: list[dict[str, Any]] = []
    for item in raw.get("room_prices") if isinstance(raw.get("room_prices"), list) else []:
        if not isinstance(item, Mapping):
            continue
        if requested_rooms is not None and str(item.get("rooms") or "") != str(requested_rooms):
            continue
        room_prices.append({key: item[key] for key in ("rooms", "price", "price_min", "price_max", "area") if key in item})
    if room_prices:
        facts.append({"field": "room_prices", "value": room_prices[:5], "scope": "unit_type", "applies_to_rooms": requested_rooms, "authoritative": True})
    elif raw.get("price") not in (None, "") or raw.get("price_min") not in (None, ""):
        facts.append({"field": "price_start", "value": raw.get("price") or raw.get("price_min"), "scope": "project", "applies_to_rooms": None, "authoritative": True})

    card_id = raw.get("entity_id") or raw.get("name") or f"card-{rank}"
    return {"card_id": str(card_id), "rank": rank, "name": str(raw.get("name") or ""), "facts": facts}


def _v5_manager_payload(*, transcript: tuple[dict[str, str], ...], current_question: str, prepared_answer: str, brief: ResponseBrief) -> dict[str, Any]:
    priorities = to_jsonable(getattr(brief, "client_priorities", {}))
    priorities = priorities if isinstance(priorities, Mapping) else {}
    constraints = priorities.get("confirmed_constraints") if isinstance(priorities.get("confirmed_constraints"), Mapping) else {}
    if not constraints:
        scenario_context = to_jsonable(getattr(brief, "scenario_context", {}))
        constraints = scenario_context.get("confirmed_constraints") if isinstance(scenario_context, Mapping) and isinstance(scenario_context.get("confirmed_constraints"), Mapping) else {}
    requested_rooms = constraints.get("rooms") or constraints.get("room")
    cards = [_v5_card_payload(card, rank=index, requested_rooms=requested_rooms) for index, card in enumerate(brief.canonical_cards[:3], 1)]
    turn_number = len(transcript)
    operator_policy = {
        "offer": turn_number == 3,
        "reason": "third_client_question" if turn_number == 3 else "not_applicable",
        "current_question": str(current_question or "") if turn_number == 3 else "",
        "instruction": "На третьем содержательном turn обязательно кратко предложи обсудить именно этот вопрос с менеджером и спроси, можно ли передать контакт; не продолжай обычный подбор." if turn_number == 3 else "",
    }
    return {
        "active_request": {
            "current_user_message": str(current_question or ""),
            "resolved_user_criteria": dict(constraints),
        },
        "dialogue_history": _v5_dialogue_history(transcript),
        "mcp_evidence": {
            "cards": cards,
            "missing_facts": [str(item) for item in brief.missing_facts[:8]],
        },
        "rewrite_policy": {
            "card_count": len(cards),
            "cta_mode": "single_card_followup" if len(cards) == 1 else "multi_card_choice",
            "question_limit": 1,
        },
        "operator_policy": operator_policy,
        "prepared_answer": str(prepared_answer or ""),
    }


def manager_rewriter_request_payload(
    *,
    transcript: tuple[dict[str, str], ...],
    current_question: str,
    prepared_answer: str,
    brief: ResponseBrief,
    prompt: str | None = None,
    model: str | None = None,
    runtime_version: str = "v2",
) -> dict[str, Any]:
    is_v5 = str(runtime_version or "v2").strip().lower() == "v5"
    payload = _v5_manager_payload(
        transcript=transcript,
        current_question=current_question,
        prepared_answer=prepared_answer,
        brief=brief,
    ) if is_v5 else {
        "full_sanitized_transcript": list(transcript),
        "current_question": str(current_question or ""),
        "prepared_answer": str(prepared_answer or ""),
        "evidence_brief": _safe_brief_payload(brief),
    }
    request = {
        "_payload_stage": "conversation_answer_manager_rewriter",
        "query": ("V5_MANAGER_REWRITER_INPUT=" if is_v5 else "V2_MANAGER_REWRITER_INPUT=") + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": model or (os.getenv("NMBOT_V5_MANAGER_REWRITER_MODEL") or V5_DEFAULT_MODEL if is_v5 else os.getenv("NMBOT_MANAGER_REWRITER_MODEL") or DEFAULT_MODEL),
        "system_prompt": prompt if prompt is not None else load_prompt(runtime_version),
        "parameters": {"temperature": 0.35, "max_tokens": 1800},
    }
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key and not is_v5:
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


def _v5_operator_offer_failsafe(text: str, *, transcript: tuple[dict[str, str], ...], current_question: str) -> str:
    """Keep the third-turn operator offer a stable runtime contract."""
    if not text or len(transcript) != 3:
        return text
    lowered = text.casefold()
    if "менеджер" in lowered or "оператор" in lowered:
        return text
    question = " ".join(str(current_question or "").split())[:240]
    if question:
        return f"По вашему вопросу «{question}» лучше подключить менеджера, чтобы уточнить варианты. Передать ему ваш контакт?"
    return "По этому вопросу лучше подключить менеджера, чтобы уточнить варианты. Передать ему ваш контакт?"


def _manager_rewriter_raw_meta(raw: Any) -> dict[str, Any]:
    if hasattr(raw, "to_meta"):
        meta = raw.to_meta()
        return dict(meta) if isinstance(meta, Mapping) else {}
    if isinstance(raw, Mapping):
        meta = raw.get("meta")
        return dict(meta) if isinstance(meta, Mapping) else {}
    return {}


def _safe_provider_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    provider = str(meta.get("provider") or "").strip().lower()
    reason = str(meta.get("reason") or "").strip().lower()
    out: dict[str, Any] = {}
    if provider in _SAFE_PROVIDER_VALUES:
        out["provider"] = provider
    if isinstance(meta.get("fallback"), bool):
        out["fallback"] = bool(meta.get("fallback"))
    if reason in _SAFE_PROVIDER_REASONS:
        out["reason"] = reason
    return {key: out[key] for key in _SAFE_PROVIDER_META_KEYS if key in out}


async def rewrite_manager_answer_async(
    *,
    transcript: tuple[dict[str, str], ...],
    current_question: str,
    prepared_answer: str,
    brief: ResponseBrief,
    rewriter: Any,
) -> ManagerRewriteResult:
    runtime_version = str(getattr(rewriter, "runtime_version", "v2") or "v2").strip().lower()
    provenance = configured_manager_rewriter_prompt_provenance(runtime_version=runtime_version, usage="invoked", coverage="complete")
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
        if text and runtime_version == "v5":
            text = _v5_operator_offer_failsafe(text, transcript=transcript, current_question=current_question)
        meta = _manager_rewriter_raw_meta(raw)
        if not text:
            return ManagerRewriteResult(used=False, status="fallback", reason="empty_response", error_code="empty_response", prompt_provenance=provenance, raw_meta=meta)
        return ManagerRewriteResult(text=text, used=True, status=str(meta.get("status") or "primary"), attempts=int(meta.get("attempts") or 1), prompt_provenance=provenance, raw_meta=meta)
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
