#!/usr/bin/env python3
"""Safely add/update nmbot API/Jivo secrets in a dotenv file.

The script never prints secret values. It is meant for the moment when the
operator receives Jivo/API credentials later.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


KNOWN_KEYS = {
    "JIVO_PROVIDER_TOKEN",
    "NMBOT_API_TOKEN",
    "NMBOT_API_PUBLIC_BASE_URL",
    "NMBOT_API_HOST",
    "NMBOT_API_PORT",
    "NMBOT_API_STATE_FILE",
    "NMBOT_CONTOUR_PROFILE",
    "NMBOT_RUNTIME_VERSION_FILE",
    "NMBOT_CALLBACK_OUTBOX_DIR",
    "NMBOT_DIALOGUE_JOURNAL",
    "NMBOT_READABLE_DIALOGUE_JOURNAL",
    "NMBOT_PLANNER_TRACE_FILE",
    "NMBOT_PLANNER_TRACE_DIR",
    "NMBOT_RELEASE_IDENTITY_FILE",
    "NMBOT_N8N_BRIDGE_TOKEN",
    "NMBOT_N8N_BRIDGE_HOST",
    "NMBOT_N8N_BRIDGE_PORT",
    "NMBOT_BRIDGE_UPSTREAM",
    "NMBOT_BRIDGE_STRUCTURED_LOG",
    "JIVO_PROVIDER_ID",
    "JIVO_API_ENDPOINT_BASE",
    "NMBOT_V2_RESPONSE_COMPOSER_MODE",
    "NMBOT_V3_RESPONSE_COMPOSER_MODE",
    "NMBOT_CARD_REFORMATTER_MODE",
    "NMBOT_V2_RESPONSE_TIMEOUT",
    "NMBOT_RESPONSE_FORMATTER_TIMEOUT",
    "NMBOT_RESPONSE_FORMATTER_MODEL",
    "NMBOT_MANAGER_REWRITER_MODE",
    "NMBOT_V2_MANAGER_REWRITER_MODE",
    "NMBOT_V3_MANAGER_REWRITER_MODE",
    "NMBOT_MANAGER_REWRITER_MODEL",
    "NMBOT_MANAGER_REWRITER_TIMEOUT",
    "NMBOT_BLUESMINDS_INTERCEPTOR",
    "NMBOT_BLUESMINDS_MODEL",
    "NMBOT_BLUESMINDS_TIMEOUT",
    "NMBOT_BRIDGE_STATUS_UPDATES_ENABLED",
    "NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS",
    "NMBOT_BRIDGE_STATUS_TEMPLATES",
    "NMBOT_BRIDGE_TIMEOUT_SECONDS",
    "NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS",
    "NMBOT_BRIDGE_FALLBACK_TEXT",
    "NMBOT_V0_MODEL",
    "NMBOT_V0_SEARCH_MODEL",
    "NMBOT_V0_ANSWER_MODEL",
    "NMBOT_V1_ONE_MODEL_GPT55_MODE",
    "NMBOT_GATEWAY_FORENSIC_LOG_ENABLED",
    "NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS",
    "NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES",
}

V0_SEARCH_MODEL_KEY = "NMBOT_V0_SEARCH_MODEL"
V0_ANSWER_MODEL_KEY = "NMBOT_V0_ANSWER_MODEL"
V0_LEGACY_MODEL_KEY = "NMBOT_V0_MODEL"
V0_MODEL_KEYS = frozenset({V0_SEARCH_MODEL_KEY, V0_ANSWER_MODEL_KEY, V0_LEGACY_MODEL_KEY})
DEFAULT_V0_MODEL_LABEL = "code-default"
V1_ONE_MODEL_GPT55_MODE_KEY = "NMBOT_V1_ONE_MODEL_GPT55_MODE"
V1_ONE_MODEL_GPT55_ALLOWED_MODES = frozenset({"off", "shadow", "publish"})
GATEWAY_FORENSIC_LOG_ENABLED_KEY = "NMBOT_GATEWAY_FORENSIC_LOG_ENABLED"
GATEWAY_FORENSIC_LOG_RETENTION_DAYS_KEY = "NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS"
GATEWAY_FORENSIC_LOG_MAX_BYTES_KEY = "NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES"
GATEWAY_FORENSIC_LOG_BOOLEAN_VALUES = frozenset({"0", "1", "false", "true", "no", "yes", "off", "on"})
GATEWAY_FORENSIC_LOG_RETENTION_DAYS_MIN = 1
GATEWAY_FORENSIC_LOG_RETENTION_DAYS_MAX = 31
GATEWAY_FORENSIC_LOG_MAX_BYTES_MIN = 1 * 1024 * 1024
GATEWAY_FORENSIC_LOG_MAX_BYTES_MAX = 100 * 1024 * 1024
_PUBLIC_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,127}$")
_SECRET_LIKE_RE = re.compile(r"(?i)(secret|token|apikey|api_key|password|passwd|bearer|sk-[A-Za-z0-9])")


def _quote_value(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("CR/LF is not allowed in dotenv values")
    if value == "":
        return '""'
    if any(ch.isspace() for ch in value) or any(ch in value for ch in ['#', '"', "'"]):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


def _validate_key_value_before_set(key: str, value: str) -> None:
    if key == V1_ONE_MODEL_GPT55_MODE_KEY and value not in V1_ONE_MODEL_GPT55_ALLOWED_MODES:
        allowed = "|".join(sorted(V1_ONE_MODEL_GPT55_ALLOWED_MODES))
        raise ValueError(f"{V1_ONE_MODEL_GPT55_MODE_KEY} must be one of: {allowed}")
    if key == GATEWAY_FORENSIC_LOG_ENABLED_KEY:
        if value.strip().lower() not in GATEWAY_FORENSIC_LOG_BOOLEAN_VALUES:
            allowed = "|".join(sorted(GATEWAY_FORENSIC_LOG_BOOLEAN_VALUES))
            raise ValueError(f"{GATEWAY_FORENSIC_LOG_ENABLED_KEY} must be a recognized boolean: {allowed}")
    if key in {GATEWAY_FORENSIC_LOG_RETENTION_DAYS_KEY, GATEWAY_FORENSIC_LOG_MAX_BYTES_KEY}:
        minimum, maximum = (
            (GATEWAY_FORENSIC_LOG_RETENTION_DAYS_MIN, GATEWAY_FORENSIC_LOG_RETENTION_DAYS_MAX)
            if key == GATEWAY_FORENSIC_LOG_RETENTION_DAYS_KEY
            else (GATEWAY_FORENSIC_LOG_MAX_BYTES_MIN, GATEWAY_FORENSIC_LOG_MAX_BYTES_MAX)
        )
        if not value.isascii() or not value.isdecimal():
            raise ValueError(f"{key} must be a positive integer between {minimum} and {maximum}")
        numeric_value = int(value)
        if not minimum <= numeric_value <= maximum:
            raise ValueError(f"{key} must be a positive integer between {minimum} and {maximum}")


def set_key(path: Path, key: str, value: str) -> str:
    if "\n" in key or "\r" in key or "=" in key:
        raise ValueError("invalid dotenv key")
    _validate_key_value_before_set(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = f"{key}={_quote_value(value)}"
    changed = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        existing_key = stripped.split("=", 1)[0].strip()
        if existing_key == key:
            if not changed:
                new_lines.append(rendered)
            changed = True
            continue
        new_lines.append(line)
    if not changed:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(rendered)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return "updated" if changed else "added"


def _unquote_dotenv_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def read_dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _unquote_dotenv_value(value)
    return values


def _public_model_or_hidden(value: str | None) -> str:
    if not value:
        return "<unset>"
    if _PUBLIC_MODEL_ID_RE.fullmatch(value) and not _SECRET_LIKE_RE.search(value):
        return value
    return "<hidden>"


def resolve_v0_model(values: dict[str, str], specific_key: str) -> tuple[str, str | None]:
    if specific_key not in {V0_SEARCH_MODEL_KEY, V0_ANSWER_MODEL_KEY}:
        raise ValueError("invalid V0 model key")
    if values.get(specific_key):
        return specific_key, values[specific_key]
    if values.get(V0_LEGACY_MODEL_KEY):
        return V0_LEGACY_MODEL_KEY, values[V0_LEGACY_MODEL_KEY]
    return DEFAULT_V0_MODEL_LABEL, None


def v0_status_lines(path: Path) -> list[str]:
    values = read_dotenv_values(path)
    search_source, search_model = resolve_v0_model(values, V0_SEARCH_MODEL_KEY)
    answer_source, answer_model = resolve_v0_model(values, V0_ANSWER_MODEL_KEY)
    return [
        f"env={path}",
        f"present {V0_SEARCH_MODEL_KEY}={'yes' if bool(values.get(V0_SEARCH_MODEL_KEY)) else 'no'}",
        f"present {V0_ANSWER_MODEL_KEY}={'yes' if bool(values.get(V0_ANSWER_MODEL_KEY)) else 'no'}",
        f"present {V0_LEGACY_MODEL_KEY}={'yes' if bool(values.get(V0_LEGACY_MODEL_KEY)) else 'no'}",
        f"effective search source={search_source} model={_public_model_or_hidden(search_model)}",
        f"effective answer source={answer_source} model={_public_model_or_hidden(answer_model)}",
    ]


def _run_v0_models_command(args: argparse.Namespace) -> None:
    env_path = Path(args.env).expanduser()
    if args.v0_command == "status":
        for line in v0_status_lines(env_path):
            print(line)
        return
    if args.v0_command == "set-search":
        status = set_key(env_path, V0_SEARCH_MODEL_KEY, args.model)
        print(f"OK: {status} {V0_SEARCH_MODEL_KEY} in {args.env} (value hidden)")
        return
    if args.v0_command == "set-answer":
        status = set_key(env_path, V0_ANSWER_MODEL_KEY, args.model)
        print(f"OK: {status} {V0_ANSWER_MODEL_KEY} in {args.env} (value hidden)")
        return
    raise SystemExit("ERROR: unknown v0-models command")


def main() -> None:
    parser = argparse.ArgumentParser(description="Add/update nmbot Jivo/API dotenv values without printing secrets")
    subparsers = parser.add_subparsers(dest="command")
    v0_parser = subparsers.add_parser("v0-models", help="Safely inspect or set V0 model keys")
    v0_parser.add_argument("--env", default=".env", help="dotenv file path")
    v0_subparsers = v0_parser.add_subparsers(dest="v0_command", required=True)
    v0_subparsers.add_parser("status", help="Show V0 model key presence and effective sources")
    set_search = v0_subparsers.add_parser("set-search", help="Set only NMBOT_V0_SEARCH_MODEL")
    set_search.add_argument("model", help="public model id; value will not be printed")
    set_answer = v0_subparsers.add_parser("set-answer", help="Set only NMBOT_V0_ANSWER_MODEL")
    set_answer.add_argument("model", help="public model id; value will not be printed")
    parser.add_argument("--env", default=".env", help="dotenv file path")
    parser.add_argument("--key", help="dotenv key to set")
    parser.add_argument("--value", help="secret/config value; will not be printed")
    args = parser.parse_args()

    if args.command == "v0-models":
        _run_v0_models_command(args)
        return

    if args.key is None or args.value is None:
        raise SystemExit("ERROR: --key and --value are required unless using a subcommand")

    key = args.key.strip()
    if not key:
        raise SystemExit("ERROR: empty key")
    if key not in KNOWN_KEYS:
        allowed = ", ".join(sorted(KNOWN_KEYS))
        raise SystemExit(f"ERROR: unknown key. Use one of: {allowed}")
    status = set_key(Path(args.env).expanduser(), key, args.value)
    print(f"OK: {status} {key} in {args.env} (value hidden)")


if __name__ == "__main__":
    main()
