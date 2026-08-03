"""V2-owned, injection-only adapters for optional answer wording stages.

This module deliberately has no gateway, environment, or provider dependency.
The composition root supplies a local executor; V2 keeps ownership of the
request shape, result validation, error redaction, and deterministic fallback.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

from .contracts import ResponseBrief
from .manager_rewriter import ManagerRewriteResult
from .response_composer import ComposerAttemptResult, compose_response_writer_formatter_async


V2ResponseStage = Literal["writer", "formatter", "manager_rewriter"]
V2ResponseExecutor = Callable[["V2ResponseAdapterRequest"], object]

_SAFE_FAILURE_CODES = {"adapter_invalid_output", "adapter_exception", "upstream_error"}


@dataclass(frozen=True)
class V2ResponseAdapterRequest:
    """The complete local boundary for an optional V2 wording operation."""

    stage: V2ResponseStage
    brief: ResponseBrief
    model: str
    writer_text: str = ""
    transcript: tuple[dict[str, str], ...] = ()
    current_question: str = ""
    prepared_answer: str = ""


@dataclass(frozen=True)
class V2ResponseAdapterOutput:
    """Validated executor output; arbitrary metadata is never exposed to V2."""

    raw: str | Mapping[str, Any] = ""
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class V2LocalResponsePorts:
    """Pair of ports that can be passed directly into ``V2RuntimePorts``."""

    response_composer: "LocalV2ResponseComposer"
    manager_rewriter: "LocalV2ManagerRewriter"


class LocalV2ResponseComposer:
    def __init__(self, executor: V2ResponseExecutor, *, writer_model: str, formatter_model: str) -> None:
        self._executor = executor
        self._writer_model = writer_model
        self._formatter_model = formatter_model

    async def compose_response(self, brief: ResponseBrief, *, fallback_text: str) -> ComposerAttemptResult:
        async def writer(inner_brief: ResponseBrief, *, model: str) -> tuple[str | Mapping[str, Any], dict[str, Any]]:
            return await self._execute(V2ResponseAdapterRequest(stage="writer", brief=inner_brief, model=model))

        async def formatter(inner_brief: ResponseBrief, *, writer_text: str, model: str) -> tuple[str | Mapping[str, Any], dict[str, Any]]:
            return await self._execute(V2ResponseAdapterRequest(stage="formatter", brief=inner_brief, model=model, writer_text=writer_text))

        return await compose_response_writer_formatter_async(
            brief,
            fallback_text=fallback_text,
            writer=writer,
            formatter=formatter,
            writer_model=self._writer_model,
            formatter_model=self._formatter_model,
        )

    async def _execute(self, request: V2ResponseAdapterRequest) -> tuple[str | Mapping[str, Any], dict[str, Any]]:
        try:
            value = self._executor(request)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return "", _failure_meta("adapter_exception")
        return _validated_composer_output(value)


class LocalV2ManagerRewriter:
    def __init__(self, executor: V2ResponseExecutor, *, model: str) -> None:
        self._executor = executor
        self._model = model

    async def rewrite_manager_answer(
        self,
        *,
        transcript: tuple[dict[str, str], ...],
        current_question: str,
        prepared_answer: str,
        brief: ResponseBrief,
    ) -> ManagerRewriteResult:
        request = V2ResponseAdapterRequest(
            stage="manager_rewriter",
            brief=brief,
            model=self._model,
            transcript=tuple(dict(item) for item in transcript),
            current_question=str(current_question or ""),
            prepared_answer=str(prepared_answer or ""),
        )
        try:
            value = self._executor(request)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            return ManagerRewriteResult(reason="rewriter_error", error_code="adapter_exception")
        if not isinstance(value, V2ResponseAdapterOutput) or not isinstance(value.raw, str):
            return ManagerRewriteResult(reason="rewriter_error", error_code="adapter_invalid_output")
        text = value.raw.strip()
        if not text:
            return ManagerRewriteResult(reason="empty_response", error_code="empty_response")
        if len(text) > 12_000:
            return ManagerRewriteResult(reason="rewriter_error", error_code="adapter_invalid_output")
        return ManagerRewriteResult(text=text, used=True, status="primary")


def build_local_response_adapter_ports(
    executor: V2ResponseExecutor,
    *,
    writer_model: str = "google/gemini-2.5-flash",
    formatter_model: str = "inclusionai/ling-2.6-flash",
    manager_rewriter_model: str = "google/gemini-2.5-flash",
) -> V2LocalResponsePorts:
    """Build V2 ports without selecting or constructing any provider client."""
    if not callable(executor):
        raise TypeError("v2_response_executor_not_callable")
    return V2LocalResponsePorts(
        response_composer=LocalV2ResponseComposer(executor, writer_model=writer_model, formatter_model=formatter_model),
        manager_rewriter=LocalV2ManagerRewriter(executor, model=manager_rewriter_model),
    )


def _validated_composer_output(value: Any) -> tuple[str | Mapping[str, Any], dict[str, Any]]:
    if not isinstance(value, V2ResponseAdapterOutput):
        return "", _failure_meta("adapter_invalid_output")
    raw = value.raw
    if not isinstance(raw, (str, Mapping)):
        return "", _failure_meta("adapter_invalid_output")
    meta = value.meta
    if not isinstance(meta, Mapping):
        return "", _failure_meta("adapter_invalid_output")
    if meta.get("ok") is False:
        return "", _failure_meta(_safe_failure_code(meta.get("error_code")))
    return raw, {"ok": True}


def _safe_failure_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    return code if code in _SAFE_FAILURE_CODES else "upstream_error"


def _failure_meta(code: str) -> dict[str, Any]:
    return {"ok": False, "_upstream_error": True, "error_code": _safe_failure_code(code)}
