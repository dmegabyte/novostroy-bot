#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nmbot_v2.semantic_planner import decision_to_dict, derive_runtime_decision, normalize_semantic_planner_result, semantic_to_dict

CASES_PATH = ROOT / "tests" / "fixtures" / "nmbot_v2_answer_planner_hypothesis.json"

SYSTEM_PROMPT = """
Ты — планировщик следующего ответа Ирины, консультанта по новостройкам.
Ты не отвечаешь клиенту и не выбираешь техническую ветку диалога.
По структурированному контексту верни только семантический смысл текущей реплики.
Runtime отдельно выведет context_source, scope, needs_search, action и остальные технические решения.

facts_needed может содержать только поля из available_fact_fields, переданных во входе.
response_viewpoint — обязательный semantic viewpoint ответа: enum investment, rental, family, life, financing, unchanged. financing — для ипотеки/финансирования; unchanged — сохранить текущий viewpoint. Это смысловая перспектива ответа, не route/search/source/scope.
scenario_needs — массив из family, rental, investment, life, financing для всех явно названных клиентом сценариев/перспектив в текущей реплике; это не фильтры и не route. mortgage/finance возвращай как financing.
scenario_change — legacy compatibility field, для нового вывода оставь null.
constraints_delta — только поисковые фильтры, которые могут изменить состав выдачи: локация, метро, комнаты, бюджет, площадь, отделка, готовность/срок, инфраструктурные фильтры и другие реальные параметры поиска. Не клади в constraints_delta смысловые пометки вроде scenario, topic, purpose, mortgage или financing.
Не придумывай факты о ЖК, ценах, ипотеке, сроках, метро, наличии или инфраструктуре.
Не решай отдельные фразы специальными правилами: определи общий смысл реплики относительно state.
Если клиент ясно просит подобрать новые объекты, отсутствие бюджета, локации или комнатности не требует clarification: верни requests_new_objects=true и clarification=null. Runtime умеет сделать широкий поиск и задать один следующий вопрос после списка.
clarification допустим только при смысловой неоднозначности и содержит ровно один короткий вопрос, а не перечень параметров.

Верни только JSON:
{
  "user_goal": "короткая смысловая цель клиента",
  "refers_to_existing_objects": true | false | "unknown",
  "requests_new_objects": true | false | "unknown",
  "selected_reference": null,
  "requested_comparison": null,
  "scenario_needs": [],
  "response_viewpoint": "unchanged",
  "scenario_change": null,
  "constraints_delta": {},
  "requires_enrichment": false,
  "facts_needed": [],
  "clarification": null,
  "confidence": 0.0,
  "reason": "коротко"
}
""".strip()

DEFAULT_CONTEXT = {
    "active_scenario": "investment",
    "params": {"location": ["центр"], "max_price": 30_000_000, "purpose": "investment"},
    "selected_option": None,
    "current_options": [
        {"name": "Премиум-квартал «Дом 56»", "location": "Басманный", "price_min": 23_300_000, "ready": "4 кв. 2026", "finishing": None, "metro": None},
        {"name": "ЖК «Павелецкая от Гранель»", "location": "Замоскворечье", "price_min": 21_400_000, "ready": "4 кв. 2026", "finishing": None, "metro": None},
        {"name": "ЖК «Шелепиха»", "location": "Пресненский", "price_min": 19_100_000, "ready": "3 кв. 2026", "finishing": None, "metro": None}
    ],
    "recent_turns": [
        {"role": "user", "text": "А в центре есть?"},
        {"role": "assistant", "text": "Показала три варианта в центре и спросила, какой разобрать подробнее."}
    ],
    "last_assistant_question": "Какой вариант хотите рассмотреть подробнее?"
}


def build_context(case: dict[str, Any]) -> dict[str, Any]:
    context = case.get("conversation_context")
    if isinstance(context, dict):
        return context
    return DEFAULT_CONTEXT

AVAILABLE_FACT_FIELDS = [
    "name", "location", "district", "price", "price_min", "price_range",
    "rooms", "room_formats", "area", "ready", "finishing", "metro",
    "developer", "property_class", "infrastructure", "schools",
    "kindergartens", "parks", "yards", "playgrounds", "clinics",
    "sales_count", "sales_date", "ads_count", "discount"
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def extract_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("no_json")
    value = json.loads(raw[start:end])
    if not isinstance(value, dict):
        raise ValueError("not_object")
    return value


def evaluate(expected: dict[str, Any], semantic_result: dict[str, Any], derived: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected_derived = expected.get("derived") if isinstance(expected.get("derived"), dict) else expected
    for key in ("context_source", "scope", "needs_search", "needs_enrichment", "action", "target", "search_policy"):
        if key in expected_derived and derived.get(key) != expected_derived.get(key):
            errors.append(f"derived.{key}:{derived.get(key)!r}!={expected_derived.get(key)!r}")
    if expected_derived.get("intent") and derived.get("intent") != expected_derived.get("intent"):
        errors.append(f"derived.intent:{derived.get('intent')!r}!={expected_derived.get('intent')!r}")
    if expected_derived.get("selected_option_name") and derived.get("selected_option_name") != expected_derived.get("selected_option_name"):
        errors.append("selected_option_not_resolved")
    if expected.get("facts_needed_any"):
        actual = {str(x).casefold() for x in (derived.get("facts_needed") or [])}
        if not actual.intersection({str(x).casefold() for x in expected["facts_needed_any"]}):
            errors.append("required_fact_missing")
    if expected.get("constraint_key"):
        delta = derived.get("constraints_patch") if isinstance(derived.get("constraints_patch"), dict) else {}
        keys = set(delta)
        for value in delta.values():
            if isinstance(value, dict):
                keys.update(value)
        if expected["constraint_key"] not in keys:
            errors.append("constraint_delta_missing")
    if expected.get("must_clarify") and not str(derived.get("clarification") or semantic_result.get("clarification") or "").strip():
        errors.append("clarification_missing")
    if "scenario_needs" in expected:
        actual_needs = semantic_result.get("scenario_needs") or []
        if actual_needs != expected["scenario_needs"]:
            errors.append(f"scenario_needs:{actual_needs!r}!={expected['scenario_needs']!r}")
    facts = {str(x) for x in (semantic_result.get("facts_needed") or [])}
    if not facts.issubset(set(AVAILABLE_FACT_FIELDS)):
        errors.append("unsupported_fact_requested")
    if semantic_result.get("response_viewpoint") not in {"investment", "rental", "family", "life", "financing", "unchanged"}:
        errors.append("invalid_response_viewpoint")
    forbidden_model_fields = {"context_source", "scope", "needs_search", "needs_enrichment", "action", "dialog_action", "target", "search_policy", "intent_policy", "search_profile", "selected_option_name", "constraints_patch"}
    leaked = sorted(forbidden_model_fields.intersection(semantic_result))
    if leaked:
        errors.append(f"semantic_contains_technical_fields:{','.join(leaked)}")
    return not errors, errors


def derive_for_case(case: dict[str, Any], model_output: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    semantic = normalize_semantic_planner_result(model_output, available_fact_fields=AVAILABLE_FACT_FIELDS)
    derived = derive_runtime_decision(semantic, build_context(case))
    return semantic_to_dict(semantic), decision_to_dict(derived)


async def call_model(case: dict[str, Any]) -> dict[str, Any]:
    token = os.environ.get("OVERMIND_TOKEN") or os.environ.get("GATEWAY_POLL_TOKEN")
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not token or not api_key:
        raise RuntimeError("missing local env")
    overmind = os.environ.get("OVERMIND_URL", "https://overmind.aiaxel.ru").rstrip("/")
    model = os.environ.get("NMBOT_DIALOG_PLANNER_MODEL", "google/gemini-3.1-flash-lite-preview")
    request_data = {
        "query": json.dumps({"user_text": case["user_text"], "conversation_context": build_context(case), "available_fact_fields": AVAILABLE_FACT_FIELDS}, ensure_ascii=False),
        "service": "openrouter",
        "model": model,
        "system_prompt": SYSTEM_PROMPT,
        "parameters": {"temperature": 0.0, "max_tokens": 900},
        "external_api_key": api_key,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    task_payload = {"agent_name": "gateway-agent", "endpoint": "/process", "request_data": request_data, "timeout_seconds": 60, "max_retries": 0}
    timeout = aiohttp.ClientTimeout(total=75)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{overmind}/api/v1/tasks/api", json=task_payload, headers=headers) as response:
            task = await response.json()
        task_id = task.get("id")
        if not task_id:
            raise RuntimeError("missing task id")
        started = time.monotonic()
        while time.monotonic() - started < 70:
            async with session.get(f"{overmind}/api/v1/tasks/api/{task_id}/status", headers={"Authorization": f"Bearer {token}"}) as response:
                status = await response.json()
            if status.get("status") in {"completed", "failed", "cancelled"}:
                async with session.get(f"{overmind}/api/v1/tasks/api/{task_id}/result", headers={"Authorization": f"Bearer {token}"}) as response:
                    result = await response.json()
                obj = result.get("result") or result
                raw = obj.get("response", "") if isinstance(obj, dict) else str(obj)
                return extract_json(raw)
            await asyncio.sleep(1)
    raise TimeoutError("planner probe timeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", type=int, help="zero-based case index")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--fixture-only", action="store_true", help="use fixture semantic_mock; never call a model")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    indices = range(len(cases)) if args.all else (args.case,)
    rows: list[dict[str, Any]] = []
    for index in indices:
        case = cases[index]
        model_output = case.get("semantic_mock") if args.fixture_only else asyncio.run(call_model(case))
        if not isinstance(model_output, dict):
            raise RuntimeError(f"case {case['id']} has no semantic_mock")
        semantic_result, derived = derive_for_case(case, model_output)
        passed, errors = evaluate(case["expected"], semantic_result, derived)
        row = {"index": index, "id": case["id"], "user_text": case["user_text"], "expected": case["expected"], "semantic_output": semantic_result, "derived_decision": derived, "passed": passed, "errors": errors}
        rows.append(row)
        if not passed and args.all:
            break
    payload = {"passed": sum(1 for row in rows if row["passed"]), "run": len(rows), "total": len(cases), "rows": rows}
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
