#!/usr/bin/env bash
# Read-only VPS wrapper for nmbot Jivo structured trace audit.
set -euo pipefail

HOST="193.107.155.236"
PORT="1905"
USER_NAME="neiro"
REMOTE_LOG="/home/neiro/novostroy-bot/logs/n8n_bridge_structured.jsonl"
LAST=""
STRICT=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/nmbot_jivo_audit.sh [--last N] [--strict] [--host HOST] [--port PORT] [--user USER] [--remote-log PATH]

Read-only: checks SSH, streams the remote structured JSONL log to a local temp file,
then runs scripts/nmbot_jivo_trace_analyze.py. Secrets and payload text are not printed.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --last) LAST="${2:?--last requires N}"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    --host) HOST="${2:?--host requires value}"; shift 2 ;;
    --port) PORT="${2:?--port requires value}"; shift 2 ;;
    --user) USER_NAME="${2:?--user requires value}"; shift 2 ;;
    --remote-log) REMOTE_LOG="${2:?--remote-log requires path}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ANALYZER="$SCRIPT_DIR/nmbot_jivo_trace_analyze.py"

if [[ ! -f "$ANALYZER" ]]; then
  echo "Analyzer not found: $ANALYZER" >&2
  exit 2
fi

echo "Проверяю SSH: ${USER_NAME}@${HOST}:${PORT}"
if ! ssh -p "$PORT" -o BatchMode=yes -o ConnectTimeout=7 "$USER_NAME@$HOST" "test -r '$REMOTE_LOG'" >/dev/null 2>&1; then
  echo "Не удалось прочитать remote log по SSH: ${REMOTE_LOG}" >&2
  exit 1
fi

tmp="$(mktemp -t nmbot-jivo-structured.XXXXXX.jsonl)"
cleanup() { rm -f -- "$tmp"; }
trap cleanup EXIT

echo "Копирую structured log во временный локальный файл без печати содержимого..."
ssh -p "$PORT" -o BatchMode=yes "$USER_NAME@$HOST" "cat '$REMOTE_LOG'" > "$tmp"

args=("$ANALYZER" "$tmp")
if [[ -n "$LAST" ]]; then
  args+=(--last "$LAST")
fi
if [[ "$STRICT" -eq 1 ]]; then
  args+=(--strict)
fi

python3 "${args[@]}"
