from __future__ import annotations

import asyncio
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import OptionCard, ResponseBrief, SafeTurnContext, SearchResult, SemanticPlan
from nmbot_v2.local_response_adapters import V2ResponseAdapterOutput, build_local_response_adapter_ports
from nmbot_v2.runtime import TurnProcessor


def _brief() -> ResponseBrief:
    return ResponseBrief(
        answer_goal="present_search_results",
        canonical_cards=(OptionCard(name="ЖК «Первый»", location="Центр", price_min=12_000_000),),
        scenario_context={"content_source": "scenario_context_only"},
        cta_template="Какой вариант хотите рассмотреть подробнее?",
        fallback_question="Какой вариант хотите рассмотреть подробнее?",
    )


def _writer_json() -> str:
    return json.dumps({
        "intro": "Нашла вариант.",
        "cards": [{"name": "ЖК «Первый»", "text": "Центр, цена от 12 млн рублей."}],
        "recommendation": "",
        "missing_note": "",
        "final_question": "Какой вариант хотите рассмотреть подробнее?",
    }, ensure_ascii=False)


def test_local_ports_use_typed_requests_and_hide_executor_metadata() -> None:
    requests = []

    async def execute(request):
        requests.append(request)
        if request.stage == "manager_rewriter":
            return V2ResponseAdapterOutput(raw="Готовый ответ менеджера.", meta={"token": "never-exposed"})
        return V2ResponseAdapterOutput(raw=_writer_json(), meta={"task_id": "never-exposed"})

    ports = build_local_response_adapter_ports(execute, writer_model="local-writer", manager_rewriter_model="local-rewriter")
    composed = asyncio.run(ports.response_composer.compose_response(_brief(), fallback_text="детерминированный ответ"))
    rewritten = asyncio.run(ports.manager_rewriter.rewrite_manager_answer(
        transcript=({"user": "вопрос", "assistant": ""},),
        current_question="вопрос",
        prepared_answer=composed.text,
        brief=_brief(),
    ))

    assert composed.status == "primary"
    assert "1. ЖК «Первый»" in composed.text
    assert composed.to_meta()["attempt_summaries"][0]["model"] == "local-writer"
    assert rewritten.used is True and rewritten.text == "Готовый ответ менеджера."
    assert "never-exposed" not in json.dumps(rewritten.to_meta(), ensure_ascii=False)
    assert [(item.stage, item.model) for item in requests] == [("writer", "local-writer"), ("manager_rewriter", "local-rewriter")]


def test_invalid_or_failing_executor_output_falls_back_with_redacted_codes() -> None:
    def invalid(_request):
        return {"raw": "not a typed output", "secret": "do-not-leak"}

    ports = build_local_response_adapter_ports(invalid)
    composed = asyncio.run(ports.response_composer.compose_response(_brief(), fallback_text="детерминированный ответ"))
    rewritten = asyncio.run(ports.manager_rewriter.rewrite_manager_answer(
        transcript=(), current_question="вопрос", prepared_answer="ответ", brief=_brief(),
    ))

    assert composed.text == "детерминированный ответ"
    assert composed.error_code == "adapter_invalid_output"
    assert "do-not-leak" not in json.dumps(composed.to_meta(), ensure_ascii=False)
    assert rewritten.used is False and rewritten.error_code == "adapter_invalid_output"

    def failing(_request):
        raise RuntimeError("provider token do-not-leak")

    failed = asyncio.run(build_local_response_adapter_ports(failing).response_composer.compose_response(
        _brief(), fallback_text="детерминированный ответ",
    ))
    assert failed.text == "детерминированный ответ"
    assert failed.error_code == "adapter_exception"
    assert "do-not-leak" not in json.dumps(failed.to_meta(), ensure_ascii=False)


def test_local_composer_port_is_compatible_with_v2_runtime_publication() -> None:
    class Planner:
        def plan(self, _context, _state):
            return SemanticPlan(operation="search")

    class Search:
        def search(self, _plan, _state):
            return SearchResult.from_dict({"facts": [{"name": "ЖК «Первый»", "location": "Центр", "price": "от 12 млн рублей"}]})

        def enrich_selected(self, option, _state, _plan):
            return option

    def execute(request):
        if request.stage == "manager_rewriter":
            return V2ResponseAdapterOutput(raw="")
        return V2ResponseAdapterOutput(raw=_writer_json())

    ports = build_local_response_adapter_ports(execute)
    result = TurnProcessor(
        planner=Planner(), search_service=Search(), response_composer=ports.response_composer, response_composer_mode="publish",
    ).process(SafeTurnContext(conversation_ref="local", user_text="подберите вариант"))

    assert result.trace["response_composer"]["used"] is True
    assert result.trace["response_composer"]["published"] is True
    assert result.response_text.startswith("Нашла вариант.")


def test_local_adapter_does_not_import_global_runtime_adapter() -> None:
    source = Path("nmbot_v2/local_response_adapters.py").read_text(encoding="utf-8")
    imported = [alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names]
    imported += [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert not any(name == "scripts" or name.startswith("scripts.") or "nmbot_runtime_adapter" in name for name in imported)
