#!/usr/bin/env python3
"""Read-only nmbot health/status summary.

Не вызывает Telegram/LLM, не печатает секреты, не читает значения токенов.
Собирает один экран состояния из systemd, env-check и JSONL-логов.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
TODAY = datetime.now(timezone.utc).date().isoformat()


def _json_loads(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if limit is not None:
        lines = lines[-limit:]
    for line in lines:
        row = _json_loads(line.strip())
        if row is not None:
            rows.append(row)
    return rows


def _add(items: list[dict[str, Any]], name: str, status: str, msg: str = "", data: dict[str, Any] | None = None) -> None:
    items.append({"name": name, "status": status, "msg": msg, "data": data or {}})


def _infer_env(repo: Path) -> str:
    if repo.name == "novostroy-bot-staging" or "staging" in str(repo):
        return "staging"
    if repo.name == "novostroy-bot" and str(repo).startswith("/home/neiro/"):
        return "prod"
    return "local"


def _auto_service(repo: Path, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    env = _infer_env(repo)
    if not str(repo).startswith("/home/neiro/"):
        return None
    if env == "prod":
        return "novostroy-bot.service"
    if env == "staging":
        return "novostroy-bot-staging.service"
    return None


def _systemd_show(service: str) -> dict[str, str]:
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                service,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "-p",
                "WorkingDirectory",
                "-p",
                "ExecStart",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - host/systemd dependent
        return {"_error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, str] = {"_returncode": str(proc.returncode)}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    if proc.stderr:
        out["_stderr"] = proc.stderr.strip()[:300]
    return out


def _pid_stats(pid: str) -> dict[str, Any]:
    if not pid or pid == "0":
        return {}
    stats: dict[str, Any] = {"pid": pid}
    try:
        proc = subprocess.run(["ps", "-o", "etime=,rss=,%cpu=", "-p", pid], capture_output=True, text=True, timeout=5, check=False)
        parts = (proc.stdout or "").strip().split()
        if len(parts) >= 3:
            stats["uptime"] = parts[0]
            stats["rss_mb"] = round(int(parts[1]) / 1024, 1)
            stats["cpu_pct"] = parts[2]
    except Exception:
        pass
    return stats


def _run_env_check(repo: Path) -> dict[str, Any]:
    script = repo / "scripts" / "nmbot_env_check.py"
    if not script.exists():
        return {"summary": {"total": 0, "pass": 0, "fail": 1}, "checks": [], "error": "missing scripts/nmbot_env_check.py"}
    proc = subprocess.run([sys.executable, str(script), "--json"], cwd=str(repo), capture_output=True, text=True, timeout=30, check=False)
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {"summary": {"total": 0, "pass": 0, "fail": 1}, "checks": [], "error": f"json parse failed: {type(exc).__name__}", "exit_code": proc.returncode}
    if isinstance(data, dict):
        data["exit_code"] = proc.returncode
        return data
    return {"summary": {"total": 0, "pass": 0, "fail": 1}, "checks": [], "error": "unexpected env-check JSON", "exit_code": proc.returncode}


def _errors_summary(logs_dir: Path) -> dict[str, Any]:
    path = logs_dir / f"bot_error_events-{TODAY}.jsonl"
    rows = _read_jsonl(path)
    types = Counter(str(r.get("error_type") or r.get("kind") or "unknown") for r in rows)
    return {
        "path": str(path),
        "exists": path.exists(),
        "count_today": len(rows),
        "types": dict(types.most_common(8)),
        "last_ts": rows[-1].get("ts") if rows else None,
        "last_error_type": rows[-1].get("error_type") if rows else None,
    }


def _client_cards_summary(logs_dir: Path) -> dict[str, Any]:
    path = logs_dir / f"client_cards-{TODAY}.jsonl"
    rows = _read_jsonl(path)
    statuses = Counter(str(r.get("summary_status") or "unknown") for r in rows)
    return {
        "path": str(path),
        "exists": path.exists(),
        "count_today": len(rows),
        "summary_statuses": dict(statuses.most_common()),
        "last_created_at": rows[-1].get("created_at") if rows else None,
        "last_summary_model": rows[-1].get("summary_model") if rows else None,
    }


def _payload_metrics_summary(logs_dir: Path) -> dict[str, Any]:
    path = logs_dir / f"model_payload_metrics-{TODAY}.jsonl"
    rows = _read_jsonl(path)
    by_stage: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("stage") or "unknown")].append(row)
    for stage, stage_rows in grouped.items():
        query_vals = [int(r.get("query_chars") or 0) for r in stage_rows]
        system_vals = [int(r.get("system_prompt_chars") or 0) for r in stage_rows]
        by_stage[stage] = {
            "count": len(stage_rows),
            "max_query_chars": max(query_vals) if query_vals else 0,
            "max_system_prompt_chars": max(system_vals) if system_vals else 0,
            "avg_query_chars": round(sum(query_vals) / len(query_vals), 1) if query_vals else 0,
            "avg_system_prompt_chars": round(sum(system_vals) / len(system_vals), 1) if system_vals else 0,
        }
    return {
        "path": str(path),
        "exists": path.exists(),
        "count_today": len(rows),
        "last_ts": rows[-1].get("ts") if rows else None,
        "by_stage": by_stage,
    }


def _artifact_summary(repo: Path) -> dict[str, Any]:
    artifacts = {}
    for rel in ("logs/dialog_reviews.md", "logs/stateful_dialog_reviews.md"):
        path = repo / rel
        if path.exists():
            artifacts[rel] = {"exists": True, "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), "size": path.stat().st_size}
        else:
            artifacts[rel] = {"exists": False}
    return artifacts


def run_health(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    repo = Path(args.repo).expanduser().resolve()
    logs_dir = Path(args.logs_dir).expanduser()
    if not logs_dir.is_absolute():
        logs_dir = repo / logs_dir
    logs_dir = logs_dir.resolve()
    service_name = _auto_service(repo, args.service)
    checks: list[dict[str, Any]] = []

    if service_name:
        service = _systemd_show(service_name)
        active = service.get("ActiveState") == "active"
        data = {
            "service": service_name,
            "active_state": service.get("ActiveState"),
            "sub_state": service.get("SubState"),
            "workdir": service.get("WorkingDirectory"),
            **_pid_stats(service.get("MainPID", "")),
        }
        _add(checks, "service_active", "ok" if active else "fail", f"service={service_name}; state={service.get('ActiveState')}", data)
    else:
        _add(checks, "service_active", "warn", "not_applicable: local/non-vps run")

    env = _run_env_check(repo)
    env_fail = int((env.get("summary") or {}).get("fail", 1))
    _add(checks, "env_contract", "ok" if env_fail == 0 else "fail", f"pass={(env.get('summary') or {}).get('pass', 0)}/{(env.get('summary') or {}).get('total', 0)}", {"summary": env.get("summary", {})})

    errors = _errors_summary(logs_dir)
    _add(checks, "bot_error_events_today", "ok" if errors["count_today"] == 0 else "warn", f"count_today={errors['count_today']}; last={errors.get('last_error_type')}", errors)

    cards = _client_cards_summary(logs_dir)
    _add(checks, "client_cards_today", "ok" if cards["count_today"] > 0 else "warn", f"count_today={cards['count_today']}; last={cards.get('last_created_at')}", cards)

    payload = _payload_metrics_summary(logs_dir)
    _add(checks, "model_payload_metrics_today", "ok" if payload["count_today"] > 0 else "warn", f"count_today={payload['count_today']}; last={payload.get('last_ts')}", payload)

    artifacts = _artifact_summary(repo)
    fresh_count = sum(1 for data in artifacts.values() if data.get("exists"))
    _add(checks, "smoke_artifacts_present", "ok" if fresh_count else "warn", f"artifacts={fresh_count}/2", artifacts)

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    status = "fail" if fails else ("warn" if warns else "ok")
    return {
        "status": status,
        "summary": {"total": len(checks), "ok": sum(1 for c in checks if c["status"] == "ok"), "warn": len(warns), "fail": len(fails)},
        "repo": str(repo),
        "logs_dir": str(logs_dir),
        "effective_env": _infer_env(repo),
        "duration_ms": int((time.time() - started) * 1000),
        "checks": checks,
        "note": "Read-only health: no Telegram/LLM calls, no secret values in output.",
    }


def _print_human(result: dict[str, Any]) -> None:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}.get(str(result.get("status")), "?")
    print(f"{mark} nmbot health: {result['status']} ({result['summary']['ok']} ok, {result['summary']['warn']} warn, {result['summary']['fail']} fail)")
    print(f"repo: {result['repo']}")
    for check in result["checks"]:
        cmark = {"ok": "✓", "warn": "!", "fail": "✗"}.get(check["status"], "?")
        print(f"  {cmark} {check['name']}: {check.get('msg', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only nmbot health summary")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--service", default=None)
    args = parser.parse_args()
    result = run_health(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result["status"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
