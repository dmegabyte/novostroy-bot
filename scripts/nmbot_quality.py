#!/usr/bin/env python3
"""nmbot_quality — оперативная проверка качества диалогов.

Использование:
  python3 scripts/nmbot_quality.py            # последние 20 диалогов
  python3 scripts/nmbot_quality.py --tail 5   # последние 5
  python3 scripts/nmbot_quality.py --date 2026-06-25
  python3 scripts/nmbot_quality.py --h_id H009

Codex-проверки:
  - no_greetings:     нет «Уважаемый»/«Дорогой»/имени клиента
  - no_sorry_empty:   при пустом search нет «к сожалению, не нашлось» без альтернативы
  - no_links:         нет novostroy-m.ru в response
  - has_md:           response без markdown-обёртки
  - valid_json:       response парсится в JSON {response, params}
  - operator_offered: при пустом search предложен оператор
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGS = REPO / "logs"

GREETING_PATTERNS = [
    re.compile(r"уважаем", re.IGNORECASE),
    re.compile(r"дорог", re.IGNORECASE),
]
SORRY_EMPTY = re.compile(r"к сожалению.{0,50}не\s+(нашлось|нашла|найдено|подобрано|подходящ)", re.IGNORECASE)
LINK_NOVOSTROY = re.compile(r"novostroy-m\.ru", re.IGNORECASE)
MD_WRAP = re.compile(r"^\s*```")


def _check(record: dict) -> dict[str, str | bool]:
    """Вернуть словарь codex-проверок для одной записи диалога."""
    response = record.get("response_text", "") or ""
    search_resp = record.get("search_response", "") or ""
    out: dict[str, str | bool] = {}

    out["no_greetings"] = not any(p.search(response) for p in GREETING_PATTERNS)

    has_search_facts = '"facts"' in search_resp and '"[]"' not in search_resp
    is_sorry = bool(SORRY_EMPTY.search(response))
    out["no_sorry_empty"] = not (is_sorry and not has_search_facts)

    out["no_links"] = not bool(LINK_NOVOSTROY.search(response))
    out["has_md"] = not bool(MD_WRAP.search(response))

    try:
        parsed = json.loads(response)
        out["valid_json"] = isinstance(parsed, dict) and "response" in parsed
    except Exception:
        out["valid_json"] = False

    out["operator_offered"] = ("оператор" in response.lower()) or (
        not has_search_facts and ("нашл" not in response.lower() and "предлож" not in response.lower())
    )

    return out


def _load(after: datetime | None = None) -> list[dict]:
    """Загрузить все user_message-записи из dialogs-*.jsonl."""
    if not LOGS.exists():
        return []
    records: list[dict] = []
    for path in sorted(LOGS.glob("dialogs-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") != "user_message":
                continue
            ts = rec.get("ts")
            if after and ts:
                try:
                    rec_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    rec_dt = None
                if rec_dt and rec_dt < after:
                    continue
            records.append(rec)
    return records


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tail", type=int, default=20, help="Последние N диалогов")
    p.add_argument("--date", type=str, help="Фильтр по дате (YYYY-MM-DD)")
    p.add_argument("--h_id", type=str, help="Фильтр по h_id")
    p.add_argument("--source", type=str, choices=("cli", "bot", "all"), default="all")
    p.add_argument("--verbose", action="store_true", help="Печатать детали по каждой записи")
    args = p.parse_args()

    after = None
    if args.date:
        try:
            d = date.fromisoformat(args.date)
            after = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except ValueError:
            print(f"[ERROR] bad date: {args.date}", file=sys.stderr)
            return 1

    records = _load(after=after)
    if args.h_id:
        records = [r for r in records if r.get("h_id") == args.h_id]
    if args.source != "all":
        records = [r for r in records if r.get("source") == args.source]

    if not records:
        print("(нет записей)")
        return 0

    tail = records[-args.tail:]
    print(f"📊 Записей: {len(records)}, показано: {len(tail)}")
    print(f"   Фильтры: date={args.date or 'all'} h_id={args.h_id or 'all'} source={args.source}")
    print()

    agg = Counter()
    bad: list[tuple[dict, dict[str, bool]]] = []
    for rec in tail:
        checks = _check(rec)
        for k, v in checks.items():
            if v is True:
                agg[k] += 1
            else:
                agg[k + "_fail"] += 1
        if not all(v for v in checks.values() if isinstance(v, bool)):
            bad.append((rec, checks))

    n = len(tail)
    print("─" * 60)
    print(f"{'Codex check':<25} {'pass':<10} {'%':<6}")
    print("─" * 60)
    for key in ("no_greetings", "no_sorry_empty", "no_links", "has_md", "valid_json", "operator_offered"):
        passed = agg[key]
        pct = (passed / n * 100) if n else 0
        print(f"{key:<25} {passed}/{n:<8} {pct:5.1f}%")
    print("─" * 60)
    errs = sum(1 for r in tail if r.get("is_error"))
    avg_dur = sum(int(r.get("duration_ms", 0)) for r in tail) / max(n, 1)
    avg_tok = sum(int((r.get("cost") or {}).get("total_tokens_used") or 0) for r in tail) / max(n, 1)
    print(f"errors: {errs}/{n}    avg_dur_ms: {int(avg_dur)}    avg_tokens: {int(avg_tok)}")
    print()

    if bad:
        print(f"❌ Записей с codex-нарушениями: {len(bad)}")
        for rec, checks in bad[-5:]:
            failed = [k for k, v in checks.items() if v is False]
            user = (rec.get("user_text") or "")[:60]
            resp = (rec.get("response_text") or "")[:80]
            print(f"  • {rec.get('h_id', '?')} | {rec.get('ts', '?')[:19]}")
            print(f"    user:  {user}")
            print(f"    reply: {resp}")
            print(f"    fails: {failed}")
        print()

    if args.verbose:
        print("─" * 60)
        print("Детально (последние 5):")
        for rec in tail[-5:]:
            checks = _check(rec)
            user = (rec.get("user_text") or "")[:80]
            resp = (rec.get("response_text") or "")[:80]
            print(f"  [{rec.get('h_id', '?')}] {user!r}")
            print(f"    → {resp!r}")
            print(f"    checks: {checks}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
