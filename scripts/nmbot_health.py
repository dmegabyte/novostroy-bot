#!/usr/bin/env python3
"""Read-only nmbot health/status summary.

Не вызывает Telegram/LLM, не печатает секреты, не читает значения токенов.
Собирает один экран состояния из systemd, env-check и JSONL-логов.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
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


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read dotenv values for internal checks only; callers must not print values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_between(later: Any, earlier: Any) -> float | None:
    dt_later = _parse_ts(later)
    dt_earlier = _parse_ts(earlier)
    if not dt_later or not dt_earlier:
        return None
    return max(0.0, (dt_later - dt_earlier).total_seconds())


def _safe_json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 3) if values else None


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


def _task_sample(rows: list[dict[str, Any]], *, sample_size: int = 20) -> dict[str, Any]:
    sample = rows[:sample_size]
    durations = [float(r["duration_sec"]) for r in sample if r.get("duration_sec") is not None]
    queues = [float(r["queue_sec"]) for r in sample if r.get("queue_sec") is not None]
    query_chars = [int(r.get("query_chars") or 0) for r in sample]
    system_chars = [int(r.get("system_prompt_chars") or 0) for r in sample]
    return {
        "count": len(sample),
        "avg_duration_sec": _mean(durations),
        "median_duration_sec": _median(durations),
        "min_duration_sec": round(min(durations), 3) if durations else None,
        "max_duration_sec": round(max(durations), 3) if durations else None,
        "avg_queue_sec": _mean(queues),
        "avg_query_chars": round(sum(query_chars) / len(query_chars), 1) if query_chars else None,
        "max_query_chars": max(query_chars) if query_chars else None,
        "avg_system_prompt_chars": round(sum(system_chars) / len(system_chars), 1) if system_chars else None,
        "max_system_prompt_chars": max(system_chars) if system_chars else None,
        "tasks": [
            {
                "id": r.get("id"),
                "created_at": r.get("created_at"),
                "duration_sec": r.get("duration_sec"),
                "queue_sec": r.get("queue_sec"),
                "query_chars": r.get("query_chars"),
                "system_prompt_chars": r.get("system_prompt_chars"),
            }
            for r in sample[:8]
        ],
    }


def _overmind_task_latency_summary(repo: Path, *, limit: int = 1000, sample_size: int = 20) -> dict[str, Any]:
    """Summarise answer-model latency from Overmind task list without logging prompts/secrets."""
    env = {**os.environ, **_read_dotenv(repo / ".env")}
    token = env.get("OVERMIND_TOKEN") or env.get("GATEWAY_POLL_TOKEN")
    base_url = (env.get("OVERMIND_URL") or "http://localhost:8080").rstrip("/")
    if not token:
        return {"available": False, "reason": "missing OVERMIND_TOKEN/GATEWAY_POLL_TOKEN"}

    url = f"{base_url}/api/v1/tasks/api/list?limit={int(limit)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - internal configured endpoint.
            body = resp.read().decode("utf-8", errors="replace")
            status_code = resp.status
    except urllib.error.HTTPError as exc:
        return {"available": False, "reason": f"http_{exc.code}", "url_path": "/api/v1/tasks/api/list"}
    except Exception as exc:  # pragma: no cover - network dependent
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "url_path": "/api/v1/tasks/api/list"}

    try:
        payload = json.loads(body)
    except Exception as exc:
        return {"available": False, "reason": f"json_parse_failed: {type(exc).__name__}", "http_status": status_code}

    if isinstance(payload, list):
        tasks = payload
    elif isinstance(payload, dict):
        tasks = payload.get("tasks") or payload.get("items") or payload.get("data") or []
    else:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []

    rows: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        request_data = _safe_json_obj(task.get("request_data"))
        model = str(request_data.get("model") or task.get("model") or "")
        if model != "google/gemini-2.5-flash":
            continue
        duration = _seconds_between(task.get("completed_at"), task.get("started_at"))
        if duration is None:
            continue
        rows.append(
            {
                "id": task.get("id") or task.get("task_id"),
                "created_at": task.get("created_at"),
                "duration_sec": round(duration, 3),
                "queue_sec": None if _seconds_between(task.get("started_at"), task.get("created_at")) is None else round(float(_seconds_between(task.get("started_at"), task.get("created_at"))), 3),
                "query_chars": len(str(request_data.get("query") or "")),
                "system_prompt_chars": len(str(request_data.get("system_prompt") or "")),
            }
        )
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    recent = rows[:sample_size]
    previous = rows[sample_size : sample_size * 2]
    recent_summary = _task_sample(recent, sample_size=sample_size)
    previous_summary = _task_sample(previous, sample_size=sample_size)
    delta: dict[str, Any] = {}
    if recent_summary.get("avg_query_chars") is not None and previous_summary.get("avg_query_chars") is not None:
        delta["avg_query_chars"] = round(float(recent_summary["avg_query_chars"]) - float(previous_summary["avg_query_chars"]), 1)
    if recent_summary.get("avg_duration_sec") is not None and previous_summary.get("avg_duration_sec") is not None:
        delta["avg_duration_sec"] = round(float(recent_summary["avg_duration_sec"]) - float(previous_summary["avg_duration_sec"]), 3)
    return {
        "available": True,
        "url_path": "/api/v1/tasks/api/list?limit=1000",
        "model_filter": "google/gemini-2.5-flash",
        "count_total": len(rows),
        "recent": recent_summary,
        "previous": previous_summary,
        "delta_recent_minus_previous": delta,
        "privacy": "Only ids/timestamps/durations and payload lengths are returned; prompts and token values are not included.",
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

    latency = _overmind_task_latency_summary(repo)
    latency_ok = bool(latency.get("available")) and int(latency.get("count_total") or 0) > 0
    recent = latency.get("recent") if isinstance(latency.get("recent"), dict) else {}
    latency_msg = (
        f"recent_n={recent.get('count')}; avg={recent.get('avg_duration_sec')}s; "
        f"median={recent.get('median_duration_sec')}s; avg_query_chars={recent.get('avg_query_chars')}"
        if latency_ok
        else f"unavailable: {latency.get('reason')}"
    )
    _add(checks, "answer_model_task_latency", "ok" if latency_ok else "warn", latency_msg, latency)

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
