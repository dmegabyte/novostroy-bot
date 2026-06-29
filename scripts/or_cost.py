#!/usr/bin/env python3
"""
Проверка расходов OpenRouter для текущего проекта.

Использование:
    or-cost            — расходы за сегодня и за всё время
    or-cost --all      — подробно: день, неделя, месяц, всего
"""

import json
import os
import sys
from urllib.request import Request, urlopen

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
if not API_KEY:
    # Пробуем достать из .env
    env_paths = [".env", "../.env", "/home/ser/projects/nmbot/.env"]
    for p in env_paths:
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("OPENROUTER_API_KEY="):
                    API_KEY = line.strip().split("=", 1)[1]
                    break

if not API_KEY:
    print("❌ OPENROUTER_API_KEY не найден")
    sys.exit(1)

req = Request(
    "https://openrouter.ai/api/v1/auth/key",
    headers={"Authorization": f"Bearer {API_KEY}"},
)
resp = urlopen(req)
data = json.loads(resp.read()).get("data", {})

usage = data.get("usage", 0)
daily = data.get("usage_daily", 0)
weekly = data.get("usage_weekly", 0)
monthly = data.get("usage_monthly", 0)

print("📊 OpenRouter расходы")
print(f"{'─' * 40}")
print(f"  За сегодня:   ${daily:>6.2f}")
print(f"  За неделю:    ${weekly:>6.2f}")
print(f"  За месяц:     ${monthly:>6.2f}")
print(f"  Всего:        ${usage:>6.2f}")
print(f"{'─' * 40}")
print(f"  Ключ: {API_KEY[:12]}...{API_KEY[-4:]}")
