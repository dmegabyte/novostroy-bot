#!/usr/bin/env python3
"""Read-only response-model probe for questions without a scenario recipe."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from nmbot_v2.contracts import OptionCard, ResponseBrief  # noqa: E402
from nmbot_v2.response_composer import compose_response_async, request_payload  # noqa: E402
import nmbot_v2_search_mcp_probe as gateway_probe  # noqa: E402


ANSWERABLE_CARD = OptionCard(
    name="Бусиновский парк",
    developer="ПИК",
)

MISSING_CARD = OptionCard(name="Бусиновский парк")

CASES = {
    "answerable": ResponseBrief(
        answer_goal="answer_open_question",
        user_question="Кто застройщик у Бусиновского парка?",
        question_subject="застройщик",
        requested_facts=("developer",),
        available_facts=("developer: ПИК",),
        response_policy="answer_directly",
        response_viewpoint="neutral",
        canonical_cards=(ANSWERABLE_CARD,),
        allowed_fact_fields=("name", "developer"),
        fallback_question="Что ещё проверить по этому ЖК?",
    ),
    "missing": ResponseBrief(
        answer_goal="answer_open_question",
        user_question="А зимой там сильно дует между домами?",
        question_subject="ветровая обстановка между корпусами",
        requested_facts=("wind_comfort",),
        missing_facts=("wind_comfort",),
        response_policy="operator_phone_request",
        operator_handoff_template="Точный ответ по ветровой обстановке уточнит оператор.",
        response_viewpoint="neutral",
        canonical_cards=(MISSING_CARD,),
        allowed_fact_fields=("name",),
        cta_template="Подскажите номер телефона, чтобы оператор уточнил это по ЖК «Бусиновский парк»?",
        fallback_question="Подскажите номер телефона, чтобы оператор уточнил это по ЖК «Бусиновский парк»?",
    ),
}


async def run_case(case_id: str, timeout: int) -> dict:
    brief = CASES[case_id]
    gateway_probe.load_env()

    async def composer(attempt_brief, *, repair_errors=(), model="google/gemini-2.5-flash"):
        return await gateway_probe.gateway_request(
            request_payload(attempt_brief, repair_errors=repair_errors, model=model),
            timeout,
        )

    result = await compose_response_async(
        brief,
        fallback_text="Этот вопрос уточнит оператор. Подскажите номер телефона для связи?",
        composer=composer,
    )
    return {
        "case": case_id,
        "status": result.status,
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "response_text": result.text,
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), required=True)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    result = await run_case(args.case, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "primary" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main()))
