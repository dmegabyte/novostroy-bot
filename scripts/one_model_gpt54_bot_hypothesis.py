#!/usr/bin/env python3
"""
H001 hypothesis probe: one-model Irina bot.

This is a standalone read-only experiment. It does NOT change the production bot
flow. The script sends one gateway request to OpenRouter model
openai/gpt-5.4-mini with MCP novostroym enabled and asks the model to both
search and formulate the final answer.

Usage:
    python3 scripts/one_model_gpt54_bot_hypothesis.py "кварира для инвестуий"
    python3 scripts/one_model_gpt54_bot_hypothesis.py --preset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
OVERMIND_URL = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
MODEL = os.getenv("NMBOT_ONE_MODEL", "openai/gpt-5.4-mini")
TEMPERATURE = float(os.getenv("NMBOT_ONE_MODEL_TEMPERATURE", "0.25"))
TIMEOUT = int(os.getenv("NMBOT_ONE_MODEL_TIMEOUT", "120"))


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


SYSTEM_PROMPT = """
Ты — Ирина, живой консультант по новостройкам Москвы и Московской области.

Твоя задача в ОДНОМ ходе:
1) Использовать MCP novostroym, чтобы найти реальные варианты по запросу клиента.
2) Сформировать короткий человеческий ответ клиенту.

Жёсткие правила фактуры:
- Используй только факты, которые пришли из MCP/search результата.
- Не придумывай цену, метро, срок сдачи, площадь, застройщика, школы, парки, двор,
  вид, ипотеку, скидки, доходность, аренду, ликвидность, рост цены или выгоду.
- Если поля нет в MCP, не упоминай его.

Формат ответа клиенту:
- Если найдено 2+ ЖК, покажи максимум 3 варианта нумерованным списком 1./2./3.
- В каждом пункте: название + 1-3 реальные факта из MCP: цена, локация,
  отделка, срок/готовность, метро, площадь — только если они реально есть.
- Добавь короткую пользу только из факта: отделка → меньше ремонта; готовность/срок →
  проще планировать переезд; цена → понятнее сравнивать бюджет; метро → удобнее ездить.
- Не используй рекламные слова: лучший, идеальный, выгодный, перспективный,
  инвестиционно привлекательный, премиальный, максимально.
- В конце ровно один вопрос: какой ЖК/вариант рассмотреть подробнее.

Верни СТРОГО JSON без markdown:
{
  "response": "готовый ответ клиенту plain text",
  "visible_options": [{"idx": 1, "name": "точное название ЖК"}],
  "params": {"purpose": "investment|family|self_use|fast_move|budget", "...": "только если уверенно"},
  "debug": {
    "facts_count": 0,
    "near_count": 0,
    "used_fields": ["price", "metro"],
    "warnings": []
  }
}
""".strip()


async def _run_gateway_request(request_data: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any], float]:
    token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN") or ""
    if not token:
        raise SystemExit("OVERMIND_TOKEN/GATEWAY_POLL_TOKEN is missing")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": request_data,
        "timeout_seconds": timeout,
        "max_retries": 0,
    }
    base = OVERMIND_URL.rstrip("/")
    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{base}/api/v1/tasks/api", json=payload, headers=headers) as resp:
            task = await resp.json()
            if resp.status not in (200, 201):
                return f"❌ create failed: {resp.status} {task}", {}, time.monotonic() - started
        task_id = task.get("id")
        if not task_id:
            return f"❌ no task id: {task}", {}, time.monotonic() - started
        while time.monotonic() - started < timeout:
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
                status_data = await resp.json()
            if status_data.get("status") in {"completed", "failed", "cancelled"}:
                async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                    result = await resp.json()
                result_obj = result.get("result") or result
                if isinstance(result_obj, dict):
                    return (
                        str(result_obj.get("response") or result_obj.get("error") or ""),
                        result_obj.get("metadata") or {},
                        time.monotonic() - started,
                    )
                return str(result_obj), {}, time.monotonic() - started
            await asyncio.sleep(3)
    return "⏱️ timeout", {}, time.monotonic() - started


async def ask_one_model(query: str) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is missing")
    request_data = {
        "query": f"Клиент написал: {query}",
        "service": "openrouter",
        "model": MODEL,
        "system_prompt": SYSTEM_PROMPT,
        "parameters": {"temperature": TEMPERATURE, "max_tokens": 5000},
        "external_api_key": api_key,
        "mcp_servers": ["novostroym"],
    }
    raw, meta, wall = await _run_gateway_request(request_data, TIMEOUT)
    parsed = _json_from_text(raw)
    return {
        "query": query,
        "model": MODEL,
        "temperature": TEMPERATURE,
        "wall_time_sec": round(wall, 2),
        "metadata": meta,
        "parsed": parsed,
        "raw": raw,
    }


def _print_result(result: dict[str, Any], *, raw: bool = False) -> None:
    parsed = result.get("parsed") or {}
    print("=" * 88)
    print(f"QUERY: {result['query']}")
    print(f"MODEL: {result['model']} temp={result['temperature']} wall={result['wall_time_sec']}s")
    meta = result.get("metadata") or {}
    if meta:
        print(f"META: response_time={meta.get('response_time')} tokens={meta.get('usage', {}).get('total_tokens')}")
    if parsed:
        print("\nANSWER:")
        print(parsed.get("response") or "<no response>")
        print("\nVISIBLE:", json.dumps(parsed.get("visible_options"), ensure_ascii=False))
        print("DEBUG:", json.dumps(parsed.get("debug"), ensure_ascii=False))
    else:
        print("\nPARSE: FAILED")
    if raw:
        print("\nRAW:")
        print(result.get("raw") or "")


async def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(description="One-model GPT-5.4 nmbot hypothesis probe")
    parser.add_argument("queries", nargs="*", help="Client query text")
    parser.add_argument("--preset", action="store_true", help="Run small preset suite")
    parser.add_argument("--json", action="store_true", help="Print JSONL results")
    parser.add_argument("--raw", action="store_true", help="Print raw model output")
    args = parser.parse_args()

    queries = list(args.queries)
    if args.preset:
        queries.extend([
            "кварира для инвестуий",
            "поближе к метро",
            "квартира в Котельниках с отделкой",
        ])
    if not queries:
        raise SystemExit("Pass query text or --preset")

    for query in queries:
        result = await ask_one_model(query)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            _print_result(result, raw=args.raw)


if __name__ == "__main__":
    asyncio.run(main())
