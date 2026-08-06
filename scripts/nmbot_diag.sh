#!/usr/bin/env bash
# nmbot_diag.sh — единая диагностика nmbot
# Точка входа: показывает статус продакшн-бота на VPS и dev-окружения
#
# Использование:
#   bash scripts/nmbot_diag.sh          # полная диагностика
#   bash scripts/nmbot_diag.sh --quick        # только статус (PID + uptime)
#   bash scripts/nmbot_diag.sh --logs         # только лог
#   bash scripts/nmbot_diag.sh --local --json # local read-only JSON, без SSH
#   bash scripts/nmbot_diag.sh --vps --json   # VPS read-only JSON

set -euo pipefail

VPS_HOST="193.107.155.236"
VPS_PORT="1905"
VPS_USER="neiro"
VPS_BOT_DIR="/home/neiro/novostroy-bot"
API_SERVICE="novostroy-bot-api.service"
BRIDGE_SERVICE="novostroy-bot-n8n-bridge.service"
SSH_BIN="${NMBOT_DIAG_SSH:-ssh}"

# ── helpers ──────────────────────────────────────────────────

section() {
  echo ""
  echo "══════════════════════════════════════════════════════════"
  echo "  $1"
  echo "══════════════════════════════════════════════════════════"
}

check_vps() {
  "$SSH_BIN" -p "$VPS_PORT" -o ConnectTimeout=5 -o BatchMode=yes "$VPS_USER@$VPS_HOST" "true" 2>/dev/null
}

diag_local_json() {
  python3 - <<'PY'
import json, os
from pathlib import Path

root = Path.cwd()
default_runtime = root / "data" / "nmbot_runtime_version.json"
runtime_path = Path(os.getenv("NMBOT_RUNTIME_VERSION_FILE", str(default_runtime))).expanduser()
supported = {"V0", "V2", "V3", "V5"}
runtime = {"path": str(runtime_path), "status": "missing", "effective_version": "V2"}
if runtime_path.exists():
    try:
        data = json.loads(runtime_path.read_text(encoding="utf-8"))
        raw = data.get("version") if isinstance(data, dict) else None
        version = str(raw or "").strip().upper()
        if version in supported:
            runtime.update({"status": "version", "version": version, "effective_version": version})
        else:
            runtime.update({"status": "invalid_default", "raw_version_present": raw is not None, "effective_version": "V2"})
    except Exception:
        runtime.update({"status": "malformed_default", "effective_version": "V2"})

report = {
    "ok": True,
    "mode": "local",
    "production": {"status": "not_checked"},
    "runtime_version": runtime,
    "config_shape": {
        "JIVO_PROVIDER_TOKEN": bool(os.getenv("JIVO_PROVIDER_TOKEN", "").strip()),
        "NMBOT_API_TOKEN": bool(os.getenv("NMBOT_API_TOKEN", "").strip()),
        "NMBOT_CALLBACK_OUTBOX_DIR": bool(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR", "").strip()),
    },
}
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
PY
}

diag_vps_json() {
  if ! check_vps; then
    python3 - <<PY
import json
print(json.dumps({
    "ok": False,
    "mode": "vps",
    "production": {"status": "unavailable", "health": "unverified", "host": "$VPS_HOST", "port": "$VPS_PORT"},
    "error": "ssh_unavailable",
}, ensure_ascii=False, sort_keys=True))
PY
    return 0
  fi

  local output
  if ! output=$("$SSH_BIN" -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" bash <<'SSH'
set -euo pipefail
cd "/home/neiro/novostroy-bot" 2>/dev/null || true
python3 - <<'PY'
import json, os, subprocess, time, urllib.error, urllib.request
from pathlib import Path

bot_dir = Path("/home/neiro/novostroy-bot")
services = ["novostroy-bot-api.service", "novostroy-bot-n8n-bridge.service"]
supported_runtime_versions = {"V0", "V2", "V3", "V5"}

def run(args):
    try:
        return subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    except Exception as exc:
        return type("Result", (), {"returncode": 1, "stdout": "", "stderr": type(exc).__name__})()

service_status = {}
for service in services:
    active = run(["systemctl", "--user", "is-active", service])
    pid = run(["systemctl", "--user", "show", service, "-p", "MainPID", "--value"])
    service_status[service] = {
        "active_state": active.stdout.strip() or "unknown",
        "main_pid_present": (pid.stdout.strip() not in {"", "0"}),
    }

def read_env_file(path):
    values = {}
    if not path.exists():
        return values
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except Exception:
        return values
    return values


env_file = bot_dir / ".env"
env_values = read_env_file(env_file)


def env_value(key):
    value = os.getenv(key, "").strip()
    if value:
        return value
    return str(env_values.get(key, "")).strip()


def live_runtime_version():
    endpoint = "http://127.0.0.1:8088/api/runtime-version"
    token = env_value("NMBOT_API_TOKEN")
    result = {"source": "live_endpoint", "endpoint": endpoint, "status": "unknown", "verified": False}
    if not token:
        result["reason"] = "token_missing"
        return result
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result.update({"status": "unavailable", "reason": "http_error", "http_status": int(exc.code)})
        return result
    except Exception:
        result.update({"status": "unavailable", "reason": "request_failed"})
        return result
    if not isinstance(payload, dict):
        result.update({"status": "malformed", "reason": "non_object_response"})
        return result
    version = str(payload.get("runtime_version") or "").strip().upper()
    if version in supported_runtime_versions:
        result.update({"status": "verified", "verified": True, "version": version})
    else:
        result.update({"status": "malformed", "reason": "unsupported_or_missing_runtime_version"})
    return result


runtime_path = Path(os.getenv("NMBOT_RUNTIME_VERSION_FILE", str(bot_dir / "data" / "nmbot_runtime_version.json"))).expanduser()
persisted_selector = {"source": "persisted_selector_file", "path": str(runtime_path), "status": "missing", "active_process_truth": False, "fallback_version": "V2"}
if runtime_path.exists():
    try:
        data = json.loads(runtime_path.read_text(encoding="utf-8"))
        raw = data.get("version") if isinstance(data, dict) else None
        version = str(raw or "").strip().upper()
        if version in supported_runtime_versions:
            persisted_selector.update({"status": "version", "version": version})
        else:
            persisted_selector.update({"status": "invalid_fallback", "raw_version_present": raw is not None})
    except Exception:
        persisted_selector.update({"status": "malformed_fallback"})

env_keys = set(env_values)

error_log = bot_dir / "logs" / ("bot_error_events-" + run(["date", "-u", "+%F"]).stdout.strip() + ".jsonl")
error_event_log = {"path": str(error_log), "exists": error_log.exists(), "freshness": "missing"}
if error_log.exists():
    stat = error_log.stat()
    age_seconds = max(0, int(time.time() - stat.st_mtime))
    freshness_limit = int(os.getenv("NMBOT_DIAG_ERROR_LOG_FRESHNESS_SECONDS", "900"))
    error_event_log.update({
        "size_bytes": stat.st_size,
        "mtime_epoch": int(stat.st_mtime),
        "age_seconds": age_seconds,
        "freshness": "fresh" if age_seconds <= freshness_limit else "stale",
        "freshness_limit_seconds": freshness_limit,
    })

live_runtime = live_runtime_version()
print(json.dumps({
    "ok": True,
    "mode": "vps",
    "production": {"status": "verified_read_only", "health": "checked"},
    "services": service_status,
    "current_runtime_version": live_runtime,
    "runtime_version": live_runtime,
    "persisted_runtime_selector": persisted_selector,
    "config_shape": {
        "JIVO_PROVIDER_TOKEN": "JIVO_PROVIDER_TOKEN" in env_keys or bool(os.getenv("JIVO_PROVIDER_TOKEN", "").strip()),
        "NMBOT_API_TOKEN": "NMBOT_API_TOKEN" in env_keys or bool(os.getenv("NMBOT_API_TOKEN", "").strip()),
        "NMBOT_CALLBACK_OUTBOX_DIR": "NMBOT_CALLBACK_OUTBOX_DIR" in env_keys or bool(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR", "").strip()),
    },
    "error_event_log": error_event_log,
}, ensure_ascii=False, sort_keys=True))
PY
SSH
  ); then
    python3 - <<PY
import json
print(json.dumps({"ok": False, "mode": "vps", "production": {"status": "unavailable", "health": "unverified"}, "error": "ssh_command_failed"}, ensure_ascii=False, sort_keys=True))
PY
    return 0
  fi

  OUTPUT="$output" python3 - <<'PY'
import json, os
raw = os.environ.get("OUTPUT", "")
try:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("not an object")
    print(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
except Exception:
    print(json.dumps({
        "ok": False,
        "mode": "vps",
        "production": {"status": "unverified", "health": "unverified"},
        "error": "malformed_vps_output",
        "current_runtime_version": {"source": "live_endpoint", "status": "unknown", "verified": False, "reason": "malformed_vps_output"},
        "runtime_version": {"source": "live_endpoint", "status": "unknown", "verified": False, "reason": "malformed_vps_output"},
        "persisted_runtime_selector": {"source": "persisted_selector_file", "status": "unknown", "active_process_truth": False},
    }, ensure_ascii=False, sort_keys=True))
PY
}

# ── диагностика ──────────────────────────────────────────────

diag_status() {
  section "🖥 Production (VPS — $VPS_HOST)"

  if ! check_vps; then
    echo "✗ VPS недоступен (ssh -p $VPS_PORT $VPS_USER@$VPS_HOST)"
    return
  fi

  "$SSH_BIN" -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" bash <<'SSH'
    API_SERVICE="novostroy-bot-api.service"
    BRIDGE_SERVICE="novostroy-bot-n8n-bridge.service"
    BOT_DIR="/home/neiro/novostroy-bot"

    for SERVICE in "$API_SERVICE" "$BRIDGE_SERVICE"; do
      echo "Сервис: $SERVICE"
      echo "───"
      systemctl --user status "$SERVICE" --no-pager --lines 0 2>&1 | head -8 || true
      PID=$(systemctl --user show "$SERVICE" -p MainPID --value 2>/dev/null || true)
      if [ "$PID" != "0" ] && [ -n "$PID" ]; then
        echo "PID: $PID"
        echo "Uptime: $(ps -o etime= -p "$PID" 2>/dev/null | xargs || true)"
        echo "Memory: $(ps -o rss= -p "$PID" 2>/dev/null | awk '{printf "%.0f MB", $1/1024}' || true)"
        echo "CPU:    $(ps -o %cpu= -p "$PID" 2>/dev/null | xargs || true)%"
        echo "Команда процесса:"
        tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null || true
        echo ""
      else
        echo "⚠ сервис не запущен (PID=0)"
      fi
      echo ""
    done

    echo "Health endpoints:"
    if command -v curl >/dev/null 2>&1; then
      curl -fsS --max-time 3 http://127.0.0.1:8088/health 2>/dev/null || echo "api health недоступен"
      echo ""
      curl -fsS --max-time 3 http://127.0.0.1:8093/health 2>/dev/null || echo "bridge health недоступен"
      echo ""
    else
      echo "curl не найден — health-check пропущен"
    fi

    echo ""
    echo "Последний коммит:"
    cd "$BOT_DIR" && git log --oneline -1 2>/dev/null || echo "не git"

    echo ""
    echo "Рабочая директория: $BOT_DIR"
    echo "Runtime-файлы:"
    ls -la "$BOT_DIR/scripts/nmbot_api_server.py" 2>/dev/null || echo "не найден: $BOT_DIR/scripts/nmbot_api_server.py"
    ls -la "$BOT_DIR/scripts/nmbot_n8n_bridge_server.py" 2>/dev/null || echo "не найден: $BOT_DIR/scripts/nmbot_n8n_bridge_server.py"
SSH
}

diag_logs() {
  section "📋 Логи Jivo/API (последние строки)"

  if ! check_vps; then
    echo "✗ VPS недоступен"
    return
  fi

  "$SSH_BIN" -p "$VPS_PORT" "$VPS_USER@$VPS_HOST" bash <<'SSH'
    BOT_DIR="/home/neiro/novostroy-bot"
    cd "$BOT_DIR" 2>/dev/null || { echo "не найден каталог $BOT_DIR"; exit 0; }
    ERROR_LOG="logs/bot_error_events-$(date -u +%F).jsonl"
    for file in "$ERROR_LOG" "logs/n8n_bridge_structured.jsonl"; do
      echo ""
      echo "== $file =="
      if [ -f "$file" ]; then
        tail -20 "$file" 2>/dev/null || true
      else
        echo "опциональный файл отсутствует"
      fi
    done

    echo ""
    echo "== runtime summary audit (logs/dialogue_journal.jsonl) =="
    if [ -f "logs/dialogue_journal.jsonl" ] && [ -f "scripts/nmbot_jivo_dialogue_diagnose.py" ]; then
      python3 scripts/nmbot_jivo_dialogue_diagnose.py --audit-log logs/dialogue_journal.jsonl --audit-only --last 20 2>/dev/null \
        || echo "audit-only диагностика недоступна в текущем deployed script"
    else
      echo "dialogue journal или diagnoser отсутствует"
    fi
SSH
}

diag_dev() {
  section "💻 Dev-окружение (local API/V2)"

  PROC=$(pgrep -f "nmbot_api_server.py|nmbot_n8n_bridge_server.py" 2>/dev/null || true)
  if [ -n "$PROC" ]; then
    echo "Dev API/bridge процесс найден (PID: $PROC)"
  else
    echo "Dev API/bridge не запущен (это нормально, если не тестируете)"
  fi

  echo "Deploy-smoke не запускается диагностикой: его default-режим может обращаться к VPS."
  echo "Для отдельной проверки используйте явно выбранный режим nmbot_deploy_smoke.py."

  echo ""
  echo "Скрипты: $(ls scripts/*.py scripts/*.sh 2>/dev/null | wc -l) шт"
  echo "Тесты: python3 -m pytest -q tests/test_nmbot_api_jivo_p1.py tests/test_nmbot_v2_runtime.py"
}

diag_all() {
  echo "nmbot — единая диагностика"
  echo "Дата: $(date '+%Y-%m-%d %H:%M:%S')"

  case "${1:-all}" in
    --quick|status) diag_status ;;
    --logs|logs)    diag_logs ;;
    --dev)           diag_dev ;;
    *)               diag_status; diag_logs; diag_dev ;;
  esac

  echo ""
  echo "✅ Диагностика завершена"
}

main() {
  local mode="default"
  local json="0"
  local legacy="all"
  for arg in "$@"; do
    case "$arg" in
      --local) mode="local" ;;
      --vps) mode="vps" ;;
      --json) json="1" ;;
      --quick|status|--logs|logs|--dev) legacy="$arg" ;;
      *) legacy="$arg" ;;
    esac
  done

  if [ "$json" = "1" ]; then
    case "$mode" in
      local) diag_local_json ;;
      vps) diag_vps_json ;;
      *) diag_local_json ;;
    esac
    return
  fi

  case "$mode" in
    local) diag_dev ;;
    vps) diag_status; diag_logs ;;
    *) diag_all "$legacy" ;;
  esac
}

# ── запуск ───────────────────────────────────────────────────

main "$@"
