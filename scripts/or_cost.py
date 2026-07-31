#!/usr/bin/env python3
"""Проверка расходов и остатка OpenRouter для текущего проекта.

Использование:
    python3 scripts/or_cost.py
    scripts/openrouter_balance
"""

import os
import sys
from urllib.request import Request, urlopen

import json

API_BASE = "https://openrouter.ai/api/v1"


def load_api_key() -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if api_key:
        return api_key.strip().strip('"').strip("'")

    for path in (".env", "../.env", "/home/ser/projects/nmbot/.env"):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                if line.startswith("OPENROUTER_API_KEY="):
                    return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def get_json(path: str, api_key: str) -> dict:
    req = Request(
        f"{API_BASE}/{path}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode()).get("data", {})


def money(value: float) -> str:
    return f"${value:.2f}"


def main() -> int:
    api_key = load_api_key()
    if not api_key:
        print("❌ OPENROUTER_API_KEY не найден")
        return 1

    usage = get_json("auth/key", api_key)
    credits = get_json("credits", api_key)
    remaining = float(credits.get("total_credits", 0)) - float(credits.get("total_usage", 0))

    print(f'- За сегодня: {money(float(usage.get("usage_daily", 0)))}')
    print(f'- За неделю: {money(float(usage.get("usage_weekly", 0)))}')
    print(f'- За месяц: {money(float(usage.get("usage_monthly", 0)))}')
    print(f'- Всего по этому ключу: {money(float(usage.get("usage", 0)))}')
    print(f'- Осталось: {money(remaining)}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
