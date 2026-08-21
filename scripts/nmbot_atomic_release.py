#!/usr/bin/env python3
"""Build, preflight and guarded atomic API-release artifacts for NMBot.

Local commands are safe by default.  Remote deploy is still expressed through the
``Remote`` protocol so tests can prove command order without SSH/SCP/systemctl.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import gzip
import hashlib
import io
import json
import os
import py_compile
import re
import shutil
import shlex
import subprocess
import stat
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "release_bundles" / "atomic_full"
DEFAULT_BOOTSTRAP_OUT_DIR = ROOT / "release_bundles" / "bootstrap"
SCHEMA_VERSION = "nmbot.atomic_release.v1"
SNAPSHOT_SCHEMA_VERSION = "nmbot.vps_source_snapshot.v1"
WORKTREE_PROVENANCE_SCHEMA = "nmbot.snapshot_worktree_provenance.v1"
DEFAULT_REMOTE_ROOT = "/home/neiro/novostroy-bot"
CLIENT_PRODUCTION_REMOTE_ROOT = "/home/neiro/novostroy-bot-client-production"
LIVE_API_HELPER_OVERLAY_FILE = "scripts/nmbot_env_secrets.py"
LIVE_API_HELPER_OVERLAY_DESTINATION = f"{DEFAULT_REMOTE_ROOT}/{LIVE_API_HELPER_OVERLAY_FILE}"
LIVE_API_HELPER_OVERLAY_LOCK = f"{DEFAULT_REMOTE_ROOT}/.live_api_helper_overlay_lock"
LIVE_API_HELPER_OVERLAY_STAGING = f"{DEFAULT_REMOTE_ROOT}/.live_api_helper_overlay_staging"
LIVE_API_HELPER_SNAPSHOT_OUT_ROOT = Path("/tmp/opencode/nmbot-live-api-helper-overlay")
DEFAULT_SNAPSHOT_CONTOUR = "test"
SNAPSHOT_CONTOURS = ("test", "client-production")
AUTHORIZED_DEPLOY_HOST = "neiro@193.107.155.236"
AUTHORIZED_DEPLOY_PORT = "1905"
API_SERVICE = "novostroy-bot-api.service"
BRIDGE_SERVICE = "novostroy-bot-n8n-bridge.service"
WORKER_SERVICE = "novostroy-bot-worker.service"
CALLBACK_WORKER_SERVICE = "nmbot-callback-sheet-worker.service"
CALLBACK_WORKER_UNIT = "/home/neiro/.config/systemd/user/nmbot-callback-sheet-worker.service"
API_HEALTH_URL = "http://127.0.0.1:8088/health"
BRIDGE_HEALTH_URL = "http://127.0.0.1:8093/health"
BRIDGE_SCHEMA_VERSION = "nmbot.bridge_release.v1"
BRIDGE_SNAPSHOT_SCHEMA_VERSION = "nmbot.bridge_source_snapshot.v1"
BRIDGE_WORKTREE_PROVENANCE_SCHEMA = "nmbot.bridge_snapshot_worktree_provenance.v1"
BRIDGE_CURRENT = "bridge-current"
BRIDGE_RELEASES = "bridge-releases"
BRIDGE_UNIT_PATH = "/home/neiro/.config/systemd/user/novostroy-bot-n8n-bridge.service"
BRIDGE_ENTRYPOINT = "scripts/nmbot_n8n_bridge_server.py"
BRIDGE_INLINE_ENVIRONMENT = "PYTHONUNBUFFERED=1"
BRIDGE_ALLOWED_FILES = (
    "scripts/nmbot_n8n_bridge_server.py",
    "scripts/dialogue_journal.py",
    "scripts/nmbot_egress_policy.py",
)
BRIDGE_IMPORT_MODULES = ("scripts.nmbot_n8n_bridge_server",)
BRIDGE_SOURCE_SCOPES = ("bridge_canonical", "api_current", "bridge_current")
IDENTITY_IN_RELEASE = "release_identity/nmbot_release_identity.json"
IDENTITY_EXTERNAL = "data/nmbot_release_identity.json"
RUNTIME_VERSION_EXTERNAL = "data/nmbot_runtime_version.json"
ENTRYPOINTS = ("scripts/nmbot_api_server.py",)
NMBOT_V1_IMPORT_MODULE_CANDIDATES = (
    "nmbot_v1.runtime",
    "nmbot_v1.state",
    "nmbot_v1.contracts",
    "nmbot_v1.ports",
    "nmbot_v1.search_contract",
    "nmbot_v1.search",
    "nmbot_v1.response",
    "nmbot_v1.execution_path",
    "nmbot_v1.provider_adapters",
    "nmbot_v1.prompt_provenance",
)
NMBOT_V1_IMPORT_MODULES = tuple(
    module for module in NMBOT_V1_IMPORT_MODULE_CANDIDATES
    if (ROOT / (module.replace(".", "/") + ".py")).is_file()
)
IMPORT_MODULES = (
    "scripts.nmbot_runtime_adapter",
    "scripts.nmbot_api_server",
    "scripts.nmbot_release_identity",
    *NMBOT_V1_IMPORT_MODULES,
    "nmbot_v2.runtime",
    "nmbot_v2.response_composer",
    "nmbot_v2.manager_rewriter",
)
V6_ONLY_PROFILE = "v6-only"
V6_CALLBACK_WORKER_PROFILE = "v6-callback-worker"
V6_ONLY_IMPORT_MODULES = ("scripts.nmbot_api_server",)
V6_ONLY_REQUIRED_DEPENDENCIES = ("aiohttp", "phonenumbers")
V6_CALLBACK_WORKER_IMPORT_MODULES = ("scripts.nmbot_api_server", "nmbot_callback_sheet_worker")
V6_CALLBACK_WORKER_REQUIRED_DEPENDENCIES = ("aiohttp", "phonenumbers", "google.oauth2", "googleapiclient.discovery")
CONFIG_REQUIREMENTS = {
    "required_secret_names": sorted(("JIVO_PROVIDER_ID", "JIVO_PROVIDER_TOKEN", "NMBOT_API_TOKEN")),
    "required_setting_names": sorted((
        "NMBOT_CONTOUR_PROFILE",
        "NMBOT_API_HOST",
        "NMBOT_API_PORT",
        "NMBOT_API_STATE_FILE",
        "NMBOT_CALLBACK_OUTBOX_DIR",
        "NMBOT_RELEASE_IDENTITY_FILE",
        "NMBOT_RUNTIME_VERSION_FILE",
    )),
    "required_mode_names": sorted(("NMBOT_V2_MANAGER_REWRITER_MODE", "NMBOT_V3_MANAGER_REWRITER_MODE")),
    "external_runtime_paths": sorted((".env", "data", "logs", "backups")),
}
V6_ONLY_CONFIG_REQUIREMENTS = {
    **CONFIG_REQUIREMENTS,
    "required_mode_names": [],
}
V6_CALLBACK_WORKER_CONFIG_REQUIREMENTS = {
    **V6_ONLY_CONFIG_REQUIREMENTS,
    "required_setting_names": sorted(set(V6_ONLY_CONFIG_REQUIREMENTS["required_setting_names"]) | {
        "NMBOT_CALLBACK_SHEET_ID", "NMBOT_CALLBACK_SHEET_TAB", "NMBOT_CALLBACK_LEDGER_DIR"
    }),
}
CANONICAL_API_ENV_VALUES = {
    "NMBOT_API_HOST": "127.0.0.1",
    "NMBOT_API_PORT": "8088",
}
SYSTEMD_ENV_OVERRIDE_DENY = frozenset({
    "NMBOT_RELEASE_IDENTITY_FILE",
    "NMBOT_API_STATE_FILE",
    "NMBOT_CALLBACK_OUTBOX_DIR",
    "NMBOT_RUNTIME_VERSION_FILE",
    "NMBOT_API_HOST",
    "NMBOT_API_PORT",
    "NMBOT_CONTOUR_PROFILE",
    "PYTHONPATH",
})
APPROVED_EXECSTART_INTERPRETERS = frozenset({"/usr/bin/python3"})
MANIFEST_ALLOWED_KEYS: dict[str, type] = {
    "schema_version": str,
    "scope": str,
    "release_id": str,
    "created_at_utc": str,
    "archive_name": str,
    "archive_sha256": str,
    "files": list,
    "entrypoints": list,
    "import_modules": list,
    "service": str,
    "forbidden_services": list,
    "config_schema_requirements": dict,
    "external_runtime_strategy": str,
    "identity_path": str,
    "source_provenance": dict,
}
MODULE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
HEX_RE = re.compile(r"[0-9a-f]{64}")
SAFE_GENERATED_AT_RE = re.compile(r"[A-Za-z0-9:._+\-TZ]{1,80}")
ARCHIVE_RE = re.compile(r"nmbot-[A-Za-z0-9][A-Za-z0-9._-]{2,79}\.tar\.gz")
BRIDGE_ARCHIVE_RE = re.compile(r"nmbot-bridge-[A-Za-z0-9][A-Za-z0-9._-]{2,79}\.tar\.gz")
MAX_FILES = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
SECRET_NAME_RE = re.compile(r"(?i)(secret|password|passwd|credentials?|database|sqlite|private[_-]?key|api[_-]?key|bearer|\.pem$|\.key$|\.db$|\.sqlite$|\.sqlite3$|id_rsa)")
DATABASE_NAME_RE = re.compile(r"(?i)(database|sqlite|\.db$|\.sqlite$|\.sqlite3$)")
SECRET_CONTENT_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}|^\s*(?:export\s+)?['\"]?[A-Za-z0-9_-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|API[_-]?KEY)[A-Za-z0-9_-]*['\"]?\s*(?::|=)\s*['\"]?[A-Za-z0-9_./+~:\-]{8,}",
    re.MULTILINE | re.IGNORECASE,
)
PY_SECRET_CONTENT_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}", re.MULTILINE)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.MULTILINE)
BEARER_LITERAL_RE = re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}")
SECRET_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?['\"]?(?P<name>[A-Za-z0-9_-]*(?:TOKEN(?!S)|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|API[_-]?KEY)[A-Za-z0-9_-]*)['\"]?\s*(?::|=)\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
BENIGN_SECRET_VALUE_RE = re.compile(
    r"^(?:None|True|False|[A-Z][A-Z0-9_]*|os\.getenv\(|os\.environ\.|environ\.get\(|getenv\(|settings\.|config\.|self\.|args\.|kwargs\.|[A-Za-z_][A-Za-z0-9_]*\(|[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?)"
)
SECRET_ERROR_RE = re.compile(r"(?i)\b[A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY|API_KEY)[A-Za-z0-9_]*\b(?:\s*=\s*\S+)?")
SECRET_ASSIGNMENT_LINE_RE = re.compile(
    r"(?im)^.*\b[A-Za-z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD)[A-Za-z0-9_]*\b\s*(?::|=).*$(?:\n)?"
)
SAFE_DIAGNOSTIC_LINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,:;@%+=/()\[\]{}!?\-]{0,300}$")
RUNTIME_DIRS = {"nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "nmbot_v6", "scripts", "prompts", "schemas"}
DEPLOY_RUNTIME_DIRS = {"deploy"}
ROOT_RUNTIME_FILES = {
    "followup_intent_classifier.py",
    "geo_intent_classifier.py",
    "llm_client.py",
    "property_cards.py",
    "quick_reply_intent.py",
    "search_profiles.py",
    "requirements.txt",
}
RUNTIME_SUFFIXES = (".py", ".txt", ".json", ".yaml", ".yml")
# ``candidates`` is excluded as a directory because it normally contains
# experiments.  This prompt is different: V1 reads it synchronously while
# constructing the API application, so a full API artifact cannot start
# without it.  Keep this list deliberately exact rather than admitting the
# candidate directory wholesale.
REQUIRED_RUNTIME_RESOURCE_PATHS = frozenset({
    "prompts/candidates/v1_one_model_gpt55_experiment_v1.txt",
})
API_RUNTIME_SCRIPT_FILES = frozenset({
    "scripts/bluesminds_answer_interceptor.py",
    "scripts/bluesminds_manager_rewriter.py",
    "scripts/bluesminds_v0_answer_writer.py",
    "scripts/dialogue_journal.py",
    "scripts/gateway_v0_answer_writer.py",
    "scripts/nmbot_api_server.py",
    "scripts/nmbot_card_reformatter.py",
    "scripts/nmbot_crm_outbox.py",
    "scripts/nmbot_diag.sh",
    "scripts/nmbot_egress_policy.py",
    "scripts/nmbot_gateway_client.py",
    "scripts/nmbot_planner_context.py",
    "scripts/nmbot_prompt_ledger.py",
    "scripts/nmbot_release_identity.py",
    "scripts/nmbot_runtime_adapter.py",
    "scripts/nmbot_v6_jivo_smoke.py",
    "scripts/nmbot_v6_simple_adapter.py",
    "scripts/nmbot_v6_journal.py",
    "scripts/planner_trace.py",
})
V6_ONLY_RUNTIME_FILES = frozenset({
    "nmbot_v6/__init__.py",
    "nmbot_v6/phone.py",
    "nmbot_v6/simple_contract.py",
    "nmbot_v6/simple_gateway.py",
    "nmbot_v6/simple_runtime.py",
    "nmbot_v6/simple_state.py",
    "nmbot_v6/url_card.py",
    "prompts/v6_simple_answer_writer.txt",
    "prompts/v6_simple_search_agent.txt",
    "requirements.txt",
    "scripts/dialogue_journal.py",
    "scripts/nmbot_api_server.py",
    "scripts/nmbot_crm_outbox.py",
    "scripts/nmbot_egress_policy.py",
    "scripts/nmbot_gateway_client.py",
    "scripts/nmbot_prompt_ledger.py",
    "scripts/nmbot_release_identity.py",
    "scripts/nmbot_diag.sh",
    "scripts/nmbot_v6_jivo_smoke.py",
    "scripts/nmbot_v6_simple_adapter.py",
})
V6_CALLBACK_WORKER_RUNTIME_FILES = frozenset((set(V6_ONLY_RUNTIME_FILES) - {"scripts/nmbot_diag.sh"}) | {
    "scripts/nmbot_v6_journal.py",
    "scripts/nmbot_callback_sheet_worker.py",
    "scripts/nmbot_callback_summary.py",
    "scripts/nmbot_google_sheets.py",
    "scripts/nmbot_callback_crm.py",
    "scripts/nmbot_callback_crm_control.py",
})
V6_ONLY_PREFLIGHT_PY_FILES = tuple(sorted(path for path in V6_ONLY_RUNTIME_FILES if path.endswith(".py")))
V6_CALLBACK_WORKER_PREFLIGHT_PY_FILES = tuple(sorted(path for path in V6_CALLBACK_WORKER_RUNTIME_FILES if path.endswith(".py")))
NMBOT_DIALOGUE_EXPORTER_SCRIPT = "scripts/nmbot_dialogue_sheet_exporter.py"
NMBOT_DIALOGUE_EXPORTER_SERVICE_TEMPLATE = "deploy/systemd/nmbot-dialogue-sheet-export.service"
NMBOT_DIALOGUE_EXPORTER_TIMER_TEMPLATE = "deploy/systemd/nmbot-dialogue-sheet-export.timer"
NMBOT_DIALOGUE_EXPORTER_FILES = frozenset({
    NMBOT_DIALOGUE_EXPORTER_SCRIPT,
    NMBOT_DIALOGUE_EXPORTER_SERVICE_TEMPLATE,
    NMBOT_DIALOGUE_EXPORTER_TIMER_TEMPLATE,
})
NMBOT_DIALOGUE_EXPORTER_DEPENDENCY_FILES = frozenset({
    "scripts/bluesminds_client.py",
    "scripts/nmbot_google_sheets.py",
})
NMBOT_DIALOGUE_EXPORTER_NAME_ONLY_SECRET_REFERENCE_FILES = frozenset()
NMBOT_DIALOGUE_EXPORTER_REMOTE_SCRIPT = f"{DEFAULT_REMOTE_ROOT}/{NMBOT_DIALOGUE_EXPORTER_SCRIPT}"
NMBOT_DIALOGUE_EXPORTER_REMOTE_SERVICE = "/home/neiro/.config/systemd/user/nmbot-dialogue-sheet-export.service"
NMBOT_DIALOGUE_EXPORTER_REMOTE_TIMER = "/home/neiro/.config/systemd/user/nmbot-dialogue-sheet-export.timer"
NMBOT_DIALOGUE_EXPORTER_TIMER_UNIT = "nmbot-dialogue-sheet-export.timer"
OPTIONAL_API_RUNTIME_SCRIPT_FILES = frozenset({
    "scripts/bluesminds_v0_answer_writer.py",
    "scripts/gateway_v0_answer_writer.py",
    "scripts/nmbot_v6_journal.py",
    "scripts/nmbot_v6_simple_adapter.py",
})
REMOTE_PREFLIGHT_PY_FILES = tuple(sorted((API_RUNTIME_SCRIPT_FILES - OPTIONAL_API_RUNTIME_SCRIPT_FILES) | {
    "followup_intent_classifier.py",
    "search_profiles.py",
    "nmbot_v2/runtime.py",
    "nmbot_v2/response_composer.py",
    "nmbot_v2/manager_rewriter.py",
}))
EXCLUDED_DIR_NAMES = {
    ".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".opencode", ".github",
    "node_modules", "__pycache__", "logs", "backups", "data", "results", "reports", "eval", "release_bundles", "tests", "candidates",
}
EXCLUDED_FILE_NAMES = {".env", ".envrc"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".log", ".jsonl", ".bak", ".tmp", ".swp")
EXCLUDED_SCRIPT_RE = re.compile(r"(^|/)(?:deploy|rollback|release|nmbot_atomic_release|nmbot_release)(?:[_-].*)?\.py$")
API_ONLY_SCRIPT_DENY = {
    "scripts/nmbot_env_secrets.py",
    "scripts/nmbot_n8n_bridge_server.py",
    "scripts/nmbot_callback_sheet_worker.py",
}
TEST_API_OVERLAY_SCRIPT_FILES = frozenset({
    "scripts/nmbot_env_secrets.py",
})
FIXED_DATA_ENV_PATHS = {
    "NMBOT_RELEASE_IDENTITY_FILE": IDENTITY_EXTERNAL,
    "NMBOT_API_STATE_FILE": "data/nmbot_api_state.json",
    "NMBOT_CALLBACK_OUTBOX_DIR": "data/private/callback-outbox",
    "NMBOT_RUNTIME_VERSION_FILE": "data/nmbot_runtime_version.json",
}
SNAPSHOT_ROOTS = ("nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v4", "nmbot_v6", "scripts", "prompts", "schemas")
SNAPSHOT_ROOT_FILES = tuple(sorted(ROOT_RUNTIME_FILES))
SNAPSHOT_MANIFEST_NAME = "snapshot-manifest.json"
SNAPSHOT_SOURCE_PREFIX = "source/"


class ReleaseError(RuntimeError):
    pass


class Remote(Protocol):
    def run(self, command: str, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]: ...
    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]: ...


class BinaryRemote(Protocol):
    def run_binary(self, command: str) -> subprocess.CompletedProcess[bytes]: ...


@dataclass(frozen=True)
class Artifact:
    archive: Path
    manifest: Path
    manifest_data: dict[str, Any]


class SshRemote:
    def __init__(self, *, host: str, port: str = "1905") -> None:
        if host != AUTHORIZED_DEPLOY_HOST or port != AUTHORIZED_DEPLOY_PORT:
            raise ReleaseError("deploy host/port is not authorized for this release helper")
        self.host = host
        self.port = port

    def run(self, command: str, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["ssh", "-p", self.port, "-o", "BatchMode=yes", self.host, command], input=input_text, text=True, capture_output=True, check=False)

    def upload(self, local: Path, remote_path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["scp", "-P", self.port, str(local), f"{self.host}:{remote_path}"], text=True, capture_output=True, check=False)

    def run_binary(self, command: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["ssh", "-p", self.port, "-o", "BatchMode=yes", self.host, command], capture_output=True, check=False)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _exact_type(value: Any, typ: type) -> bool:
    return type(value) is typ


def _tree_hash_from_records(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [{"path": item["path"], "sha256": item["sha256"], "size": item.get("size"), "mode": item.get("mode")} for item in records],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _source_provenance_absent() -> dict[str, Any]:
    return {"present": False}


def _validate_size_limits(rows: list[dict[str, Any]], *, label: str) -> None:
    if len(rows) > MAX_FILES:
        raise ReleaseError(f"{label} file count exceeds limit")
    total = 0
    for row in rows:
        size = row.get("size")
        if not _exact_type(size, int) or size < 0 or size > MAX_FILE_BYTES:
            raise ReleaseError(f"{label} file size exceeds limit")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ReleaseError(f"{label} total size exceeds limit")


def _release_id(value: str | None = None) -> str:
    raw = value or time.strftime("%Y%m%d-%H%M%S")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", raw):
        raise ReleaseError("unsafe release_id")
    return raw


def _safe_rel(rel: str) -> str:
    p = PurePosixPath(str(rel).replace(os.sep, "/"))
    if not str(p) or p.is_absolute() or ".." in p.parts:
        raise ReleaseError(f"unsafe relative path: {rel!r}")
    return str(p)


def _manifest_path(rel: str) -> str:
    safe = _safe_rel(rel)
    if not re.fullmatch(r"[A-Za-z0-9._/@+-][A-Za-z0-9._/@+\-/]{0,240}", safe):
        raise ReleaseError(f"unsafe manifest path: {rel!r}")
    return safe


def _is_excluded(rel: str, path: Path) -> bool:
    if rel in REQUIRED_RUNTIME_RESOURCE_PATHS:
        return False
    parts = PurePosixPath(rel).parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDED_FILE_NAMES or name.startswith(".env"):
        return True
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    if path.is_dir() and name in EXCLUDED_DIR_NAMES:
        return True
    return False


def _is_allowed_runtime_file(rel: str, *, include_dialogue_exporter: bool = False) -> bool:
    parts = PurePosixPath(rel).parts
    if not parts:
        return False
    if rel in REQUIRED_RUNTIME_RESOURCE_PATHS:
        return True
    if include_dialogue_exporter and rel in (NMBOT_DIALOGUE_EXPORTER_FILES | NMBOT_DIALOGUE_EXPORTER_DEPENDENCY_FILES):
        return True
    if rel in ROOT_RUNTIME_FILES:
        return True
    if parts[0] not in RUNTIME_DIRS:
        return False
    if any(part.startswith(".") for part in parts):
        return False
    if rel in API_ONLY_SCRIPT_DENY:
        return False
    if parts[0] == "scripts":
        return rel in API_RUNTIME_SCRIPT_FILES
    return rel.endswith(RUNTIME_SUFFIXES)


def _assert_required_runtime_resources_present(paths: set[str], *, root: Path | None = None) -> None:
    """Require every resource read during API construction in a full artifact."""

    missing = sorted(REQUIRED_RUNTIME_RESOURCE_PATHS - paths)
    if root is not None:
        missing.extend(
            rel for rel in REQUIRED_RUNTIME_RESOURCE_PATHS
            if rel in paths and (not (root / rel).is_file() or (root / rel).is_symlink())
        )
    if missing:
        raise ReleaseError("required runtime resource missing from release: " + ",".join(sorted(set(missing))))


def _is_allowed_test_api_overlay_file(rel: str) -> bool:
    return rel in TEST_API_OVERLAY_SCRIPT_FILES


def _is_allowed_live_api_helper_overlay_file(rel: str) -> bool:
    return rel == LIVE_API_HELPER_OVERLAY_FILE


def _validate_live_api_helper_overlay_paths(paths: list[str]) -> list[str]:
    if paths != [LIVE_API_HELPER_OVERLAY_FILE]:
        raise ReleaseError("live-api-helper-overlay permits exactly one fixed helper path")
    return paths


def _is_allowed_runtime_file_for_policy(rel: str, *, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> bool:
    if rel in test_api_overlay_paths and _is_allowed_test_api_overlay_file(rel):
        return True
    if profile == V6_CALLBACK_WORKER_PROFILE and rel in V6_CALLBACK_WORKER_RUNTIME_FILES:
        return True
    return _is_allowed_runtime_file(rel, include_dialogue_exporter=include_dialogue_exporter)


def _is_name_allowed_for_test_api_overlay(rel: str) -> bool:
    return _is_allowed_test_api_overlay_file(rel)


def _manifest_allows_test_api_overlay(manifest: dict[str, Any], rel: str) -> bool:
    if not _is_allowed_test_api_overlay_file(rel):
        return False
    provenance = manifest.get("source_provenance")
    return isinstance(provenance, dict) and provenance.get("present") is True and provenance.get("contour") == DEFAULT_SNAPSHOT_CONTOUR and provenance.get("remote_root") == DEFAULT_REMOTE_ROOT


def _manifest_test_api_overlay_paths(manifest: dict[str, Any]) -> frozenset[str]:
    return frozenset(item["path"] for item in manifest["files"] if _manifest_allows_test_api_overlay(manifest, item["path"]))


def _manifest_has_dialogue_exporter(manifest: dict[str, Any]) -> bool:
    paths = {str(item.get("path", "")) for item in manifest.get("files", []) if isinstance(item, dict)}
    present = paths & NMBOT_DIALOGUE_EXPORTER_FILES
    if present and present != NMBOT_DIALOGUE_EXPORTER_FILES:
        raise ReleaseError("dialogue exporter release files must be the exact allowlisted script+service+timer set")
    return present == NMBOT_DIALOGUE_EXPORTER_FILES


def _reject_secret_like(path: Path, rel: str) -> None:
    if SECRET_NAME_RE.search(PurePosixPath(rel).name) and not _is_name_allowed_for_test_api_overlay(rel):
        raise ReleaseError(f"secret-like filename rejected: {rel}")
    if rel.endswith((".py", ".txt", ".json", ".yaml", ".yml", ".cfg", ".ini")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if rel in NMBOT_DIALOGUE_EXPORTER_NAME_ONLY_SECRET_REFERENCE_FILES:
            if PRIVATE_KEY_RE.search(text) or BEARER_LITERAL_RE.search(text):
                raise ReleaseError(f"secret-like content rejected: {rel}")
            return
        if _has_secret_like_content(text, python_source=rel.endswith(".py")):
            raise ReleaseError(f"secret-like content rejected: {rel}")


def _has_secret_like_content(text: str, *, python_source: bool = False) -> bool:
    if PRIVATE_KEY_RE.search(text) or BEARER_LITERAL_RE.search(text):
        return True
    if python_source:
        return _has_secret_assignment_literal(text)
    return SECRET_CONTENT_RE.search(text) is not None


def _has_secret_assignment_literal(text: str) -> bool:
    for line in text.splitlines():
        match = SECRET_ASSIGNMENT_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        name = match.group("name").upper().replace("-", "_")
        # Lowercase locals such as ``token = os.getenv(...)`` are runtime
        # plumbing, not embedded secret assignments. Secret/config names are
        # conventionally uppercase; keep the detector strict for those.
        raw_name = match.group("name")
        if raw_name != raw_name.upper():
            continue
        strong_name = any(marker in name for marker in ("SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY", "API_KEY", "API_TOKEN"))
        if value.startswith(("'", '"')) and len(value) >= 2:
            quote = value[0]
            end = value.find(quote, 1)
            literal = value[1:end] if end >= 1 else value[1:]
            if literal and (strong_name or _looks_like_credential_literal(literal)):
                return True
            continue
        if BENIGN_SECRET_VALUE_RE.match(value):
            continue
        if re.search(r"[A-Za-z0-9_./+~:-]{8,}", value):
            return True
    return False


def _looks_like_credential_literal(value: str) -> bool:
    if len(value) >= 24:
        return True
    if re.match(r"(?i)^(?:sk|pk|tok|key|secret|bearer)[_-]", value):
        return True
    return bool(re.search(r"[A-Za-z]", value) and re.search(r"\d", value) and len(value) >= 16)


def iter_snapshot_files(root: Path = ROOT, *, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset()) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        parts = PurePosixPath(rel).parts
        if _is_excluded(rel, path):
            continue
        if path.is_symlink() or (path.exists() and not path.is_dir() and not path.is_file()):
            raise ReleaseError(f"non-regular file rejected: {rel}")
        if path.is_file():
            if parts and (parts[0] in RUNTIME_DIRS or rel in ROOT_RUNTIME_FILES) and DATABASE_NAME_RE.search(PurePosixPath(rel).name):
                raise ReleaseError(f"secret-like filename rejected: {rel}")
            if not _is_allowed_runtime_file_for_policy(rel, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths):
                continue
            _reject_secret_like(path, rel)
            files.append(path)
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def _v6_only_runtime_files(root: Path) -> list[Path]:
    files: list[Path] = []
    missing: list[str] = []
    for rel in sorted(V6_ONLY_RUNTIME_FILES):
        path = root / rel
        if not path.is_file() or path.is_symlink():
            missing.append(rel)
            continue
        _reject_secret_like(path, rel)
        files.append(path)
    if missing:
        raise ReleaseError("V6-only runtime file missing: " + ",".join(missing))
    return files


def _profile_runtime_files(root: Path, allowlist: frozenset[str], label: str) -> list[Path]:
    files: list[Path] = []
    missing: list[str] = []
    for rel in sorted(allowlist):
        path = root / rel
        if not path.is_file() or path.is_symlink():
            missing.append(rel)
            continue
        _reject_secret_like(path, rel)
        files.append(path)
    if missing:
        raise ReleaseError(f"{label} runtime file missing: " + ",".join(missing))
    return files


def _runtime_files_for_profile(
    root: Path,
    *,
    profile: str | None,
    include_dialogue_exporter: bool = False,
    test_api_overlay_paths: set[str] | frozenset[str] = frozenset(),
) -> list[Path]:
    if profile == V6_ONLY_PROFILE:
        if include_dialogue_exporter or test_api_overlay_paths:
            raise ReleaseError("V6-only profile does not permit release overlays")
        return _v6_only_runtime_files(root)
    if profile == V6_CALLBACK_WORKER_PROFILE:
        if include_dialogue_exporter or test_api_overlay_paths:
            raise ReleaseError("V6 callback-worker profile does not permit release overlays")
        return _profile_runtime_files(root, V6_CALLBACK_WORKER_RUNTIME_FILES, "V6 callback-worker")
    if profile is not None:
        raise ReleaseError(f"unknown release profile: {profile}")
    return iter_snapshot_files(root, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths)


def _file_records(files: list[Path], root: Path = ROOT) -> list[dict[str, str]]:
    return [{"path": path.relative_to(root).as_posix(), "sha256": _sha256_file(path)} for path in files]


def _remote_preflight_py_files(paths: set[str] | None = None) -> tuple[str, ...]:
    v1_files: set[str] = set()
    if paths is not None:
        v1_files = {rel for rel in paths if rel.startswith("nmbot_v1/") and rel.endswith(".py")}
    return tuple(sorted(set(REMOTE_PREFLIGHT_PY_FILES) | v1_files))


def _assert_remote_preflight_sources_present(root: Path, paths: set[str] | None = None) -> None:
    available = paths if paths is not None else {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    missing = [rel for rel in _remote_preflight_py_files(available) if rel not in available or not (root / rel).is_file() or (root / rel).is_symlink()]
    if missing:
        raise ReleaseError("remote preflight source missing from artifact: " + ",".join(missing))


def _deterministic_generated_at() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError) as exc:
            raise ReleaseError("SOURCE_DATE_EPOCH must be an integer unix timestamp") from exc
    return "deterministic-build-clock-not-recorded"


def _identity_payload(release_id: str, file_records: list[dict[str, str]]) -> str:
    tracked = [item for item in file_records if item["path"] != IDENTITY_IN_RELEASE]
    payload = {
        "schema": "nmbot.release_identity.v1",
        "release_id": release_id,
        "generated_at": _deterministic_generated_at(),
        "tracked_files": sorted(tracked, key=lambda item: item["path"]),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _identity_record(release_id: str, base_records: list[dict[str, str]]) -> dict[str, str]:
    payload = _identity_payload(release_id, base_records).encode("utf-8")
    return {"path": IDENTITY_IN_RELEASE, "sha256": _sha256_bytes(payload)}


def _file_records_with_metadata(files: list[Path], root: Path = ROOT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        st = path.stat()
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "size": st.st_size,
            "mode": 0o755 if path.relative_to(root).as_posix().startswith("scripts/") and path.suffix == ".py" else 0o644,
        })
    return rows


def build(*, release_id: str | None = None, out_dir: Path = DEFAULT_OUT_DIR, root: Path = ROOT, source_provenance: dict[str, Any] | None = None, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> Artifact:
    rid = _release_id(release_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = _runtime_files_for_profile(root, profile=profile, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths)
    selected_paths = {p.relative_to(root).as_posix() for p in files}
    if profile not in {V6_ONLY_PROFILE, V6_CALLBACK_WORKER_PROFILE}:
        _assert_required_runtime_resources_present(selected_paths, root=root)
    if include_dialogue_exporter and not NMBOT_DIALOGUE_EXPORTER_FILES.issubset(selected_paths):
        missing = sorted(NMBOT_DIALOGUE_EXPORTER_FILES - selected_paths)
        raise ReleaseError("dialogue exporter allowlist files missing from release source: " + ",".join(missing))
    if not include_dialogue_exporter and selected_paths & NMBOT_DIALOGUE_EXPORTER_FILES:
        raise ReleaseError("dialogue exporter files require explicit opt-in")
    if profile not in {V6_ONLY_PROFILE, V6_CALLBACK_WORKER_PROFILE}:
        _assert_remote_preflight_sources_present(root, selected_paths)
    metadata_records = _file_records_with_metadata(files, root)
    _validate_size_limits(metadata_records, label="release")
    base_records = [{"path": item["path"], "sha256": item["sha256"]} for item in metadata_records]
    identity_record = _identity_record(rid, base_records)
    file_records = sorted([*base_records, identity_record], key=lambda item: item["path"])
    archive = out_dir / f"nmbot-{rid}.tar.gz"
    manifest_path = out_dir / f"nmbot-{rid}.manifest.json"
    if os.path.lexists(archive) or os.path.lexists(manifest_path):
        raise ReleaseError("refusing to overwrite existing immutable artifact")
    with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for path in files:
            rel = path.relative_to(root).as_posix()
            info = tf.gettarinfo(str(path), arcname=rel)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if rel.startswith("scripts/") and rel.endswith(".py") else 0o644
            with path.open("rb") as fh:
                tf.addfile(info, fh)
        payload = _identity_payload(rid, base_records).encode("utf-8")
        info = tarfile.TarInfo(IDENTITY_IN_RELEASE)
        info.size = len(payload)
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = 0
        info.mode = 0o600
        tf.addfile(info, io.BytesIO(payload))
    archive_sha = _sha256_file(archive)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope": "api",
        "release_id": rid,
        "created_at_utc": "deterministic-build-clock-not-recorded",
        "archive_name": archive.name,
        "archive_sha256": archive_sha,
        "files": file_records,
        "entrypoints": list(ENTRYPOINTS),
        "import_modules": list(V6_ONLY_IMPORT_MODULES if profile == V6_ONLY_PROFILE else V6_CALLBACK_WORKER_IMPORT_MODULES if profile == V6_CALLBACK_WORKER_PROFILE else IMPORT_MODULES),
        "service": API_SERVICE,
        "forbidden_services": [BRIDGE_SERVICE] if profile == V6_CALLBACK_WORKER_PROFILE else [BRIDGE_SERVICE, WORKER_SERVICE],
        "config_schema_requirements": V6_ONLY_CONFIG_REQUIREMENTS if profile == V6_ONLY_PROFILE else V6_CALLBACK_WORKER_CONFIG_REQUIREMENTS if profile == V6_CALLBACK_WORKER_PROFILE else CONFIG_REQUIREMENTS,
        "external_runtime_strategy": "Keep .env, data, logs and backups outside immutable TEST API releases; deploy links manifest external_runtime_paths into each release before switching current.",
        "identity_path": IDENTITY_IN_RELEASE,
        "source_provenance": source_provenance or _source_provenance_absent(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Artifact(archive=archive, manifest=manifest_path, manifest_data=manifest)


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ReleaseError("manifest must be an object")
    keys = set(manifest)
    allowed = set(MANIFEST_ALLOWED_KEYS)
    if keys != allowed:
        raise ReleaseError(f"manifest keys mismatch extra={sorted(keys - allowed)} missing={sorted(allowed - keys)}")
    for key, typ in MANIFEST_ALLOWED_KEYS.items():
        if not _exact_type(manifest[key], typ):
            raise ReleaseError(f"manifest field has wrong type: {key}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ReleaseError("unsupported manifest schema")
    if manifest["scope"] != "api":
        raise ReleaseError("manifest scope must be api")
    rid = _release_id(manifest["release_id"])
    if not ARCHIVE_RE.fullmatch(manifest["archive_name"]) or manifest["archive_name"] != f"nmbot-{rid}.tar.gz":
        raise ReleaseError("invalid archive_name")
    if not HEX_RE.fullmatch(manifest["archive_sha256"]):
        raise ReleaseError("invalid archive sha256")
    if manifest["service"] != API_SERVICE:
        raise ReleaseError("manifest must restart only API service")
    if not all(isinstance(item, str) and item for item in manifest["forbidden_services"]):
        raise ReleaseError("invalid forbidden services")
    callback_worker = manifest["import_modules"] == list(V6_CALLBACK_WORKER_IMPORT_MODULES)
    if BRIDGE_SERVICE not in manifest["forbidden_services"] or (not callback_worker and WORKER_SERVICE not in manifest["forbidden_services"]):
        raise ReleaseError("manifest must forbid bridge/worker restart")
    if manifest["identity_path"] != IDENTITY_IN_RELEASE:
        raise ReleaseError("invalid release identity path")
    v6_only = manifest["import_modules"] == list(V6_ONLY_IMPORT_MODULES)
    seen: set[str] = set()
    if len(manifest["files"]) > MAX_FILES:
        raise ReleaseError("manifest file count exceeds limit")
    include_dialogue_exporter = _manifest_has_dialogue_exporter(manifest)
    previous_path = ""
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ReleaseError("invalid file row")
        rel = _manifest_path(str(item["path"]))
        if rel in seen:
            raise ReleaseError(f"duplicate file in manifest: {rel}")
        seen.add(rel)
        if rel != IDENTITY_IN_RELEASE and (_is_excluded(rel, Path(rel)) or (not _is_allowed_runtime_file_for_policy(rel, include_dialogue_exporter=include_dialogue_exporter, profile=V6_CALLBACK_WORKER_PROFILE if callback_worker else None) and not _manifest_allows_test_api_overlay(manifest, rel))):
            raise ReleaseError(f"excluded file in manifest: {rel}")
        if previous_path and rel <= previous_path:
            raise ReleaseError("manifest files must be sorted by path")
        previous_path = rel
        if not isinstance(item["sha256"], str) or not HEX_RE.fullmatch(item["sha256"]):
            raise ReleaseError(f"invalid file hash: {rel}")
    if v6_only:
        expected_v6_paths = set(V6_ONLY_RUNTIME_FILES) | {IDENTITY_IN_RELEASE}
        if seen != expected_v6_paths:
            raise ReleaseError("V6-only manifest file set must exactly match its allowlist")
    elif callback_worker:
        expected_paths = set(V6_CALLBACK_WORKER_RUNTIME_FILES) | {IDENTITY_IN_RELEASE}
        if seen != expected_paths:
            raise ReleaseError("V6 callback-worker manifest file set must exactly match its allowlist")
    else:
        _assert_required_runtime_resources_present(seen)
    req = manifest["config_schema_requirements"]
    expected_requirements = V6_ONLY_CONFIG_REQUIREMENTS if v6_only else V6_CALLBACK_WORKER_CONFIG_REQUIREMENTS if callback_worker else CONFIG_REQUIREMENTS
    if req != expected_requirements:
        raise ReleaseError("config requirements must exactly match release contract")
    encoded = json.dumps(req, ensure_ascii=False, sort_keys=True)
    if "=" in encoded:
        raise ReleaseError("config requirements must contain names only")
    if manifest["entrypoints"] != list(ENTRYPOINTS):
        raise ReleaseError("manifest entrypoints must be API-only")
    for rel in manifest["entrypoints"]:
        if _manifest_path(rel) not in seen:
            raise ReleaseError(f"entrypoint missing from manifest: {rel}")
    expected_imports = V6_ONLY_IMPORT_MODULES if v6_only else V6_CALLBACK_WORKER_IMPORT_MODULES if callback_worker else IMPORT_MODULES
    if manifest["import_modules"] != list(expected_imports):
        raise ReleaseError("manifest import_modules must exactly match release contract")
    if not all(isinstance(m, str) and MODULE_RE.fullmatch(m) for m in manifest["import_modules"]):
        raise ReleaseError("invalid import module in manifest")
    _validate_release_source_provenance(manifest["source_provenance"], require_present=False)


def _validate_release_source_provenance(provenance: Any, *, require_present: bool) -> dict[str, Any]:
    if not isinstance(provenance, dict):
        raise ReleaseError("release source provenance schema invalid")
    if provenance == {"present": False}:
        if require_present:
            raise ReleaseError("deploy requires artifact built from verified snapshot worktree provenance")
        return provenance
    allowed = {
        "present",
        "source_snapshot_id",
        "source_snapshot_manifest_sha256",
        "source_host",
        "remote_root",
        "contour",
        "worktree_source_tree_sha256",
        "worktree_source_manifest_sha256",
        "worktree_provenance_sha256",
    }
    if set(provenance) != allowed:
        raise ReleaseError("release source provenance keys mismatch")
    if provenance["present"] is not True:
        raise ReleaseError("release source provenance present must be exact bool")
    for key in allowed - {"present"}:
        if not _exact_type(provenance[key], str) or not provenance[key]:
            raise ReleaseError(f"release source provenance field invalid: {key}")
    _snapshot_id(provenance["source_snapshot_id"])
    for key in ("source_snapshot_manifest_sha256", "worktree_source_tree_sha256", "worktree_source_manifest_sha256", "worktree_provenance_sha256"):
        if not HEX_RE.fullmatch(provenance[key]):
            raise ReleaseError(f"release source provenance hash invalid: {key}")
    if provenance["source_host"] != AUTHORIZED_DEPLOY_HOST:
        raise ReleaseError("release source provenance source mismatch")
    _validate_snapshot_contour_root(provenance["contour"], provenance["remote_root"])
    return provenance


def validate_release_identity(identity: dict[str, Any], manifest: dict[str, Any], *, source: str = "release identity") -> None:
    if not isinstance(identity, dict) or set(identity) != {"schema", "release_id", "generated_at", "tracked_files"}:
        raise ReleaseError(f"{source} schema invalid")
    if identity["schema"] != "nmbot.release_identity.v1":
        raise ReleaseError(f"{source} schema invalid")
    if identity["release_id"] != manifest["release_id"]:
        raise ReleaseError(f"{source} release_id mismatch")
    if not isinstance(identity["generated_at"], str) or not SAFE_GENERATED_AT_RE.fullmatch(identity["generated_at"]):
        raise ReleaseError(f"{source} generated_at invalid")
    expected = {item["path"]: item["sha256"] for item in manifest["files"] if item["path"] != IDENTITY_IN_RELEASE}
    tracked = identity["tracked_files"]
    if not isinstance(tracked, list):
        raise ReleaseError(f"{source} tracked_files invalid")
    actual: dict[str, str] = {}
    for item in tracked:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ReleaseError(f"{source} tracked_files row invalid")
        rel = _manifest_path(str(item["path"]))
        if rel == IDENTITY_IN_RELEASE:
            raise ReleaseError(f"{source} must not track identity file")
        if rel in actual:
            raise ReleaseError(f"{source} duplicate tracked path")
        sha = item["sha256"]
        if not isinstance(sha, str) or not HEX_RE.fullmatch(sha):
            raise ReleaseError(f"{source} tracked hash invalid")
        actual[rel] = sha
    if actual != expected:
        raise ReleaseError(f"{source} tracked_files mismatch")


def safe_extract(archive: Path, dest: Path, *, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        dest.mkdir(parents=True, exist_ok=True)
        for member in tf.getmembers():
            rel = _safe_rel(member.name)
            if not member.isfile() or member.isdir() or member.issym() or member.islnk() or member.isdev():
                raise ReleaseError(f"unsafe tar member: {member.name}")
            if rel != IDENTITY_IN_RELEASE and not _is_allowed_runtime_file_for_policy(rel, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths, profile=profile):
                raise ReleaseError(f"unexpected tar member: {member.name}")
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise ReleaseError(f"tar path traversal: {member.name}")
        tf.extractall(dest, filter="data")


def verify_archive_against_manifest(archive: Path, manifest: dict[str, Any]) -> None:
    if archive.name != manifest["archive_name"]:
        raise ReleaseError("artifact filename must equal manifest archive_name")
    if _sha256_file(archive) != manifest["archive_sha256"]:
        raise ReleaseError("archive sha256 mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "extract"
        root.mkdir()
        test_api_overlay_paths = _manifest_test_api_overlay_paths(manifest)
        callback_worker = manifest.get("import_modules") == list(V6_CALLBACK_WORKER_IMPORT_MODULES)
        safe_extract(archive, root, include_dialogue_exporter=_manifest_has_dialogue_exporter(manifest), test_api_overlay_paths=test_api_overlay_paths, profile=V6_CALLBACK_WORKER_PROFILE if callback_worker else None)
        expected = {item["path"]: item["sha256"] for item in manifest["files"]}
        actual = _file_records([p for p in sorted(root.rglob("*")) if p.is_file()], root)
        actual_map = {item["path"]: item["sha256"] for item in actual}
        if actual_map != expected:
            raise ReleaseError("extracted file set/hash mismatch")
        identity_path = root / IDENTITY_IN_RELEASE
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ReleaseError("release identity invalid JSON") from exc
        validate_release_identity(identity, manifest)


def local_preflight(*, archive: Path, manifest_path: Path) -> str:
    manifest = load_manifest(manifest_path)
    verify_archive_against_manifest(archive, manifest)
    test_api_overlay_paths = _manifest_test_api_overlay_paths(manifest)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "release"
        root.mkdir()
        callback_worker = manifest.get("import_modules") == list(V6_CALLBACK_WORKER_IMPORT_MODULES)
        safe_extract(archive, root, include_dialogue_exporter=_manifest_has_dialogue_exporter(manifest), test_api_overlay_paths=test_api_overlay_paths, profile=V6_CALLBACK_WORKER_PROFILE if callback_worker else None)
        for rel in manifest["entrypoints"]:
            if not (root / rel).is_file():
                raise ReleaseError(f"required entrypoint missing: {rel}")
        artifact_paths = {item["path"] for item in manifest["files"]}
        v6_only = manifest["import_modules"] == list(V6_ONLY_IMPORT_MODULES)
        callback_worker = manifest["import_modules"] == list(V6_CALLBACK_WORKER_IMPORT_MODULES)
        if not v6_only and not callback_worker:
            _assert_required_runtime_resources_present(artifact_paths, root=root)
            _assert_remote_preflight_sources_present(root, artifact_paths)
        identity = json.loads((root / IDENTITY_IN_RELEASE).read_text(encoding="utf-8"))
        validate_release_identity(identity, manifest)
        if v6_only:
            expected_v6_paths = set(V6_ONLY_RUNTIME_FILES) | {IDENTITY_IN_RELEASE}
            if artifact_paths != expected_v6_paths:
                raise ReleaseError("V6-only local preflight file set must exactly match its allowlist")
            py_files = [root / rel for rel in V6_ONLY_PREFLIGHT_PY_FILES]
        elif callback_worker:
            expected_paths = set(V6_CALLBACK_WORKER_RUNTIME_FILES) | {IDENTITY_IN_RELEASE}
            if artifact_paths != expected_paths:
                raise ReleaseError("V6 callback-worker local preflight file set must exactly match its allowlist")
            py_files = [root / rel for rel in V6_CALLBACK_WORKER_PREFLIGHT_PY_FILES]
        else:
            required_preflight_files = _remote_preflight_py_files(artifact_paths)
            py_files = sorted({*root.rglob("*.py"), *(root / rel for rel in required_preflight_files)})
        for idx, path in enumerate(py_files):
            py_compile.compile(str(path), cfile=str(Path(tmp) / f"compiled-{idx}.pyc"), doraise=True)
        code = "import importlib, json, sys\nfor name in json.loads(sys.argv[1]):\n    importlib.import_module(name)\nprint('import=ok')\n"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        pythonpath = os.pathsep.join([str(root), str(root / "scripts")])
        env["PYTHONPATH"] = pythonpath
        required_dependencies = V6_ONLY_REQUIRED_DEPENDENCIES if v6_only else V6_CALLBACK_WORKER_REQUIRED_DEPENDENCIES if callback_worker else ()
        import_names = [*manifest["import_modules"], *required_dependencies]
        proc = subprocess.run([sys.executable, "-c", code, json.dumps(import_names)], cwd=root, env=env, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise ReleaseError((proc.stdout + proc.stderr)[-2000:])
        startup_env = {
            "PYTHONPATH": pythonpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "NMBOT_CONTOUR_PROFILE": "test",
            "NMBOT_API_STATE_FILE": str(Path(tmp) / "state.json"),
            "NMBOT_RUNTIME_VERSION_FILE": str(Path(tmp) / "runtime-version.json"),
            "NMBOT_CALLBACK_OUTBOX_DIR": str(Path(tmp) / "callback-outbox"),
            "NMBOT_API_TOKEN": "",
            "JIVO_PROVIDER_ID": "",
            "JIVO_PROVIDER_TOKEN": "",
            "OPENROUTER_API_KEY": "",
            "OVERMIND_TOKEN": "",
            "GATEWAY_POLL_TOKEN": "",
        }
        startup_code = (
            "import asyncio\n"
            "from scripts import nmbot_api_server as api\n"
            "app = api.create_app()\n"
            "asyncio.run(api.close_client(app))\n"
            "print('create_app=ok')\n"
        )
        startup = subprocess.run([sys.executable, "-c", startup_code], cwd=root, env=startup_env, text=True, capture_output=True, check=False)
        if startup.returncode != 0 or "create_app=ok" not in startup.stdout:
            raise ReleaseError("extracted artifact create_app startup failed: " + (startup.stdout + startup.stderr)[-2000:])
    return f"preflight=ok release_id={manifest['release_id']} files={len(manifest['files'])} py_compile={len(py_files)} import_modules={len(manifest['import_modules'])} startup=create_app\n"


def render_plan(*, release_id: str, remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    rid = _release_id(release_id)
    current_root = f"{remote_root}/current"
    return "\n".join([
        "plan=atomic_api_release",
        f"release_id={rid}",
        "default=dry-run/local-only",
        "scope=api_only",
        f"staging={remote_root}/.release_staging/{rid}",
        f"extract={remote_root}/releases/{rid}",
        "migration_prerequisite=live systemd unit must already be migrated before deploy",
        f"migration_required_working_directory={current_root}",
        f"migration_required_execstart_contains={current_root}/scripts/nmbot_api_server.py",
        "migration_guard=systemctl --user show API properties before any remote write/upload",
        "external_runtime=link manifest external_runtime_paths (.env data logs backups) into extracted TEST API release",
        "pre_switch=remote checksum + safe extractor + py_compile + import smoke",
        "cutover=brief API stop, verify inactive, switch current, publish matching external identity, start API",
        "switch=ln -sfn releases/<release_id> temporary symlink + atomic mv -T current",
        f"start_after_switch={API_SERVICE} only",
        f"health={API_HEALTH_URL} plus scripts/nmbot_release_identity.py read identity proof",
        f"forbidden_restart={BRIDGE_SERVICE},{WORKER_SERVICE}",
        "rollback=stop API, restore previous current symlink and previous identity, start API, verify previous health+identity",
        "production_migrated=required_not_assumed",
    ]) + "\n"


def render_migration_plan(*, remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    current_root = f"{remote_root}/current"
    return "\n".join([
        "plan=atomic_release_migration_prerequisites",
        "scope=local-only description; this command does not write remote systemd",
        f"required_service={API_SERVICE}",
        f"required_working_directory={current_root}",
        f"required_execstart_contains={current_root}/scripts/nmbot_api_server.py",
        f"required_health_after_owner_migration={API_HEALTH_URL}",
        "deploy_guard=atomic deploy refuses until systemctl show proves these values",
    ]) + "\n"


def _allowed_bootstrap_out_dir(out_dir: Path) -> Path:
    original = out_dir.expanduser()
    if ".." in original.parts:
        raise ReleaseError("bootstrap output directory must not contain parent traversal")
    allowed_roots = [Path("/tmp/opencode").resolve(strict=False), (ROOT / "release_bundles" / "bootstrap").resolve(strict=False)]
    if os.path.lexists(original) and original.is_symlink():
        raise ReleaseError("bootstrap output directory must not be a symlink")
    cwd = Path.cwd().resolve(strict=False)
    abs_original = original if original.is_absolute() else (cwd / original)
    if ".." in abs_original.parts:
        raise ReleaseError("bootstrap output directory must not contain parent traversal")
    allowed_root: Path | None = None
    for root in allowed_roots:
        try:
            abs_original.relative_to(root)
            allowed_root = root
            break
        except ValueError:
            continue
    if allowed_root is None:
        raise ReleaseError("bootstrap output directory must be under /tmp/opencode or project release_bundles/bootstrap")
    probe = Path(abs_original.anchor)
    for part in abs_original.parts[1:]:
        probe = probe / part
        if os.path.lexists(probe) and probe.is_symlink():
            raise ReleaseError("bootstrap output path contains a symlink component")
    return abs_original.resolve(strict=False)


def _write_new_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ReleaseError(f"refusing to overwrite bootstrap output: {path}")
    path.write_text(content, encoding="utf-8")


def _renameat2_syscall_number() -> int:
    machine = os.uname().machine
    if machine in {"x86_64", "amd64"}:
        return 316
    if machine in {"aarch64", "arm64"}:
        return 276
    raise ReleaseError(f"renameat2 RENAME_NOREPLACE is not supported by this platform: {machine}")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_noreplace(src: Path, dst: Path) -> None:
    """Publish ``src`` at ``dst`` only if no lexical destination exists.

    This intentionally uses Linux ``renameat2(RENAME_NOREPLACE)`` instead of
    plain ``rename`` because plain directory rename may replace an empty
    destination directory.  Unsupported kernels/filesystems fail closed.
    """
    if not src.is_dir() or src.is_symlink():
        raise ReleaseError(f"rename_noreplace source must be a private real directory: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    syscall_no = _renameat2_syscall_number()
    libc = ctypes.CDLL(None, use_errno=True)
    rename_noreplace = 1
    at_fdcwd = -100
    result = libc.syscall(
        ctypes.c_long(syscall_no),
        ctypes.c_int(at_fdcwd),
        ctypes.c_char_p(os.fsencode(src)),
        ctypes.c_int(at_fdcwd),
        ctypes.c_char_p(os.fsencode(dst)),
        ctypes.c_uint(rename_noreplace),
    )
    if result != 0:
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise ReleaseError(f"refusing to overwrite existing immutable release directory: {dst}")
        if err in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}:
            raise ReleaseError(f"renameat2 RENAME_NOREPLACE is not supported here; refusing unsafe publish: {dst}")
        if err == errno.EXDEV:
            raise ReleaseError(f"release publication must stay on one filesystem: {src} -> {dst}")
        raise OSError(err, os.strerror(err), str(dst))
    _fsync_dir(dst.parent)


def _make_private_staging_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=".nmbot-capture-staging-", dir=out))


def _cleanup_private_staging(staging: Path, out: Path) -> None:
    try:
        staging.relative_to(out)
    except ValueError as exc:
        raise ReleaseError(f"refusing to cleanup staging outside output parent: {staging}") from exc
    if staging.name.startswith(".nmbot-capture-staging-") is False:
        raise ReleaseError(f"refusing to cleanup unexpected staging path: {staging}")
    if os.path.lexists(staging) and staging.is_symlink():
        raise ReleaseError(f"refusing to cleanup symlink staging path: {staging}")
    if staging.exists():
        shutil.rmtree(staging)


def _safe_stderr_detail(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")[-1200:]
    text = SECRET_ASSIGNMENT_LINE_RE.sub("[REDACTED]\n", text)
    safe_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SECRET_ERROR_RE.search(stripped) or not SAFE_DIAGNOSTIC_LINE_RE.fullmatch(stripped):
            safe_lines.append("[REDACTED]")
        else:
            safe_lines.append(stripped[:300])
    return "\n".join(safe_lines)[-1200:].strip()


def _sanitized_binary_error(proc: subprocess.CompletedProcess[bytes]) -> str:
    detail = _safe_stderr_detail(proc.stderr or b"")
    if detail:
        return f"remote binary command failed with exit={proc.returncode}; stderr={detail}"
    return f"remote binary command failed with exit={proc.returncode}"


def _contract_capture_paths(root: Path = ROOT) -> list[str]:
    return [path.relative_to(root).as_posix() for path in iter_snapshot_files(root)]


def _snapshot_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,120}", value):
        raise ReleaseError("unsafe snapshot_id")
    return value


def _snapshot_contour(value: str = DEFAULT_SNAPSHOT_CONTOUR) -> str:
    if value not in SNAPSHOT_CONTOURS:
        raise ReleaseError("snapshot contour must be one of: test, client-production")
    return value


def _snapshot_remote_root_for_contour(contour: str = DEFAULT_SNAPSHOT_CONTOUR) -> str:
    profile = _snapshot_contour(contour)
    if profile == "test":
        return DEFAULT_REMOTE_ROOT
    if profile == "client-production":
        return CLIENT_PRODUCTION_REMOTE_ROOT
    raise ReleaseError("snapshot contour must be one of: test, client-production")


def _validate_snapshot_contour_root(contour: str, remote_root: str) -> str:
    profile = _snapshot_contour(contour)
    expected_root = _snapshot_remote_root_for_contour(profile)
    if remote_root != expected_root:
        raise ReleaseError("snapshot contour/root mismatch")
    return profile


def _snapshot_source_path(rel: str, *, test_api_overlay_paths: set[str] | frozenset[str] = frozenset()) -> str:
    safe = _manifest_path(rel)
    parts = PurePosixPath(safe).parts
    if any(part.startswith(".") for part in parts):
        raise ReleaseError(f"unsafe snapshot path: {rel!r}")
    if _is_excluded(safe, Path(safe)) or not _is_allowed_runtime_file_for_policy(safe, test_api_overlay_paths=test_api_overlay_paths):
        raise ReleaseError(f"snapshot path is outside fixed source policy: {safe}")
    return safe


def _canonical_snapshot_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _snapshot_manifest_hash(manifest: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_snapshot_manifest_bytes(manifest))


def _validate_snapshot_manifest_data(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ReleaseError("snapshot manifest must be an object")
    allowed = {"schema_version", "snapshot_id", "created_at_utc", "source_host", "remote_root", "contour", "policy", "files", "tar_members"}
    if set(manifest) != allowed:
        raise ReleaseError("snapshot manifest keys mismatch")
    if manifest["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise ReleaseError("unsupported snapshot manifest schema")
    if not _exact_type(manifest["snapshot_id"], str):
        raise ReleaseError("snapshot_id must be a string")
    _snapshot_id(manifest["snapshot_id"])
    if manifest["source_host"] != AUTHORIZED_DEPLOY_HOST:
        raise ReleaseError("snapshot manifest source mismatch")
    _validate_snapshot_contour_root(manifest["contour"], manifest["remote_root"])
    if not _exact_type(manifest["created_at_utc"], str) or not SAFE_GENERATED_AT_RE.fullmatch(manifest["created_at_utc"]):
        raise ReleaseError("snapshot manifest timestamp invalid")
    policy = manifest["policy"]
    if not isinstance(policy, dict) or set(policy) != {"roots", "root_files", "runtime_suffixes", "exclude_secret_like", "exclude_hidden", "exclude_deploy_control_scripts"}:
        raise ReleaseError("snapshot policy schema invalid")
    for name in ("exclude_secret_like", "exclude_hidden", "exclude_deploy_control_scripts"):
        if not _exact_type(policy[name], bool):
            raise ReleaseError("snapshot policy booleans must be exact bool")
    if policy != {
        "roots": list(SNAPSHOT_ROOTS),
        "root_files": list(SNAPSHOT_ROOT_FILES),
        "runtime_suffixes": list(RUNTIME_SUFFIXES),
        "exclude_secret_like": True,
        "exclude_hidden": True,
        "exclude_deploy_control_scripts": True,
    }:
        raise ReleaseError("snapshot policy mismatch")
    files = manifest["files"]
    tar_members = manifest["tar_members"]
    if not _exact_type(files, list) or not _exact_type(tar_members, list):
        raise ReleaseError("snapshot manifest file fields invalid")
    if len(files) > MAX_FILES:
        raise ReleaseError("snapshot file count exceeds limit")
    seen: set[str] = set()
    expected_members = [SNAPSHOT_MANIFEST_NAME]
    previous_path = ""
    total_size = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size", "mode"}:
            raise ReleaseError("snapshot manifest file row invalid")
        if not _exact_type(item["path"], str):
            raise ReleaseError("snapshot path must be a string")
        rel = _snapshot_source_path(item["path"])
        if rel in seen:
            raise ReleaseError(f"duplicate snapshot path: {rel}")
        if previous_path and rel <= previous_path:
            raise ReleaseError("snapshot files must be sorted by path")
        previous_path = rel
        seen.add(rel)
        if not _exact_type(item["sha256"], str) or not HEX_RE.fullmatch(item["sha256"]):
            raise ReleaseError(f"invalid snapshot hash: {rel}")
        if not _exact_type(item["size"], int) or item["size"] < 0 or item["size"] > MAX_FILE_BYTES:
            raise ReleaseError(f"invalid snapshot size: {rel}")
        total_size += item["size"]
        if total_size > MAX_TOTAL_BYTES:
            raise ReleaseError("snapshot total size exceeds limit")
        if not _exact_type(item["mode"], int) or item["mode"] not in {0o644, 0o755}:
            raise ReleaseError(f"invalid snapshot mode: {rel}")
        expected_members.append(SNAPSHOT_SOURCE_PREFIX + rel)
    if list(tar_members) != expected_members:
        raise ReleaseError("snapshot tar member list mismatch")
    return manifest


def _snapshot_vps_source_command(remote_root: str | None = None, *, contour: str = DEFAULT_SNAPSHOT_CONTOUR) -> str:
    profile = _snapshot_contour(contour)
    expected_root = _snapshot_remote_root_for_contour(profile)
    selected_root = expected_root if remote_root is None else remote_root
    _validate_snapshot_contour_root(profile, selected_root)
    payload = json.dumps({
        "root": selected_root,
        "contour": profile,
        "host": AUTHORIZED_DEPLOY_HOST,
        "roots": list(SNAPSHOT_ROOTS),
        "root_files": list(SNAPSHOT_ROOT_FILES),
        "suffixes": list(RUNTIME_SUFFIXES),
        "api_scripts": sorted(API_RUNTIME_SCRIPT_FILES),
        "api_script_deny": sorted(API_ONLY_SCRIPT_DENY),
        "max_files": MAX_FILES,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_total_bytes": MAX_TOTAL_BYTES,
    }, sort_keys=True)
    code = r'''
import datetime, hashlib, io, json, os, pathlib, re, stat, sys, tarfile
cfg=json.loads(sys.argv[1]); configured_root=cfg["root"]; contour=cfg["contour"]; canonical_root=pathlib.Path(configured_root).resolve(); root=canonical_root
roots=tuple(cfg["roots"]); root_files=set(cfg["root_files"]); suffixes=tuple(cfg["suffixes"]); api_scripts=set(cfg["api_scripts"]); api_deny=set(cfg["api_script_deny"])
max_files=int(cfg["max_files"]); max_file_bytes=int(cfg["max_file_bytes"]); max_total_bytes=int(cfg["max_total_bytes"])
secret_name=re.compile(r"(?i)(secret|password|passwd|credentials?|database|sqlite|private[_-]?key|api[_-]?key|bearer|\.pem$|\.key$|\.db$|\.sqlite$|\.sqlite3$|id_rsa)")
secret_content=re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}|^\s*(?:export\s+)?['\"]?[A-Za-z0-9_-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|API[_-]?KEY)[A-Za-z0-9_-]*['\"]?\s*(?::|=)\s*['\"]?[A-Za-z0-9_./+~:\-]{8,}", re.M|re.I)
private_key=re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.M)
bearer_literal=re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}")
secret_assignment=re.compile(r"^\s*(?:export\s+)?['\"]?(?P<name>[A-Za-z0-9_-]*(?:TOKEN(?!S)|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|API[_-]?KEY)[A-Za-z0-9_-]*)['\"]?\s*(?::|=)\s*(?P<value>.+?)\s*$", re.I)
benign_secret_value=re.compile(r"^(?:None|True|False|[A-Z][A-Z0-9_]*|os\.getenv\(|os\.environ\.|environ\.get\(|getenv\(|settings\.|config\.|self\.|args\.|kwargs\.|[A-Za-z_][A-Za-z0-9_]*\(|[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?)")
excluded_dirs={".git",".venv",".pytest_cache",".mypy_cache",".ruff_cache",".cache",".opencode",".github","node_modules","__pycache__","logs","backups","data","results","reports","eval","release_bundles","tests"}
excluded_suffixes=(".pyc",".pyo",".log",".jsonl",".bak",".tmp",".swp")
deploy_re=re.compile(r"(^|/)(?:deploy|rollback|release|nmbot_atomic_release|nmbot_release)(?:[_-].*)?\.py$")
def fail(msg): print(json.dumps({"ok": False, "error": msg}), file=sys.stderr); sys.exit(2)
if configured_root != cfg["root"] or not canonical_root.is_absolute(): fail("snapshot root is fixed")
if contour == "test":
    current=canonical_root/"current"
    if os.path.lexists(current):
        if not current.is_symlink(): fail("snapshot current must be a symlink")
        try: resolved=current.resolve(strict=True)
        except Exception: fail("snapshot current symlink is broken")
        releases=canonical_root/"releases"
        relid=resolved.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", relid): fail("snapshot current release id is unsafe")
        if resolved.parent != releases or not resolved.is_dir(): fail("snapshot current release target is outside releases")
        root=resolved
def safe_rel(raw):
    p=pathlib.PurePosixPath(str(raw))
    if not str(p) or p.is_absolute() or ".." in p.parts or any(part.startswith(".") for part in p.parts): fail("unsafe snapshot path")
    return str(p)
def allowed(rel):
    parts=pathlib.PurePosixPath(rel).parts
    if not parts or any(part in excluded_dirs for part in parts[:-1]): return False
    name=parts[-1]
    if name.startswith(".env") or name in {".env",".envrc"} or any(name.endswith(s) for s in excluded_suffixes): return False
    if secret_name.search(name): return False
    if rel in root_files: return True
    if parts[0] not in roots: return False
    if parts[0] == "scripts": return rel in api_scripts and rel not in api_deny
    if deploy_re.search(rel): return False
    return rel.endswith(suffixes)
def inside(p):
    try: return p.resolve().is_relative_to(root)
    except AttributeError: return str(p.resolve()).startswith(str(root)+os.sep) or p.resolve()==root
def read_openat_no_follow(rel):
    pp=pathlib.PurePosixPath(rel); parts=pp.parts
    if not parts: fail("unsafe snapshot path")
    root_fd=os.open(str(root), os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))
    fds=[root_fd]
    try:
        dirfd=root_fd
        for part in parts[:-1]:
            fd=os.open(part, os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0), dir_fd=dirfd)
            st=os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode): fail("snapshot directory component invalid")
            fds.append(fd); dirfd=fd
        fd=os.open(parts[-1], os.O_RDONLY|getattr(os,"O_NOFOLLOW",0), dir_fd=dirfd)
        fds.append(fd); st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): fail("snapshot source is not regular file")
        if st.st_size < 0 or st.st_size > max_file_bytes: fail("snapshot file size exceeds limit")
        chunks=[]; remaining=st.st_size
        while True:
            chunk=os.read(fd, 1024*1024)
            if not chunk: break
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > max_file_bytes: fail("snapshot file size exceeds limit")
        data=b"".join(chunks)
        st2=os.fstat(fd)
        if st2.st_ino != st.st_ino or st2.st_dev != st.st_dev or st2.st_size != len(data): fail("snapshot source changed during read")
        return data, st
    finally:
        for fd in reversed(fds):
            try: os.close(fd)
            except OSError: pass
def has_secret_assignment_literal(text):
    for line in text.splitlines():
        m=secret_assignment.match(line)
        if not m: continue
        value=m.group("value").strip()
        name=m.group("name").upper().replace("-", "_")
        strong_name=any(marker in name for marker in ("SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY", "API_KEY", "API_TOKEN"))
        if value.startswith(("'", '"')) and len(value) >= 2:
            quote=value[0]; end=value.find(quote, 1); literal=value[1:end] if end >= 1 else value[1:]
            if literal and (strong_name or looks_like_credential_literal(literal)): return True
            continue
        if benign_secret_value.match(value): continue
        if re.search(r"[A-Za-z0-9_./+~:-]{8,}", value): return True
    return False
def looks_like_credential_literal(value):
    if len(value) >= 24: return True
    if re.match(r"(?i)^(?:sk|pk|tok|key|secret|bearer)[_-]", value): return True
    return bool(re.search(r"[A-Za-z]", value) and re.search(r"\d", value) and len(value) >= 16)
def has_secret_like_content(text, rel):
    if private_key.search(text) or bearer_literal.search(text): return True
    if rel.endswith(".py"): return has_secret_assignment_literal(text)
    return secret_content.search(text) is not None
rows=[]
for top in list(roots)+sorted(root_files):
    base=root/top
    if base.is_file() and not base.is_symlink(): candidates=[base]
    elif base.is_dir() and not base.is_symlink(): candidates=sorted(base.rglob("*"), key=lambda p: str(p.relative_to(root)).replace(os.sep,"/"))
    else: continue
    for p in candidates:
        raw_rel=str(p.relative_to(root)).replace(os.sep,"/")
        if any(part.startswith(".") for part in pathlib.PurePosixPath(raw_rel).parts): continue
        rel=safe_rel(raw_rel)
        if not allowed(rel): continue
        if not inside(p): continue
        try: data, st=read_openat_no_follow(rel)
        except OSError: continue
        if rel.endswith((".py",".txt",".json",".yaml",".yml",".cfg",".ini")) and has_secret_like_content(data.decode("utf-8", errors="ignore"), rel): continue
        mode=0o755 if rel.startswith("scripts/") and rel.endswith(".py") else 0o644
        rows.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": mode, "data": data})
seen=set(); uniq=[]
for row in sorted(rows, key=lambda r: r["path"]):
    if row["path"] in seen: fail("duplicate snapshot path")
    seen.add(row["path"]); uniq.append(row)
if len(uniq) > max_files: fail("snapshot file count exceeds limit")
total=sum(r["size"] for r in uniq)
if total > max_total_bytes: fail("snapshot total size exceeds limit")
created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
# A capture is evidence for this invocation.  Add a collector-side nonce so a
# second fresh capture in the same second cannot collide with an immutable
# local snapshot directory.
sid="vps-source-"+created.replace(":","").replace("-","").replace("T","-").replace("Z","")+"-"+hashlib.sha256(("\n".join(seen)).encode()+os.urandom(16)).hexdigest()[:12]
files=[{k:r[k] for k in ("path","sha256","size","mode")} for r in uniq]
manifest={"schema_version":"nmbot.vps_source_snapshot.v1","snapshot_id":sid,"created_at_utc":created,"source_host":cfg["host"],"remote_root":configured_root,"contour":contour,"policy":{"roots":list(roots),"root_files":sorted(root_files),"runtime_suffixes":list(suffixes),"exclude_secret_like":True,"exclude_hidden":True,"exclude_deploy_control_scripts":True},"files":files,"tar_members":["snapshot-manifest.json"]+["source/"+r["path"] for r in files]}
mb=(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",",":"))+"\n").encode("utf-8")
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
    info=tarfile.TarInfo("snapshot-manifest.json"); info.size=len(mb); info.uid=info.gid=0; info.uname=info.gname=""; info.mtime=0; info.mode=0o644; tf.addfile(info, io.BytesIO(mb))
    for row in uniq:
        info=tarfile.TarInfo("source/"+row["path"]); info.size=row["size"]; info.uid=info.gid=0; info.uname=info.gname=""; info.mtime=0; info.mode=row["mode"]; tf.addfile(info, io.BytesIO(row["data"]))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _extract_snapshot_tar(payload: bytes, staging_snapshot_dir: Path) -> dict[str, Any]:
    source_dir = staging_snapshot_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tf:
        members = tf.getmembers()
        names = [member.name for member in members]
        if names.count(SNAPSHOT_MANIFEST_NAME) != 1:
            raise ReleaseError("snapshot tar must contain exactly one manifest")
        manifest_member = members[names.index(SNAPSHOT_MANIFEST_NAME)]
        if not manifest_member.isfile() or manifest_member.issym() or manifest_member.islnk() or manifest_member.isdev():
            raise ReleaseError("unsafe snapshot manifest member")
        manifest_file = tf.extractfile(manifest_member)
        if manifest_file is None:
            raise ReleaseError("snapshot manifest missing payload")
        try:
            manifest = json.loads(manifest_file.read().decode("utf-8"))
        except Exception as exc:
            raise ReleaseError("snapshot manifest invalid JSON") from exc
        manifest = _validate_snapshot_manifest_data(manifest)
        if names != manifest["tar_members"] or len(names) != len(set(names)):
            raise ReleaseError("snapshot tar members do not match manifest exactly")
        rows = {item["path"]: item for item in manifest["files"]}
        for member in members:
            if member.name == SNAPSHOT_MANIFEST_NAME:
                continue
            if not member.name.startswith(SNAPSHOT_SOURCE_PREFIX):
                raise ReleaseError("unexpected snapshot tar member")
            rel = _snapshot_source_path(member.name[len(SNAPSHOT_SOURCE_PREFIX):])
            row = rows.get(rel)
            if row is None:
                raise ReleaseError("snapshot tar member absent from manifest")
            if not member.isfile() or member.isdir() or member.issym() or member.islnk() or member.isdev():
                raise ReleaseError(f"unsafe snapshot member: {member.name}")
            if member.size != row["size"] or (member.mode & 0o777) != row["mode"]:
                raise ReleaseError(f"snapshot member metadata mismatch: {rel}")
            fh = tf.extractfile(member)
            if fh is None:
                raise ReleaseError(f"snapshot member missing payload: {rel}")
            data = fh.read()
            if len(data) != row["size"] or _sha256_bytes(data) != row["sha256"]:
                raise ReleaseError(f"snapshot member hash/size mismatch: {rel}")
            if SECRET_NAME_RE.search(PurePosixPath(rel).name):
                raise ReleaseError(f"secret-like filename rejected: {rel}")
            target = (source_dir / rel).resolve()
            if not str(target).startswith(str(source_dir.resolve()) + os.sep):
                raise ReleaseError("snapshot path traversal")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, row["mode"])
    (staging_snapshot_dir / SNAPSHOT_MANIFEST_NAME).write_bytes(_canonical_snapshot_manifest_bytes(manifest))
    for path in sorted(source_dir.rglob("*")):
        rel = path.relative_to(source_dir).as_posix()
        if path.is_symlink() or (path.exists() and not path.is_dir() and not path.is_file()):
            raise ReleaseError(f"unsafe snapshot node: {rel}")
        if path.is_file():
            _reject_secret_like(path, rel)
    return manifest


def snapshot_vps_source(*, remote: BinaryRemote, out_dir: Path = DEFAULT_BOOTSTRAP_OUT_DIR, keep_tar: bool = True, contour: str = DEFAULT_SNAPSHOT_CONTOUR) -> dict[str, Any]:
    out = _allowed_bootstrap_out_dir(out_dir)
    profile = _snapshot_contour(contour)
    proc = remote.run_binary(_snapshot_vps_source_command(contour=profile))
    if proc.returncode != 0:
        raise ReleaseError(_sanitized_binary_error(proc))
    staging = _make_private_staging_dir(out)
    published = False
    try:
        inner = staging / "incoming"
        manifest = _extract_snapshot_tar(proc.stdout, inner)
        sid = _snapshot_id(manifest["snapshot_id"])
        if keep_tar:
            (inner / "snapshot.tar").write_bytes(proc.stdout)
        final = out / sid
        _rename_noreplace(inner, final)
        published = True
        return {"snapshot_id": sid, "snapshot_dir": str(final), "manifest": str(final / SNAPSHOT_MANIFEST_NAME), "manifest_sha256": _snapshot_manifest_hash(manifest), "contour": profile, "remote_root": manifest["remote_root"], "files": len(manifest["files"])}
    finally:
        _cleanup_private_staging(staging, out)


def _bridge_snapshot_id(value: str) -> str:
    return _snapshot_id(value)


def _bridge_manifest_path(rel: str) -> str:
    safe = _manifest_path(rel)
    if safe not in BRIDGE_ALLOWED_FILES:
        raise ReleaseError(f"bridge path is outside exact allowlist: {safe}")
    return safe


def _bridge_source_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rel in BRIDGE_ALLOWED_FILES:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"bridge source file missing or unsafe: {rel}")
        _reject_secret_like(path, rel)
        st = path.stat()
        rows.append({"path": rel, "sha256": _sha256_file(path), "size": st.st_size, "mode": 0o755})
    _validate_size_limits(rows, label="bridge")
    return rows


def _validate_bridge_file_rows(rows: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(BRIDGE_ALLOWED_FILES):
        raise ReleaseError(f"{label} files invalid")
    out: list[dict[str, Any]] = []
    for expected, item in zip(BRIDGE_ALLOWED_FILES, rows):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size", "mode"} or item["path"] != expected:
            raise ReleaseError(f"{label} file row invalid")
        if not isinstance(item["sha256"], str) or not HEX_RE.fullmatch(item["sha256"]) or not isinstance(item["size"], int) or item["size"] < 0 or item["size"] > MAX_FILE_BYTES or item["mode"] != 0o755:
            raise ReleaseError(f"{label} file metadata invalid")
        out.append(dict(item))
    _validate_size_limits(out, label=label)
    return out


def _validate_bridge_baseline_rows(rows: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(BRIDGE_ALLOWED_FILES):
        raise ReleaseError(f"{label} files invalid")
    out: list[dict[str, Any]] = []
    for expected, item in zip(BRIDGE_ALLOWED_FILES, rows):
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size", "mode", "source_scope"} or item["path"] != expected:
            raise ReleaseError(f"{label} file row invalid")
        if item["source_scope"] not in BRIDGE_SOURCE_SCOPES:
            raise ReleaseError(f"{label} source_scope invalid")
        if not isinstance(item["sha256"], str) or not HEX_RE.fullmatch(item["sha256"]) or not isinstance(item["size"], int) or item["size"] < 0 or item["size"] > MAX_FILE_BYTES or item["mode"] != 0o755:
            raise ReleaseError(f"{label} file metadata invalid")
        out.append(dict(item))
    return out


def _bridge_file_rows_hash(rows: list[dict[str, Any]]) -> str:
    if rows and "source_scope" in rows[0]:
        payload = json.dumps(_validate_bridge_baseline_rows(rows, label="bridge baseline"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _sha256_bytes(payload)
    return _tree_hash_from_records(_validate_bridge_file_rows(rows, label="bridge"))


def _bridge_snapshot_manifest_hash(manifest: dict[str, Any]) -> str:
    return _sha256_bytes((json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def _validate_bridge_snapshot_manifest_data(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ReleaseError("bridge snapshot manifest must be an object")
    allowed = {"schema_version", "snapshot_id", "created_at_utc", "source_host", "remote_root", "source_mode", "active_release_id", "api_current_release_id", "policy", "files", "tar_members"}
    if set(manifest) != allowed:
        raise ReleaseError("bridge snapshot manifest keys mismatch")
    if manifest["schema_version"] != BRIDGE_SNAPSHOT_SCHEMA_VERSION:
        raise ReleaseError("unsupported bridge snapshot manifest schema")
    _bridge_snapshot_id(manifest["snapshot_id"])
    if manifest["source_host"] != AUTHORIZED_DEPLOY_HOST or manifest["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge snapshot source/root mismatch")
    if manifest["source_mode"] not in {"first_migration_canonical", "first_migration_mixed", "bridge_current"}:
        raise ReleaseError("bridge snapshot source_mode invalid")
    if not isinstance(manifest["active_release_id"], str):
        raise ReleaseError("bridge snapshot active_release_id invalid")
    if manifest["active_release_id"]:
        _release_id(manifest["active_release_id"])
    if not isinstance(manifest["api_current_release_id"], str):
        raise ReleaseError("bridge snapshot api_current_release_id invalid")
    if manifest["api_current_release_id"]:
        _release_id(manifest["api_current_release_id"])
    if not _exact_type(manifest["created_at_utc"], str) or not SAFE_GENERATED_AT_RE.fullmatch(manifest["created_at_utc"]):
        raise ReleaseError("bridge snapshot timestamp invalid")
    if manifest["policy"] != {"allowlist": list(BRIDGE_ALLOWED_FILES), "exclude_env_data_logs_units": True, "reject_symlinks": True}:
        raise ReleaseError("bridge snapshot policy mismatch")
    files = _validate_bridge_baseline_rows(manifest["files"], label="bridge snapshot")
    scopes = {row["source_scope"] for row in files}
    if manifest["source_mode"] == "bridge_current":
        if scopes != {"bridge_current"} or not manifest["active_release_id"] or manifest["api_current_release_id"]:
            raise ReleaseError("bridge snapshot bridge_current provenance mismatch")
    if manifest["source_mode"] == "first_migration_canonical":
        if scopes != {"bridge_canonical"} or manifest["active_release_id"] or manifest["api_current_release_id"]:
            raise ReleaseError("bridge snapshot canonical first migration provenance mismatch")
    if manifest["source_mode"] == "first_migration_mixed":
        if "api_current" not in scopes or not manifest["api_current_release_id"] or manifest["active_release_id"]:
            raise ReleaseError("bridge snapshot mixed first migration provenance mismatch")
        for row in files:
            if row["source_scope"] == "api_current" and row["path"] != "scripts/nmbot_egress_policy.py":
                raise ReleaseError("bridge snapshot api_current scope is only allowed for egress policy")
    expected_members = [SNAPSHOT_MANIFEST_NAME] + [SNAPSHOT_SOURCE_PREFIX + rel for rel in BRIDGE_ALLOWED_FILES]
    if manifest["tar_members"] != expected_members:
        raise ReleaseError("bridge snapshot tar member list mismatch")
    manifest["files"] = files
    return manifest


def _snapshot_vps_bridge_source_command(remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    if remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge snapshot root is fixed to TEST")
    payload = json.dumps({"root": DEFAULT_REMOTE_ROOT, "host": AUTHORIZED_DEPLOY_HOST, "allowlist": list(BRIDGE_ALLOWED_FILES), "current": BRIDGE_CURRENT, "releases": BRIDGE_RELEASES, "api_current": "current", "api_releases": "releases", "egress": "scripts/nmbot_egress_policy.py"}, sort_keys=True)
    code = r'''
import datetime, hashlib, io, json, os, pathlib, re, stat, sys, tarfile
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve(); allow=list(cfg["allowlist"]); current=root/cfg["current"]; releases=root/cfg["releases"]; api_current=root/cfg["api_current"]; api_releases=root/cfg["api_releases"]
def fail(msg): print(json.dumps({"ok": False, "error": msg}), file=sys.stderr); sys.exit(2)
if str(root) != cfg["root"]: fail("bridge snapshot root is fixed")
source=root; source_mode="first_migration_canonical"; active=""; api_active=""
if os.path.lexists(current):
    if not current.is_symlink(): fail("bridge-current must be absent or a symlink")
    try: resolved=current.resolve(strict=True)
    except Exception: fail("bridge-current symlink is broken")
    active=resolved.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", active): fail("bridge-current release id is unsafe")
    if resolved.parent != releases or not resolved.is_dir(): fail("bridge-current target is outside bridge-releases")
    source=resolved; source_mode="bridge_current"
def safe_api_current():
    if not api_current.is_symlink(): fail("api current is required for missing bridge egress policy")
    try: resolved=api_current.resolve(strict=True)
    except Exception: fail("api current symlink is broken")
    rid=resolved.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", rid): fail("api current release id is unsafe")
    if resolved.parent != api_releases or not resolved.is_dir(): fail("api current target is outside releases")
    return resolved, rid
def read_file(base, rel):
    pp=pathlib.PurePosixPath(rel)
    if str(pp) != rel or pp.is_absolute() or ".." in pp.parts or any(part.startswith(".") for part in pp.parts): fail("unsafe bridge path")
    root_fd=os.open(str(base), os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0)); fds=[root_fd]
    try:
        dirfd=root_fd
        for part in pp.parts[:-1]:
            fd=os.open(part, os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0), dir_fd=dirfd); fds.append(fd)
            if not stat.S_ISDIR(os.fstat(fd).st_mode): fail("bridge source dir component invalid")
            dirfd=fd
        fd=os.open(pp.parts[-1], os.O_RDONLY|getattr(os,"O_NOFOLLOW",0), dir_fd=dirfd); fds.append(fd); st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): fail("bridge source is not regular")
        data=os.read(fd, st.st_size+1)
        st2=os.fstat(fd)
        if st2.st_ino != st.st_ino or st2.st_dev != st.st_dev or len(data) != st.st_size: fail("bridge source changed during read")
        return data
    finally:
        for fd in reversed(fds):
            try: os.close(fd)
            except OSError: pass
rows=[]
for rel in allow:
    scope="bridge_current" if source_mode=="bridge_current" else "bridge_canonical"
    base=source
    if source_mode != "bridge_current" and rel == cfg["egress"] and not (root/rel).exists():
        base, api_active = safe_api_current(); scope="api_current"; source_mode="first_migration_mixed"
    elif source_mode != "bridge_current" and rel == cfg["egress"] and (root/rel).is_symlink():
        fail("bridge canonical egress policy is unsafe")
    elif source_mode != "bridge_current" and rel != cfg["egress"] and not (root/rel).is_file():
        fail("bridge canonical file missing: "+rel)
    data=read_file(base, rel)
    rows.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "mode": 0o755, "source_scope": scope, "data": data})
created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
sid="bridge-source-"+created.replace(":","").replace("-","").replace("T","-").replace("Z","")+"-"+hashlib.sha256(("\n".join(allow)+source_mode+active).encode()).hexdigest()[:12]
manifest={"schema_version":"nmbot.bridge_source_snapshot.v1","snapshot_id":sid,"created_at_utc":created,"source_host":cfg["host"],"remote_root":cfg["root"],"source_mode":source_mode,"active_release_id":active,"api_current_release_id":api_active,"policy":{"allowlist":allow,"exclude_env_data_logs_units":True,"reject_symlinks":True},"files":[{k:r[k] for k in ("path","sha256","size","mode","source_scope")} for r in rows],"tar_members":["snapshot-manifest.json"]+["source/"+r["path"] for r in rows]}
mb=(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",",":"))+"\n").encode()
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
    info=tarfile.TarInfo("snapshot-manifest.json"); info.size=len(mb); info.uid=info.gid=0; info.uname=info.gname=""; info.mtime=0; info.mode=0o644; tf.addfile(info, io.BytesIO(mb))
    for row in rows:
        info=tarfile.TarInfo("source/"+row["path"]); info.size=row["size"]; info.uid=info.gid=0; info.uname=info.gname=""; info.mtime=0; info.mode=0o755; tf.addfile(info, io.BytesIO(row["data"]))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _extract_bridge_snapshot_tar(payload: bytes, staging_snapshot_dir: Path) -> dict[str, Any]:
    source_dir = staging_snapshot_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tf:
        members = tf.getmembers()
        names = [member.name for member in members]
        if names.count(SNAPSHOT_MANIFEST_NAME) != 1:
            raise ReleaseError("bridge snapshot tar must contain exactly one manifest")
        manifest_file = tf.extractfile(members[names.index(SNAPSHOT_MANIFEST_NAME)])
        if manifest_file is None:
            raise ReleaseError("bridge snapshot manifest missing payload")
        manifest = _validate_bridge_snapshot_manifest_data(json.loads(manifest_file.read().decode("utf-8")))
        if names != manifest["tar_members"] or len(names) != len(set(names)):
            raise ReleaseError("bridge snapshot tar members do not match manifest exactly")
        rows = {item["path"]: item for item in manifest["files"]}
        for member in members:
            if member.name == SNAPSHOT_MANIFEST_NAME:
                continue
            if not member.name.startswith(SNAPSHOT_SOURCE_PREFIX):
                raise ReleaseError("unexpected bridge snapshot tar member")
            rel = _bridge_manifest_path(member.name[len(SNAPSHOT_SOURCE_PREFIX):])
            row = rows[rel]
            if not member.isfile() or member.isdir() or member.issym() or member.islnk() or member.isdev():
                raise ReleaseError("unsafe bridge snapshot member")
            data_fh = tf.extractfile(member)
            data = data_fh.read() if data_fh else b""
            if len(data) != row["size"] or _sha256_bytes(data) != row["sha256"]:
                raise ReleaseError("bridge snapshot member hash/size mismatch")
            target = (source_dir / rel).resolve()
            if not str(target).startswith(str(source_dir.resolve()) + os.sep):
                raise ReleaseError("bridge snapshot path traversal")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, 0o755)
    (staging_snapshot_dir / SNAPSHOT_MANIFEST_NAME).write_bytes((json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    return manifest


def snapshot_vps_bridge_source(*, remote: BinaryRemote, out_dir: Path = DEFAULT_BOOTSTRAP_OUT_DIR, keep_tar: bool = True) -> dict[str, Any]:
    out = _allowed_bootstrap_out_dir(out_dir)
    proc = remote.run_binary(_snapshot_vps_bridge_source_command())
    if proc.returncode != 0:
        raise ReleaseError(_sanitized_binary_error(proc))
    staging = _make_private_staging_dir(out)
    try:
        inner = staging / "incoming"
        manifest = _extract_bridge_snapshot_tar(proc.stdout, inner)
        sid = _bridge_snapshot_id(manifest["snapshot_id"])
        if keep_tar:
            (inner / "snapshot.tar").write_bytes(proc.stdout)
        final = out / sid
        _rename_noreplace(inner, final)
        return {"snapshot_id": sid, "snapshot_dir": str(final), "manifest": str(final / SNAPSHOT_MANIFEST_NAME), "manifest_sha256": _bridge_snapshot_manifest_hash(manifest), "remote_root": manifest["remote_root"], "source_mode": manifest["source_mode"], "files": len(manifest["files"])}
    finally:
        _cleanup_private_staging(staging, out)


def verify_bridge_snapshot_dir(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / SNAPSHOT_MANIFEST_NAME
    source_dir = snapshot_dir / "source"
    if not snapshot_dir.exists() or snapshot_dir.is_symlink() or not source_dir.is_dir() or source_dir.is_symlink():
        raise ReleaseError("bridge snapshot directory layout invalid")
    manifest = _validate_bridge_snapshot_manifest_data(json.loads(manifest_path.read_text(encoding="utf-8")))
    actual = _bridge_source_rows(source_dir)
    expected_plain = [{k: row[k] for k in ("path", "sha256", "size", "mode")} for row in manifest["files"]]
    if actual != expected_plain:
        raise ReleaseError("bridge snapshot source tree does not match manifest")
    for path in sorted(source_dir.rglob("*")):
        rel = path.relative_to(source_dir).as_posix()
        if path.is_symlink() or (os.path.lexists(path) and not path.is_file() and not path.is_dir()):
            raise ReleaseError(f"unsafe bridge snapshot node: {rel}")
        if path.is_file() and rel not in BRIDGE_ALLOWED_FILES:
            raise ReleaseError(f"unexpected bridge snapshot file: {rel}")
    return manifest


def prepare_bridge_worktree(*, snapshot_dir: Path, out_dir: Path) -> dict[str, Any]:
    manifest = verify_bridge_snapshot_dir(snapshot_dir)
    out = _allowed_bootstrap_out_dir(out_dir)
    sid = _bridge_snapshot_id(manifest["snapshot_id"])
    staging = _make_private_staging_dir(out)
    try:
        work = staging / sid
        _write_bridge_manifest_driven_source_copy(source_dir=snapshot_dir / "source", dest_dir=work / "source", manifest=manifest)
        rows = _bridge_source_rows(work / "source")
        source_tree_sha = _tree_hash_from_records(rows)
        source_manifest_sha = _source_manifest_sha(rows)
        baseline_files = _validate_bridge_baseline_rows(manifest["files"], label="bridge baseline")
        provenance = {"schema": BRIDGE_WORKTREE_PROVENANCE_SCHEMA, "snapshot_id": sid, "snapshot_manifest_sha256": _bridge_snapshot_manifest_hash(manifest), "source_host": manifest["source_host"], "remote_root": manifest["remote_root"], "source_mode": manifest["source_mode"], "active_release_id": manifest["active_release_id"], "api_current_release_id": manifest["api_current_release_id"], "baseline_files": baseline_files, "baseline_files_sha256": _bridge_file_rows_hash(baseline_files), "source_tree_sha256": source_tree_sha, "source_manifest_sha256": source_manifest_sha}
        (work / "snapshot-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        final = out / sid
        _rename_noreplace(work, final)
        return {"worktree_dir": str(final), "snapshot_id": sid, "snapshot_manifest_sha256": provenance["snapshot_manifest_sha256"], "source_tree_sha256": source_tree_sha, "source_manifest_sha256": source_manifest_sha, "files": len(rows)}
    finally:
        _cleanup_private_staging(staging, out)


def _read_bridge_file_openat_no_follow(root: Path, rel: str, expected: dict[str, Any]) -> tuple[bytes, int]:
    safe = _bridge_manifest_path(rel)
    parts = PurePosixPath(safe).parts
    root_fd = os.open(root.resolve(strict=True), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fds = [root_fd]
    try:
        dir_fd = root_fd
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ReleaseError(f"bridge snapshot directory component invalid: {safe}")
            fds.append(fd)
            dir_fd = fd
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        fds.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ReleaseError(f"bridge snapshot file is not regular: {safe}")
        data = os.read(fd, st.st_size + 1)
        if len(data) != expected["size"] or _sha256_bytes(data) != expected["sha256"]:
            raise ReleaseError(f"bridge snapshot file hash/size mismatch: {safe}")
        if expected["mode"] != 0o755:
            raise ReleaseError(f"bridge snapshot file mode mismatch: {safe}")
        return data, 0o755
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _write_bridge_manifest_driven_source_copy(*, source_dir: Path, dest_dir: Path, manifest: dict[str, Any]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=False)
    for row in manifest["files"]:
        rel = _bridge_manifest_path(row["path"])
        data, mode = _read_bridge_file_openat_no_follow(source_dir, rel, row)
        if _has_secret_like_content(data.decode("utf-8", errors="ignore"), python_source=True):
            raise ReleaseError(f"secret-like bridge snapshot file rejected: {rel}")
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(target, mode)


def verify_bridge_prepared_worktree(worktree_dir: Path) -> dict[str, Any]:
    work = _allowed_prepared_worktree_dir(worktree_dir)
    source = work / "source"
    prov_path = work / "snapshot-provenance.json"
    if not work.is_dir() or not source.is_dir() or source.is_symlink() or not prov_path.is_file() or prov_path.is_symlink():
        raise ReleaseError("bridge prepared worktree layout invalid")
    provenance = json.loads(prov_path.read_text(encoding="utf-8"))
    allowed = {"schema", "snapshot_id", "snapshot_manifest_sha256", "source_host", "remote_root", "source_mode", "active_release_id", "api_current_release_id", "baseline_files", "baseline_files_sha256", "source_tree_sha256", "source_manifest_sha256"}
    if not isinstance(provenance, dict) or set(provenance) != allowed or provenance["schema"] != BRIDGE_WORKTREE_PROVENANCE_SCHEMA:
        raise ReleaseError("bridge prepared worktree provenance schema invalid")
    _bridge_snapshot_id(provenance["snapshot_id"])
    if provenance["source_host"] != AUTHORIZED_DEPLOY_HOST or provenance["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge prepared worktree provenance source mismatch")
    baseline_files = _validate_bridge_baseline_rows(provenance["baseline_files"], label="bridge baseline")
    if provenance["baseline_files_sha256"] != _bridge_file_rows_hash(baseline_files):
        raise ReleaseError("bridge prepared worktree baseline hash invalid")
    for key in ("snapshot_manifest_sha256", "baseline_files_sha256", "source_tree_sha256", "source_manifest_sha256"):
        if not isinstance(provenance[key], str) or not HEX_RE.fullmatch(provenance[key]):
            raise ReleaseError("bridge prepared worktree provenance hash invalid")
    rows = _bridge_source_rows(source)
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source).as_posix()
        if path.is_symlink() or (os.path.lexists(path) and not path.is_file() and not path.is_dir()):
            raise ReleaseError(f"unsafe bridge worktree node: {rel}")
        if path.is_file() and rel not in BRIDGE_ALLOWED_FILES:
            raise ReleaseError(f"unexpected bridge worktree file: {rel}")
    return {"worktree_dir": str(work), "source_dir": str(source), "provenance": provenance, "rows": rows, "source_tree_sha256": _tree_hash_from_records(rows), "source_manifest_sha256": _source_manifest_sha(rows)}


def _validate_bridge_release_provenance(provenance: Any, *, require_present: bool) -> dict[str, Any]:
    if not isinstance(provenance, dict):
        raise ReleaseError("bridge source provenance schema invalid")
    allowed = {"present", "source_snapshot_id", "source_snapshot_manifest_sha256", "source_host", "remote_root", "source_mode", "active_release_id", "api_current_release_id", "baseline_files", "baseline_files_sha256", "prepared_source_tree_sha256", "prepared_source_manifest_sha256", "worktree_source_tree_sha256", "worktree_source_manifest_sha256", "worktree_provenance_sha256"}
    if set(provenance) != allowed or provenance["present"] is not True:
        raise ReleaseError("bridge source provenance keys mismatch")
    if require_present is False:
        return provenance
    _bridge_snapshot_id(provenance["source_snapshot_id"])
    if provenance["source_host"] != AUTHORIZED_DEPLOY_HOST or provenance["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge source provenance source mismatch")
    if provenance["source_mode"] not in {"first_migration_canonical", "first_migration_mixed", "bridge_current"}:
        raise ReleaseError("bridge source provenance source_mode invalid")
    if not isinstance(provenance["active_release_id"], str) or not isinstance(provenance["api_current_release_id"], str):
        raise ReleaseError("bridge source provenance release id invalid")
    if provenance["active_release_id"]:
        _release_id(provenance["active_release_id"])
    if provenance["api_current_release_id"]:
        _release_id(provenance["api_current_release_id"])
    baseline_files = _validate_bridge_baseline_rows(provenance["baseline_files"], label="bridge baseline")
    if provenance["baseline_files_sha256"] != _bridge_file_rows_hash(baseline_files):
        raise ReleaseError("bridge source provenance baseline hash invalid")
    scopes = {row["source_scope"] for row in baseline_files}
    if provenance["source_mode"] == "bridge_current" and (scopes != {"bridge_current"} or not provenance["active_release_id"] or provenance["api_current_release_id"]):
        raise ReleaseError("bridge source provenance bridge_current mismatch")
    if provenance["source_mode"] == "first_migration_canonical" and (scopes != {"bridge_canonical"} or provenance["active_release_id"] or provenance["api_current_release_id"]):
        raise ReleaseError("bridge source provenance canonical mismatch")
    if provenance["source_mode"] == "first_migration_mixed":
        if "api_current" not in scopes or not provenance["api_current_release_id"] or provenance["active_release_id"]:
            raise ReleaseError("bridge source provenance mixed mismatch")
        if any(row["source_scope"] == "api_current" and row["path"] != "scripts/nmbot_egress_policy.py" for row in baseline_files):
            raise ReleaseError("bridge source provenance api_current path invalid")
    worktree_provenance = {"schema": BRIDGE_WORKTREE_PROVENANCE_SCHEMA, "snapshot_id": provenance["source_snapshot_id"], "snapshot_manifest_sha256": provenance["source_snapshot_manifest_sha256"], "source_host": provenance["source_host"], "remote_root": provenance["remote_root"], "source_mode": provenance["source_mode"], "active_release_id": provenance["active_release_id"], "api_current_release_id": provenance["api_current_release_id"], "baseline_files": baseline_files, "baseline_files_sha256": provenance["baseline_files_sha256"], "source_tree_sha256": provenance["prepared_source_tree_sha256"], "source_manifest_sha256": provenance["prepared_source_manifest_sha256"]}
    expected_worktree_sha = _sha256_bytes(json.dumps(worktree_provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if provenance["worktree_provenance_sha256"] != expected_worktree_sha:
        raise ReleaseError("bridge source provenance worktree hash invalid")
    for key in ("source_snapshot_manifest_sha256", "baseline_files_sha256", "prepared_source_tree_sha256", "prepared_source_manifest_sha256", "worktree_source_tree_sha256", "worktree_source_manifest_sha256", "worktree_provenance_sha256"):
        if not isinstance(provenance[key], str) or not HEX_RE.fullmatch(provenance[key]):
            raise ReleaseError("bridge source provenance hash invalid")
    return provenance


def build_bridge_from_worktree(*, worktree_dir: Path, release_id: str | None = None, out_dir: Path = DEFAULT_OUT_DIR) -> Artifact:
    verified = verify_bridge_prepared_worktree(worktree_dir)
    provenance = verified["provenance"]
    provenance_payload = json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    release_provenance = {"present": True, "source_snapshot_id": provenance["snapshot_id"], "source_snapshot_manifest_sha256": provenance["snapshot_manifest_sha256"], "source_host": provenance["source_host"], "remote_root": provenance["remote_root"], "source_mode": provenance["source_mode"], "active_release_id": provenance["active_release_id"], "api_current_release_id": provenance["api_current_release_id"], "baseline_files": provenance["baseline_files"], "baseline_files_sha256": provenance["baseline_files_sha256"], "prepared_source_tree_sha256": provenance["source_tree_sha256"], "prepared_source_manifest_sha256": provenance["source_manifest_sha256"], "worktree_source_tree_sha256": verified["source_tree_sha256"], "worktree_source_manifest_sha256": verified["source_manifest_sha256"], "worktree_provenance_sha256": _sha256_bytes(provenance_payload)}
    _validate_bridge_release_provenance(release_provenance, require_present=True)
    rid = _release_id(release_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"nmbot-bridge-{rid}.tar.gz"
    manifest_path = out_dir / f"nmbot-bridge-{rid}.manifest.json"
    if os.path.lexists(archive) or os.path.lexists(manifest_path):
        raise ReleaseError("refusing to overwrite existing immutable bridge artifact")
    rows = _bridge_source_rows(Path(verified["source_dir"]))
    with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for row in rows:
            path = Path(verified["source_dir"]) / row["path"]
            info = tf.gettarinfo(str(path), arcname=row["path"])
            info.uid = info.gid = 0; info.uname = info.gname = ""; info.mtime = 0; info.mode = 0o755
            with path.open("rb") as fh:
                tf.addfile(info, fh)
    manifest = {"schema_version": BRIDGE_SCHEMA_VERSION, "scope": "bridge", "release_id": rid, "created_at_utc": "deterministic-build-clock-not-recorded", "archive_name": archive.name, "archive_sha256": _sha256_file(archive), "files": rows, "entrypoints": [BRIDGE_ENTRYPOINT], "import_modules": list(BRIDGE_IMPORT_MODULES), "service": BRIDGE_SERVICE, "forbidden_services": [API_SERVICE, WORKER_SERVICE], "unit_contract": {"fragment_path": BRIDGE_UNIT_PATH, "working_directory": DEFAULT_REMOTE_ROOT, "environment_file": f"{DEFAULT_REMOTE_ROOT}/.env", "inline_environment": BRIDGE_INLINE_ENVIRONMENT, "health_url": BRIDGE_HEALTH_URL}, "source_provenance": release_provenance}
    validate_bridge_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Artifact(archive=archive, manifest=manifest_path, manifest_data=manifest)


def validate_bridge_manifest(manifest: dict[str, Any]) -> None:
    allowed = {"schema_version", "scope", "release_id", "created_at_utc", "archive_name", "archive_sha256", "files", "entrypoints", "import_modules", "service", "forbidden_services", "unit_contract", "source_provenance"}
    if not isinstance(manifest, dict) or set(manifest) != allowed:
        raise ReleaseError("bridge manifest keys mismatch")
    if manifest["schema_version"] != BRIDGE_SCHEMA_VERSION or manifest["scope"] != "bridge":
        raise ReleaseError("unsupported bridge manifest schema")
    rid = _release_id(manifest["release_id"])
    if manifest["archive_name"] != f"nmbot-bridge-{rid}.tar.gz" or not BRIDGE_ARCHIVE_RE.fullmatch(manifest["archive_name"]):
        raise ReleaseError("invalid bridge archive_name")
    if not HEX_RE.fullmatch(manifest["archive_sha256"]):
        raise ReleaseError("invalid bridge archive sha256")
    if manifest["service"] != BRIDGE_SERVICE or manifest["forbidden_services"] != [API_SERVICE, WORKER_SERVICE]:
        raise ReleaseError("bridge manifest must restart only bridge service")
    if manifest["entrypoints"] != [BRIDGE_ENTRYPOINT] or manifest["import_modules"] != list(BRIDGE_IMPORT_MODULES):
        raise ReleaseError("bridge manifest entry/import contract mismatch")
    if manifest["unit_contract"] != {"fragment_path": BRIDGE_UNIT_PATH, "working_directory": DEFAULT_REMOTE_ROOT, "environment_file": f"{DEFAULT_REMOTE_ROOT}/.env", "inline_environment": BRIDGE_INLINE_ENVIRONMENT, "health_url": BRIDGE_HEALTH_URL}:
        raise ReleaseError("bridge unit contract mismatch")
    if manifest["files"] != _bridge_sorted_manifest_rows(manifest["files"]):
        raise ReleaseError("bridge manifest files must equal exact allowlist")
    _validate_bridge_release_provenance(manifest["source_provenance"], require_present=True)


def _bridge_sorted_manifest_rows(rows: Any) -> list[dict[str, Any]]:
    return _validate_bridge_file_rows(rows, label="bridge manifest")


def load_bridge_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_bridge_manifest(manifest)
    return manifest


def safe_extract_bridge(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        names = [member.name for member in members]
        if names != list(BRIDGE_ALLOWED_FILES):
            raise ReleaseError("bridge archive member set must equal exact allowlist")
        for member in members:
            rel = _bridge_manifest_path(member.name)
            if not member.isfile() or member.isdir() or member.issym() or member.islnk() or member.isdev():
                raise ReleaseError("unsafe bridge tar member")
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise ReleaseError("bridge tar path traversal")
        dest.mkdir(parents=True, exist_ok=True)
        tf.extractall(dest, filter="data")


def verify_bridge_archive_against_manifest(archive: Path, manifest: dict[str, Any]) -> None:
    if archive.name != manifest["archive_name"] or _sha256_file(archive) != manifest["archive_sha256"]:
        raise ReleaseError("bridge archive sha256/name mismatch")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bridge"
        safe_extract_bridge(archive, root)
        if _bridge_source_rows(root) != manifest["files"]:
            raise ReleaseError("bridge extracted file set/hash mismatch")


def bridge_preflight(*, archive: Path, manifest_path: Path) -> str:
    manifest = load_bridge_manifest(manifest_path)
    verify_bridge_archive_against_manifest(archive, manifest)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bridge"
        safe_extract_bridge(archive, root)
        for idx, rel in enumerate(BRIDGE_ALLOWED_FILES):
            py_compile.compile(str(root / rel), cfile=str(Path(tmp) / f"bridge-{idx}.pyc"), doraise=True)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = os.pathsep.join([str(root), str(root / "scripts")])
        code = "import importlib, json, sys\nmod=importlib.import_module('scripts.nmbot_n8n_bridge_server')\napp=mod.create_app()\nroutes={(r.method, getattr(r.resource, 'canonical', '')) for r in app.router.routes()}\nassert ('GET','/health') in routes\nprint('bridge-import=ok')\n"
        proc = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            raise ReleaseError((proc.stdout + proc.stderr)[-2000:])
    return f"bridge-preflight=ok release_id={manifest['release_id']} files={len(manifest['files'])} py_compile={len(BRIDGE_ALLOWED_FILES)} import_modules={len(BRIDGE_IMPORT_MODULES)}\n"


def _read_file_openat_no_follow(root: Path, rel: str, expected: dict[str, Any], *, test_api_overlay_paths: set[str] | frozenset[str] = frozenset()) -> tuple[bytes, int]:
    safe = _snapshot_source_path(rel, test_api_overlay_paths=test_api_overlay_paths)
    parts = PurePosixPath(safe).parts
    root_fd = os.open(root.resolve(strict=True), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fds = [root_fd]
    try:
        dir_fd = root_fd
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise ReleaseError(f"snapshot directory component invalid: {safe}")
            fds.append(fd)
            dir_fd = fd
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        fds.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ReleaseError(f"snapshot file is not regular: {safe}")
        if st.st_size != expected["size"] or st.st_size > MAX_FILE_BYTES:
            raise ReleaseError(f"snapshot file size mismatch: {safe}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ReleaseError(f"snapshot file size exceeds limit: {safe}")
            chunks.append(chunk)
        data = b"".join(chunks)
        st2 = os.fstat(fd)
        if st2.st_dev != st.st_dev or st2.st_ino != st.st_ino or st2.st_size != len(data):
            raise ReleaseError(f"snapshot file changed during read: {safe}")
        if _sha256_bytes(data) != expected["sha256"]:
            raise ReleaseError(f"snapshot file hash mismatch: {safe}")
        mode = 0o755 if safe.startswith("scripts/") and safe.endswith(".py") else 0o644
        if expected["mode"] != mode:
            raise ReleaseError(f"snapshot file mode mismatch: {safe}")
        return data, mode
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _source_manifest_from_tree(source_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in manifest["files"]:
        rel = row["path"]
        data, mode = _read_file_openat_no_follow(source_dir, rel, row)
        rows.append({"path": rel, "sha256": _sha256_bytes(data), "size": len(data), "mode": mode})
    return rows


def _source_manifest_sha(rows: list[dict[str, Any]]) -> str:
    return _sha256_bytes((json.dumps({"files": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


def _write_manifest_driven_source_copy(*, source_dir: Path, dest_dir: Path, manifest: dict[str, Any]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=False)
    for row in manifest["files"]:
        rel = row["path"]
        data, mode = _read_file_openat_no_follow(source_dir, rel, row)
        if SECRET_NAME_RE.search(PurePosixPath(rel).name) or _has_secret_like_content(data.decode("utf-8", errors="ignore"), python_source=rel.endswith(".py")):
            raise ReleaseError(f"secret-like snapshot file rejected: {rel}")
        target = dest_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(target, mode)


def verify_snapshot_dir(snapshot_dir: Path) -> dict[str, Any]:
    snap = snapshot_dir.resolve(strict=False)
    manifest_path = snap / SNAPSHOT_MANIFEST_NAME
    source_dir = snap / "source"
    if not snapshot_dir.exists() or snapshot_dir.is_symlink() or not source_dir.is_dir() or source_dir.is_symlink():
        raise ReleaseError("snapshot directory layout invalid")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseError("snapshot manifest invalid JSON") from exc
    manifest = _validate_snapshot_manifest_data(manifest)
    expected = {item["path"]: item for item in manifest["files"]}
    actual: dict[str, dict[str, Any]] = {row["path"]: row for row in _source_manifest_from_tree(source_dir, manifest)}
    for path in sorted(source_dir.rglob("*")):
        rel = path.relative_to(source_dir).as_posix()
        if path.is_symlink() or (os.path.lexists(path) and not path.is_file() and not path.is_dir()):
            raise ReleaseError(f"unsafe snapshot node: {rel}")
        if path.is_dir():
            continue
        safe = _snapshot_source_path(rel)
        if safe not in expected:
            raise ReleaseError(f"unexpected snapshot file: {safe}")
        _reject_secret_like(path, safe)
    if actual != expected:
        raise ReleaseError("snapshot source tree does not match manifest")
    return manifest


def prepare_worktree(*, snapshot_dir: Path, out_dir: Path) -> dict[str, Any]:
    manifest = verify_snapshot_dir(snapshot_dir)
    out = _allowed_bootstrap_out_dir(out_dir)
    sid = _snapshot_id(manifest["snapshot_id"])
    final = out / sid
    staging = _make_private_staging_dir(out)
    published = False
    try:
        work = staging / sid
        _write_manifest_driven_source_copy(source_dir=snapshot_dir / "source", dest_dir=work / "source", manifest=manifest)
        copied_rows = _source_manifest_from_tree(work / "source", manifest)
        if copied_rows != manifest["files"]:
            raise ReleaseError("prepared worktree source does not match snapshot manifest")
        source_tree_sha = _tree_hash_from_records(copied_rows)
        source_manifest_sha = _source_manifest_sha(copied_rows)
        provenance = {
            "schema": WORKTREE_PROVENANCE_SCHEMA,
            "snapshot_id": sid,
            "snapshot_manifest_sha256": _snapshot_manifest_hash(manifest),
            "source_host": manifest["source_host"],
            "remote_root": manifest["remote_root"],
            "contour": manifest["contour"],
            "source_tree_sha256": source_tree_sha,
            "source_manifest_sha256": source_manifest_sha,
        }
        (work / "snapshot-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _rename_noreplace(work, final)
        published = True
        return {"worktree_dir": str(final), "snapshot_id": sid, "snapshot_manifest_sha256": provenance["snapshot_manifest_sha256"], "source_tree_sha256": source_tree_sha, "source_manifest_sha256": source_manifest_sha, "files": len(manifest["files"])}
    finally:
        _cleanup_private_staging(staging, out)


def _allowed_prepared_worktree_dir(worktree_dir: Path) -> Path:
    path = worktree_dir.resolve(strict=False)
    allowed_roots = [Path("/tmp/opencode").resolve(strict=False), (ROOT / "release_bundles" / "bootstrap").resolve(strict=False)]
    if os.path.lexists(worktree_dir) and worktree_dir.is_symlink():
        raise ReleaseError("prepared worktree must not be a symlink")
    if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
        raise ReleaseError("prepared worktree must be under allowed output roots")
    return path


def _worktree_source_rows(source_dir: Path, *, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> list[dict[str, Any]]:
    files = _runtime_files_for_profile(source_dir, profile=profile, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths)
    rows = _file_records_with_metadata(files, source_dir)
    _validate_size_limits(rows, label="worktree")
    return sorted(rows, key=lambda row: row["path"])


def verify_prepared_worktree(worktree_dir: Path, *, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> dict[str, Any]:
    work = _allowed_prepared_worktree_dir(worktree_dir)
    source = work / "source"
    provenance_path = work / "snapshot-provenance.json"
    if not work.is_dir() or not source.is_dir() or source.is_symlink() or not provenance_path.is_file() or provenance_path.is_symlink():
        raise ReleaseError("prepared worktree layout invalid")
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseError("prepared worktree provenance invalid JSON") from exc
    allowed = {"schema", "snapshot_id", "snapshot_manifest_sha256", "source_host", "remote_root", "contour", "source_tree_sha256", "source_manifest_sha256"}
    if not isinstance(provenance, dict) or set(provenance) != allowed:
        raise ReleaseError("prepared worktree provenance schema invalid")
    if provenance["schema"] != WORKTREE_PROVENANCE_SCHEMA:
        raise ReleaseError("prepared worktree provenance schema invalid")
    for key in allowed - {"schema"}:
        if not _exact_type(provenance[key], str) or not provenance[key]:
            raise ReleaseError(f"prepared worktree provenance field invalid: {key}")
    _snapshot_id(provenance["snapshot_id"])
    for key in ("snapshot_manifest_sha256", "source_tree_sha256", "source_manifest_sha256"):
        if not HEX_RE.fullmatch(provenance[key]):
            raise ReleaseError(f"prepared worktree provenance hash invalid: {key}")
    if provenance["source_host"] != AUTHORIZED_DEPLOY_HOST:
        raise ReleaseError("prepared worktree provenance source mismatch")
    _validate_snapshot_contour_root(provenance["contour"], provenance["remote_root"])
    rows = _worktree_source_rows(source, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths, profile=profile)
    current_source_tree_sha = _tree_hash_from_records(rows)
    current_source_manifest_sha = _source_manifest_sha(rows)
    return {"worktree_dir": str(work), "source_dir": str(source), "provenance": provenance, "rows": rows, "source_tree_sha256": current_source_tree_sha, "source_manifest_sha256": current_source_manifest_sha}


def build_from_worktree(*, worktree_dir: Path, release_id: str | None = None, out_dir: Path = DEFAULT_OUT_DIR, include_dialogue_exporter: bool = False, test_api_overlay_paths: set[str] | frozenset[str] = frozenset(), profile: str | None = None) -> Artifact:
    verified = verify_prepared_worktree(worktree_dir, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths, profile=profile)
    provenance = verified["provenance"]
    provenance_payload = json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    release_provenance = {
        "present": True,
        "source_snapshot_id": provenance["snapshot_id"],
        "source_snapshot_manifest_sha256": provenance["snapshot_manifest_sha256"],
        "source_host": provenance["source_host"],
        "remote_root": provenance["remote_root"],
        "contour": provenance["contour"],
        "worktree_source_tree_sha256": verified["source_tree_sha256"],
        "worktree_source_manifest_sha256": verified["source_manifest_sha256"],
        "worktree_provenance_sha256": _sha256_bytes(provenance_payload),
    }
    _validate_release_source_provenance(release_provenance, require_present=True)
    return build(release_id=release_id, out_dir=out_dir, root=Path(verified["source_dir"]), source_provenance=release_provenance, include_dialogue_exporter=include_dialogue_exporter, test_api_overlay_paths=test_api_overlay_paths, profile=profile)


def compare_snapshot(*, snapshot_dir: Path, project_root: Path, contour: str = DEFAULT_SNAPSHOT_CONTOUR) -> dict[str, Any]:
    root = project_root.resolve(strict=False)
    if root != ROOT.resolve(strict=False):
        raise ReleaseError("compare project-root is fixed to the NMBot project root")
    manifest = verify_snapshot_dir(snapshot_dir)
    profile = _snapshot_contour(contour)
    if manifest["contour"] != profile:
        raise ReleaseError("compare snapshot contour mismatch")
    snapshot = {item["path"]: item["sha256"] for item in manifest["files"]}
    local_paths = [path.relative_to(root).as_posix() for path in iter_snapshot_files(root)]
    local = {rel: _sha256_file(root / rel) for rel in local_paths}
    added = sorted(({"path": rel, "project_sha256": local[rel]} for rel in set(local) - set(snapshot)), key=lambda x: x["path"])
    missing = sorted(({"path": rel, "snapshot_sha256": snapshot[rel]} for rel in set(snapshot) - set(local)), key=lambda x: x["path"])
    changed = sorted(({"path": rel, "snapshot_sha256": snapshot[rel], "project_sha256": local[rel]} for rel in set(snapshot) & set(local) if snapshot[rel] != local[rel]), key=lambda x: x["path"])
    return {"schema": "nmbot.snapshot_compare.v1", "snapshot_id": manifest["snapshot_id"], "snapshot_manifest_sha256": _snapshot_manifest_hash(manifest), "contour": profile, "remote_root": manifest["remote_root"], "project_root": str(ROOT), "added": added, "missing": missing, "changed": changed}


def _validate_test_release_target(*, host: str, port: str, confirm: bool) -> None:
    if not confirm:
        raise ReleaseError("test-release requires --confirm")
    if host != AUTHORIZED_DEPLOY_HOST or port != AUTHORIZED_DEPLOY_PORT:
        raise ReleaseError("test-release target is not authorized")


def _validate_live_api_helper_overlay_target(*, host: str, port: str, confirm: bool) -> None:
    if not confirm:
        raise ReleaseError("live-api-helper-overlay requires --confirm")
    if host != AUTHORIZED_DEPLOY_HOST or port != AUTHORIZED_DEPLOY_PORT:
        raise ReleaseError("live-api-helper-overlay target is not authorized")


def _require_real_directory_no_follow(path: Path, label: str) -> None:
    """Reject a missing, non-directory, or symlinked directory component."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ReleaseError(f"{label} missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ReleaseError(f"{label} must be a real non-symlink directory")


def _validate_live_api_helper_overlay_parent_components(root: Path, release_id: str, *, require_release_staging: bool) -> None:
    """Validate fixed-root parents component-wise, never via resolve()."""
    _require_real_directory_no_follow(root, "fixed root")
    _require_real_directory_no_follow(root / "scripts", "helper destination parent")
    _require_real_directory_no_follow(root / ".live_api_helper_overlay_staging", "overlay staging parent")
    if require_release_staging:
        _require_real_directory_no_follow(root / ".live_api_helper_overlay_staging" / release_id, "overlay release staging parent")
    _require_real_directory_no_follow(root / "backups", "helper backup parent")


def _live_api_helper_overlay_command(*, release_id: str, expected_sha256: str, staging_file: str, mode: str = "publish", staged_data_b64: str | None = None) -> str:
    """Return a descriptor-confined remote operation for the live helper overlay.

    It intentionally has no release/current/service/env handling: its only publish
    target is the stable helper path under the fixed live API root.
    """
    payload_data = {
        "root": DEFAULT_REMOTE_ROOT,
        "destination": LIVE_API_HELPER_OVERLAY_DESTINATION,
        "lock": LIVE_API_HELPER_OVERLAY_LOCK,
        "staging_file": staging_file,
        "release_id": release_id,
        "expected_sha256": expected_sha256,
        "mode": mode,
    }
    if staged_data_b64 is not None:
        payload_data["staged_data_b64"] = staged_data_b64
    payload = json.dumps(payload_data, sort_keys=True)
    code = r'''
import base64, hashlib, json, os, stat, sys, time
cfg=json.loads(sys.argv[1])
def digest(data): return hashlib.sha256(data).hexdigest()
def fail(message): print(json.dumps({"ok":False,"operation":"live_api_helper_overlay","error":message}, sort_keys=True)); sys.exit(2)
class PublishValidationError(Exception): pass
NOFOLLOW=getattr(os,"O_NOFOLLOW",0)
DIRECTORY=getattr(os,"O_DIRECTORY",0)
def require_dir(fd, label):
    if not stat.S_ISDIR(os.fstat(fd).st_mode): fail(label+" must be a real directory")
    return fd
def open_dir(parent, name, label):
    try: return require_dir(os.open(name, os.O_RDONLY|DIRECTORY|NOFOLLOW, dir_fd=parent), label)
    except FileNotFoundError: fail(label+" missing")
    except OSError: fail(label+" must be a real non-symlink directory")
def mkdir_open(parent, name, label, existing_ok=False):
    try: os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        if not existing_ok: fail(label+" already exists")
    except OSError: fail(label+" creation failed")
    return open_dir(parent, name, label)
def read_regular(parent, name, label):
    try: fd=os.open(name, os.O_RDONLY|NOFOLLOW, dir_fd=parent)
    except FileNotFoundError: fail(label+" missing")
    except OSError: fail(label+" must be a regular non-symlink file")
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode): fail(label+" must be a regular non-symlink file")
        chunks=[]
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk: break
            chunks.append(chunk)
        return b"".join(chunks)
    finally: os.close(fd)
def write_new(parent, name, data, mode, label):
    try: fd=os.open(name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|NOFOLLOW, mode, dir_fd=parent)
    except FileExistsError: fail(label+" already exists")
    except OSError: fail(label+" creation failed")
    try:
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            if written <= 0: fail(label+" write failed")
            view=view[written:]
        os.fsync(fd)
    finally: os.close(fd)
def close_all(fds):
    for fd in reversed(fds):
        try: os.close(fd)
        except OSError: pass
if cfg["root"] != "/home/neiro/novostroy-bot" or cfg["destination"] != "/home/neiro/novostroy-bot/scripts/nmbot_env_secrets.py": fail("fixed destination mismatch")
if not __import__("re").fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", cfg["release_id"]): fail("unsafe release id")
if cfg["lock"] != "/home/neiro/novostroy-bot/.live_api_helper_overlay_lock": fail("fixed lock mismatch")
if cfg["staging_file"] != "/home/neiro/novostroy-bot/.live_api_helper_overlay_staging/"+cfg["release_id"]+"/nmbot_env_secrets.py": fail("staging path is outside fixed root")
basefd=homefd=neirofd=rootfd=scriptsfd=stagingfd=backupfd=releasefd=lockfd=None
try:
    basefd=require_dir(os.open("/", os.O_RDONLY|DIRECTORY|NOFOLLOW),"trusted filesystem root")
    homefd=open_dir(basefd,"home","fixed root parent")
    neirofd=open_dir(homefd,"neiro","fixed root parent")
    rootfd=open_dir(neirofd,"novostroy-bot","fixed root")
    scriptsfd=open_dir(rootfd,"scripts","helper destination parent")
    stagingfd=mkdir_open(rootfd,".live_api_helper_overlay_staging","overlay staging parent",existing_ok=True)
    backupfd=open_dir(rootfd,"backups","helper backup parent")
    if cfg["mode"] == "preflight": print(json.dumps({"ok":True,"operation":"live_api_helper_overlay_preflight"}, sort_keys=True)); sys.exit(0)
    if cfg["mode"] == "prepare":
        releasefd=mkdir_open(stagingfd,cfg["release_id"],"overlay release staging parent")
        print(json.dumps({"ok":True,"operation":"live_api_helper_overlay_prepare","staging_file":cfg["staging_file"]}, sort_keys=True)); sys.exit(0)
    if cfg["mode"] == "acquire-lock":
        lockfd=mkdir_open(rootfd,".live_api_helper_overlay_lock","overlay lock")
        print(json.dumps({"ok":True,"operation":"live_api_helper_overlay_lock"}, sort_keys=True)); sys.exit(0)
    if cfg["mode"] == "cleanup":
        try:
            releasefd=open_dir(stagingfd,cfg["release_id"],"overlay release staging parent")
            try: os.unlink("nmbot_env_secrets.py", dir_fd=releasefd)
            except FileNotFoundError: pass
            os.rmdir(cfg["release_id"], dir_fd=stagingfd)
        except FileNotFoundError: pass
        try: os.rmdir(".live_api_helper_overlay_lock", dir_fd=rootfd)
        except FileNotFoundError: pass
        print(json.dumps({"ok":True,"operation":"live_api_helper_overlay_cleanup"}, sort_keys=True)); sys.exit(0)
    if cfg["mode"] == "stage":
        try:
            source=base64.b64decode(cfg["staged_data_b64"],validate=True)
        except (KeyError, TypeError, ValueError): fail("staged helper encoding invalid")
        if digest(source) != cfg["expected_sha256"]: fail("staged helper hash mismatch")
        releasefd=open_dir(stagingfd,cfg["release_id"],"overlay release staging parent")
        write_new(releasefd,"nmbot_env_secrets.py",source,0o755,"staged helper")
        if digest(read_regular(releasefd,"nmbot_env_secrets.py","staged helper")) != cfg["expected_sha256"]: fail("staged helper hash mismatch")
        if stat.S_IMODE(os.stat("nmbot_env_secrets.py",dir_fd=releasefd,follow_symlinks=False).st_mode) != 0o755: fail("staged helper mode mismatch")
        print(json.dumps({"ok":True,"operation":"live_api_helper_overlay_stage","sha256":cfg["expected_sha256"]}, sort_keys=True)); sys.exit(0)
    if cfg["mode"] != "publish": fail("unsafe operation mode")
    releasefd=open_dir(stagingfd,cfg["release_id"],"overlay release staging parent")
    lockfd=open_dir(rootfd,".live_api_helper_overlay_lock","overlay lock")
    source=read_regular(releasefd,"nmbot_env_secrets.py","staged helper")
    if digest(source) != cfg["expected_sha256"]: fail("staged helper hash mismatch")
    previous=read_regular(scriptsfd,"nmbot_env_secrets.py","helper destination")
    backup="nmbot_env_secrets.py."+time.strftime("%Y%m%d-%H%M%S",time.gmtime())+"."+cfg["release_id"]+".bak"
    write_new(backupfd,backup,previous,0o700,"helper backup")
    if digest(read_regular(backupfd,backup,"helper backup")) != digest(previous): fail("backup hash mismatch")
    tmp=".nmbot_env_secrets.py."+cfg["release_id"]+".tmp"; rollback_tmp=".nmbot_env_secrets.py."+cfg["release_id"]+".rollback.tmp"; replaced=False
    try:
        write_new(scriptsfd,tmp,source,0o755,"temporary helper")
        os.chmod(tmp,0o755,dir_fd=scriptsfd); read_regular(scriptsfd,tmp,"temporary helper")
        os.replace(tmp,"nmbot_env_secrets.py",src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd); replaced=True
        os.fsync(scriptsfd)
        if digest(read_regular(scriptsfd,"nmbot_env_secrets.py","published helper")) != cfg["expected_sha256"]: raise PublishValidationError("published helper hash mismatch")
        if stat.S_IMODE(os.stat("nmbot_env_secrets.py",dir_fd=scriptsfd,follow_symlinks=False).st_mode) != 0o755: raise PublishValidationError("published helper mode mismatch")
    except BaseException as exc:
        if not replaced:
            # `fail()` deliberately exits immediately before publication; keep
            # that precise pre-replace failure behaviour intact.
            if isinstance(exc, SystemExit): raise
            try: os.unlink(tmp,dir_fd=scriptsfd)
            except FileNotFoundError: pass
            fail("helper publish failed; backup restored=false")
        restored=False
        if replaced:
            try:
                backup_data=read_regular(backupfd,backup,"helper backup")
                write_new(scriptsfd,rollback_tmp,backup_data,0o755,"rollback temporary helper")
                os.replace(rollback_tmp,"nmbot_env_secrets.py",src_dir_fd=scriptsfd,dst_dir_fd=scriptsfd); os.fsync(scriptsfd)
                if digest(read_regular(scriptsfd,"nmbot_env_secrets.py","restored helper")) != digest(backup_data): raise PublishValidationError("backup restore hash mismatch")
                restored=True
            except BaseException: restored=False
        try: os.unlink(tmp,dir_fd=scriptsfd)
        except FileNotFoundError: pass
        try: os.unlink(rollback_tmp,dir_fd=scriptsfd)
        except FileNotFoundError: pass
        fail("helper publish failed; backup restored="+("true" if restored else "false"))
    print(json.dumps({"ok":True,"operation":"live_api_helper_overlay","release_id":cfg["release_id"],"destination":cfg["destination"],"sha256":cfg["expected_sha256"],"backup":"/home/neiro/novostroy-bot/backups/"+backup}, sort_keys=True))
finally:
    close_all([fd for fd in (lockfd,releasefd,backupfd,stagingfd,scriptsfd,rootfd,neirofd,homefd,basefd) if fd is not None])
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _assert_live_api_helper_execstart(remote: Remote) -> None:
    """Require the same complete current-unit contract as atomic deploys."""
    try:
        _assert_remote_unit_migrated(remote, remote_root=DEFAULT_REMOTE_ROOT)
    except ReleaseError as exc:
        raise ReleaseError("live-api-helper-overlay could not prove strict active API WorkingDirectory/ExecStart contract before remote write") from exc


def _capture_live_api_helper_source(remote: BinaryRemote, *, release_id: str) -> dict[str, Any]:
    """Capture and validate this invocation's read-only live-root provenance."""
    snapshot = snapshot_vps_source(
        remote=remote,
        out_dir=LIVE_API_HELPER_SNAPSHOT_OUT_ROOT / _release_id(release_id),
        keep_tar=True,
        contour=DEFAULT_SNAPSHOT_CONTOUR,
    )
    snapshot_dir = Path(snapshot["snapshot_dir"])
    manifest = verify_snapshot_dir(snapshot_dir)
    if manifest["contour"] != DEFAULT_SNAPSHOT_CONTOUR or manifest["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("live-api-helper-overlay internal source capture did not prove physical root /home/neiro/novostroy-bot")
    manifest_sha256 = _snapshot_manifest_hash(manifest)
    if snapshot.get("manifest_sha256") != manifest_sha256:
        raise ReleaseError("live-api-helper-overlay internal source capture manifest hash mismatch")
    return {
        "snapshot_id": manifest["snapshot_id"],
        "manifest_sha256": manifest_sha256,
    }


def _live_api_helper_remote_ok(proc: subprocess.CompletedProcess[str], *, operation: str) -> None:
    """Do not surface remote stdout/stderr: the helper handles secret-adjacent code."""
    if proc.returncode != 0:
        raise ReleaseError(f"live-api-helper-overlay {operation} failed")


def _live_api_helper_remote_json(remote: Remote, command: str) -> dict[str, Any]:
    proc = remote.run(command)
    _live_api_helper_remote_ok(proc, operation="publish")
    try:
        value = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise ReleaseError("live-api-helper-overlay remote publish returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReleaseError("live-api-helper-overlay remote publish returned invalid JSON")
    return value


def live_api_helper_overlay(*, release_id: str, confirm: bool, remote: Remote, host: str = AUTHORIZED_DEPLOY_HOST, port: str = AUTHORIZED_DEPLOY_PORT) -> dict[str, Any]:
    _validate_live_api_helper_overlay_target(host=host, port=port, confirm=confirm)
    rid = _release_id(release_id)
    # Both calls are read-only and must precede all lock/staging/publish operations.
    # The snapshot cannot be caller supplied: only this invocation's collector can
    # authorize a helper write.
    _assert_live_api_helper_execstart(remote)
    source_capture = _capture_live_api_helper_source(remote, release_id=rid)  # type: ignore[arg-type]
    _validate_live_api_helper_overlay_paths([LIVE_API_HELPER_OVERLAY_FILE])
    data, expected = _read_overlay_source_no_follow(LIVE_API_HELPER_OVERLAY_FILE)
    if not _is_allowed_live_api_helper_overlay_file(expected["path"]):
        raise ReleaseError("live-api-helper-overlay helper path is not allowlisted")
    staging_dir = f"{LIVE_API_HELPER_OVERLAY_STAGING}/{rid}"
    staging_file = f"{staging_dir}/{Path(LIVE_API_HELPER_OVERLAY_FILE).name}"
    # Validate all already-existing fixed-root parents before any overlay write.
    # The per-release staging directory is checked again after its controlled
    # creation and before upload.
    _live_api_helper_remote_ok(
        remote.run(_live_api_helper_overlay_command(release_id=rid, expected_sha256=expected["sha256"], staging_file=staging_file, mode="preflight")),
        operation="parent preflight",
    )
    lock_acquired = False
    cleanup_error: str | None = None
    try:
        _live_api_helper_remote_ok(
            remote.run(_live_api_helper_overlay_command(release_id=rid, expected_sha256=expected["sha256"], staging_file=staging_file, mode="acquire-lock")),
            operation="lock acquisition",
        )
        lock_acquired = True
        _live_api_helper_remote_ok(
            remote.run(_live_api_helper_overlay_command(release_id=rid, expected_sha256=expected["sha256"], staging_file=staging_file, mode="prepare")),
            operation="staging setup",
        )
        _live_api_helper_remote_ok(
            remote.run(
                _live_api_helper_overlay_command(
                    release_id=rid,
                    expected_sha256=expected["sha256"],
                    staging_file=staging_file,
                    mode="stage",
                    staged_data_b64=base64.b64encode(data).decode("ascii"),
                )
            ),
            operation="descriptor-confined staging",
        )
        result = _live_api_helper_remote_json(remote, _live_api_helper_overlay_command(release_id=rid, expected_sha256=expected["sha256"], staging_file=staging_file))
        if result.get("ok") is not True or result.get("sha256") != expected["sha256"] or result.get("destination") != LIVE_API_HELPER_OVERLAY_DESTINATION:
            raise ReleaseError("live-api-helper-overlay remote verification failed")
        return result | {"source_snapshot_id": source_capture["snapshot_id"], "source_snapshot_manifest_sha256": source_capture["manifest_sha256"]}
    finally:
        if lock_acquired:
            cleanup_proc = remote.run(
                _live_api_helper_overlay_command(release_id=rid, expected_sha256=expected["sha256"], staging_file=staging_file, mode="cleanup")
            )
            if cleanup_proc.returncode != 0:
                cleanup_error = "live API helper overlay lock cleanup failed"
        if cleanup_error:
            raise ReleaseError(cleanup_error)


def _default_test_release_out_dir(release_id: str) -> Path:
    return Path("/tmp/opencode") / f"nmbot-test-release-{_release_id(release_id)}"


def _validate_overlay_path_list(overlays: list[str]) -> list[str]:
    if not overlays:
        raise ReleaseError("test-release requires at least one --overlay")
    safe_paths: list[str] = []
    seen: set[str] = set()
    for raw in overlays:
        rel = _manifest_path(raw)
        if rel in seen:
            raise ReleaseError(f"duplicate overlay path: {rel}")
        seen.add(rel)
        path = ROOT / rel
        if _is_excluded(rel, path) or (not _is_allowed_runtime_file(rel) and not _is_allowed_test_api_overlay_file(rel)):
            raise ReleaseError(f"overlay path is outside safe runtime policy: {rel}")
        if path.is_symlink() or not path.is_file():
            raise ReleaseError(f"overlay source must already be a safe regular file: {rel}")
        _reject_secret_like(path, rel)
        safe_paths.append(rel)
    return sorted(safe_paths)


def _select_auto_test_release_overlay_paths(*, snapshot_dir: Path) -> list[str]:
    """Select only safe local additions and changes from this run's TEST snapshot."""
    comparison = compare_snapshot(snapshot_dir=snapshot_dir, project_root=ROOT, contour=DEFAULT_SNAPSHOT_CONTOUR)
    if comparison["missing"]:
        raise ReleaseError("test-release auto-overlays refuses local paths missing from the project")
    selected = [item["path"] for item in [*comparison["added"], *comparison["changed"]]]
    if not selected:
        raise ReleaseError("test-release auto-overlays found no local runtime changes")
    return _validate_overlay_path_list(selected)


def _read_overlay_source_no_follow(rel: str) -> tuple[bytes, dict[str, Any]]:
    safe = _manifest_path(rel)
    path = ROOT / safe
    if _is_excluded(safe, path) or (not _is_allowed_runtime_file(safe) and not _is_allowed_test_api_overlay_file(safe)):
        raise ReleaseError(f"overlay path is outside safe runtime policy: {safe}")
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(f"overlay source must already be a safe regular file: {safe}")
    _reject_secret_like(path, safe)
    parts = PurePosixPath(safe).parts
    root_fd = os.open(ROOT.resolve(strict=True), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fds = [root_fd]
    try:
        dir_fd = root_fd
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ReleaseError(f"overlay source directory component invalid: {safe}")
            fds.append(fd)
            dir_fd = fd
        fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        fds.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
            raise ReleaseError(f"overlay source is not a safe regular file: {safe}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ReleaseError(f"overlay source file size exceeds limit: {safe}")
            chunks.append(chunk)
        data = b"".join(chunks)
        st2 = os.fstat(fd)
        if st2.st_dev != st.st_dev or st2.st_ino != st.st_ino or st2.st_size != len(data):
            raise ReleaseError(f"overlay source changed during read: {safe}")
        mode = 0o755 if safe.startswith("scripts/") and safe.endswith(".py") else 0o644
        expected = {"path": safe, "sha256": _sha256_bytes(data), "size": len(data), "mode": mode}
        verified, verified_mode = _read_file_openat_no_follow(ROOT, safe, expected, test_api_overlay_paths=frozenset({safe}) if _is_allowed_test_api_overlay_file(safe) else frozenset())
        if verified != data or verified_mode != mode:
            raise ReleaseError(f"overlay source verification mismatch: {safe}")
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass
    if (SECRET_NAME_RE.search(PurePosixPath(safe).name) and not _is_name_allowed_for_test_api_overlay(safe)) or _has_secret_like_content(data.decode("utf-8", errors="ignore"), python_source=safe.endswith(".py")):
        raise ReleaseError(f"secret-like content rejected: {safe}")
    return data, expected


def _replace_overlay_dest_no_follow(*, source_dir: Path, rel: str, data: bytes, expected: dict[str, Any], must_exist: bool = True) -> None:
    safe = _manifest_path(rel)
    if expected["path"] != safe or len(data) != expected["size"] or _sha256_bytes(data) != expected["sha256"]:
        raise ReleaseError(f"overlay source payload mismatch: {safe}")
    mode = expected["mode"]
    if mode != (0o755 if safe.startswith("scripts/") and safe.endswith(".py") else 0o644):
        raise ReleaseError(f"overlay destination mode invalid: {safe}")
    parts = PurePosixPath(safe).parts
    root_fd = os.open(source_dir.resolve(strict=True), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    fds = [root_fd]
    tmp_name = f".{parts[-1]}.test-release.{os.getpid()}.{time.time_ns()}.tmp"
    parent_fd: int | None = None
    tmp_created = False
    fd: int | None = None

    def publish_verified_add_fd(*, verified_fd: int, expected_identity: tuple[int, int]) -> None:
        if parent_fd is None:
            raise ReleaseError(f"overlay destination publish state invalid after add: {safe}")
        fd_st = os.fstat(verified_fd)
        if not stat.S_ISREG(fd_st.st_mode) or (fd_st.st_dev, fd_st.st_ino) != expected_identity:
            raise ReleaseError(f"overlay destination verified fd identity invalid before add publish: {safe}")
        proc_fd_path = f"/proc/self/fd/{verified_fd}"
        try:
            proc_st = os.stat(proc_fd_path, follow_symlinks=True)
        except OSError as exc:
            raise ReleaseError(f"overlay destination proc fd unavailable before add publish: {safe}") from exc
        if not stat.S_ISREG(proc_st.st_mode) or (proc_st.st_dev, proc_st.st_ino) != expected_identity:
            raise ReleaseError(f"overlay destination proc fd identity mismatch before add publish: {safe}")
        try:
            os.link(proc_fd_path, parts[-1], dst_dir_fd=parent_fd, follow_symlinks=True)
        except FileExistsError as exc:
            raise ReleaseError(f"overlay destination already exists for add: {safe}") from exc
        except OSError as exc:
            raise ReleaseError(f"overlay destination publish failed after add: {safe}") from exc

    def verify_final_fd_matches_expected(*, fd: int, phase: str, expected_identity: tuple[int, int] | None = None) -> None:
        final_st = os.fstat(fd)
        if expected_identity is not None and (final_st.st_dev, final_st.st_ino) != expected_identity:
            raise ReleaseError(f"overlay destination identity mismatch after {phase}: {safe}")
        if not stat.S_ISREG(final_st.st_mode) or final_st.st_size != expected["size"] or (final_st.st_mode & 0o777) != mode:
            raise ReleaseError(f"overlay destination metadata mismatch after {phase}: {safe}")
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(fd, 1024 * 1024)
            except OSError as exc:
                raise ReleaseError(f"overlay destination read failed after {phase}: {safe}") from exc
            if not chunk:
                break
            chunks.append(chunk)
        if _sha256_bytes(b"".join(chunks)) != expected["sha256"]:
            raise ReleaseError(f"overlay destination hash mismatch after {phase}: {safe}")

    try:
        dir_fd = root_fd
        for part in parts[:-1]:
            try:
                fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            except FileNotFoundError:
                if must_exist:
                    raise ReleaseError(f"overlay destination directory component missing: {safe}")
                try:
                    os.mkdir(part, 0o755, dir_fd=dir_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ReleaseError(f"overlay destination directory component invalid: {safe}") from exc
                try:
                    os.fsync(dir_fd)
                except OSError:
                    pass
                try:
                    fd = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
                except OSError as exc:
                    raise ReleaseError(f"overlay destination directory component invalid: {safe}") from exc
            except OSError as exc:
                raise ReleaseError(f"overlay destination directory component invalid: {safe}") from exc
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ReleaseError(f"overlay destination directory component invalid: {safe}")
            fds.append(fd)
            dir_fd = fd
        parent_fd = dir_fd
        try:
            dest_st = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            if must_exist:
                raise ReleaseError(f"overlay destination missing: {safe}") from exc
            dest_st = None
        if dest_st is not None:
            if must_exist:
                if not stat.S_ISREG(dest_st.st_mode):
                    raise ReleaseError(f"overlay destination is not regular: {safe}")
            else:
                raise ReleaseError(f"overlay destination already exists for add: {safe}")
        if must_exist:
            fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent_fd)
            tmp_created = True
        else:
            o_tmpfile = getattr(os, "O_TMPFILE", None)
            if o_tmpfile is None:
                raise ReleaseError(f"overlay destination unnamed temp unavailable for add: {safe}")
            add_flags = os.O_RDWR | o_tmpfile | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(".", add_flags, mode, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno in {errno.EOPNOTSUPP, errno.EISDIR, errno.EINVAL, errno.ENOENT, errno.ENOTSUP}:
                    raise ReleaseError(f"overlay destination unnamed temp unavailable for add: {safe}") from exc
                raise ReleaseError(f"overlay destination unnamed temp open failed for add: {safe}") from exc
            add_st = os.fstat(fd)
            if not stat.S_ISREG(add_st.st_mode):
                raise ReleaseError(f"overlay destination metadata mismatch after add: {safe}")
            add_identity = (add_st.st_dev, add_st.st_ino)
        try:
            view = memoryview(data)
            written = 0
            while written < len(view):
                try:
                    count = os.write(fd, view[written:])
                except OSError as exc:
                    raise ReleaseError(f"overlay destination write failed: {safe}") from exc
                if count <= 0:
                    raise ReleaseError(f"overlay destination short write: {safe}")
                written += count
            try:
                os.fchmod(fd, mode)
            except OSError as exc:
                raise ReleaseError(f"overlay destination chmod failed: {safe}") from exc
            try:
                os.fsync(fd)
            except OSError as exc:
                raise ReleaseError(f"overlay destination fsync failed: {safe}") from exc
        finally:
            if must_exist:
                os.close(fd)
                fd = None
        if must_exist:
            os.replace(tmp_name, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            tmp_created = False
            verify_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
            try:
                verify_final_fd_matches_expected(fd=verify_fd, phase="replace")
            finally:
                os.close(verify_fd)
        else:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                verify_final_fd_matches_expected(fd=fd, phase="add", expected_identity=add_identity)
                publish_verified_add_fd(verified_fd=fd, expected_identity=add_identity)
                final_fd = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
                try:
                    verify_final_fd_matches_expected(fd=final_fd, phase="add publish", expected_identity=add_identity)
                finally:
                    os.close(final_fd)
            finally:
                os.close(fd)
                fd = None
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            if not must_exist:
                raise ReleaseError(f"overlay destination parent fsync failed after add: {safe}") from exc
    except Exception:
        if not must_exist and fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    finally:
        if tmp_created and parent_fd is not None:
            try:
                os.unlink(tmp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _worktree_all_runtime_rows(source_dir: Path, *, test_api_overlay_paths: set[str] | frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*"), key=lambda p: p.relative_to(source_dir).as_posix()):
        rel = path.relative_to(source_dir).as_posix()
        if path.is_symlink() or (os.path.lexists(path) and not path.is_file() and not path.is_dir()):
            raise ReleaseError(f"unsafe worktree node: {rel}")
        if path.is_dir():
            continue
        safe = _manifest_path(rel)
        if _is_excluded(safe, path) or not _is_allowed_runtime_file_for_policy(safe, test_api_overlay_paths=test_api_overlay_paths):
            raise ReleaseError(f"unexpected worktree file: {safe}")
        _reject_secret_like(path, safe)
        st = path.stat()
        rows.append({"path": safe, "sha256": _sha256_file(path), "size": st.st_size, "mode": 0o755 if safe.startswith("scripts/") and safe.endswith(".py") else 0o644})
    _validate_size_limits(rows, label="test-release worktree")
    return rows


def _diff_rows_against_baseline(*, baseline_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = {item["path"]: item for item in baseline_rows}
    current = {item["path"]: item for item in current_rows}
    added = sorted(({"path": path, "worktree_sha256": current[path]["sha256"]} for path in set(current) - set(baseline)), key=lambda item: item["path"])
    missing = sorted(({"path": path, "snapshot_sha256": baseline[path]["sha256"]} for path in set(baseline) - set(current)), key=lambda item: item["path"])
    changed = sorted(({"path": path, "snapshot_sha256": baseline[path]["sha256"], "worktree_sha256": current[path]["sha256"]} for path in set(baseline) & set(current) if baseline[path]["sha256"] != current[path]["sha256"]), key=lambda item: item["path"])
    return {"schema": "nmbot.test_release_overlay_diff.v1", "added": added, "missing": missing, "changed": changed}


def apply_test_release_overlay(*, worktree_dir: Path, overlays: list[str]) -> dict[str, Any]:
    overlay_paths = _validate_overlay_path_list(overlays)
    verified = verify_prepared_worktree(worktree_dir)
    provenance = verified["provenance"]
    if verified["source_tree_sha256"] != provenance["source_tree_sha256"] or verified["source_manifest_sha256"] != provenance["source_manifest_sha256"]:
        raise ReleaseError("prepared worktree does not match provenance before overlay")
    if provenance["contour"] != DEFAULT_SNAPSHOT_CONTOUR or provenance["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("test-release worktree provenance is not TEST")
    baseline_paths = {item["path"] for item in verified["rows"]}
    source = Path(verified["source_dir"])
    expected_by_path: dict[str, dict[str, Any]] = {}
    for rel in overlay_paths:
        data, expected = _read_overlay_source_no_follow(rel)
        expected_by_path[rel] = expected
        _replace_overlay_dest_no_follow(source_dir=source, rel=rel, data=data, expected=expected, must_exist=rel in baseline_paths)
    test_api_overlay_paths = frozenset(rel for rel in overlay_paths if _is_allowed_test_api_overlay_file(rel))
    current_rows = _worktree_all_runtime_rows(source, test_api_overlay_paths=test_api_overlay_paths)
    current_by_path = {item["path"]: item for item in current_rows}
    diff = _diff_rows_against_baseline(baseline_rows=verified["rows"], current_rows=current_rows)
    added_paths = [item["path"] for item in diff["added"]]
    changed_paths = [item["path"] for item in diff["changed"]]
    if diff["missing"] or sorted(added_paths + changed_paths) != overlay_paths:
        raise ReleaseError("test-release exact diff mismatch; overlays must equal actual added and changed paths")
    for rel in overlay_paths:
        expected = expected_by_path[rel]
        current = current_by_path.get(rel)
        if current is None or current["sha256"] != expected["sha256"] or current["mode"] != expected["mode"] or current["size"] != expected["size"]:
            raise ReleaseError(f"test-release overlay final hash/mode/size mismatch: {rel}")
    return {**diff, "overlay_paths": overlay_paths, "source_tree_sha256": _tree_hash_from_records(current_rows), "source_manifest_sha256": _source_manifest_sha(current_rows)}


def _assert_test_release_recon(*, recon_data: dict[str, Any], release_id: str) -> None:
    rid = _release_id(release_id)
    if recon_data["current"]["target_name"] != rid:
        raise ReleaseError("post-deploy recon current target does not match release id")
    health = recon_data["health"]
    if health["reachable"] is not True or health["ok"] is not True:
        raise ReleaseError("post-deploy health is not reachable/ok")
    identity = recon_data["identity"]
    if identity["exists"] is not True or identity["schema_ok"] is not True or identity["release_id_present"] is not True or identity["tracked_hashes_shape_ok"] is not True:
        raise ReleaseError("post-deploy identity guards failed")
    if identity["release_id"] != rid:
        raise ReleaseError("post-deploy identity release_id does not match deployed release")
    systemd = recon_data["systemd"]
    required_true = ("show_ok", "working_directory_is_canonical", "execstart_mentions_current_api", "environment_file_canonical", "exec_start_pre_empty")
    if any(systemd[key] is not True for key in required_true) or systemd["has_environment_inline"] is not False:
        raise ReleaseError("post-deploy canonical systemd guards failed")


def _test_release_with_remote(*, release_id: str, overlays: list[str], auto_overlays: bool = False, out_dir: Path | None, confirm: bool, remote: Any, host: str = AUTHORIZED_DEPLOY_HOST, port: str = AUTHORIZED_DEPLOY_PORT) -> dict[str, Any]:
    rid = _release_id(release_id)
    _validate_test_release_target(host=host, port=port, confirm=confirm)
    if auto_overlays and overlays:
        raise ReleaseError("test-release --auto-overlays cannot be combined with --overlay")
    if not auto_overlays:
        overlay_paths = _validate_overlay_path_list(overlays)
    run_dir = _allowed_bootstrap_out_dir(out_dir or _default_test_release_out_dir(rid))
    if os.path.lexists(run_dir):
        raise ReleaseError("test-release run directory already exists; refusing to overwrite immutable run")
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = snapshot_vps_source(remote=remote, out_dir=run_dir / "snapshots", keep_tar=True, contour=DEFAULT_SNAPSHOT_CONTOUR)
    if snapshot["contour"] != DEFAULT_SNAPSHOT_CONTOUR or snapshot["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("test-release snapshot is not TEST")
    if auto_overlays:
        overlay_paths = _select_auto_test_release_overlay_paths(snapshot_dir=Path(snapshot["snapshot_dir"]))
    worktree = prepare_worktree(snapshot_dir=Path(snapshot["snapshot_dir"]), out_dir=run_dir / "worktrees")
    diff = apply_test_release_overlay(worktree_dir=Path(worktree["worktree_dir"]), overlays=overlay_paths)
    artifact = build_from_worktree(worktree_dir=Path(worktree["worktree_dir"]), release_id=rid, out_dir=run_dir / "artifacts", test_api_overlay_paths=frozenset(rel for rel in overlay_paths if _is_allowed_test_api_overlay_file(rel)))
    preflight_status = local_preflight(archive=artifact.archive, manifest_path=artifact.manifest).strip()
    deploy_status = deploy(release_id=rid, archive=artifact.archive, manifest_path=artifact.manifest, confirm=confirm, remote=remote, remote_root=DEFAULT_REMOTE_ROOT, source_snapshot_manifest_sha256=snapshot["manifest_sha256"]).strip()
    recon_data = recon(remote)
    _assert_test_release_recon(recon_data=recon_data, release_id=rid)
    return {
        "schema": "nmbot.test_release_result.v1",
        "release_id": rid,
        "overlay_mode": "auto" if auto_overlays else "manual",
        "selected_overlay_paths": diff["overlay_paths"],
        "snapshot": {"id": snapshot["snapshot_id"], "manifest": snapshot["manifest"], "manifest_sha256": snapshot["manifest_sha256"], "dir": snapshot["snapshot_dir"]},
        "worktree_dir": worktree["worktree_dir"],
        "diff": {"added": diff["added"], "missing": diff["missing"], "changed": diff["changed"], "overlay_paths": diff["overlay_paths"]},
        "artifact": {"archive": str(artifact.archive), "archive_sha256": artifact.manifest_data["archive_sha256"], "manifest": str(artifact.manifest), "manifest_sha256": _sha256_file(artifact.manifest)},
        "preflight": {"status": preflight_status},
        "deploy": {"status": deploy_status},
        "current_target": recon_data["current"]["target_name"],
        "health": {"reachable": recon_data["health"]["reachable"], "ok": recon_data["health"]["ok"]},
        "identity": recon_data["identity"],
        "systemd": recon_data["systemd"],
    }


def test_release(*, release_id: str, overlays: list[str], auto_overlays: bool = False, out_dir: Path | None, confirm: bool, host: str = AUTHORIZED_DEPLOY_HOST, port: str = AUTHORIZED_DEPLOY_PORT) -> dict[str, Any]:
    _validate_test_release_target(host=host, port=port, confirm=confirm)
    _release_id(release_id)
    if auto_overlays and overlays:
        raise ReleaseError("test-release --auto-overlays cannot be combined with --overlay")
    if not auto_overlays:
        _validate_overlay_path_list(overlays)
    _allowed_bootstrap_out_dir(out_dir or _default_test_release_out_dir(release_id))
    remote = SshRemote(host=host, port=port)
    return _test_release_with_remote(release_id=release_id, overlays=overlays, auto_overlays=auto_overlays, out_dir=out_dir, confirm=confirm, remote=remote, host=host, port=port)


def _validate_contract_capture_paths(paths: list[str]) -> list[str]:
    expected = _contract_capture_paths(ROOT)
    if paths != expected or len(paths) != len(set(paths)):
        raise ReleaseError("capture paths must exactly match the sorted release contract")
    for rel in paths:
        safe = _manifest_path(rel)
        if safe != rel or _is_excluded(rel, Path(rel)) or not _is_allowed_runtime_file(rel):
            raise ReleaseError("capture paths must exactly match safe runtime files")
    return list(paths)


def _safe_text_command_error(proc: subprocess.CompletedProcess[str], label: str) -> str:
    stderr = _safe_stderr_detail((proc.stderr or "").encode("utf-8", errors="replace"))
    return f"{label} with exit={proc.returncode}" + (f"; stderr={stderr}" if stderr else "")


def _expect_exact_keys(obj: Any, keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ReleaseError(f"remote recon {name} schema invalid")
    return obj


def _expect_bool_map(obj: Any, keys: set[str], name: str) -> None:
    data = _expect_exact_keys(obj, keys, name)
    if not all(isinstance(value, bool) for value in data.values()):
        raise ReleaseError(f"remote recon {name} types invalid")


def _validate_recon_payload(data: Any) -> None:
    top = _expect_exact_keys(data, {"ok", "remote_root", "paths", "systemd", "env_names", "canonical_api", "modes", "current", "identity", "health"}, "top-level")
    if top["ok"] is not True or top["remote_root"] != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("remote recon root/status invalid")
    paths = _expect_exact_keys(top["paths"], {"current", "env_file", "identity_file"}, "paths")
    expected_paths = {
        "current": f"{DEFAULT_REMOTE_ROOT}/current",
        "env_file": f"{DEFAULT_REMOTE_ROOT}/.env",
        "identity_file": f"{DEFAULT_REMOTE_ROOT}/{IDENTITY_EXTERNAL}",
    }
    if paths != expected_paths:
        raise ReleaseError("remote recon paths mismatch")
    env_required = set(CONFIG_REQUIREMENTS["required_secret_names"]) | set(CONFIG_REQUIREMENTS["required_setting_names"]) | set(CONFIG_REQUIREMENTS["required_mode_names"])
    env_names = _expect_exact_keys(top["env_names"], env_required, "env_names")
    for row in env_names.values():
        item = _expect_exact_keys(row, {"present", "nonempty"}, "env_names row")
        if not isinstance(item["present"], bool) or not isinstance(item["nonempty"], bool):
            raise ReleaseError("remote recon env_names types invalid")
    _expect_bool_map(top["canonical_api"], set(CANONICAL_API_ENV_VALUES), "canonical_api")
    _expect_bool_map(top["modes"], {"NMBOT_V2_MANAGER_REWRITER_MODE", "NMBOT_V3_MANAGER_REWRITER_MODE"}, "modes")
    systemd = _expect_exact_keys(top["systemd"], {"show_ok", "working_directory_is_canonical", "execstart_mentions_current_api", "environment_file_canonical", "exec_start_pre_empty", "has_environment_inline"}, "systemd")
    if not all(isinstance(value, bool) for value in systemd.values()):
        raise ReleaseError("remote recon systemd types invalid")
    current = _expect_exact_keys(top["current"], {"is_symlink", "target_name"}, "current")
    if not isinstance(current["is_symlink"], bool) or not isinstance(current["target_name"], str):
        raise ReleaseError("remote recon current types invalid")
    if current["target_name"] and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", current["target_name"]):
        raise ReleaseError("remote recon current target invalid")
    identity = _expect_exact_keys(top["identity"], {"exists", "schema_ok", "release_id_present", "release_id", "tracked_hashes_shape_ok"}, "identity")
    bool_keys = {"exists", "schema_ok", "release_id_present", "tracked_hashes_shape_ok"}
    if not all(isinstance(identity[key], bool) for key in bool_keys) or not isinstance(identity["release_id"], str):
        raise ReleaseError("remote recon identity types invalid")
    if identity["release_id"]:
        _release_id(identity["release_id"])
    health = _expect_exact_keys(top["health"], {"reachable", "ok", "jivo_token_configured", "api_token_configured"}, "health")
    if not all(isinstance(value, bool) for value in health.values()):
        raise ReleaseError("remote recon health types invalid")


def _readonly_recon_command(remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    if remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("recon remote root is fixed")
    payload = json.dumps({
        "root": DEFAULT_REMOTE_ROOT,
        "service": API_SERVICE,
        "health_url": API_HEALTH_URL,
        "identity": IDENTITY_EXTERNAL,
        "required": CONFIG_REQUIREMENTS,
        "canonical_api": CANONICAL_API_ENV_VALUES,
        "required_modes": {"NMBOT_V2_MANAGER_REWRITER_MODE": "off", "NMBOT_V3_MANAGER_REWRITER_MODE": "publish"},
    }, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, re, shlex, subprocess, sys, urllib.request
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve(); current=root/"current"; env=root/".env"; identity=root/cfg["identity"]
def parse_env(path):
    out={}
    if not path.is_file() or path.is_symlink(): return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1); key=k.strip().removeprefix("export ").strip()
        try: parsed=shlex.split(v, comments=False, posix=True)[0] if v.strip() else ""
        except Exception: parsed=v.strip().strip("'\"")
        out[key]=parsed
    return out
def unit_show():
    p=subprocess.run(["systemctl","--user","show",cfg["service"],"--no-pager"], text=True, capture_output=True, timeout=10)
    data={}
    for line in p.stdout.splitlines():
        if "=" in line:
            k,v=line.split("=",1); data[k]=v
    return {"ok": p.returncode == 0, "working_directory": data.get("WorkingDirectory",""), "exec_start": data.get("ExecStart",""), "environment_files": data.get("EnvironmentFiles", data.get("EnvironmentFile", "")), "exec_start_pre_empty": not data.get("ExecStartPre", "").strip(), "has_environment_inline": bool(data.get("Environment", "").strip())}
envv=parse_env(env); required=set(cfg["required"]["required_secret_names"])|set(cfg["required"]["required_setting_names"])|set(cfg["required"]["required_mode_names"])
env_report={name: {"present": name in envv, "nonempty": bool(str(envv.get(name,"")).strip())} for name in sorted(required)}
canonical={k: envv.get(k)==v for k,v in cfg["canonical_api"].items()}
modes={k: envv.get(k)==v for k,v in cfg["required_modes"].items()}
identity_ok=False; identity_release_id=""; identity_hash_ok=False
if identity.is_file() and not identity.is_symlink():
    try:
        data=json.loads(identity.read_text(encoding="utf-8")); identity_release_id=str(data.get("release_id", "")); identity_ok=data.get("schema")=="nmbot.release_identity.v1" and bool(identity_release_id)
        identity_hash_ok=all(isinstance(i,dict) and isinstance(i.get("sha256"),str) and len(i.get("sha256"))==64 for i in data.get("tracked_files", []))
    except Exception: pass
health={"reachable": False, "ok": False, "jivo_token_configured": False, "api_token_configured": False}
try:
    h=json.loads(urllib.request.urlopen(cfg["health_url"], timeout=3).read().decode())
    health={"reachable": True, "ok": h.get("ok") is True, "jivo_token_configured": h.get("jivo_token_configured") is True, "api_token_configured": h.get("api_token_configured") is True}
except Exception: pass
u=unit_show(); print(json.dumps({"ok": True, "remote_root": str(root), "paths": {"current": str(current), "env_file": str(env), "identity_file": str(identity)}, "systemd": {"show_ok": u["ok"], "working_directory_is_canonical": u["working_directory"]==str(current), "execstart_mentions_current_api": str(current/"scripts/nmbot_api_server.py") in u["exec_start"], "environment_file_canonical": str(env) in u["environment_files"] and ".env.client-production" not in u["environment_files"], "exec_start_pre_empty": u["exec_start_pre_empty"], "has_environment_inline": u["has_environment_inline"]}, "env_names": env_report, "canonical_api": canonical, "modes": modes, "current": {"is_symlink": current.is_symlink(), "target_name": current.resolve().name if current.is_symlink() else ""}, "identity": {"exists": identity.is_file() and not identity.is_symlink(), "schema_ok": identity_ok, "release_id_present": bool(identity_release_id), "release_id": identity_release_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", identity_release_id) else "", "tracked_hashes_shape_ok": identity_hash_ok}, "health": health}, sort_keys=True))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def recon(remote: Remote) -> dict[str, Any]:
    proc = remote.run(_readonly_recon_command(DEFAULT_REMOTE_ROOT))
    if proc.returncode != 0:
        raise ReleaseError(_safe_text_command_error(proc, "remote recon failed"))
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as exc:
        raise ReleaseError("remote recon returned invalid JSON") from exc
    _validate_recon_payload(data)
    return data


def _capture_baseline_command(paths: list[str], remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    if remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("capture remote root is fixed")
    safe_paths = _validate_contract_capture_paths(paths)
    contract_literal = json.dumps(safe_paths, sort_keys=True)
    payload = json.dumps({"root": DEFAULT_REMOTE_ROOT, "paths": safe_paths, "expected": safe_paths}, sort_keys=True)
    code = ("CONTRACT=" + repr(contract_literal) + r'''
import json, os, pathlib, stat, sys, tarfile
cfg=json.loads(sys.argv[1]); configured_root=cfg.get("root"); root=pathlib.Path(configured_root).resolve(); paths=cfg.get("paths"); expected=cfg.get("expected")
def fail(msg): print(json.dumps({"ok": False, "error": msg}), file=sys.stderr); sys.exit(2)
if not isinstance(configured_root, str) or not configured_root.startswith("/"): fail("capture root is fixed")
contract=json.loads(CONTRACT)
if not isinstance(paths, list) or not isinstance(expected, list) or paths != expected or paths != contract or paths != sorted(paths) or len(paths) != len(set(paths)): fail("capture paths are not the exact contract set")
def inside(p):
    try: return p.resolve().is_relative_to(root)
    except AttributeError: return str(p.resolve()).startswith(str(root)+os.sep) or p.resolve()==root
validated=[]
for rel in paths:
    if not isinstance(rel, str): fail("unsafe requested path")
    pp=pathlib.PurePosixPath(rel)
    if not str(pp) or pp.is_absolute() or ".." in pp.parts: fail("unsafe requested path")
    if rel.startswith(".env") or "/.env" in rel or any(part.startswith(".") for part in pp.parts): fail("unsafe requested path")
    p=(root/rel)
    if not inside(p): fail("requested path outside root")
    if p.is_symlink() or not p.is_file(): fail("requested path is not safe regular file: "+rel)
    st=p.stat()
    if not stat.S_ISREG(st.st_mode): fail("requested path is not regular: "+rel)
    validated.append((rel,p))
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
    for rel,p in validated:
        info=tf.gettarinfo(str(p), arcname=rel)
        if not info.isfile() or info.isdir() or info.issym() or info.islnk() or info.isdev(): fail("unsafe tar source: "+rel)
        info.uid=info.gid=0; info.uname=info.gname=""; info.mtime=0; info.mode=0o755 if rel.startswith("scripts/") and rel.endswith(".py") else 0o644
        with p.open("rb") as fh: tf.addfile(info, fh)
''')
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _extract_capture_tar(payload: bytes, dest: Path, expected_paths: list[str]) -> None:
    expected = _validate_contract_capture_paths(expected_paths)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tf:
        members = tf.getmembers()
        names = [member.name for member in members]
        safe_names = [_safe_rel(name) for name in names]
        if safe_names != expected or len(safe_names) != len(set(safe_names)):
            raise ReleaseError("captured tar member set does not exactly match expected paths")
        for member in members:
            rel = _safe_rel(member.name)
            if SECRET_NAME_RE.search(PurePosixPath(rel).name):
                raise ReleaseError(f"secret-like filename rejected: {rel}")
            if not member.isfile() or member.isdir() or member.issym() or member.islnk() or member.isdev():
                raise ReleaseError(f"unsafe captured member: {member.name}")
            if not _is_allowed_runtime_file(rel):
                raise ReleaseError(f"unexpected captured member: {member.name}")
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                raise ReleaseError(f"captured tar path traversal: {member.name}")
        tf.extractall(dest, filter="data")
    for path in sorted(dest.rglob("*")):
        rel = path.relative_to(dest).as_posix()
        if path.is_symlink() or (path.exists() and not path.is_dir() and not path.is_file()):
            raise ReleaseError(f"unsafe captured node: {rel}")
        if path.is_file():
            _reject_secret_like(path, rel)


def capture_baseline(*, remote: BinaryRemote, out_dir: Path = DEFAULT_BOOTSTRAP_OUT_DIR, release_id: str = "baseline-capture") -> Artifact:
    out = _allowed_bootstrap_out_dir(out_dir)
    rid = _release_id(release_id)
    paths = _contract_capture_paths(ROOT)
    _validate_contract_capture_paths(paths)
    final_release_dir = out / rid
    proc = remote.run_binary(_capture_baseline_command(paths))
    if proc.returncode != 0:
        raise ReleaseError(_sanitized_binary_error(proc))
    staging = _make_private_staging_dir(out)
    published = False
    try:
        captured = staging / "captured"
        staging_release_dir = staging / rid
        _extract_capture_tar(proc.stdout, captured, paths)
        artifact = build(release_id=rid, out_dir=staging_release_dir, root=captured)
        local_preflight(archive=artifact.archive, manifest_path=artifact.manifest)
        _rename_noreplace(staging_release_dir, final_release_dir)
        published = True
        return Artifact(
            archive=final_release_dir / artifact.archive.name,
            manifest=final_release_dir / artifact.manifest.name,
            manifest_data=artifact.manifest_data,
        )
    except Exception as exc:
        if published:
            raise
        if isinstance(exc, ReleaseError):
            raise
        raise ReleaseError("atomic local release directory publish failed; no artifact pair was published") from exc
    finally:
        try:
            _cleanup_private_staging(staging, out)
        except Exception:
            if not published:
                raise


def _load_verified_artifact(*, archive: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    verify_archive_against_manifest(archive, manifest)
    return manifest


def _render_bootstrap_unit() -> str:
    return "\n".join([
        "[Unit]",
        "Description=Novostroy Bot API",
        "After=network.target",
        "",
        "[Service]",
        "WorkingDirectory=/home/neiro/novostroy-bot/current",
        "EnvironmentFile=/home/neiro/novostroy-bot/.env",
        "ExecStart=/usr/bin/python3 /home/neiro/novostroy-bot/current/scripts/nmbot_api_server.py",
        "Restart=always",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]) + "\n"


def _render_bootstrap_env_additions() -> str:
    return "\n".join([
        "NMBOT_CONTOUR_PROFILE=api_production",
        f"NMBOT_RELEASE_IDENTITY_FILE={DEFAULT_REMOTE_ROOT}/{IDENTITY_EXTERNAL}",
        f"NMBOT_RUNTIME_VERSION_FILE={DEFAULT_REMOTE_ROOT}/{RUNTIME_VERSION_EXTERNAL}",
    ]) + "\n"


def _bootstrap_release_paths(remote_root: str, release_id: str) -> dict[str, str]:
    rid = _release_id(release_id)
    root = remote_root.rstrip("/")
    return {
        "root": root,
        "rid": rid,
        "staging": f"{root}/.release_staging/{rid}",
        "release_dir": f"{root}/releases/{rid}",
        "lock_dir": f"{root}/.release_lock",
        "backup_dir": f"{root}/backups/bootstrap-{rid}",
    }


def _bootstrap_preconditions_command(remote_root: str, release_id: str) -> str:
    paths = _bootstrap_release_paths(remote_root, release_id)
    payload = json.dumps({"root": paths["root"], "rid": paths["rid"], "release_dir": paths["release_dir"], "staging": paths["staging"], "lock": paths["lock_dir"], "backup": paths["backup_dir"], "service": API_SERVICE, "health_url": API_HEALTH_URL, "external": sorted(CONFIG_REQUIREMENTS["external_runtime_paths"])}, sort_keys=True)
    code = r'''
import json, os, pathlib, subprocess, sys, urllib.request
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve()
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
if str(root) != cfg["root"]: fail("remote root must be canonical absolute path")
for raw in (cfg["release_dir"], cfg["staging"], cfg["lock"], cfg["backup"]):
    if os.path.lexists(raw): fail("bootstrap path already exists: "+pathlib.PurePosixPath(raw).name)
for name in cfg["external"]:
    p=root/name
    if not p.exists() or p.is_symlink(): fail("external target missing or symlink: "+name)
    if name in ("data","logs","backups"):
        if not p.is_dir(): fail("external dir invalid: "+name)
    elif not p.is_file(): fail("external file invalid: "+name)
cur=root/"current"
current_state="absent"
if os.path.lexists(cur):
    if cur.is_symlink(): fail("current is already an atomic release symlink")
    fail("current path already exists; first migration requires absent current")
show=subprocess.run(["systemctl","--user","show",cfg["service"],"--no-pager"], text=True, capture_output=True, timeout=10)
if show.returncode != 0: fail("api unit show failed")
data={}
for line in show.stdout.splitlines():
    if "=" in line:
        k,v=line.split("=",1); data[k]=v
fragment=data.get("FragmentPath","")
home=os.path.expanduser("~")
expected=pathlib.Path(home)/".config"/"systemd"/"user"/cfg["service"]
if not fragment or pathlib.Path(fragment).resolve() != expected.resolve(): fail("api unit FragmentPath is outside expected user systemd location")
if "Environment=" in show.stdout:
    pass
try:
    health=json.loads(urllib.request.urlopen(cfg["health_url"], timeout=3).read().decode())
except Exception:
    fail("api health is not reachable")
if health.get("ok") is not True or health.get("jivo_token_configured") is not True or health.get("api_token_configured") is not True: fail("api health config proof failed")
print(json.dumps({"ok": True, "unit_path": str(expected), "current_state": current_state}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bootstrap_backup_command(remote_root: str, release_id: str, unit_path: str, state: dict[str, Any]) -> str:
    paths = _bootstrap_release_paths(remote_root, release_id)
    payload = json.dumps({"root": paths["root"], "rid": paths["rid"], "backup": paths["backup_dir"], "unit_path": unit_path, "state": {"current_state": state.get("current_state", "unknown")}, "identity": IDENTITY_EXTERNAL}, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, sys
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]); backup=pathlib.Path(cfg["backup"]); unit=pathlib.Path(cfg["unit_path"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
try: backup.mkdir(mode=0o700, parents=False, exist_ok=False)
except FileExistsError: fail("bootstrap backup already exists")
except FileNotFoundError: fail("bootstrap backup parent missing")
os.chmod(backup,0o700)
if not unit.is_file() or unit.is_symlink(): fail("api unit file is not a safe regular file")
shutil.copy2(unit, backup/"api-unit.service")
env=root/".env"
if not env.is_file() or env.is_symlink(): fail("root env file is not a safe regular file")
shutil.copy2(env, backup/"root.env")
identity=root/cfg["identity"]
if identity.exists():
    if not identity.is_file() or identity.is_symlink(): fail("external identity is not a safe regular file")
    shutil.copy2(identity, backup/"external-identity.json")
meta={"schema":"nmbot.bootstrap_backup.v1","release_id":cfg["rid"],"unit_path":str(unit),"identity_existed":identity.exists(),"state":cfg["state"]}
(backup/"metadata.json").write_text(json.dumps(meta, sort_keys=True, indent=2)+"\n", encoding="utf-8")
for p in backup.iterdir(): os.chmod(p,0o600)
print(json.dumps({"ok": True, "backup": str(backup)}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bootstrap_env_update_command(remote_root: str, release_id: str) -> str:
    paths = _bootstrap_release_paths(remote_root, release_id)
    additions = dict(line.split("=", 1) for line in _render_bootstrap_env_additions().splitlines() if line.strip())
    payload = json.dumps({"root": paths["root"], "backup": paths["backup_dir"], "add": additions}, sort_keys=True)
    code = r'''
import json, os, pathlib, sys, tempfile
cfg=json.loads(sys.argv[1]); env=pathlib.Path(cfg["root"])/".env"; backup=pathlib.Path(cfg["backup"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
if not (backup/"root.env").is_file(): fail("bootstrap env backup missing")
raw=env.read_bytes(); text=raw.decode("utf-8")
lines=text.splitlines(keepends=True); seen={}
for line in lines:
    s=line.strip()
    if not s or s.startswith("#") or "=" not in s: continue
    key=s.split("=",1)[0].replace("export ","").strip()
    value=s.split("=",1)[1].strip().strip("'\"")
    if key in cfg["add"]: seen[key]=value
for key, value in cfg["add"].items():
    if key in seen and seen[key] != value: fail("conflicting existing bootstrap env assignment: "+key)
append=""
for key, value in cfg["add"].items():
    if key not in seen: append += key+"="+value+"\n"
if append:
    new=raw + (b"" if raw.endswith(b"\n") else b"\n") + append.encode("utf-8")
    fd,tmp=tempfile.mkstemp(prefix=".env.bootstrap.", dir=str(env.parent)); os.close(fd)
    pathlib.Path(tmp).write_bytes(new); os.chmod(tmp,0o600); os.replace(tmp, env)
print(json.dumps({"ok": True, "updated_keys": sorted(cfg["add"])}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bootstrap_unit_replace_command(remote_root: str, unit_path: str) -> str:
    payload = json.dumps({"unit_path": unit_path, "content": _render_bootstrap_unit()}, sort_keys=True)
    code = r'''
import json, os, pathlib, sys, tempfile
cfg=json.loads(sys.argv[1]); unit=pathlib.Path(cfg["unit_path"])
fd,tmp=tempfile.mkstemp(prefix=unit.name+".bootstrap.", dir=str(unit.parent)); os.close(fd)
pathlib.Path(tmp).write_text(cfg["content"], encoding="utf-8"); os.chmod(tmp,0o600); os.replace(tmp, unit)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bootstrap_rollback_command(remote_root: str, release_id: str, unit_path: str) -> str:
    paths = _bootstrap_release_paths(remote_root, release_id)
    payload = json.dumps({"root": paths["root"], "rid": paths["rid"], "backup": paths["backup_dir"], "unit_path": unit_path, "identity": IDENTITY_EXTERNAL, "tmp_current": f"{paths['root']}/.current.{paths['rid']}.tmp"}, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, subprocess, sys, time
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]); backup=pathlib.Path(cfg["backup"]); unit=pathlib.Path(cfg["unit_path"])
def run(args):
    p=subprocess.run(args, text=True, capture_output=True, timeout=15)
    if p.returncode != 0: raise RuntimeError("command failed: "+" ".join(args))
def prove_inactive():
    deadline=time.time()+10; last=""
    while time.time()<deadline:
        p=subprocess.run(["systemctl","--user","is-active","novostroy-bot-api.service"], text=True, capture_output=True, timeout=3)
        last=(p.stdout+p.stderr).strip()
        if last in ("inactive","failed") or p.returncode != 0: return
        time.sleep(1)
    raise RuntimeError("api still active during bootstrap rollback: "+last)
run(["systemctl","--user","stop","novostroy-bot-api.service"])
prove_inactive()
shutil.copy2(backup/"api-unit.service", unit)
shutil.copy2(backup/"root.env", root/".env")
identity=root/cfg["identity"]
if (backup/"external-identity.json").is_file():
    shutil.copy2(backup/"external-identity.json", identity)
else:
    try: identity.unlink()
    except FileNotFoundError: pass
cur=root/"current"
if cur.is_symlink() and os.readlink(cur) == "releases/"+cfg["rid"]: cur.unlink()
tmp=pathlib.Path(cfg["tmp_current"])
if tmp.is_symlink() and os.readlink(tmp) == "releases/"+cfg["rid"]: tmp.unlink()
run(["systemctl","--user","daemon-reload"])
run(["systemctl","--user","start","novostroy-bot-api.service"])
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def bootstrap_plan(*, baseline_archive: Path, baseline_manifest: Path, candidate_archive: Path, candidate_manifest: Path, out_dir: Path) -> dict[str, Any]:
    out = _allowed_bootstrap_out_dir(out_dir)
    baseline = _load_verified_artifact(archive=baseline_archive, manifest_path=baseline_manifest)
    candidate = _load_verified_artifact(archive=candidate_archive, manifest_path=candidate_manifest)
    plan = {
        "schema": "nmbot.first_migration_bootstrap_plan.v1",
        "remote_writes_performed": False,
        "cutover_authorized": False,
        "remote_root": DEFAULT_REMOTE_ROOT,
        "baseline": {"release_id": baseline["release_id"], "archive": baseline_archive.name, "archive_sha256": baseline["archive_sha256"], "files": len(baseline["files"])},
        "candidate": {"release_id": candidate["release_id"], "archive": candidate_archive.name, "archive_sha256": candidate["archive_sha256"], "files": len(candidate["files"])},
        "steps": [
            "After separate approval, upload baseline and candidate artifacts to the VPS.",
            "Create immutable release directories for baseline and candidate without overwriting existing paths.",
            "Link manifest external runtime paths .env, data, logs and backups into immutable TEST API releases.",
            "Set current to the captured baseline release first, not the future candidate.",
            "Publish the baseline external release identity.",
            "Migrate the API systemd unit to WorkingDirectory current and canonical external env file.",
            "Health verify the baseline API and release identity.",
            "Only after baseline verification, schedule a separate future candidate deploy approval.",
        ],
        "rollback": [
            "Restore the previous API systemd unit file.",
            "Restore previous current path or symlink target.",
            "Restore previous external release identity file.",
            "Start API and verify previous health and identity before continuing.",
        ],
        "generated_files": {
            "plan_json": "nmbot_first_migration_bootstrap_plan.json",
            "systemd_unit_candidate": "novostroy-bot-api.service.candidate",
            "env_additions": "nmbot_api_env_additions.nonsecret.env",
        },
        "apply_script_generated": False,
    }
    _write_new_file(out / "nmbot_first_migration_bootstrap_plan.json", json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    _write_new_file(out / "novostroy-bot-api.service.candidate", _render_bootstrap_unit())
    _write_new_file(out / "nmbot_api_env_additions.nonsecret.env", _render_bootstrap_env_additions())
    return plan


def _remote_ok(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.returncode != 0:
        raise ReleaseError((proc.stdout + proc.stderr)[-2000:])


def _remote_json(remote: Remote, command: str) -> dict[str, Any]:
    proc = remote.run(command)
    _remote_ok(proc)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # pragma: no cover
        raise ReleaseError("remote returned invalid JSON") from exc


def _parse_systemctl_show(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _split_systemd_words(value: str) -> list[str]:
    if not value.strip():
        return []
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise ReleaseError("remote unit systemd field is not parseable") from exc


def _extract_systemd_argv(exec_start: str) -> list[str]:
    match = re.search(r"argv\[\]=([^;}]+)", exec_start)
    if not match:
        raise ReleaseError("remote unit ExecStart argv is missing")
    return _split_systemd_words(match.group(1).strip())


def _extract_systemd_path(exec_start: str) -> str:
    match = re.search(r"(?:^|[;{]\s*)path=([^\s;}]+)", exec_start)
    if not match:
        raise ReleaseError("remote unit ExecStart path is missing")
    return match.group(1).strip()


def _parse_environment_file_paths(value: str) -> list[str]:
    paths: list[str] = []
    for token in _split_systemd_words(value.replace(";", " ")):
        if token in {"-", "(ignore_errors=yes)", "(ignore_errors=no)"} or token.startswith("ignore_errors="):
            continue
        if token.startswith("-") and len(token) > 1:
            token = token[1:]
        if token.startswith("/"):
            paths.append(token)
    return paths


def _environment_assignment_names(value: str) -> set[str]:
    names: set[str] = set()
    for token in _split_systemd_words(value):
        if "=" not in token:
            continue
        key = token.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            names.add(key)
    return names


def _assert_remote_unit_migrated(remote: Remote, *, remote_root: str) -> None:
    current_root = f"{remote_root.rstrip('/')}/current"
    command = f"systemctl --user show {shlex.quote(API_SERVICE)} --no-pager"
    proc = remote.run(command)
    _remote_ok(proc)
    data = _parse_systemctl_show(proc.stdout)
    working_directory = data.get("WorkingDirectory", "")
    exec_start = data.get("ExecStart", "")
    env_files = data.get("EnvironmentFiles", data.get("EnvironmentFile", ""))
    environment = data.get("Environment", "")
    exec_start_pre = data.get("ExecStartPre", "")
    required_entrypoint = f"{current_root}/scripts/nmbot_api_server.py"
    if working_directory != current_root:
        raise ReleaseError("remote unit is not migrated for atomic releases: expected WorkingDirectory/current and API ExecStart under current")
    paths = _parse_environment_file_paths(env_files)
    env_file_assignments = sorted({token.split("=", 1)[0] for token in _split_systemd_words(env_files) if "=" in token} & set(SYSTEMD_ENV_OVERRIDE_DENY))
    if env_file_assignments:
        raise ReleaseError("remote unit EnvironmentFiles assignment is not allowed for fields: " + ",".join(env_file_assignments))
    allowed_env_files = {f"{current_root}/.env", f"{remote_root.rstrip('/')}/.env"}
    if len(paths) != 1 or paths[0] not in allowed_env_files:
        raise ReleaseError("remote unit EnvironmentFiles must contain exactly one canonical env file")
    if exec_start_pre.strip():
        raise ReleaseError("remote unit ExecStartPre must be empty")
    argv = _extract_systemd_argv(exec_start)
    executable_path = _extract_systemd_path(exec_start)
    exec_assignments = sorted({token.split("=", 1)[0] for token in argv if "=" in token} & set(SYSTEMD_ENV_OVERRIDE_DENY))
    if exec_assignments:
        raise ReleaseError("remote unit ExecStart assignment is not allowed for fields: " + ",".join(exec_assignments))
    if executable_path not in APPROVED_EXECSTART_INTERPRETERS or len(argv) != 2 or argv[0] != executable_path or argv[1] != required_entrypoint:
        raise ReleaseError("remote unit ExecStart must be approved python interpreter plus API server only")
    denied = sorted(_environment_assignment_names(environment) & set(SYSTEMD_ENV_OVERRIDE_DENY))
    if denied:
        raise ReleaseError("remote unit Environment override is not allowed for fields: " + ",".join(denied))


def _remote_preflight_command(
    release_dir: str,
    modules: list[str] | None = None,
    compile_files: list[str] | None = None,
    *,
    profile: str | None = None,
) -> str:
    if profile not in {None, V6_ONLY_PROFILE, V6_CALLBACK_WORKER_PROFILE}:
        raise ReleaseError(f"unknown remote preflight profile: {profile}")
    expected_modules = list(V6_ONLY_IMPORT_MODULES if profile == V6_ONLY_PROFILE else V6_CALLBACK_WORKER_IMPORT_MODULES if profile == V6_CALLBACK_WORKER_PROFILE else IMPORT_MODULES)
    if modules is not None and modules != expected_modules:
        raise ReleaseError("remote preflight modules must exactly match release profile contract")
    if profile in {V6_ONLY_PROFILE, V6_CALLBACK_WORKER_PROFILE}:
        expected_compile = V6_ONLY_PREFLIGHT_PY_FILES if profile == V6_ONLY_PROFILE else V6_CALLBACK_WORKER_PREFLIGHT_PY_FILES
        expected_deps = V6_ONLY_REQUIRED_DEPENDENCIES if profile == V6_ONLY_PROFILE else V6_CALLBACK_WORKER_REQUIRED_DEPENDENCIES
        selected_compile_files = list(expected_compile) if compile_files is None else compile_files
        if selected_compile_files != list(expected_compile):
            raise ReleaseError(f"{profile} remote preflight compile files must exactly match its allowlist")
        payload_data = {
            "profile": profile,
            "modules": expected_modules,
            "compile_files": selected_compile_files,
            "required_dependencies": list(expected_deps),
        }
    else:
        allowed_compile_files = set(REMOTE_PREFLIGHT_PY_FILES) | {NMBOT_DIALOGUE_EXPORTER_SCRIPT} | set(NMBOT_DIALOGUE_EXPORTER_DEPENDENCY_FILES)
        selected_compile_files = list(REMOTE_PREFLIGHT_PY_FILES) if compile_files is None else compile_files
        if not set(selected_compile_files).issubset(allowed_compile_files):
            raise ReleaseError("remote preflight compile files must match release contract")
        # Keep the default payload and generated command behavior unchanged.
        payload_data = {"modules": expected_modules, "compile_files": sorted(selected_compile_files)}
    payload = json.dumps(payload_data, sort_keys=True)
    code = r'''
import hashlib, importlib, json, os, pathlib, py_compile, sys, tempfile
cfg=json.loads(sys.argv[1]); root=pathlib.Path.cwd().resolve(); modules=cfg["modules"]; compile_files=cfg["compile_files"]; profile=cfg.get("profile"); required_dependencies=cfg.get("required_dependencies",[])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def v1_py_files():
    base=root/"nmbot_v1"
    if not base.is_dir() or base.is_symlink(): return []
    out=[]
    for p in base.rglob("*.py"):
        if p.is_file() and not p.is_symlink(): out.append(str(p.relative_to(root)).replace(os.sep,"/"))
    return out
def snapshot():
    out={}
    for dirpath, dirnames, filenames in os.walk(root):
        d=pathlib.Path(dirpath)
        dirnames[:]=[name for name in dirnames if not (d/name).is_symlink()]
        for name in filenames:
            p=d/name
            if p.is_symlink() or not p.is_file():
                continue
            rel=str(p.relative_to(root)).replace(os.sep,"/")
            out[rel]=hashlib.sha256(p.read_bytes()).hexdigest()
    return out
before=snapshot()
if profile in ("v6-only", "v6-callback-worker"):
    if compile_files != sorted(compile_files) or len(compile_files) != len(set(compile_files)): fail("profile compile file set is not exact")
else:
    compile_files=sorted(set(compile_files)|set(v1_py_files()))
with tempfile.TemporaryDirectory(prefix="nmbot-preflight-pyc-") as td:
    tmp=pathlib.Path(td)
    for idx, rel in enumerate(compile_files):
        p=root/rel
        if not p.is_file() or p.is_symlink(): fail("compile file missing: "+rel)
        py_compile.compile(str(p), cfile=str(tmp/(str(idx)+".pyc")), doraise=True)
os.environ["PYTHONDONTWRITEBYTECODE"]="1"
sys.dont_write_bytecode=True
release_paths={str(root), str(root/"scripts")}
sys.path[:]=[str(root), str(root/"scripts")]+[p for p in sys.path if p and p not in release_paths and not pathlib.Path(p).resolve().is_relative_to(root)]
for name in modules:
    importlib.import_module(name)
for name in required_dependencies:
    importlib.import_module(name)
after=snapshot()
if after != before: fail("release file set/hash changed during preflight")
if profile == "v6-only":
    print(json.dumps({"ok": True, "profile": profile, "import": "ok", "py_compile": len(compile_files), "import_modules": len(modules), "required_dependencies": len(required_dependencies)}))
else:
    print(json.dumps({"ok": True, "import": "ok", "py_compile": len(compile_files), "import_modules": len(modules)}))
'''
    return " && ".join([
        "test -d " + shlex.quote(release_dir),
        "cd " + shlex.quote(release_dir),
        "PYTHONPATH=" + shlex.quote(release_dir + os.pathsep + release_dir.rstrip("/") + "/scripts") + " PYTHONDONTWRITEBYTECODE=1 python3 -c " + shlex.quote(code) + " " + shlex.quote(payload),
    ])


def _remote_guard_command(remote_root: str, manifest: dict[str, Any]) -> str:
    payload = json.dumps({"root": remote_root, "required": manifest["config_schema_requirements"], "fixed_data_env_paths": FIXED_DATA_ENV_PATHS, "canonical_api": CANONICAL_API_ENV_VALUES, "required_modes": {"NMBOT_V2_MANAGER_REWRITER_MODE": "off", "NMBOT_V3_MANAGER_REWRITER_MODE": "publish"}}, sort_keys=True)
    code = r'''
import json, os, pathlib, shlex, sys
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve(); current=(root/"current").resolve(); data=(root/"data").resolve(); req=cfg["required"]; fixed=cfg["fixed_data_env_paths"]; canonical=cfg["canonical_api"]; required_modes=cfg["required_modes"]
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def inside(p):
    try: return pathlib.Path(p).resolve().is_relative_to(root)
    except AttributeError: return str(pathlib.Path(p).resolve()).startswith(str(root)+os.sep) or pathlib.Path(p).resolve()==root
def resolve_env_path(raw):
    p=pathlib.Path(raw)
    if not p.is_absolute(): p=current/p
    return p.resolve()
for name in req["external_runtime_paths"]:
    p=root/name
    if not p.exists() or p.is_symlink(): fail("external target missing or symlink: "+name)
    if name in ("data","logs","backups") and not p.is_dir(): fail("external dir invalid: "+name)
    if name not in ("data","logs","backups") and not p.is_file(): fail("external file invalid: "+name)
    if not inside(p): fail("external target outside root: "+name)
required_names=set(req["required_secret_names"])|set(req["required_setting_names"])|set(req["required_mode_names"])
env_names=set(); env_values={}; values_by_file={".env":{}, ".env.client-production":{}}
for line in (root/".env").read_text(encoding="utf-8", errors="ignore").splitlines():
    s=line.strip()
    if not s or s.startswith("#") or "=" not in s: continue
    k,v=s.split("=",1); key=k.strip().removeprefix("export ").strip()
    env_names.add(key)
    try: parsed=shlex.split(v, comments=False, posix=True)[0] if v.strip() else ""
    except Exception: parsed=v.strip().strip("'\"")
    env_values[key]=parsed; values_by_file[".env"][key]=parsed
# Optional bridge/client env is inspected only if present to ensure it does not
# redefine API-owned settings. It can never satisfy a missing API requirement.
bridge=root/".env.client-production"
if bridge.exists() or bridge.is_symlink():
    if not bridge.is_file() or bridge.is_symlink(): fail("optional bridge env is not a safe regular file")
    for line in bridge.read_text(encoding="utf-8", errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1); key=k.strip().removeprefix("export ").strip()
        try: parsed=shlex.split(v, comments=False, posix=True)[0] if v.strip() else ""
        except Exception: parsed=v.strip().strip("'\"")
        values_by_file[".env.client-production"][key]=parsed
missing=sorted(required_names-env_names)
if missing: fail("missing env names: "+",".join(missing))
empty=sorted(k for k in required_names if not str(env_values.get(k,"")).strip())
if empty: fail("empty env values: "+",".join(empty))
bridge_required=sorted(required_names & set(values_by_file[".env.client-production"]))
if bridge_required: fail("bridge env must not define API-owned required fields: "+",".join(bridge_required))
bad_api=[k for k,v in sorted(canonical.items()) if values_by_file[".env"].get(k) != v]
if bad_api: fail("canonical API env mismatch: "+",".join(bad_api))
if values_by_file[".env"].get("NMBOT_CONTOUR_PROFILE") == "client_production": fail("contour profile is incompatible with canonical API bind: NMBOT_CONTOUR_PROFILE")
bad_modes=[k for k,v in sorted(required_modes.items()) if values_by_file[".env"].get(k) != v]
if bad_modes: fail("mode env mismatch: "+",".join(bad_modes))
for field, rel in fixed.items():
    if field not in env_values: fail("missing env path: "+field)
    raw_value=env_values[field]
    canonical_path=root/rel; actual=resolve_env_path(raw_value); expected=canonical_path.resolve()
    if field == "NMBOT_RELEASE_IDENTITY_FILE":
        if raw_value != str(canonical_path): fail("env path must use fixed external data path: "+field)
        if not canonical_path.exists() or canonical_path.is_symlink() or not canonical_path.is_file(): fail("release identity file missing or invalid")
    if actual != expected: fail("env path outside fixed external data path: "+field)
    if field == "NMBOT_CALLBACK_OUTBOX_DIR":
        try: under_data=expected.resolve().is_relative_to(data.resolve())
        except AttributeError: under_data=str(expected.resolve()).startswith(str(data.resolve())+os.sep) or expected.resolve()==data.resolve()
        if not under_data: fail("callback outbox path outside external data parent")
        if not canonical_path.exists() or canonical_path.is_symlink() or not canonical_path.is_dir(): fail("callback outbox directory missing or invalid")
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bootstrap_guard_command(remote_root: str, manifest: dict[str, Any]) -> str:
    additions = dict(line.split("=", 1) for line in _render_bootstrap_env_additions().splitlines() if line.strip())
    optional = set(additions)
    payload = json.dumps({"root": remote_root, "required": manifest["config_schema_requirements"], "fixed_data_env_paths": FIXED_DATA_ENV_PATHS, "canonical_api": CANONICAL_API_ENV_VALUES, "required_modes": {"NMBOT_V2_MANAGER_REWRITER_MODE": "off", "NMBOT_V3_MANAGER_REWRITER_MODE": "publish"}, "bootstrap_additions": additions, "bootstrap_optional": sorted(optional)}, sort_keys=True)
    code = r'''
import json, os, pathlib, shlex, sys
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve(); req=cfg["required"]; fixed=cfg["fixed_data_env_paths"]; canonical=cfg["canonical_api"]; required_modes=cfg["required_modes"]; additions=cfg["bootstrap_additions"]; optional=set(cfg["bootstrap_optional"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def inside(p):
    try: return pathlib.Path(p).resolve().is_relative_to(root)
    except AttributeError: return str(pathlib.Path(p).resolve()).startswith(str(root)+os.sep) or pathlib.Path(p).resolve()==root
def parse_env(path):
    values={}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s=line.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1); key=k.strip().removeprefix("export ").strip()
        try: parsed=shlex.split(v, comments=False, posix=True)[0] if v.strip() else ""
        except Exception: parsed=v.strip().strip("'\"")
        values[key]=parsed
    return values
def parse_optional_bridge_env(path):
    if not path.exists() and not path.is_symlink(): return {}
    if not path.is_file() or path.is_symlink(): fail("optional bridge env is not a safe regular file")
    return parse_env(path)
for name in req["external_runtime_paths"]:
    p=root/name
    if not p.exists() or p.is_symlink(): fail("external target missing or symlink: "+name)
    if name in ("data","logs","backups") and not p.is_dir(): fail("external dir invalid: "+name)
    if name not in ("data","logs","backups") and not p.is_file(): fail("external file invalid: "+name)
    if not inside(p): fail("external target outside root: "+name)
values_by_file={".env": parse_env(root/".env"), ".env.client-production": parse_optional_bridge_env(root/".env.client-production")}
env_values=values_by_file[".env"]; env_names=set(env_values)
all_required=set(req["required_secret_names"])|set(req["required_setting_names"])|set(req["required_mode_names"])
required_now=all_required-optional
missing=sorted(required_now-env_names)
if missing: fail("missing env names: "+",".join(missing))
empty=sorted(k for k in required_now if not str(env_values.get(k," ")).strip())
if empty: fail("empty env values: "+",".join(empty))
bridge_required=sorted(all_required & set(values_by_file[".env.client-production"]))
if bridge_required: fail("bridge env must not define API-owned required fields: "+",".join(bridge_required))
bad_api=[k for k,v in sorted(canonical.items()) if env_values.get(k) != v]
if bad_api: fail("canonical API env mismatch: "+",".join(bad_api))
bad_modes=[k for k,v in sorted(required_modes.items()) if env_values.get(k) != v]
if bad_modes: fail("mode env mismatch: "+",".join(bad_modes))
bad_bootstrap=[k for k,v in sorted(additions.items()) if k in env_values and env_values.get(k) != v]
if bad_bootstrap: fail("bootstrap env mismatch: "+",".join(bad_bootstrap))
def resolve_env_path(raw):
    p=pathlib.Path(raw)
    if not p.is_absolute(): p=root/p
    return p.resolve()
for field, rel in fixed.items():
    if field in optional and field not in env_values: continue
    if field not in env_values: fail("missing env path: "+field)
    canonical_path=root/rel; actual=resolve_env_path(env_values[field]); expected=canonical_path.resolve()
    if actual != expected: fail("env path outside fixed external data path: "+field)
    if field == "NMBOT_CALLBACK_OUTBOX_DIR":
        data=(root/"data").resolve()
        try: under_data=expected.resolve().is_relative_to(data)
        except AttributeError: under_data=str(expected.resolve()).startswith(str(data)+os.sep) or expected.resolve()==data
        if not under_data: fail("callback outbox path outside external data parent")
        if not canonical_path.exists() or canonical_path.is_symlink() or not canonical_path.is_dir(): fail("callback outbox directory missing or invalid")
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _previous_state_probe_command(remote_root: str, release_id: str) -> str:
    payload = json.dumps({"root": remote_root, "release_id": _release_id(release_id), "identity": IDENTITY_IN_RELEASE, "external_symlinks": sorted(CONFIG_REQUIREMENTS["external_runtime_paths"])}, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, re, sys
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]).resolve(); rid=cfg["release_id"]; identity=cfg["identity"]; external=set(cfg["external_symlinks"])
safe=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}")
safe_generated=re.compile(r"[A-Za-z0-9:._+\-TZ]{1,80}")
hex64=re.compile(r"[0-9a-f]{64}")
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def safe_rel(raw):
    p=pathlib.PurePosixPath(str(raw))
    if not str(p) or p.is_absolute() or ".." in p.parts: fail("previous release identity unsafe path")
    return str(p)
def is_python_cache(rel):
    p=pathlib.PurePosixPath(rel)
    return "__pycache__" in p.parts or rel.endswith((".pyc",".pyo"))
cur=root/"current"
if not cur.is_symlink(): fail("current is not a release symlink")
prev=cur.resolve(); releases=(root/"releases").resolve()
try: under=prev.is_relative_to(releases)
except AttributeError: under=str(prev).startswith(str(releases)+os.sep)
prev_id=prev.name
if not under or prev.parent != releases or not safe.fullmatch(prev_id): fail("current symlink target is not a safe release")
target=root/"releases"/rid
if os.path.lexists(target): fail("release id already exists")
ip=prev/identity
if not ip.is_file() or ip.is_symlink(): fail("previous release identity missing")
try: data=json.loads(ip.read_text(encoding="utf-8"))
except Exception: fail("previous release identity invalid")
if set(data) != {"schema","release_id","generated_at","tracked_files"}: fail("previous release identity schema invalid")
if data.get("schema") != "nmbot.release_identity.v1" or data.get("release_id") != prev_id or not safe.fullmatch(str(data.get("release_id",""))): fail("previous release identity mismatch")
if not isinstance(data.get("generated_at"), str) or not safe_generated.fullmatch(data.get("generated_at")): fail("previous release identity generated_at invalid")
tracked=data.get("tracked_files")
if not isinstance(tracked, list) or not tracked: fail("previous release identity tracked_files invalid")
expected={}
for item in tracked:
    if not isinstance(item, dict) or set(item)!={"path","sha256"}: fail("previous release identity tracked row invalid")
    rel=safe_rel(item.get("path")); sha=item.get("sha256")
    if rel==identity or rel in external: fail("previous release identity tracks unmanaged path")
    if rel in expected: fail("previous release identity duplicate path")
    if not isinstance(sha, str) or not hex64.fullmatch(sha): fail("previous release identity invalid hash")
    fp=prev/rel
    if not fp.is_file() or fp.is_symlink(): fail("previous release tracked file missing")
    if hashlib.sha256(fp.read_bytes()).hexdigest()!=sha: fail("previous release tracked file hash mismatch")
    expected[rel]=sha
actual=set()
for rel in external:
    if not (prev/rel).is_symlink(): fail("previous release external symlink missing")
for path in prev.rglob("*"):
    rel=str(path.relative_to(prev)).replace(os.sep,"/")
    if path.is_symlink():
        if rel not in external: fail("previous release unexpected symlink")
        continue
    if path.is_dir(): continue
    if not path.is_file(): fail("previous release unexpected node")
    if rel==identity: continue
    if is_python_cache(rel): continue
    actual.add(rel)
if actual != set(expected): fail("previous release tracked file set mismatch")
print(json.dumps({"ok": True, "previous_id": prev_id, "release_exists": False}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _api_inactive_command() -> str:
    payload = json.dumps({"service": API_SERVICE}, sort_keys=True)
    code = r'''
import json, subprocess, sys, time
cfg=json.loads(sys.argv[1]); deadline=time.time()+10; last=""
while time.time()<deadline:
    proc=subprocess.run(["systemctl","--user","is-active",cfg["service"]], text=True, capture_output=True, timeout=3)
    last=(proc.stdout+proc.stderr).strip()
    if last in ("inactive", "failed") or proc.returncode != 0:
        print(json.dumps({"ok": True, "state": last})); sys.exit(0)
    time.sleep(1)
print(json.dumps({"ok": False, "error": "api still active: "+last})); sys.exit(2)
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _stop_api_command() -> str:
    return "systemctl --user stop " + shlex.quote(API_SERVICE)


def _start_api_command() -> str:
    return "systemctl --user start " + shlex.quote(API_SERVICE)


def _callback_worker_install_command(remote_root: str, release_id: str) -> str:
    payload = json.dumps({"root": remote_root, "unit": CALLBACK_WORKER_UNIT, "service": CALLBACK_WORKER_SERVICE, "backup": f"{remote_root}/backups/{release_id}.callback-worker.service.bak"}, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, subprocess, sys
cfg=json.loads(sys.argv[1]); unit=pathlib.Path(cfg["unit"]); backup=pathlib.Path(cfg["backup"]); root=cfg["root"]
def fail(msg): print(json.dumps({"ok":False,"error":msg})); sys.exit(2)
if not unit.is_file() or unit.is_symlink(): fail("callback worker unit missing or unsafe")
backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True); shutil.copy2(unit, backup)
text="""[Unit]
Description=NMBOT callback Google Sheets worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={root}/current
EnvironmentFile={root}/.env
ExecStart=/usr/bin/python3 {root}/current/scripts/nmbot_callback_sheet_worker.py --loop --poll-seconds 5
Restart=always
RestartSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
""".format(root=root)
tmp=unit.with_name("."+unit.name+".nmbot."+str(os.getpid())+".tmp")
try:
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    if tmp.is_symlink() or tmp.read_text(encoding="utf-8") != text: fail("callback worker unit staging mismatch")
    os.replace(tmp, unit)
finally:
    tmp.unlink(missing_ok=True)
subprocess.run(["systemctl","--user","daemon-reload"], check=True, timeout=20)
print(json.dumps({"ok":True,"backup":str(backup)}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _callback_worker_restore_command(remote_root: str, release_id: str) -> str:
    payload = json.dumps({"unit": CALLBACK_WORKER_UNIT, "backup": f"{remote_root}/backups/{release_id}.callback-worker.service.bak", "service": CALLBACK_WORKER_SERVICE}, sort_keys=True)
    code = r'''
import json, pathlib, shutil, subprocess, sys
cfg=json.loads(sys.argv[1]); unit=pathlib.Path(cfg["unit"]); backup=pathlib.Path(cfg["backup"])
if not backup.is_file() or backup.is_symlink(): print(json.dumps({"ok":False,"error":"callback worker backup missing"})); sys.exit(2)
shutil.copy2(backup, unit); subprocess.run(["systemctl","--user","daemon-reload"], check=True, timeout=20); print(json.dumps({"ok":True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _remote_extract_command(staging: str, release_dir: str, manifest: dict[str, Any]) -> str:
    payload = json.dumps({"archive": staging + "/" + manifest["archive_name"], "dest": release_dir, "manifest": manifest, "identity": IDENTITY_IN_RELEASE}, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, re, shutil, sys, tarfile
cfg=json.loads(sys.argv[1]); m=cfg["manifest"]; archive=pathlib.Path(cfg["archive"]); dest=pathlib.Path(cfg["dest"]); identity=cfg["identity"]
hex64=re.compile(r"[0-9a-f]{64}"); safe_generated=re.compile(r"[A-Za-z0-9:._+\-TZ]{1,80}")
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def safe_rel(raw):
    p=pathlib.PurePosixPath(str(raw))
    if not str(p) or p.is_absolute() or ".." in p.parts: fail("release identity unsafe path")
    return str(p)
if archive.name != m["archive_name"]: fail("archive name mismatch")
if hashlib.sha256(archive.read_bytes()).hexdigest() != m["archive_sha256"]: fail("archive sha256 mismatch")
expected={i["path"]: i["sha256"] for i in m["files"]}; tmp=dest.parent/(dest.name+".extract.tmp")
if os.path.lexists(tmp): fail("temporary extract path already exists")
tmp.mkdir(parents=True)
try:
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            name=member.name; p=pathlib.PurePosixPath(name)
            if not name or p.is_absolute() or ".." in p.parts: fail("unsafe tar path")
            if not member.isfile() or member.issym() or member.islnk() or member.isdev(): fail("unsafe tar member")
            if name not in expected: fail("unexpected tar member")
            target=(tmp/name).resolve()
            if not str(target).startswith(str(tmp.resolve())+os.sep): fail("tar traversal")
        tf.extractall(tmp, filter="data")
    actual={}
    for path in tmp.rglob("*"):
        if path.is_symlink() or (os.path.lexists(path) and not path.is_file() and not path.is_dir()): fail("unsafe extracted node")
        if path.is_file(): actual[str(path.relative_to(tmp)).replace(os.sep,"/")]=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected: fail("file set/hash mismatch")
    try: data=json.loads((tmp/identity).read_text(encoding="utf-8"))
    except Exception: fail("release identity invalid JSON")
    if not isinstance(data, dict) or set(data)!={"schema","release_id","generated_at","tracked_files"}: fail("release identity schema invalid")
    if data.get("schema") != "nmbot.release_identity.v1" or data.get("release_id") != m["release_id"]: fail("release identity mismatch")
    if not isinstance(data.get("generated_at"), str) or not safe_generated.fullmatch(data.get("generated_at")): fail("release identity generated_at invalid")
    tracked=data.get("tracked_files")
    if not isinstance(tracked, list): fail("release identity tracked_files invalid")
    identity_expected={k:v for k,v in expected.items() if k != identity}; identity_actual={}
    for item in tracked:
        if not isinstance(item, dict) or set(item)!={"path","sha256"}: fail("release identity tracked row invalid")
        rel=safe_rel(item.get("path")); sha=item.get("sha256")
        if rel==identity or rel in identity_actual: fail("release identity duplicate or self-tracked path")
        if not isinstance(sha, str) or not hex64.fullmatch(sha): fail("release identity tracked hash invalid")
        identity_actual[rel]=sha
    if identity_actual != identity_expected: fail("release identity tracked_files mismatch")
    if os.path.lexists(dest): fail("release already exists")
    os.replace(tmp, dest)
    print(json.dumps({"ok": True}))
finally:
    if os.path.lexists(tmp) and tmp.is_dir() and not tmp.is_symlink(): shutil.rmtree(tmp)
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _health_and_identity_command(remote_root: str, expected_release_id: str, identity_path: str | None = None) -> str:
    current = f"{remote_root.rstrip('/')}/current"
    canonical_identity = identity_path or f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}"
    if canonical_identity != f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}":
        raise ReleaseError("health identity path must be canonical external release identity")
    payload = json.dumps({"url": API_HEALTH_URL, "expected": expected_release_id, "current": current, "identity_path": canonical_identity}, sort_keys=True)
    code = r'''
import json, os, subprocess, sys, time, urllib.request
cfg=json.loads(sys.argv[1]); deadline=time.time()+20; last=""
while time.time()<deadline:
    try:
        data=json.loads(urllib.request.urlopen(cfg["url"], timeout=3).read().decode())
        if data.get("ok") is True and data.get("jivo_token_configured") is True and data.get("api_token_configured") is True:
            env=os.environ.copy(); env["NMBOT_RELEASE_IDENTITY_FILE"]=cfg["identity_path"]
            proc=subprocess.run(["python3", cfg["current"]+"/scripts/nmbot_release_identity.py", "read"], env=env, text=True, capture_output=True, timeout=5)
            rid=proc.stdout.strip().splitlines()[-1] if proc.returncode==0 and proc.stdout.strip() else ""
            if rid == cfg["expected"]:
                print(json.dumps({"ok": True, "release_id": rid})); sys.exit(0)
            last="release identity mismatch"
        else: last="health config proof failed"
    except Exception as exc: last=type(exc).__name__
    time.sleep(1)
print(json.dumps({"ok": False, "error": last})); sys.exit(2)
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _health_config_command() -> str:
    payload = json.dumps({"url": API_HEALTH_URL}, sort_keys=True)
    code = r'''
import json, sys, urllib.request
cfg=json.loads(sys.argv[1])
try: data=json.loads(urllib.request.urlopen(cfg["url"], timeout=3).read().decode())
except Exception as exc: print(json.dumps({"ok": False, "error": type(exc).__name__})); sys.exit(2)
if data.get("ok") is True and data.get("jivo_token_configured") is True and data.get("api_token_configured") is True:
    print(json.dumps({"ok": True})); sys.exit(0)
print(json.dumps({"ok": False, "error": "health config proof failed"})); sys.exit(2)
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _validate_bridge_recon_payload(data: Any, *, strict: bool = False) -> None:
    top = _expect_exact_keys(data, {"ok", "remote_root", "service", "unit", "systemd", "bridge_current", "health", "active_manifest"}, "bridge top-level")
    if not isinstance(top["ok"], bool) or top["remote_root"] != DEFAULT_REMOTE_ROOT or top["service"] != BRIDGE_SERVICE:
        raise ReleaseError("bridge recon root/status invalid")
    unit = _expect_exact_keys(top["unit"], {"fragment_path", "environment_file", "expected_execstart", "expected_working_directory"}, "bridge unit")
    allowed_units = [
        {"fragment_path": BRIDGE_UNIT_PATH, "environment_file": f"{DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {DEFAULT_REMOTE_ROOT}/{BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": DEFAULT_REMOTE_ROOT},
        {"fragment_path": BRIDGE_UNIT_PATH, "environment_file": f"{DEFAULT_REMOTE_ROOT}/.env", "expected_execstart": f"/usr/bin/python3 {DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}/{BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "expected_working_directory": f"{DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}"},
    ]
    if unit not in allowed_units:
        raise ReleaseError("bridge recon unit contract mismatch")
    systemd = _expect_exact_keys(top["systemd"], {"fragment_path_ok", "environment_file_canonical", "inline_environment_expected", "active", "main_pid_present", "execstart_expected", "working_directory_expected"}, "bridge systemd")
    if not all(isinstance(value, bool) for value in systemd.values()):
        raise ReleaseError("bridge recon systemd types invalid")
    current = _expect_exact_keys(top["bridge_current"], {"state", "target_name", "safe_release_symlink"}, "bridge current")
    if current["state"] not in {"absent", "symlink"} or not isinstance(current["target_name"], str) or not isinstance(current["safe_release_symlink"], bool):
        raise ReleaseError("bridge recon current types invalid")
    health = _expect_exact_keys(top["health"], {"reachable", "ok"}, "bridge health")
    if not all(isinstance(value, bool) for value in health.values()):
        raise ReleaseError("bridge recon health types invalid")
    active = _expect_exact_keys(top["active_manifest"], {"exists", "schema_ok", "release_id", "tracked_hashes_match"}, "bridge active manifest")
    if not isinstance(active["exists"], bool) or not isinstance(active["schema_ok"], bool) or not isinstance(active["release_id"], str) or not isinstance(active["tracked_hashes_match"], bool):
        raise ReleaseError("bridge recon active manifest types invalid")
    required_ok = all(systemd.values()) and current["safe_release_symlink"] is True and health["reachable"] is True and health["ok"] is True
    if top["ok"] != required_ok:
        raise ReleaseError("bridge recon ok/status mismatch")
    if strict and not required_ok:
        raise ReleaseError("bridge recon strict contract/health failed")


def _readonly_bridge_recon_command(remote_root: str = DEFAULT_REMOTE_ROOT) -> str:
    if remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge recon remote root is fixed")
    payload = json.dumps({"root": DEFAULT_REMOTE_ROOT, "service": BRIDGE_SERVICE, "unit_path": BRIDGE_UNIT_PATH, "env": f"{DEFAULT_REMOTE_ROOT}/.env", "inline_env": BRIDGE_INLINE_ENVIRONMENT, "health_url": BRIDGE_HEALTH_URL, "current": BRIDGE_CURRENT, "releases": BRIDGE_RELEASES, "entrypoint": BRIDGE_ENTRYPOINT}, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, re, shlex, subprocess, sys, urllib.request
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]); cur=root/cfg["current"]; releases=root/cfg["releases"]
def argv_from_execstart(raw):
    m=re.search(r"argv\[\]=([^;}]+)", raw or "")
    if m: return shlex.split(m.group(1).strip())
    if raw.startswith("/usr/bin/python3 "): return shlex.split(raw)
    return []
def env_file_exact(raw):
    s=" ".join((raw or "").strip().split())
    if not s: return False
    if s == cfg["env"] or s == cfg["env"]+" (ignore_errors=no)": return True
    m=re.fullmatch(r"\{\s*path=([^\s;]+)\s*;\s*ignore_errors=(yes|no)\s*;\s*\}", s)
    return bool(m and m.group(1)==cfg["env"] and m.group(2)=="no")
def inline_env_exact(raw):
    return (raw or "").strip() == cfg["inline_env"]
show=subprocess.run(["systemctl","--user","show",cfg["service"],"--no-pager"], text=True, capture_output=True, timeout=10)
data={}
for line in show.stdout.splitlines():
    if "=" in line:
        k,v=line.split("=",1); data[k]=v
fragment=data.get("FragmentPath",""); env_files=data.get("EnvironmentFiles", data.get("EnvironmentFile", "")); environment=data.get("Environment", ""); exec_start=data.get("ExecStart",""); wd=data.get("WorkingDirectory",""); active=data.get("ActiveState","") == "active"; main=data.get("MainPID","") not in ("", "0")
state="absent"; target=""; safe=False
if os.path.lexists(cur):
    state="symlink" if cur.is_symlink() else "unsafe"
    if cur.is_symlink():
        try:
            resolved=cur.resolve(strict=True); target=resolved.name; safe=(resolved.parent == releases and resolved.is_dir() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", target) is not None)
        except Exception: safe=False
expected_wd=str(cur) if state=="symlink" else str(root)
expected_exec=f"/usr/bin/python3 {(cur if state=='symlink' else root)/cfg['entrypoint']} --host 0.0.0.0 --port 8093"
expected_argv=shlex.split(expected_exec)
health={"reachable": False, "ok": False}
try:
    h=json.loads(urllib.request.urlopen(cfg["health_url"], timeout=3).read().decode()); health={"reachable": True, "ok": h.get("ok") is True}
except Exception: pass
manifest={"exists": False, "schema_ok": False, "release_id": "", "tracked_hashes_match": False}
if safe:
    mp=cur/"bridge-release-manifest.json"
    if mp.is_file() and not mp.is_symlink():
        try:
            m=json.loads(mp.read_text(encoding="utf-8")); files=m.get("files",[]); ok=True
            for item in files:
                p=cur/item.get("path",""); ok=ok and p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==item.get("sha256")
            manifest={"exists": True, "schema_ok": m.get("schema_version")=="nmbot.bridge_release.v1", "release_id": str(m.get("release_id","")), "tracked_hashes_match": bool(ok)}
        except Exception: pass
systemd={"fragment_path_ok": pathlib.Path(fragment).resolve()==pathlib.Path(cfg["unit_path"]).resolve(), "environment_file_canonical": env_file_exact(env_files), "inline_environment_expected": inline_env_exact(environment), "active": active, "main_pid_present": main, "execstart_expected": argv_from_execstart(exec_start)==expected_argv, "working_directory_expected": wd==expected_wd}
bridge_current={"state": state, "target_name": target, "safe_release_symlink": safe or state=="absent"}
ok=all(systemd.values()) and bridge_current["safe_release_symlink"] and health["reachable"] and health["ok"]
print(json.dumps({"ok": ok, "remote_root": str(root), "service": cfg["service"], "unit": {"fragment_path": cfg["unit_path"], "environment_file": cfg["env"], "expected_execstart": expected_exec, "expected_working_directory": expected_wd}, "systemd": systemd, "bridge_current": bridge_current, "health": health, "active_manifest": manifest}, sort_keys=True))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def bridge_recon(remote: Remote) -> dict[str, Any]:
    proc = remote.run(_readonly_bridge_recon_command(DEFAULT_REMOTE_ROOT))
    if proc.returncode != 0:
        raise ReleaseError(_safe_text_command_error(proc, "remote bridge recon failed"))
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception as exc:
        raise ReleaseError("remote bridge recon returned invalid JSON") from exc
    _validate_bridge_recon_payload(data, strict=False)
    return data


def _bridge_remote_paths(remote_root: str, release_id: str) -> dict[str, str]:
    rid = _release_id(release_id)
    root = remote_root.rstrip("/")
    return {"root": root, "rid": rid, "staging": f"{root}/.bridge_release_staging/{rid}", "release_dir": f"{root}/{BRIDGE_RELEASES}/{rid}", "lock_dir": f"{root}/.bridge_release_lock", "backup_dir": f"{root}/backups/bridge-{rid}", "current": f"{root}/{BRIDGE_CURRENT}"}


def _validate_bridge_guard_state(state: Any) -> dict[str, Any]:
    allowed = {"ok", "unit_path", "previous_state", "previous_target", "previous_working_directory", "previous_exec_argv", "previous_environment_file", "previous_inline_environment", "previous_fragment_path"}
    if not isinstance(state, dict) or set(state) != allowed or state.get("ok") is not True:
        raise ReleaseError("bridge guard state schema invalid")
    for key in allowed - {"ok", "previous_exec_argv"}:
        if not isinstance(state[key], str):
            raise ReleaseError(f"bridge guard state field invalid: {key}")
    if state["unit_path"] != BRIDGE_UNIT_PATH or state["previous_fragment_path"] != BRIDGE_UNIT_PATH or state["previous_environment_file"] != f"{DEFAULT_REMOTE_ROOT}/.env" or state["previous_inline_environment"] != BRIDGE_INLINE_ENVIRONMENT:
        raise ReleaseError("bridge guard state contract mismatch")
    if state["previous_state"] not in {"absent", "symlink"}:
        raise ReleaseError("bridge guard previous_state invalid")
    if state["previous_state"] == "absent" and state["previous_target"]:
        raise ReleaseError("bridge guard absent current must not have target")
    if state["previous_state"] == "symlink" and not re.fullmatch(r"bridge-releases/[A-Za-z0-9][A-Za-z0-9._-]{2,79}", state["previous_target"]):
        raise ReleaseError("bridge guard previous target invalid")
    argv = state["previous_exec_argv"]
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ReleaseError("bridge guard previous exec argv invalid")
    expected_root_argv = ["/usr/bin/python3", f"{DEFAULT_REMOTE_ROOT}/{BRIDGE_ENTRYPOINT}", "--host", "0.0.0.0", "--port", "8093"]
    expected_current_argv = ["/usr/bin/python3", f"{DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}/{BRIDGE_ENTRYPOINT}", "--host", "0.0.0.0", "--port", "8093"]
    if state["previous_state"] == "absent":
        if state["previous_working_directory"] != DEFAULT_REMOTE_ROOT or argv != expected_root_argv:
            raise ReleaseError("bridge guard first-migration unit state mismatch")
    if state["previous_state"] == "symlink":
        if state["previous_working_directory"] != f"{DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}" or argv != expected_current_argv:
            raise ReleaseError("bridge guard migrated unit state mismatch")
    return dict(state)


def _systemd_env_file_is_exact(raw: str, expected: str) -> bool:
    s = " ".join((raw or "").strip().split())
    if not s:
        return False
    if s == expected or s == f"{expected} (ignore_errors=no)":
        return True
    match = re.fullmatch(r"\{\s*path=([^\s;]+)\s*;\s*ignore_errors=(yes|no)\s*;\s*\}", s)
    return bool(match and match.group(1) == expected and match.group(2) == "no")


def _bridge_remote_guard_command(remote_root: str, manifest: dict[str, Any], snapshot_sha: str) -> str:
    paths = _bridge_remote_paths(remote_root, manifest["release_id"])
    baseline = _validate_bridge_baseline_rows(manifest["source_provenance"]["baseline_files"], label="bridge baseline")
    payload = json.dumps({"root": paths["root"], "rid": paths["rid"], "release_dir": paths["release_dir"], "staging": paths["staging"], "lock": paths["lock_dir"], "backup": paths["backup_dir"], "current": paths["current"], "api_current": f"{remote_root.rstrip('/')}/current", "api_releases": f"{remote_root.rstrip('/')}/releases", "service": BRIDGE_SERVICE, "unit_path": BRIDGE_UNIT_PATH, "env": f"{DEFAULT_REMOTE_ROOT}/.env", "inline_env": BRIDGE_INLINE_ENVIRONMENT, "snapshot_sha": snapshot_sha, "expected_snapshot_sha": manifest["source_provenance"]["source_snapshot_manifest_sha256"], "baseline_files": baseline, "source_mode": manifest["source_provenance"]["source_mode"], "active_release_id": manifest["source_provenance"].get("active_release_id", ""), "api_current_release_id": manifest["source_provenance"].get("api_current_release_id", ""), "entrypoint": BRIDGE_ENTRYPOINT}, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, re, shlex, subprocess, sys
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["root"]); cur=pathlib.Path(cfg["current"]); releases=root/"bridge-releases"; api_current=pathlib.Path(cfg["api_current"]); api_releases=pathlib.Path(cfg["api_releases"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def argv_from_execstart(raw):
    m=re.search(r"argv\[\]=([^;}]+)", raw or "")
    if m:
        return shlex.split(m.group(1).strip())
    if raw.startswith("/usr/bin/python3 "):
        return shlex.split(raw)
    return []
def env_file_exact(raw):
    s=" ".join((raw or "").strip().split())
    if not s: return False
    if s == cfg["env"] or s == cfg["env"]+" (ignore_errors=no)": return True
    m=re.fullmatch(r"\{\s*path=([^\s;]+)\s*;\s*ignore_errors=(yes|no)\s*;\s*\}", s)
    return bool(m and m.group(1)==cfg["env"] and m.group(2)=="no")
def inline_env_exact(raw):
    return (raw or "").strip() == cfg["inline_env"]
if cfg["snapshot_sha"] != cfg["expected_snapshot_sha"]: fail("source snapshot manifest sha256 mismatch")
for raw in (cfg["release_dir"], cfg["staging"], cfg["lock"], cfg["backup"]):
    if os.path.lexists(raw): fail("bridge deploy path already exists: "+pathlib.PurePosixPath(raw).name)
show=subprocess.run(["systemctl","--user","show",cfg["service"],"--no-pager"], text=True, capture_output=True, timeout=10)
if show.returncode != 0: fail("bridge unit show failed")
data={}
for line in show.stdout.splitlines():
    if "=" in line:
        k,v=line.split("=",1); data[k]=v
if pathlib.Path(data.get("FragmentPath","")).resolve() != pathlib.Path(cfg["unit_path"]).resolve(): fail("bridge unit FragmentPath mismatch")
if not env_file_exact(data.get("EnvironmentFiles", data.get("EnvironmentFile", ""))): fail("bridge unit EnvironmentFile mismatch")
if data.get("ExecStartPre","").strip(): fail("bridge unit ExecStartPre must be empty")
if not inline_env_exact(data.get("Environment", "")): fail("bridge unit inline Environment mismatch")
if data.get("DropInPaths","").strip() or data.get("FragmentPath","") != cfg["unit_path"]: fail("bridge unit drop-in/fragment mismatch")
if data.get("ActiveState","") != "active" or data.get("SubState","") not in ("running", "") or data.get("MainPID","") in ("", "0"): fail("bridge unit is not active/running with pid")
source=root
expected_wd=str(root); expected_argv=["/usr/bin/python3", str(root/cfg["entrypoint"]), "--host", "0.0.0.0", "--port", "8093"]
bridge_current_source=None
if os.path.lexists(cur):
    if not cur.is_symlink(): fail("bridge-current is unsafe")
    resolved=cur.resolve(strict=True)
    if resolved.parent != releases or not resolved.is_dir() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", resolved.name): fail("bridge-current target unsafe")
    if cfg["source_mode"] != "bridge_current" or resolved.name != cfg.get("active_release_id", ""): fail("bridge-current provenance mismatch")
    source=resolved; bridge_current_source=resolved
    expected_wd=str(cur); expected_argv=["/usr/bin/python3", str(cur/cfg["entrypoint"]), "--host", "0.0.0.0", "--port", "8093"]
elif cfg["source_mode"] not in ("first_migration_canonical", "first_migration_mixed"): fail("bridge first migration provenance mismatch")
if data.get("WorkingDirectory","") != expected_wd: fail("bridge unit WorkingDirectory mismatch")
if argv_from_execstart(data.get("ExecStart", "")) != expected_argv: fail("bridge unit ExecStart mismatch")
api_source=None
if cfg.get("api_current_release_id"):
    if not api_current.is_symlink(): fail("api current is required for api_current baseline")
    api_resolved=api_current.resolve(strict=True)
    if api_resolved.parent != api_releases or api_resolved.name != cfg["api_current_release_id"]: fail("api current release id mismatch")
    api_source=api_resolved
scopes={item.get("path"): item.get("source_scope") for item in cfg["baseline_files"]}
if cfg["source_mode"] == "bridge_current":
    if bridge_current_source is None or set(scopes.values()) != {"bridge_current"} or not cfg.get("active_release_id") or cfg.get("api_current_release_id"): fail("bridge_current baseline provenance mismatch")
elif cfg["source_mode"] == "first_migration_canonical":
    if set(scopes.values()) != {"bridge_canonical"} or cfg.get("active_release_id") or cfg.get("api_current_release_id"): fail("canonical bridge baseline provenance mismatch")
elif cfg["source_mode"] == "first_migration_mixed":
    if cfg.get("active_release_id") or not cfg.get("api_current_release_id"): fail("mixed bridge baseline provenance mismatch")
    if scopes != {"scripts/nmbot_n8n_bridge_server.py":"bridge_canonical", "scripts/dialogue_journal.py":"bridge_canonical", "scripts/nmbot_egress_policy.py":"api_current"}: fail("mixed bridge baseline scopes mismatch")
else: fail("bridge source_mode invalid")
for item in cfg["baseline_files"]:
    rel=item["path"]; scope=item["source_scope"]
    if scope == "bridge_current":
        if bridge_current_source is None: fail("bridge_current baseline without active bridge-current")
        p=bridge_current_source/rel
    elif scope == "bridge_canonical":
        p=root/rel
    elif scope == "api_current":
        if api_source is None or rel != "scripts/nmbot_egress_policy.py": fail("api_current baseline invalid")
        p=api_source/rel
    else: fail("baseline source scope invalid")
    if p.is_symlink() or not p.is_file(): fail("bridge baseline file missing: "+rel)
    data_b=p.read_bytes()
    if len(data_b) != item["size"] or hashlib.sha256(data_b).hexdigest() != item["sha256"]: fail("bridge baseline hash mismatch: "+rel)
print(json.dumps({"ok": True, "unit_path": cfg["unit_path"], "previous_state": "symlink" if cur.is_symlink() else "absent", "previous_target": os.readlink(cur) if cur.is_symlink() else "", "previous_working_directory": expected_wd, "previous_exec_argv": expected_argv, "previous_environment_file": cfg["env"], "previous_inline_environment": cfg["inline_env"], "previous_fragment_path": cfg["unit_path"]}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bridge_remote_extract_command(staging: str, release_dir: str, manifest: dict[str, Any]) -> str:
    payload = json.dumps({"archive": staging + "/" + manifest["archive_name"], "manifest": manifest, "dest": release_dir}, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, shutil, sys, tarfile
cfg=json.loads(sys.argv[1]); dest=pathlib.Path(cfg["dest"]); archive=pathlib.Path(cfg["archive"]); manifest=cfg["manifest"]
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
tmp=pathlib.Path(str(dest)+".tmp")
if os.path.lexists(dest) or os.path.lexists(tmp): fail("bridge release already exists")
tmp.mkdir(parents=True)
expected=[i["path"] for i in manifest["files"]]
with tarfile.open(archive, "r:gz") as tf:
    members=tf.getmembers(); names=[m.name for m in members]
    if names != expected: fail("bridge archive member set mismatch")
    for m in members:
        if not m.isfile() or m.isdir() or m.issym() or m.islnk() or m.isdev(): fail("unsafe bridge archive member")
    tf.extractall(tmp, filter="data")
for item in manifest["files"]:
    p=tmp/item["path"]
    if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=item["sha256"]: fail("bridge extracted hash mismatch")
(tmp/"bridge-release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+"\n", encoding="utf-8")
os.replace(tmp, dest)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bridge_remote_preflight_command(release_dir: str) -> str:
    return " && ".join(["test -d " + shlex.quote(release_dir), "cd " + shlex.quote(release_dir), "PYTHONPATH=" + shlex.quote(release_dir + os.pathsep + release_dir.rstrip("/") + "/scripts") + " PYTHONDONTWRITEBYTECODE=1 python3 -c " + shlex.quote("import importlib, py_compile\nfor p in " + repr(list(BRIDGE_ALLOWED_FILES)) + ": py_compile.compile(p, doraise=True)\nmod=importlib.import_module('scripts.nmbot_n8n_bridge_server')\napp=mod.create_app()\nroutes={(r.method, getattr(r.resource, 'canonical', '')) for r in app.router.routes()}\nassert ('GET','/health') in routes\nprint('ok')")])


def _bridge_backup_command(remote_root: str, release_id: str, unit_path: str, state: dict[str, Any]) -> str:
    paths = _bridge_remote_paths(remote_root, release_id)
    guard_state = _validate_bridge_guard_state(state)
    if unit_path != BRIDGE_UNIT_PATH:
        raise ReleaseError("bridge backup unit path mismatch")
    payload = json.dumps({"backup": paths["backup_dir"], "unit_path": unit_path, "current": paths["current"], "state": guard_state}, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, sys
cfg=json.loads(sys.argv[1]); backup=pathlib.Path(cfg["backup"]); unit=pathlib.Path(cfg["unit_path"]); cur=pathlib.Path(cfg["current"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def validate_state(state):
    keys={"ok","unit_path","previous_state","previous_target","previous_working_directory","previous_exec_argv","previous_environment_file","previous_inline_environment","previous_fragment_path"}
    if not isinstance(state, dict) or set(state) != keys or state.get("ok") is not True: fail("bridge backup state schema invalid")
    if state["unit_path"] != str(unit) or state["previous_fragment_path"] != str(unit): fail("bridge backup unit state mismatch")
    if state["previous_state"] not in ("absent","symlink"): fail("bridge backup previous state invalid")
    if not isinstance(state["previous_exec_argv"], list) or not all(isinstance(x, str) for x in state["previous_exec_argv"]): fail("bridge backup exec argv invalid")
    return state
backup.mkdir(mode=0o700, parents=False, exist_ok=False)
if not unit.is_file() or unit.is_symlink(): fail("bridge unit file is not safe")
shutil.copy2(unit, backup/"bridge-unit.service")
state=validate_state(cfg["state"])
meta={"schema":"nmbot.bridge_backup.v1","unit_path":str(unit),"previous_state":state["previous_state"],"previous_target":state["previous_target"],"previous_fragment_path":state["previous_fragment_path"],"previous_working_directory":state["previous_working_directory"],"previous_environment_file":state["previous_environment_file"],"previous_inline_environment":state["previous_inline_environment"],"previous_exec_argv":state["previous_exec_argv"]}
(backup/"metadata.json").write_text(json.dumps(meta, sort_keys=True, indent=2)+"\n", encoding="utf-8")
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bridge_unit_replace_command(unit_path: str) -> str:
    content = "\n".join(["[Unit]", "Description=Novostroy Bot N8N Bridge", "After=network.target", "", "[Service]", f"WorkingDirectory={DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}", f"EnvironmentFile={DEFAULT_REMOTE_ROOT}/.env", f"Environment={BRIDGE_INLINE_ENVIRONMENT}", f"ExecStart=/usr/bin/python3 {DEFAULT_REMOTE_ROOT}/{BRIDGE_CURRENT}/{BRIDGE_ENTRYPOINT} --host 0.0.0.0 --port 8093", "Restart=always", "RestartSec=5", "", "[Install]", "WantedBy=default.target", ""]) 
    payload = json.dumps({"unit_path": unit_path, "content": content}, sort_keys=True)
    code = r'''
import json, os, pathlib, sys, tempfile
cfg=json.loads(sys.argv[1]); unit=pathlib.Path(cfg["unit_path"])
fd,tmp=tempfile.mkstemp(prefix=unit.name+".bridge.", dir=str(unit.parent)); os.close(fd)
pathlib.Path(tmp).write_text(cfg["content"], encoding="utf-8"); os.chmod(tmp,0o600); os.replace(tmp, unit)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bridge_health_command(remote_root: str, release_id: str) -> str:
    paths = _bridge_remote_paths(remote_root, release_id)
    payload = json.dumps({"url": BRIDGE_HEALTH_URL, "current": paths["current"], "rid": paths["rid"]}, sort_keys=True)
    code = r'''
import hashlib, json, pathlib, sys, time, urllib.request
cfg=json.loads(sys.argv[1]); deadline=time.time()+20; cur=pathlib.Path(cfg["current"]); last=""
while time.time()<deadline:
    try:
        h=json.loads(urllib.request.urlopen(cfg["url"], timeout=3).read().decode())
        m=json.loads((cur/"bridge-release-manifest.json").read_text(encoding="utf-8"))
        hashes=all(hashlib.sha256((cur/i["path"]).read_bytes()).hexdigest()==i["sha256"] for i in m.get("files",[]))
        if h.get("ok") is True and m.get("release_id")==cfg["rid"] and hashes:
            print(json.dumps({"ok": True, "release_id": cfg["rid"]})); sys.exit(0)
        last="bridge health/manifest mismatch"
    except Exception as exc: last=type(exc).__name__
    time.sleep(1)
print(json.dumps({"ok": False, "error": last})); sys.exit(2)
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _bridge_rollback_command(remote_root: str, release_id: str, unit_path: str) -> str:
    paths = _bridge_remote_paths(remote_root, release_id)
    payload = json.dumps({"backup": paths["backup_dir"], "unit_path": unit_path, "current": paths["current"], "new_target": f"{BRIDGE_RELEASES}/{paths['rid']}", "service": BRIDGE_SERVICE, "health_url": BRIDGE_HEALTH_URL}, sort_keys=True)
    code = r'''
import json, os, pathlib, re, shlex, shutil, subprocess, sys, time, urllib.request
cfg=json.loads(sys.argv[1]); backup=pathlib.Path(cfg["backup"]); cur=pathlib.Path(cfg["current"]); unit=pathlib.Path(cfg["unit_path"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
def run(args):
    p=subprocess.run(args, text=True, capture_output=True, timeout=15)
    if p.returncode != 0: fail("rollback command failed: "+" ".join(args))
def argv_from_execstart(raw):
    m=re.search(r"argv\[\]=([^;}]+)", raw or "")
    return shlex.split(m.group(1).strip()) if m else []
def env_file_exact(raw, expected):
    s=" ".join((raw or "").strip().split())
    if not s: return False
    if s == expected or s == expected+" (ignore_errors=no)": return True
    m=re.fullmatch(r"\{\s*path=([^\s;]+)\s*;\s*ignore_errors=(yes|no)\s*;\s*\}", s)
    return bool(m and m.group(1)==expected and m.group(2)=="no")
def inline_env_exact(raw, expected):
    return (raw or "").strip() == expected
def validate_meta(meta):
    keys={"schema","unit_path","previous_state","previous_target","previous_fragment_path","previous_working_directory","previous_environment_file","previous_inline_environment","previous_exec_argv"}
    if not isinstance(meta, dict) or set(meta) != keys or meta.get("schema") != "nmbot.bridge_backup.v1": fail("bridge rollback metadata schema invalid")
    for key in keys-{"previous_exec_argv"}:
        if not isinstance(meta[key], str): fail("bridge rollback metadata field invalid: "+key)
    if meta["unit_path"] != cfg["unit_path"] or meta["previous_fragment_path"] != cfg["unit_path"]: fail("bridge rollback metadata unit mismatch")
    if meta["previous_state"] not in ("absent","symlink"): fail("bridge rollback metadata previous_state invalid")
    if meta["previous_state"] == "absent" and meta["previous_target"]: fail("bridge rollback metadata absent target invalid")
    if meta["previous_state"] == "symlink" and not re.fullmatch(r"bridge-releases/[A-Za-z0-9][A-Za-z0-9._-]{2,79}", meta["previous_target"]): fail("bridge rollback metadata target invalid")
    if not isinstance(meta["previous_exec_argv"], list) or not all(isinstance(x, str) for x in meta["previous_exec_argv"]): fail("bridge rollback metadata exec argv invalid")
    return meta
try:
    meta=validate_meta(json.loads((backup/"metadata.json").read_text(encoding="utf-8")))
except Exception as exc:
    fail("bridge rollback metadata malformed")
shutil.copy2(backup/"bridge-unit.service", unit)
if cur.is_symlink() and os.readlink(cur)==cfg["new_target"]: cur.unlink()
if meta.get("previous_state")=="symlink" and meta.get("previous_target"):
    tmp=cur.parent/(".bridge-current.rollback.tmp");
    if tmp.exists() or tmp.is_symlink(): tmp.unlink()
    tmp.symlink_to(meta["previous_target"]); os.replace(tmp, cur)
elif os.path.lexists(cur):
    if cur.is_symlink(): cur.unlink()
run(["systemctl","--user","daemon-reload"])
run(["systemctl","--user","restart",cfg["service"]])
if meta.get("previous_state")=="absent" and os.path.lexists(cur): fail("rollback current should be absent")
if meta.get("previous_state")=="symlink":
    if not cur.is_symlink() or os.readlink(cur) != meta.get("previous_target",""): fail("rollback current target mismatch")
show=subprocess.run(["systemctl","--user","show",cfg["service"],"--no-pager"], text=True, capture_output=True, timeout=10)
if show.returncode != 0: fail("rollback unit show failed")
data={}
for line in show.stdout.splitlines():
    if "=" in line:
        k,v=line.split("=",1); data[k]=v
if pathlib.Path(data.get("FragmentPath","")).resolve() != pathlib.Path(meta.get("previous_fragment_path", cfg["unit_path"])).resolve(): fail("rollback unit fragment mismatch")
if data.get("WorkingDirectory","") != meta.get("previous_working_directory",""): fail("rollback unit working directory mismatch")
if not env_file_exact(data.get("EnvironmentFiles", data.get("EnvironmentFile", "")), meta.get("previous_environment_file","")): fail("rollback unit env mismatch")
if not inline_env_exact(data.get("Environment", ""), meta.get("previous_inline_environment", "")): fail("rollback unit inline env mismatch")
if argv_from_execstart(data.get("ExecStart", "")) != meta.get("previous_exec_argv",[]): fail("rollback unit exec mismatch")
if data.get("ActiveState","") != "active" or data.get("SubState","") not in ("running", "") or data.get("MainPID","") in ("", "0"): fail("rollback bridge is not active/running with pid")
deadline=time.time()+20; last=""
while time.time()<deadline:
    try:
        h=json.loads(urllib.request.urlopen(cfg["health_url"], timeout=3).read().decode())
        if h.get("ok") is True:
            print(json.dumps({"ok": True})); sys.exit(0)
        last="health false"
    except Exception as exc: last=type(exc).__name__
    time.sleep(1)
fail("rollback health failed: "+last)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def bridge_deploy(*, release_id: str, archive: Path, manifest_path: Path, confirm: bool, remote: Remote, host: str = AUTHORIZED_DEPLOY_HOST, port: str = AUTHORIZED_DEPLOY_PORT, remote_root: str = DEFAULT_REMOTE_ROOT, source_snapshot_manifest_sha256: str | None = None) -> str:
    if not confirm:
        raise ReleaseError("bridge-deploy requires --confirm")
    if host != AUTHORIZED_DEPLOY_HOST or port != AUTHORIZED_DEPLOY_PORT or remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bridge-deploy target is not authorized")
    if not isinstance(source_snapshot_manifest_sha256, str) or not HEX_RE.fullmatch(source_snapshot_manifest_sha256):
        raise ReleaseError("bridge-deploy requires explicit source snapshot manifest sha256 provenance")
    manifest = load_bridge_manifest(manifest_path)
    provenance = _validate_bridge_release_provenance(manifest["source_provenance"], require_present=True)
    if provenance["source_snapshot_manifest_sha256"] != source_snapshot_manifest_sha256:
        raise ReleaseError("source snapshot manifest sha256 does not match bridge artifact provenance")
    rid = _release_id(release_id)
    if rid != manifest["release_id"]:
        raise ReleaseError("bridge deploy release_id does not match manifest")
    bridge_preflight(archive=archive, manifest_path=manifest_path)
    paths = _bridge_remote_paths(remote_root, rid)
    mutation_started = False
    lock_acquired = False
    deployment_error: Exception | None = None
    cleanup_error: str | None = None
    unit_path = BRIDGE_UNIT_PATH
    try:
        state = _remote_json(remote, _bridge_remote_guard_command(remote_root, manifest, source_snapshot_manifest_sha256))
        unit_path = str(state.get("unit_path") or BRIDGE_UNIT_PATH)
        _remote_ok(remote.run("mkdir " + shlex.quote(paths["lock_dir"])))
        lock_acquired = True
        _remote_ok(remote.run(_bridge_backup_command(remote_root, rid, unit_path, state)))
        _remote_ok(remote.run("mkdir -p " + shlex.quote(paths["staging"]) + " " + shlex.quote(f"{remote_root}/{BRIDGE_RELEASES}")))
        remote_archive = paths["staging"] + "/" + archive.name
        remote_manifest = paths["staging"] + "/" + manifest_path.name
        _remote_ok(remote.upload(archive, remote_archive))
        _remote_ok(remote.upload(manifest_path, remote_manifest))
        _remote_ok(remote.run(_bridge_remote_extract_command(paths["staging"], paths["release_dir"], manifest)))
        _remote_ok(remote.run(_bridge_remote_preflight_command(paths["release_dir"])))
        mutation_started = True
        tmp_link = f"{remote_root}/.bridge-current.{rid}.tmp"
        _remote_ok(remote.run("ln -sfn " + shlex.quote(f"{BRIDGE_RELEASES}/{rid}") + " " + shlex.quote(tmp_link) + " && mv -Tf " + shlex.quote(tmp_link) + " " + shlex.quote(paths["current"])))
        _remote_ok(remote.run(_bridge_unit_replace_command(unit_path)))
        _remote_ok(remote.run("systemctl --user daemon-reload"))
        _remote_ok(remote.run("systemctl --user restart " + shlex.quote(BRIDGE_SERVICE)))
        _remote_ok(remote.run(_bridge_health_command(remote_root, rid)))
    except Exception as exc:
        deployment_error = exc
        if mutation_started:
            try:
                _remote_ok(remote.run(_bridge_rollback_command(remote_root, rid, unit_path)))
            except Exception as rollback_exc:
                deployment_error = ReleaseError(f"bridge-deploy failed: {exc}; rollback failed: {rollback_exc}")
    finally:
        if lock_acquired:
            cleanup_proc = remote.run("rmdir " + shlex.quote(paths["lock_dir"]))
            if cleanup_proc.returncode != 0:
                cleanup_error = (cleanup_proc.stdout + cleanup_proc.stderr)[-2000:] or "bridge release lock cleanup failed"
    if deployment_error:
        if cleanup_error:
            raise ReleaseError(f"{deployment_error}; bridge release lock cleanup failed: {cleanup_error}") from deployment_error
        raise deployment_error
    if cleanup_error:
        raise ReleaseError(f"bridge deploy completed but release lock cleanup failed; bridge state is preserved: {cleanup_error}")
    return f"bridge-deploy=ok release_id={rid}\n"


def _publish_identity_command(remote_root: str, release_id: str) -> str:
    rid = _release_id(release_id)
    release_dir = f"{remote_root.rstrip('/')}/releases/{rid}"
    source = f"{release_dir}/{IDENTITY_IN_RELEASE}"
    target = f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}"
    temporary = target + f".{rid}.tmp"
    return " && ".join([
        "test -f " + shlex.quote(source),
        "cp -f " + shlex.quote(source) + " " + shlex.quote(temporary),
        "chmod 600 " + shlex.quote(temporary),
        "mv -Tf " + shlex.quote(temporary) + " " + shlex.quote(target),
    ])


def _dialogue_exporter_backup_dir(remote_root: str, release_id: str) -> str:
    return f"{remote_root.rstrip('/')}/backups/dialogue-exporter-{_release_id(release_id)}"


def _dialogue_exporter_backup_command(remote_root: str, release_id: str) -> str:
    payload = json.dumps({
        "backup": _dialogue_exporter_backup_dir(remote_root, release_id),
        "paths": {
            "script": NMBOT_DIALOGUE_EXPORTER_REMOTE_SCRIPT,
            "service": NMBOT_DIALOGUE_EXPORTER_REMOTE_SERVICE,
            "timer": NMBOT_DIALOGUE_EXPORTER_REMOTE_TIMER,
        },
        "timer_unit": NMBOT_DIALOGUE_EXPORTER_TIMER_UNIT,
    }, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, subprocess, sys
cfg=json.loads(sys.argv[1]); backup=pathlib.Path(cfg["backup"]); paths=cfg["paths"]
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
if backup.exists() or backup.is_symlink(): fail("dialogue exporter backup already exists")
backup.mkdir(mode=0o700, parents=False)
meta={"schema":"nmbot.dialogue_exporter_backup.v1","paths":paths,"timer_unit":cfg["timer_unit"],"files":{},"timer_enabled":"unknown","timer_active":"unknown"}
for name, raw in paths.items():
    p=pathlib.Path(raw); row={"exists": False}
    if os.path.lexists(p):
        if p.is_symlink() or not p.is_file(): fail("dialogue exporter existing path is not safe regular file: "+name)
        shutil.copy2(p, backup/(name+".bak")); row={"exists": True, "backup": name+".bak"}
    meta["files"][name]=row
for field, args in (("timer_enabled", ["systemctl","--user","is-enabled",cfg["timer_unit"]]), ("timer_active", ["systemctl","--user","is-active",cfg["timer_unit"]])):
    proc=subprocess.run(args, text=True, capture_output=True, timeout=10)
    meta[field]=(proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else "unknown"
(backup/"metadata.json").write_text(json.dumps(meta, sort_keys=True, indent=2)+"\n", encoding="utf-8")
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _dialogue_exporter_install_command(release_dir: str, manifest: dict[str, Any]) -> str:
    expected = {item["path"]: item["sha256"] for item in manifest["files"] if item["path"] in NMBOT_DIALOGUE_EXPORTER_FILES}
    if set(expected) != NMBOT_DIALOGUE_EXPORTER_FILES:
        raise ReleaseError("dialogue exporter install requires exact allowlisted files")
    payload = json.dumps({
        "release_dir": release_dir,
        "expected": expected,
        "destinations": {
            NMBOT_DIALOGUE_EXPORTER_SCRIPT: NMBOT_DIALOGUE_EXPORTER_REMOTE_SCRIPT,
            NMBOT_DIALOGUE_EXPORTER_SERVICE_TEMPLATE: NMBOT_DIALOGUE_EXPORTER_REMOTE_SERVICE,
            NMBOT_DIALOGUE_EXPORTER_TIMER_TEMPLATE: NMBOT_DIALOGUE_EXPORTER_REMOTE_TIMER,
        },
        "timer_unit": NMBOT_DIALOGUE_EXPORTER_TIMER_UNIT,
    }, sort_keys=True)
    code = r'''
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile
cfg=json.loads(sys.argv[1]); root=pathlib.Path(cfg["release_dir"]); expected=cfg["expected"]
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
for rel, sha in expected.items():
    p=root/rel
    if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha: fail("dialogue exporter source hash mismatch: "+rel)
for rel, raw_dest in cfg["destinations"].items():
    src=root/rel; dest=pathlib.Path(raw_dest); dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=dest.name+".nmbot.", dir=str(dest.parent)); os.close(fd)
    shutil.copy2(src, tmp); os.chmod(tmp, 0o755 if rel.endswith(".py") else 0o600); os.replace(tmp, dest)
subprocess.run(["systemctl","--user","daemon-reload"], check=True, timeout=20)
subprocess.run(["systemctl","--user","enable","--now",cfg["timer_unit"]], check=True, timeout=20)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def _dialogue_exporter_rollback_command(remote_root: str, release_id: str) -> str:
    payload = json.dumps({"backup": _dialogue_exporter_backup_dir(remote_root, release_id), "timer_unit": NMBOT_DIALOGUE_EXPORTER_TIMER_UNIT}, sort_keys=True)
    code = r'''
import json, os, pathlib, shutil, subprocess, sys
cfg=json.loads(sys.argv[1]); backup=pathlib.Path(cfg["backup"])
def fail(msg): print(json.dumps({"ok": False, "error": msg})); sys.exit(2)
try: meta=json.loads((backup/"metadata.json").read_text(encoding="utf-8"))
except Exception: fail("dialogue exporter rollback metadata missing")
if meta.get("schema") != "nmbot.dialogue_exporter_backup.v1" or meta.get("timer_unit") != cfg["timer_unit"]: fail("dialogue exporter rollback metadata invalid")
subprocess.run(["systemctl","--user","disable","--now",cfg["timer_unit"]], text=True, capture_output=True, timeout=20)
for name, row in meta.get("files", {}).items():
    dest=pathlib.Path(meta["paths"][name])
    if row.get("exists") is True:
        src=backup/row.get("backup", "")
        if not src.is_file() or src.is_symlink(): fail("dialogue exporter backup file missing: "+name)
        shutil.copy2(src, dest)
    else:
        if os.path.lexists(dest):
            if dest.is_file() and not dest.is_symlink(): dest.unlink()
            else: fail("dialogue exporter rollback unsafe destination: "+name)
subprocess.run(["systemctl","--user","daemon-reload"], check=True, timeout=20)
if meta.get("timer_enabled") == "enabled": subprocess.run(["systemctl","--user","enable",cfg["timer_unit"]], check=True, timeout=20)
if meta.get("timer_active") == "active": subprocess.run(["systemctl","--user","start",cfg["timer_unit"]], check=True, timeout=20)
print(json.dumps({"ok": True}))
'''
    return "python3 -c " + shlex.quote(code) + " " + shlex.quote(payload)


def deploy(*, release_id: str, archive: Path, manifest_path: Path, confirm: bool, remote: Remote, remote_root: str = DEFAULT_REMOTE_ROOT, source_snapshot_manifest_sha256: str | None = None) -> str:
    if not confirm:
        raise ReleaseError("deploy requires --confirm")
    if isinstance(remote, SshRemote) and (not isinstance(source_snapshot_manifest_sha256, str) or not HEX_RE.fullmatch(source_snapshot_manifest_sha256)):
        raise ReleaseError("deploy requires explicit source snapshot manifest sha256 provenance")
    manifest = load_manifest(manifest_path)
    provenance = _validate_release_source_provenance(manifest.get("source_provenance"), require_present=source_snapshot_manifest_sha256 is not None or isinstance(remote, SshRemote))
    if source_snapshot_manifest_sha256 is not None and provenance.get("source_snapshot_manifest_sha256") != source_snapshot_manifest_sha256:
        raise ReleaseError("source snapshot manifest sha256 does not match artifact provenance")
    rid = _release_id(release_id)
    if rid != manifest["release_id"]:
        raise ReleaseError("deploy release_id does not match manifest")
    include_dialogue_exporter = _manifest_has_dialogue_exporter(manifest)
    callback_worker = manifest["import_modules"] == list(V6_CALLBACK_WORKER_IMPORT_MODULES)
    if include_dialogue_exporter and remote_root.rstrip("/") != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("dialogue exporter remote paths are fixed to the default API root")
    if callback_worker and remote_root.rstrip("/") != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("callback worker remote paths are fixed to the default API root")
    verify_archive_against_manifest(archive, manifest)
    _assert_remote_unit_migrated(remote, remote_root=remote_root)
    _remote_ok(remote.run(_remote_guard_command(remote_root, manifest)))
    staging = f"{remote_root}/.release_staging/{rid}"
    release_dir = f"{remote_root}/releases/{rid}"
    lock_dir = f"{remote_root}/.release_lock"
    cutover_started = False
    worker_unit_changed = False
    lock_acquired = False
    cleanup_error: str | None = None
    deployment_error: Exception | None = None
    try:
        _remote_ok(remote.run("mkdir " + shlex.quote(lock_dir)))
        lock_acquired = True
        previous_data = _remote_json(remote, _previous_state_probe_command(remote_root, rid))
        prev_id = _release_id(str(previous_data.get("previous_id") or "")) if previous_data.get("previous_id") else ""
        if not prev_id:
            raise ReleaseError("no valid previous current release; first migration is not implemented")
        if previous_data.get("release_exists"):
            raise ReleaseError("release id already exists; immutable releases cannot be overwritten")
        _remote_ok(remote.run("mkdir -p " + shlex.quote(staging) + " " + shlex.quote(remote_root + "/releases")))
        remote_archive = staging + "/" + archive.name
        remote_manifest = staging + "/" + manifest_path.name
        _remote_ok(remote.upload(archive, remote_archive))
        _remote_ok(remote.upload(manifest_path, remote_manifest))
        _remote_ok(remote.run(_remote_extract_command(staging, release_dir, manifest)))
        for name in manifest["config_schema_requirements"]["external_runtime_paths"]:
            _remote_ok(remote.run("ln -sfn " + shlex.quote(remote_root + "/" + name) + " " + shlex.quote(release_dir + "/" + name)))
        v6_only = manifest["import_modules"] == list(V6_ONLY_IMPORT_MODULES)
        compile_files = list(V6_ONLY_PREFLIGHT_PY_FILES if v6_only else V6_CALLBACK_WORKER_PREFLIGHT_PY_FILES if callback_worker else REMOTE_PREFLIGHT_PY_FILES)
        if include_dialogue_exporter:
            _remote_ok(remote.run(_dialogue_exporter_backup_command(remote_root, rid)))
            compile_files.append(NMBOT_DIALOGUE_EXPORTER_SCRIPT)
            compile_files.extend(sorted(NMBOT_DIALOGUE_EXPORTER_DEPENDENCY_FILES))
        _remote_ok(remote.run(_remote_preflight_command(release_dir, list(manifest["import_modules"]), sorted(compile_files), profile=V6_ONLY_PROFILE if v6_only else V6_CALLBACK_WORKER_PROFILE if callback_worker else None)))
        tmp_link = f"{remote_root}/.current.{rid}.tmp"
        cutover_started = True
        if callback_worker:
            _remote_ok(remote.run("systemctl --user stop " + shlex.quote(CALLBACK_WORKER_SERVICE)))
            _remote_ok(remote.run(_callback_worker_install_command(remote_root, rid)))
            worker_unit_changed = True
        _remote_ok(remote.run(_stop_api_command()))
        _remote_ok(remote.run(_api_inactive_command()))
        _remote_ok(remote.run("ln -sfn " + shlex.quote("releases/" + rid) + " " + shlex.quote(tmp_link) + " && mv -Tf " + shlex.quote(tmp_link) + " " + shlex.quote(remote_root + "/current")))
        _remote_ok(remote.run(_publish_identity_command(remote_root, rid)))
        _remote_ok(remote.run(_start_api_command()))
        _remote_ok(remote.run(_health_and_identity_command(remote_root, rid, f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}")))
        if callback_worker:
            _remote_ok(remote.run("systemctl --user start " + shlex.quote(CALLBACK_WORKER_SERVICE)))
        if include_dialogue_exporter:
            _remote_ok(remote.run(_dialogue_exporter_install_command(release_dir, manifest)))
    except Exception as deploy_exc:
        deployment_error = deploy_exc
        if cutover_started:
            rollback_errors: list[Exception] = []
            try:
                if include_dialogue_exporter:
                    try:
                        _remote_ok(remote.run(_dialogue_exporter_rollback_command(remote_root, rid)))
                    except Exception as exporter_rollback_exc:
                        rollback_errors.append(exporter_rollback_exc)
                _remote_ok(remote.run(_stop_api_command()))
                _remote_ok(remote.run(_api_inactive_command()))
                if callback_worker and worker_unit_changed:
                    _remote_ok(remote.run("systemctl --user stop " + shlex.quote(CALLBACK_WORKER_SERVICE)))
                    _remote_ok(remote.run(_callback_worker_restore_command(remote_root, rid)))
                tmp_prev = f"{remote_root}/.current.rollback.tmp"
                _remote_ok(remote.run("ln -sfn " + shlex.quote("releases/" + prev_id) + " " + shlex.quote(tmp_prev) + " && mv -Tf " + shlex.quote(tmp_prev) + " " + shlex.quote(remote_root + "/current")))
                _remote_ok(remote.run(_publish_identity_command(remote_root, prev_id)))
                _remote_ok(remote.run(_start_api_command()))
                if callback_worker and worker_unit_changed:
                    _remote_ok(remote.run("systemctl --user start " + shlex.quote(CALLBACK_WORKER_SERVICE)))
                _remote_ok(remote.run(_health_and_identity_command(remote_root, prev_id, f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}")))
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
            if rollback_errors:
                deployment_error = ReleaseError(f"deploy failed: {deploy_exc}; rollback failed: " + "; ".join(str(err) for err in rollback_errors))
    finally:
        if lock_acquired:
            cleanup_proc = remote.run("rmdir " + shlex.quote(lock_dir))
            if cleanup_proc.returncode != 0:
                cleanup_error = (cleanup_proc.stdout + cleanup_proc.stderr)[-2000:] or "release lock cleanup failed"
    if deployment_error:
        if cleanup_error:
            raise ReleaseError(f"{deployment_error}; release lock cleanup failed: {cleanup_error}") from deployment_error
        raise deployment_error
    if cleanup_error:
        raise ReleaseError(f"deploy completed but release lock cleanup failed; API state is preserved, remove stale lock manually after verification: {cleanup_error}")
    return f"deploy=ok release_id={rid}\n"


def bootstrap_apply(*, release_id: str, archive: Path, manifest_path: Path, confirm: bool, remote: Remote, host: str = AUTHORIZED_DEPLOY_HOST, port: str = AUTHORIZED_DEPLOY_PORT, remote_root: str = DEFAULT_REMOTE_ROOT, source_snapshot_manifest_sha256: str | None = None) -> str:
    if not confirm:
        raise ReleaseError("bootstrap-apply requires --confirm")
    if host != AUTHORIZED_DEPLOY_HOST or port != AUTHORIZED_DEPLOY_PORT or remote_root != DEFAULT_REMOTE_ROOT:
        raise ReleaseError("bootstrap-apply target is not authorized")
    if not isinstance(source_snapshot_manifest_sha256, str) or not HEX_RE.fullmatch(source_snapshot_manifest_sha256):
        raise ReleaseError("bootstrap-apply requires explicit source snapshot manifest sha256 provenance")
    manifest = load_manifest(manifest_path)
    provenance = _validate_release_source_provenance(manifest.get("source_provenance"), require_present=True)
    if provenance.get("source_snapshot_manifest_sha256") != source_snapshot_manifest_sha256:
        raise ReleaseError("source snapshot manifest sha256 does not match artifact provenance")
    rid = _release_id(release_id)
    if rid != manifest["release_id"]:
        raise ReleaseError("bootstrap release_id does not match manifest")
    verify_archive_against_manifest(archive, manifest)
    paths = _bootstrap_release_paths(remote_root, rid)
    mutation_started = False
    lock_acquired = False
    bootstrap_error: Exception | None = None
    cleanup_error: str | None = None
    unit_path = ""
    try:
        state = _remote_json(remote, _bootstrap_preconditions_command(remote_root, rid))
        unit_path = str(state.get("unit_path") or "")
        _remote_ok(remote.run(_bootstrap_guard_command(remote_root, manifest)))
        _remote_ok(remote.run("mkdir " + shlex.quote(paths["lock_dir"])))
        lock_acquired = True
        _remote_ok(remote.run(_bootstrap_backup_command(remote_root, rid, unit_path, state)))
        _remote_ok(remote.run("mkdir -p " + shlex.quote(paths["staging"]) + " " + shlex.quote(remote_root.rstrip("/") + "/releases")))
        remote_archive = paths["staging"] + "/" + archive.name
        remote_manifest = paths["staging"] + "/" + manifest_path.name
        _remote_ok(remote.upload(archive, remote_archive))
        _remote_ok(remote.upload(manifest_path, remote_manifest))
        _remote_ok(remote.run(_remote_extract_command(paths["staging"], paths["release_dir"], manifest)))
        for name in manifest["config_schema_requirements"]["external_runtime_paths"]:
            _remote_ok(remote.run("ln -sfn " + shlex.quote(remote_root.rstrip("/") + "/" + name) + " " + shlex.quote(paths["release_dir"] + "/" + name)))
        v6_only = manifest["import_modules"] == list(V6_ONLY_IMPORT_MODULES)
        _remote_ok(remote.run(_remote_preflight_command(paths["release_dir"], list(manifest["import_modules"]), profile=V6_ONLY_PROFILE if v6_only else None)))
        _remote_ok(remote.run(_stop_api_command()))
        _remote_ok(remote.run(_api_inactive_command()))
        mutation_started = True
        _remote_ok(remote.run(_bootstrap_env_update_command(remote_root, rid)))
        _remote_ok(remote.run(_bootstrap_unit_replace_command(remote_root, unit_path)))
        _remote_ok(remote.run("systemctl --user daemon-reload"))
        tmp_link = f"{remote_root.rstrip('/')}/.current.{rid}.tmp"
        _remote_ok(remote.run("ln -sfn " + shlex.quote("releases/" + rid) + " " + shlex.quote(tmp_link) + " && mv -Tf " + shlex.quote(tmp_link) + " " + shlex.quote(remote_root.rstrip("/") + "/current")))
        _remote_ok(remote.run(_publish_identity_command(remote_root, rid)))
        _remote_ok(remote.run(_start_api_command()))
        _remote_ok(remote.run(_health_and_identity_command(remote_root, rid, f"{remote_root.rstrip('/')}/{IDENTITY_EXTERNAL}")))
        _assert_remote_unit_migrated(remote, remote_root=remote_root)
    except Exception as exc:
        bootstrap_error = exc
        if mutation_started:
            try:
                _remote_ok(remote.run(_bootstrap_rollback_command(remote_root, rid, unit_path)))
                _remote_ok(remote.run(_health_config_command()))
            except Exception as rollback_exc:
                bootstrap_error = ReleaseError(f"bootstrap-apply failed: {exc}; rollback failed: {rollback_exc}")
    finally:
        if lock_acquired:
            cleanup_proc = remote.run("rmdir " + shlex.quote(paths["lock_dir"]))
            if cleanup_proc.returncode != 0:
                cleanup_error = (cleanup_proc.stdout + cleanup_proc.stderr)[-2000:] or "release lock cleanup failed"
    if bootstrap_error:
        if cleanup_error:
            raise ReleaseError(f"{bootstrap_error}; release lock cleanup failed: {cleanup_error}") from bootstrap_error
        raise bootstrap_error
    if cleanup_error:
        raise ReleaseError(f"bootstrap-apply completed but release lock cleanup failed; remove stale lock manually after verification: {cleanup_error}")
    return f"bootstrap-apply=ok release_id={rid} backup=bootstrap-{rid}\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atomic API-release helper for NMBot")
    sub = p.add_subparsers(dest="command")
    b = sub.add_parser("build")
    b.add_argument("--release-id")
    b.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    b.add_argument("--include-dialogue-exporter", action="store_true")
    bw = sub.add_parser("build-from-worktree")
    bw.add_argument("--worktree-dir", type=Path, required=True)
    bw.add_argument("--release-id")
    bw.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    bw.add_argument("--include-dialogue-exporter", action="store_true")
    bw.add_argument("--profile", choices=(V6_ONLY_PROFILE, V6_CALLBACK_WORKER_PROFILE))
    pre = sub.add_parser("preflight")
    pre.add_argument("--archive", type=Path, required=True)
    pre.add_argument("--manifest", type=Path, required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--release-id", required=True)
    sub.add_parser("migration-plan")
    recon_p = sub.add_parser("recon")
    recon_p.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    recon_p.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    cap = sub.add_parser("capture-baseline")
    cap.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    cap.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    cap.add_argument("--out-dir", type=Path, default=DEFAULT_BOOTSTRAP_OUT_DIR)
    cap.add_argument("--release-id", default="baseline-capture")
    snap = sub.add_parser("snapshot-vps-source")
    snap.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    snap.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    snap.add_argument("--contour", choices=SNAPSHOT_CONTOURS, default=DEFAULT_SNAPSHOT_CONTOUR)
    snap.add_argument("--out-dir", type=Path, default=DEFAULT_BOOTSTRAP_OUT_DIR)
    snap.add_argument("--discard-tar", action="store_true")
    work = sub.add_parser("prepare-worktree")
    work.add_argument("--snapshot-dir", type=Path, required=True)
    work.add_argument("--out-dir", type=Path, required=True)
    trel = sub.add_parser("test-release")
    trel.add_argument("--release-id", required=True)
    overlay_mode = trel.add_mutually_exclusive_group()
    overlay_mode.add_argument("--overlay", action="append", default=[])
    overlay_mode.add_argument("--auto-overlays", action="store_true")
    trel.add_argument("--out-dir", type=Path)
    trel.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    trel.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    trel.add_argument("--confirm", action="store_true")
    helper_overlay = sub.add_parser("live-api-helper-overlay")
    helper_overlay.add_argument("--release-id", required=True)
    helper_overlay.add_argument("--host", required=True)
    helper_overlay.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    helper_overlay.add_argument("--confirm", action="store_true")
    bsnap = sub.add_parser("snapshot-vps-bridge-source")
    bsnap.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    bsnap.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    bsnap.add_argument("--out-dir", type=Path, default=DEFAULT_BOOTSTRAP_OUT_DIR)
    bsnap.add_argument("--discard-tar", action="store_true")
    bwork = sub.add_parser("prepare-bridge-worktree")
    bwork.add_argument("--snapshot-dir", type=Path, required=True)
    bwork.add_argument("--out-dir", type=Path, required=True)
    bbuild = sub.add_parser("build-bridge-from-worktree")
    bbuild.add_argument("--worktree-dir", type=Path, required=True)
    bbuild.add_argument("--release-id", required=True)
    bbuild.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    bpre = sub.add_parser("bridge-preflight")
    bpre.add_argument("--archive", type=Path, required=True)
    bpre.add_argument("--manifest", type=Path, required=True)
    brecon = sub.add_parser("bridge-recon")
    brecon.add_argument("--host", default=AUTHORIZED_DEPLOY_HOST)
    brecon.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    bdep = sub.add_parser("bridge-deploy")
    bdep.add_argument("--release-id", required=True)
    bdep.add_argument("--archive", type=Path, required=True)
    bdep.add_argument("--manifest", type=Path, required=True)
    bdep.add_argument("--host", required=True)
    bdep.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    bdep.add_argument("--source-snapshot-manifest-sha256", required=True)
    bdep.add_argument("--confirm", action="store_true")
    cmp_p = sub.add_parser("compare-snapshot")
    cmp_p.add_argument("--snapshot-dir", type=Path, required=True)
    cmp_p.add_argument("--project-root", type=Path, default=ROOT)
    cmp_p.add_argument("--contour", choices=SNAPSHOT_CONTOURS, default=DEFAULT_SNAPSHOT_CONTOUR)
    boot = sub.add_parser("bootstrap-plan")
    boot.add_argument("--baseline-archive", type=Path, required=True)
    boot.add_argument("--baseline-manifest", type=Path, required=True)
    boot.add_argument("--candidate-archive", type=Path, required=True)
    boot.add_argument("--candidate-manifest", type=Path, required=True)
    boot.add_argument("--out-dir", type=Path, required=True)
    boot_apply = sub.add_parser("bootstrap-apply")
    boot_apply.add_argument("--release-id", required=True)
    boot_apply.add_argument("--baseline-archive", type=Path, required=True)
    boot_apply.add_argument("--baseline-manifest", type=Path, required=True)
    boot_apply.add_argument("--host", required=True)
    boot_apply.add_argument("--port", default=AUTHORIZED_DEPLOY_PORT)
    boot_apply.add_argument("--confirm", action="store_true")
    boot_apply.add_argument("--source-snapshot-manifest-sha256", required=True)
    dep = sub.add_parser("deploy")
    dep.add_argument("--release-id", required=True)
    dep.add_argument("--archive", type=Path, required=True)
    dep.add_argument("--manifest", type=Path, required=True)
    dep.add_argument("--host", required=True)
    dep.add_argument("--port", default="1905")
    dep.add_argument("--confirm", action="store_true")
    dep.add_argument("--source-snapshot-manifest-sha256", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = args.command or "plan"
    try:
        if command == "build":
            artifact = build(release_id=args.release_id, out_dir=args.out_dir, include_dialogue_exporter=args.include_dialogue_exporter)
            print(f"build=ok release_id={artifact.manifest_data['release_id']} archive={artifact.archive} manifest={artifact.manifest}")
        elif command == "build-from-worktree":
            artifact = build_from_worktree(worktree_dir=args.worktree_dir, release_id=args.release_id, out_dir=args.out_dir, include_dialogue_exporter=args.include_dialogue_exporter, profile=args.profile)
            provenance = artifact.manifest_data["source_provenance"]
            print(json.dumps({"build": "ok", "release_id": artifact.manifest_data["release_id"], "archive": str(artifact.archive), "manifest": str(artifact.manifest), "source_snapshot_id": provenance["source_snapshot_id"], "source_snapshot_manifest_sha256": provenance["source_snapshot_manifest_sha256"]}, ensure_ascii=False, sort_keys=True))
        elif command == "preflight":
            print(local_preflight(archive=args.archive, manifest_path=args.manifest), end="")
        elif command == "plan":
            print(render_plan(release_id=args.release_id), end="")
        elif command == "migration-plan":
            print(render_migration_plan(), end="")
        elif command == "recon":
            remote = SshRemote(host=args.host, port=args.port)
            print(json.dumps(recon(remote), ensure_ascii=False, indent=2, sort_keys=True))
        elif command == "capture-baseline":
            remote = SshRemote(host=args.host, port=args.port)
            artifact = capture_baseline(remote=remote, out_dir=args.out_dir, release_id=args.release_id)
            print(json.dumps({"capture": "ok", "release_id": artifact.manifest_data["release_id"], "archive": str(artifact.archive), "manifest": str(artifact.manifest), "archive_sha256": artifact.manifest_data["archive_sha256"], "files": len(artifact.manifest_data["files"])}, ensure_ascii=False, sort_keys=True))
        elif command == "snapshot-vps-source":
            remote = SshRemote(host=args.host, port=args.port)
            print(json.dumps(snapshot_vps_source(remote=remote, out_dir=args.out_dir, keep_tar=not args.discard_tar, contour=args.contour), ensure_ascii=False, sort_keys=True))
        elif command == "prepare-worktree":
            print(json.dumps(prepare_worktree(snapshot_dir=args.snapshot_dir, out_dir=args.out_dir), ensure_ascii=False, sort_keys=True))
        elif command == "test-release":
            _validate_test_release_target(host=args.host, port=args.port, confirm=args.confirm)
            _release_id(args.release_id)
            if not args.auto_overlays:
                _validate_overlay_path_list(args.overlay)
            _allowed_bootstrap_out_dir(args.out_dir or _default_test_release_out_dir(args.release_id))
            print(json.dumps(test_release(release_id=args.release_id, overlays=args.overlay, auto_overlays=args.auto_overlays, out_dir=args.out_dir, confirm=args.confirm, host=args.host, port=args.port), ensure_ascii=False, sort_keys=True))
        elif command == "live-api-helper-overlay":
            remote = SshRemote(host=args.host, port=args.port)
            print(json.dumps(live_api_helper_overlay(release_id=args.release_id, confirm=args.confirm, remote=remote, host=args.host, port=args.port), ensure_ascii=False, sort_keys=True))
        elif command == "snapshot-vps-bridge-source":
            remote = SshRemote(host=args.host, port=args.port)
            print(json.dumps(snapshot_vps_bridge_source(remote=remote, out_dir=args.out_dir, keep_tar=not args.discard_tar), ensure_ascii=False, sort_keys=True))
        elif command == "prepare-bridge-worktree":
            print(json.dumps(prepare_bridge_worktree(snapshot_dir=args.snapshot_dir, out_dir=args.out_dir), ensure_ascii=False, sort_keys=True))
        elif command == "build-bridge-from-worktree":
            artifact = build_bridge_from_worktree(worktree_dir=args.worktree_dir, release_id=args.release_id, out_dir=args.out_dir)
            provenance = artifact.manifest_data["source_provenance"]
            print(json.dumps({"build": "ok", "scope": "bridge", "release_id": artifact.manifest_data["release_id"], "archive": str(artifact.archive), "manifest": str(artifact.manifest), "source_snapshot_id": provenance["source_snapshot_id"], "source_snapshot_manifest_sha256": provenance["source_snapshot_manifest_sha256"]}, ensure_ascii=False, sort_keys=True))
        elif command == "bridge-preflight":
            print(bridge_preflight(archive=args.archive, manifest_path=args.manifest), end="")
        elif command == "bridge-recon":
            remote = SshRemote(host=args.host, port=args.port)
            print(json.dumps(bridge_recon(remote), ensure_ascii=False, indent=2, sort_keys=True))
        elif command == "compare-snapshot":
            print(json.dumps(compare_snapshot(snapshot_dir=args.snapshot_dir, project_root=args.project_root, contour=args.contour), ensure_ascii=False, sort_keys=True))
        elif command == "bootstrap-plan":
            plan_data = bootstrap_plan(baseline_archive=args.baseline_archive, baseline_manifest=args.baseline_manifest, candidate_archive=args.candidate_archive, candidate_manifest=args.candidate_manifest, out_dir=args.out_dir)
            print(json.dumps({"bootstrap_plan": "ok", "out_dir": str(_allowed_bootstrap_out_dir(args.out_dir)), "remote_writes_performed": plan_data["remote_writes_performed"], "cutover_authorized": plan_data["cutover_authorized"], "files": plan_data["generated_files"]}, ensure_ascii=False, sort_keys=True))
        elif command == "bootstrap-apply":
            remote = SshRemote(host=args.host, port=args.port)
            print(bootstrap_apply(release_id=args.release_id, archive=args.baseline_archive, manifest_path=args.baseline_manifest, confirm=args.confirm, remote=remote, host=args.host, port=args.port, source_snapshot_manifest_sha256=args.source_snapshot_manifest_sha256), end="")
        elif command == "deploy":
            remote = SshRemote(host=args.host, port=args.port)
            print(deploy(release_id=args.release_id, archive=args.archive, manifest_path=args.manifest, confirm=args.confirm, remote=remote, source_snapshot_manifest_sha256=args.source_snapshot_manifest_sha256), end="")
        elif command == "bridge-deploy":
            remote = SshRemote(host=args.host, port=args.port)
            print(bridge_deploy(release_id=args.release_id, archive=args.archive, manifest_path=args.manifest, confirm=args.confirm, remote=remote, host=args.host, port=args.port, source_snapshot_manifest_sha256=args.source_snapshot_manifest_sha256), end="")
        else:
            raise ReleaseError(f"unknown command: {command}")
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
