#!/usr/bin/env python3
"""Safe nmbot environment contract check.

Проверяет .env, токены, контур, логи и systemd unit без вывода секретов.
JSON-режим пригоден для `scripts/nmbot_test_agent.py --suite env`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parent.parent
ALLOWED_ENVS = {"prod", "staging", "local"}


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _token_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_len(value: str | None) -> int:
    return len(value or "")


def _looks_like_telegram_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", value or ""))


def _looks_like_openrouter_key(value: str) -> bool:
    return bool(value) and value.startswith("sk-or-") and len(value) >= 40


def _infer_env(repo: Path, env_values: dict[str, str], expected_env: str | None) -> tuple[str, str]:
    if expected_env:
        return expected_env, "arg"
    if env_values.get("NMBOT_ENV"):
        return env_values["NMBOT_ENV"], ".env"
    if os.environ.get("NMBOT_ENV"):
        return os.environ["NMBOT_ENV"], "process"
    name = repo.name
    if name == "novostroy-bot-staging" or "staging" in str(repo):
        return "staging", "path"
    if name == "novostroy-bot" and str(repo).startswith("/home/neiro/"):
        return "prod", "path"
    return "local", "path"


def _auto_compare_env(repo: Path, compare_env_file: str | None) -> Path | None:
    if compare_env_file:
        return Path(compare_env_file).expanduser().resolve()
    repo_s = str(repo)
    if repo_s == "/home/neiro/novostroy-bot":
        return Path("/home/neiro/novostroy-bot-staging/.env")
    if repo_s == "/home/neiro/novostroy-bot-staging":
        return Path("/home/neiro/novostroy-bot/.env")
    return None


def _auto_service_name(repo: Path, effective_env: str, service: str | None) -> str | None:
    if service:
        return service
    if not str(repo).startswith("/home/neiro/"):
        return None
    if effective_env == "prod":
        return "novostroy-bot.service"
    if effective_env == "staging":
        return "novostroy-bot-staging.service"
    return None


def _systemd_show(service: str) -> dict[str, str]:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "show", service, "-p", "WorkingDirectory", "-p", "ExecStart", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - depends on host systemd
        return {"_error": f"{type(exc).__name__}: {exc}"}
    out: dict[str, str] = {"_returncode": str(proc.returncode)}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
    if proc.stderr:
        out["_stderr"] = proc.stderr.strip()[:300]
    return out


def _add(checks: list[dict[str, Any]], name: str, passed: bool, msg: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "msg": msg})


def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    repo = Path(args.repo).expanduser().resolve()
    env_file = Path(args.env_file).expanduser()
    if not env_file.is_absolute():
        env_file = repo / env_file
    env_file = env_file.resolve()
    logs_dir = Path(args.logs_dir).expanduser()
    if not logs_dir.is_absolute():
        logs_dir = repo / logs_dir
    logs_dir = logs_dir.resolve()

    env_values = _parse_dotenv(env_file)
    effective_env, env_source = _infer_env(repo, env_values, args.expected_env)
    compare_env = _auto_compare_env(repo, args.compare_env_file)
    service_name = _auto_service_name(repo, effective_env, args.service)
    expected_workdir = Path(args.expected_workdir).expanduser().resolve() if args.expected_workdir else repo

    checks: list[dict[str, Any]] = []

    _add(checks, "env_file_exists", env_file.exists(), f"path={env_file}")

    tg = env_values.get("TELEGRAM_BOT_TOKEN", "")
    _add(checks, "telegram_token_present", bool(tg), f"len={_safe_len(tg)}")
    _add(checks, "telegram_token_shape", _looks_like_telegram_token(tg), f"len={_safe_len(tg)}; has_colon={':' in tg}")

    openrouter = env_values.get("OPENROUTER_API_KEY", "")
    _add(checks, "openrouter_key_present", bool(openrouter), f"len={_safe_len(openrouter)}; shape_ok={_looks_like_openrouter_key(openrouter)}")

    auth_keys = [key for key in ("OVERMIND_TOKEN", "GATEWAY_POLL_TOKEN") if env_values.get(key)]
    _add(checks, "overmind_or_gateway_token_present", bool(auth_keys), f"keys_set={auth_keys}")

    _add(checks, "nmbot_env_valid", effective_env in ALLOWED_ENVS, f"env={effective_env}; source={env_source}; allowed={sorted(ALLOWED_ENVS)}")

    if compare_env and compare_env.exists():
        other_values = _parse_dotenv(compare_env)
        other_tg = other_values.get("TELEGRAM_BOT_TOKEN", "")
        same = bool(tg and other_tg and _token_fingerprint(tg) == _token_fingerprint(other_tg))
        _add(checks, "prod_staging_telegram_tokens_differ", bool(tg and other_tg and not same), f"compare_path={compare_env}; current_fp={_token_fingerprint(tg) if tg else 'missing'}; compare_fp={_token_fingerprint(other_tg) if other_tg else 'missing'}")
    else:
        _add(checks, "prod_staging_telegram_tokens_differ", True, "not_applicable: compare env file not found on this host")

    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        probe = logs_dir / f".env_check_write_test_{os.getpid()}"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        logs_ok = True
        logs_msg = f"path={logs_dir}"
    except Exception as exc:
        logs_ok = False
        logs_msg = f"path={logs_dir}; error={type(exc).__name__}: {exc}"
    _add(checks, "logs_dir_writable", logs_ok, logs_msg)

    if service_name:
        service = _systemd_show(service_name)
        rc_ok = service.get("_returncode") == "0"
        workdir = service.get("WorkingDirectory", "")
        execstart = service.get("ExecStart", "")
        workdir_ok = rc_ok and Path(workdir).resolve() == expected_workdir if workdir else False
        exec_ok = "scripts/chat_tester_bot.py" in execstart or "scripts/run_bot.sh" in execstart
        _add(checks, "service_unit_workdir", workdir_ok, f"service={service_name}; workdir={workdir or 'missing'}; expected={expected_workdir}")
        _add(checks, "service_unit_exec", rc_ok and exec_ok, f"service={service_name}; exec_mentions_runtime={exec_ok}")
    else:
        _add(checks, "service_unit_workdir", True, "not_applicable: local/non-vps run")
        _add(checks, "service_unit_exec", True, "not_applicable: local/non-vps run")

    failed = [c for c in checks if not c["passed"]]
    return {
        "summary": {"total": len(checks), "pass": len(checks) - len(failed), "fail": len(failed)},
        "repo": str(repo),
        "env_file": str(env_file),
        "effective_env": effective_env,
        "duration_ms": int((time.time() - started) * 1000),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe nmbot env check — no secret values in output")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--repo", default=str(REPO), help="repo root")
    parser.add_argument("--env-file", default=".env", help="dotenv file path")
    parser.add_argument("--expected-env", choices=sorted(ALLOWED_ENVS), default=None)
    parser.add_argument("--compare-env-file", default=None)
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--service", default=None)
    parser.add_argument("--expected-workdir", default=None)
    args = parser.parse_args()

    result = run_checks(args)
    ok = result["summary"]["fail"] == 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mark = "✓" if ok else "✗"
        print(f"{mark} nmbot env check: {result['summary']['pass']}/{result['summary']['total']} pass")
        for check in result["checks"]:
            cmark = "✓" if check["passed"] else "✗"
            print(f"  {cmark} {check['name']}: {check.get('msg', '')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
