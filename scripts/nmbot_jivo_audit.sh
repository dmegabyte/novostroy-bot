#!/usr/bin/env bash
# Read-only VPS wrapper for nmbot Jivo structured trace audit.
set -euo pipefail

HOST="193.107.155.236"
PORT="1905"
USER_NAME="neiro"
REMOTE_LOG="/home/neiro/novostroy-bot/logs/n8n_bridge_structured.jsonl"
DELIVERY_TRACE_LOG="/home/neiro/novostroy-bot/logs/jivo_delivery_trace.jsonl"
MAX_LAST=1000
MAX_REMOTE_BYTES=$((1024 * 1024))
LAST="$MAX_LAST"
STRICT=0
DELIVERY_TRACE=0
REMOTE_LOG_OVERRIDDEN=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/nmbot_jivo_audit.sh [--last N] [--strict] [--delivery-trace] [--host HOST] [--port PORT] [--user USER] [--remote-log PATH]

Read-only: checks SSH, streams the remote structured JSONL log (or the privacy-safe canonical delivery trace with --delivery-trace) to a local temp file,
then runs scripts/nmbot_jivo_trace_analyze.py. Secrets and payload text are not printed.
--delivery-trace cannot be combined with --remote-log.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --last) LAST="${2:?--last requires N}"; shift 2 ;;
    --strict) STRICT=1; shift ;;
    --delivery-trace) REMOTE_LOG="$DELIVERY_TRACE_LOG"; DELIVERY_TRACE=1; shift ;;
    --host) [[ $# -ge 2 ]] || { echo "--host requires value" >&2; exit 2; }; HOST="$2"; shift 2 ;;
    --port) [[ $# -ge 2 ]] || { echo "--port requires value" >&2; exit 2; }; PORT="$2"; shift 2 ;;
    --user) [[ $# -ge 2 ]] || { echo "--user requires value" >&2; exit 2; }; USER_NAME="$2"; shift 2 ;;
    --remote-log) REMOTE_LOG="${2:?--remote-log requires path}"; REMOTE_LOG_OVERRIDDEN=1; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! "$LAST" =~ ^[0-9]+$ ]] || (( 10#$LAST < 1 || 10#$LAST > MAX_LAST )); then
  echo "--last must be an integer from 1 to $MAX_LAST" >&2
  exit 2
fi

if [[ "$STRICT" -eq 1 && "$DELIVERY_TRACE" -ne 1 ]]; then
  echo "--strict requires --delivery-trace; legacy structured logs cannot prove the full Jivo delivery lifecycle" >&2
  exit 2
fi

if [[ "$DELIVERY_TRACE" -eq 1 && "$REMOTE_LOG_OVERRIDDEN" -eq 1 ]]; then
  echo "--delivery-trace cannot be combined with --remote-log; it always uses the canonical delivery trace" >&2
  exit 2
fi

# Connection values are command-line controlled. Keep this allowlist deliberately
# narrow: it supports the production defaults plus ordinary DNS names, IPv4
# addresses, and Unix-style account names, while rejecting SSH option injection
# and shell metacharacters before SSH is invoked.
if [[ ! "$HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "Unsafe SSH host" >&2
  exit 2
fi

if [[ ! "$USER_NAME" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]]; then
  echo "Unsafe SSH user" >&2
  exit 2
fi

if [[ ! "$PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
  echo "Unsafe SSH port" >&2
  exit 2
fi

# The path is interpolated into a remote shell command below. Restrict it to
# absolute filesystem paths with ordinary filename characters.
if [[ ! "$REMOTE_LOG" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  echo "Unsafe remote log path" >&2
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ANALYZER="$SCRIPT_DIR/nmbot_jivo_trace_analyze.py"

if [[ ! -f "$ANALYZER" ]]; then
  echo "Analyzer not found: $ANALYZER" >&2
  exit 2
fi

echo "Проверяю SSH: ${USER_NAME}@${HOST}:${PORT}"
if ! ssh -p "$PORT" -o BatchMode=yes -o ConnectTimeout=7 -- "$USER_NAME@$HOST" "test -r '$REMOTE_LOG'" >/dev/null 2>&1; then
  echo "Не удалось прочитать remote log по SSH: ${REMOTE_LOG}" >&2
  exit 1
fi

tmp="$(mktemp -t nmbot-jivo-structured.XXXXXX.jsonl)"
extract_meta="$(mktemp -t nmbot-jivo-extract.XXXXXX.meta)"
cleanup() { rm -f -- "$tmp" "$extract_meta"; }
trap cleanup EXIT

echo "Копирую не более ${LAST} последних строк structured log во временный локальный файл без печати содержимого..."
# Scan the remote file backwards in fixed-sized blocks.  The extractor emits a
# chronological suffix of *newline-terminated* records only, so a byte cap can
# never leave a partial JSONL record for the analyzer.  It also never transfers
# more than the cap over SSH.  The source is base64 encoded solely to transport
# it as one shell-safe SSH command; path and numeric arguments are validated
# above before interpolation.
read -r -d '' REMOTE_EXTRACTOR <<'PY' || true
import sys

path, requested_text, cap_text = sys.argv[1:]
requested = int(requested_text)
cap = int(cap_text)
chunk_size = 65536


def last_newline_before(handle, position):
    """Return the final newline offset before position without reading a record."""
    while position > 0:
        start = max(0, position - chunk_size)
        handle.seek(start)
        block = handle.read(position - start)
        found = block.rfind(b"\n")
        if found >= 0:
            return start + found
        position = start
    return -1


records = []
used = 0
truncated = False
with open(path, "rb") as handle:
    handle.seek(0, 2)
    end = last_newline_before(handle, handle.tell())
    # A line not terminated by a newline may be an in-progress append.  Leave
    # it out rather than converting it into apparent valid JSONL.
    if end >= 0:
        end += 1
        while end > 0 and len(records) < requested:
            previous_newline = last_newline_before(handle, end - 1)
            start = previous_newline + 1
            length = end - start
            # Reject a record from its newline offsets before materialising it.
            # Keep scanning: an older complete record may still fit the cap.
            if length > cap - used:
                truncated = True
                end = start
                continue
            handle.seek(start)
            record = handle.read(length)
            # Empty separators (including whitespace-only lines) are not JSONL
            # records. Skip them without consuming one of the requested slots.
            if not record.strip():
                end = start
                continue
            records.append(record)
            used += length
            end = start

for record in reversed(records):
    sys.stdout.buffer.write(record)
sys.stderr.write(
    f"nmbot_jivo_audit_extract records={len(records)} bytes={used} "
    f"requested={requested} truncated={'true' if truncated else 'false'}\n"
)
PY
REMOTE_EXTRACTOR_B64="$(printf '%s' "$REMOTE_EXTRACTOR" | base64 | tr -d '\n')"
REMOTE_COMMAND="python3 -c 'import base64; exec(compile(base64.b64decode(\"$REMOTE_EXTRACTOR_B64\"), \"<nmbot-jivo-extract>\", \"exec\"))' '$REMOTE_LOG' '$LAST' '$MAX_REMOTE_BYTES'"

if ! ssh -p "$PORT" -o BatchMode=yes -- "$USER_NAME@$HOST" "$REMOTE_COMMAND" > "$tmp" 2> "$extract_meta"; then
  echo "Не удалось извлечь bounded JSONL по SSH" >&2
  exit 1
fi

extract_status="$(<"$extract_meta")"
if [[ ! "$extract_status" =~ ^nmbot_jivo_audit_extract\ records=[0-9]+\ bytes=[0-9]+\ requested=[0-9]+\ truncated=(true|false)$ ]]; then
  echo "Remote extraction did not report bounded metadata" >&2
  exit 1
fi
echo "Structured extraction: ${extract_status#nmbot_jivo_audit_extract }"

args=("$ANALYZER" "$tmp")
args+=(--last "$LAST")
if [[ "$STRICT" -eq 1 ]]; then
  args+=(--strict)
fi

python3 "${args[@]}"
