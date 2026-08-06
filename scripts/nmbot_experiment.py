#!/usr/bin/env python3
"""Local, offline experiment registry for declared NMBot development stages."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
STAGE_MAP_SCHEMA = "nmbot.stage_map.v1"
DEVELOPMENT_SCHEMA = "nmbot.development.v1"
PARAMETER_SCHEMA = "nmbot.parameter_profile.v1"
EXPERIMENT_SCHEMA = "nmbot.experiment.v1"
CHECK_SCHEMA = "nmbot.experiment_check.v1"
RECEIPT_SCHEMA = "nmbot.workflow_receipt.v1"
COMPARE_SCHEMA = "nmbot.experiment_compare.v1"
CAPABILITIES = {"prompt_candidate", "parameter_overlay", "focused_check", "full_check"}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SAFE_PARAM_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
EXECUTION_KEYS = {"argv", "command", "commands", "executable", "shell", "network", "url", "endpoint", "host", "environment", "env"}


class ExperimentError(ValueError):
    pass


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _canonical_hash(value: Any) -> str:
    return _hash_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _inside(root: Path, value: str | Path, *, label: str, must_exist: bool = False) -> Path:
    root = root.resolve()
    path = Path(value)
    path = path if path.is_absolute() else root / path
    try:
        resolved = path.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ExperimentError(f"{label} must stay inside root") from exc
    return resolved


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} must be a JSON object")
    return value


def _safe_repo_file(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute() or ".." in Path(value).parts:
        raise ExperimentError(f"invalid {label} path")
    path = _inside(root, value, label=label, must_exist=True)
    if not path.is_file():
        raise ExperimentError(f"missing {label}: {value}")
    return path


def _expanded_path(path_id: str, paths: dict[str, Any], stack: tuple[str, ...] = ()) -> list[str]:
    if path_id in stack:
        raise ExperimentError(f"path inheritance cycle: {' -> '.join((*stack, path_id))}")
    row = paths.get(path_id)
    if not isinstance(row, dict):
        raise ExperimentError(f"unknown path reference: {path_id}")
    stage_ids = row.get("stage_ids")
    if not isinstance(stage_ids, list) or any(not isinstance(item, str) or not item for item in stage_ids):
        raise ExperimentError(f"invalid stage_ids for path: {path_id}")
    inherited: list[str] = []
    if "extends" in row:
        if not isinstance(row["extends"], str):
            raise ExperimentError(f"invalid extends for path: {path_id}")
        inherited = _expanded_path(row["extends"], paths, (*stack, path_id))
    result = inherited + stage_ids
    if len(result) != len(set(result)):
        raise ExperimentError(f"duplicate stage_ids in path: {path_id}")
    return result


def _validate_parameter_profile(profile: Any) -> dict[str, Any]:
    if not isinstance(profile, dict) or profile.get("schema") != PARAMETER_SCHEMA or profile.get("version") != 1:
        raise ExperimentError("invalid parameter profile schema")
    params = profile.get("parameters")
    if not isinstance(params, dict):
        raise ExperimentError("parameter profile requires parameters")
    for key, rule in params.items():
        if not isinstance(key, str) or not SAFE_PARAM_KEY.fullmatch(key) or not isinstance(rule, dict):
            raise ExperimentError("invalid parameter declaration")
        if rule.get("type") != "string" or not isinstance(rule.get("default"), str):
            raise ExperimentError(f"unsupported parameter type: {key}")
        if not isinstance(rule.get("max_length"), int) or rule["max_length"] < 1:
            raise ExperimentError(f"invalid max_length: {key}")
        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            raise ExperimentError(f"invalid pattern: {key}")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ExperimentError(f"invalid pattern: {key}") from exc
        _validate_param_value(key, rule["default"], rule)
    return profile


def _validate_development(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != DEVELOPMENT_SCHEMA or value.get("version") != 1:
        raise ExperimentError("invalid development profile schema")
    caps = value.get("capabilities")
    if not isinstance(caps, list) or set(caps) != CAPABILITIES or len(caps) != len(CAPABILITIES):
        raise ExperimentError("invalid development capability set")
    def metadata_keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node).intersection(EXECUTION_KEYS) | set().union(*(metadata_keys(item) for item in node.values()), set())
        if isinstance(node, list):
            return set().union(*(metadata_keys(item) for item in node), set())
        return set()

    forbidden = metadata_keys(value)
    if forbidden:
        raise ExperimentError(f"execution/network metadata is forbidden: {sorted(forbidden)[0]}")
    if not isinstance(value.get("check_scope"), str) or not re.fullmatch(r"[a-z0-9_.-]+", value["check_scope"]):
        raise ExperimentError("invalid development check_scope")
    prompt_check = value.get("prompt_check")
    if not isinstance(prompt_check, dict) or prompt_check.get("kind") not in {"presenter", "search"} or set(prompt_check) != {"kind"}:
        raise ExperimentError("invalid prompt_check")
    case_set = value.get("case_set")
    if not isinstance(case_set, dict) or set(case_set) != {"id", "fingerprint"} or any(not isinstance(v, str) or not v for v in case_set.values()):
        raise ExperimentError("invalid case_set")
    _validate_parameter_profile(value.get("parameter_profile"))
    return value


def validate_registry(stage_map: Path, root: Path) -> dict[str, Any]:
    """Validate a registry against the supplied root (including synthetic roots)."""
    root = root.resolve()
    stage_map = _inside(root, stage_map, label="stage map", must_exist=True)
    registry = _read_object(stage_map, "stage map")
    if registry.get("schema") != STAGE_MAP_SCHEMA or registry.get("schema_version") != 1:
        raise ExperimentError("unsupported stage map schema")
    paths, stages, active = registry.get("paths"), registry.get("stages"), registry.get("active_by_version")
    if not isinstance(paths, dict) or not paths or not isinstance(stages, dict) or not isinstance(active, dict):
        raise ExperimentError("stage map requires paths, stages, and active_by_version")
    expanded: dict[str, list[str]] = {}
    for path_id in paths:
        if not isinstance(path_id, str) or not path_id:
            raise ExperimentError("invalid path id")
        expanded[path_id] = _expanded_path(path_id, paths)
        for stage_id in expanded[path_id]:
            if stage_id not in stages:
                raise ExperimentError(f"path references unknown stage: {stage_id}")
    for version, path_id in active.items():
        if not isinstance(version, str) or not isinstance(path_id, str) or path_id not in paths:
            raise ExperimentError(f"ambiguous or unknown active path: {version}")
    if len(set(active.values())) != len(active.values()):
        raise ExperimentError("ambiguous active path references")
    for stage_id, row in stages.items():
        if not isinstance(stage_id, str) or not isinstance(row, dict):
            raise ExperimentError("invalid stage row")
        for field in ("source", "doc", "test"):
            _safe_repo_file(root, row.get(field), f"{stage_id}.{field}")
        if not isinstance(row.get("source_symbol"), str) or not row["source_symbol"]:
            raise ExperimentError(f"missing source symbol: {stage_id}")
        source_text = _safe_repo_file(root, row["source"], f"{stage_id}.source").read_text(encoding="utf-8")
        symbol_leaf = row["source_symbol"].split(".")[-1]
        if re.search(rf"\b{re.escape(symbol_leaf)}\b", source_text) is None:
            raise ExperimentError(f"source symbol not found: {stage_id}.{row['source_symbol']}")
        if "prompt" in row:
            _safe_repo_file(root, row["prompt"], f"{stage_id}.prompt")
        if "development" in row:
            if "prompt" not in row or not isinstance(row.get("payload_stage"), str):
                raise ExperimentError(f"development stage lacks prompt/payload owner: {stage_id}")
            _validate_development(row["development"])
    registry["_expanded_paths"] = expanded
    registry["_map_path"] = str(stage_map.relative_to(root))
    registry["_map_hash"] = _hash_file(stage_map)
    return registry


def _validate_param_value(key: str, value: Any, rule: dict[str, Any]) -> str:
    if not isinstance(value, str):
        raise ExperimentError(f"parameter {key} must be a string")
    if len(value) > rule["max_length"] or re.fullmatch(rule["pattern"], value) is None:
        raise ExperimentError(f"invalid value for parameter: {key}")
    return value


def _typed_overlay(profile: dict[str, Any], raw: Iterable[str]) -> dict[str, str]:
    rules = profile["parameter_profile"]["parameters"]
    result = {key: rule["default"] for key, rule in rules.items()}
    seen: set[str] = set()
    for item in raw:
        if "=" not in item:
            raise ExperimentError("parameters must use KEY=VALUE")
        key, value = item.split("=", 1)
        if key in seen:
            raise ExperimentError(f"duplicate parameter: {key}")
        seen.add(key)
        if key not in rules:
            raise ExperimentError(f"unknown parameter: {key}")
        result[key] = _validate_param_value(key, value, rules[key])
    return result


def _stage_path(registry: dict[str, Any], stage_id: str) -> str:
    matches = [path_id for path_id in registry["active_by_version"].values() if stage_id in registry["_expanded_paths"][path_id]]
    if len(matches) != 1:
        raise ExperimentError(f"stage has ambiguous or no active path: {stage_id}")
    return matches[0]


def _next_id(store: Path) -> str:
    prefix = datetime.now(timezone.utc).strftime("H%Y%m%d-")
    numbers = []
    if store.is_dir():
        for child in store.iterdir():
            if child.is_dir() and re.fullmatch(re.escape(prefix) + r"\d{3}", child.name):
                numbers.append(int(child.name[-3:]))
    return prefix + f"{max(numbers, default=0) + 1:03d}"


def _experiment_dir(root: Path, store_value: str, experiment_id: str | None = None, *, create_store: bool = False) -> Path:
    store = _inside(root, store_value, label="store directory")
    if create_store:
        store.mkdir(parents=True, exist_ok=True)
    if experiment_id is None:
        return store
    if not SAFE_ID.fullmatch(experiment_id):
        raise ExperimentError("unsafe experiment id")
    return _inside(root, store / experiment_id, label="experiment directory")


def _public_registry(registry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in registry.items() if not key.startswith("_")}


def start(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    stage_map = _inside(root, args.stage_map, label="stage map", must_exist=True)
    registry = validate_registry(stage_map, root)
    row = registry["stages"].get(args.stage)
    if not isinstance(row, dict):
        raise ExperimentError(f"unknown stage: {args.stage}")
    profile = row.get("development")
    if profile is None:
        raise ExperimentError(f"stage has no development profile: {args.stage}")
    profile = _validate_development(profile)
    overlay = _typed_overlay(profile, args.param)
    baseline = _safe_repo_file(root, row["prompt"], f"{args.stage}.prompt")
    candidate = baseline
    supplied = args.prompt_file or args.prompt
    if supplied:
        candidate = _inside(root, supplied, label="candidate prompt", must_exist=True)
        if not candidate.is_file():
            raise ExperimentError("candidate prompt is not a file")
    path_id = _stage_path(registry, args.stage)
    store = _experiment_dir(root, args.store_dir, create_store=True)
    experiment_id = args.id or _next_id(store)
    target = _experiment_dir(root, args.store_dir, experiment_id)
    if target.exists():
        raise ExperimentError(f"experiment already exists: {experiment_id}")
    target.mkdir()
    try:
        shutil.copyfile(baseline, target / "baseline_prompt.txt")
        shutil.copyfile(candidate, target / "candidate_prompt.txt")
        (target / "parameter_overlay.json").write_text(json.dumps(overlay, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        metadata = {
            "schema": EXPERIMENT_SCHEMA,
            "version": 1,
            "id": experiment_id,
            "title": args.title,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "refs": {"hypothesis": args.hypothesis, "prompt_version": args.prompt_version, "model_version": args.model_version},
            "stage": args.stage,
            "path_id": path_id,
            "payload_stage": row["payload_stage"],
            "stage_map": {"ref": registry["_map_path"], "fingerprint": registry["_map_hash"]},
            "owner": {key: row[key] for key in ("source", "source_symbol", "prompt", "doc", "test")},
            "development": {"schema": profile["schema"], "version": profile["version"], "fingerprint": _canonical_hash(profile)},
            "parameter_profile": {"schema": profile["parameter_profile"]["schema"], "version": profile["parameter_profile"]["version"], "fingerprint": _canonical_hash(profile["parameter_profile"])},
            "case_set": profile["case_set"],
            "files": {"baseline": "baseline_prompt.txt", "candidate": "candidate_prompt.txt", "parameters": "parameter_overlay.json"},
            "hashes": {"baseline": _hash_file(target / "baseline_prompt.txt"), "candidate": _hash_file(target / "candidate_prompt.txt"), "parameters": _hash_file(target / "parameter_overlay.json")},
            "safety": {"local_only": True, "offline": True, "read_only_runtime": True, "raw_prompt_in_metadata": False},
        }
        (target / "experiment.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return metadata


def _load_experiment(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    root = Path(args.root).resolve()
    directory = _experiment_dir(root, args.store_dir, args.id)
    metadata = _read_object(directory / "experiment.json", "experiment")
    if metadata.get("schema") != EXPERIMENT_SCHEMA or metadata.get("version") != 1 or metadata.get("id") != args.id:
        raise ExperimentError("invalid experiment schema or id")
    files = metadata.get("files")
    if not isinstance(files, dict) or set(files) != {"baseline", "candidate", "parameters"}:
        raise ExperimentError("invalid experiment file references")
    for key, value in files.items():
        path = _inside(directory, value, label=f"experiment {key}", must_exist=True)
        if path.parent != directory or not path.is_file():
            raise ExperimentError("experiment files must be isolated")
    hashes = metadata.get("hashes")
    if not isinstance(hashes, dict) or any(hashes.get(key) != _hash_file(directory / files[key]) for key in files):
        raise ExperimentError("experiment file hash mismatch")
    return directory, metadata


def _current(args: argparse.Namespace, metadata: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    root = Path(args.root).resolve()
    registry = validate_registry(_inside(root, args.stage_map, label="stage map", must_exist=True), root)
    reasons: list[str] = []
    stage = registry["stages"].get(metadata.get("stage"))
    if not isinstance(stage, dict):
        return registry, None, ["stage_removed"]
    try:
        path_id = _stage_path(registry, metadata["stage"])
    except ExperimentError:
        reasons.append("active_path_changed")
        path_id = None
    if path_id != metadata.get("path_id"):
        reasons.append("active_path_changed")
    profile = stage.get("development")
    if not isinstance(profile, dict):
        reasons.append("development_profile_removed")
    else:
        if _canonical_hash(profile) != metadata.get("development", {}).get("fingerprint"):
            reasons.append("development_profile_changed")
        if stage.get("payload_stage") != metadata.get("payload_stage"):
            reasons.append("payload_stage_changed")
        owner = metadata.get("owner", {})
        if any(stage.get(key) != owner.get(key) for key in ("source", "source_symbol", "prompt", "doc", "test")):
            reasons.append("stage_owner_changed")
        else:
            current_prompt = _safe_repo_file(root, stage["prompt"], f"{metadata['stage']}.prompt")
            if _hash_file(current_prompt) != metadata.get("hashes", {}).get("baseline"):
                reasons.append("baseline_prompt_changed")
    return registry, stage, sorted(set(reasons))


def _validated_overlay(directory: Path, metadata: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    overlay = _read_object(directory / metadata["files"]["parameters"], "parameter overlay")
    rules = profile["parameter_profile"]["parameters"]
    if set(overlay) != set(rules):
        raise ExperimentError("parameter overlay keys do not match profile")
    return {key: _validate_param_value(key, overlay[key], rules[key]) for key in rules}


def _redact_line(line: str) -> str:
    patterns = (
        r"AKIA[0-9A-Z]{16}", r"\bsk-[A-Za-z0-9_-]{16,}\b", r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        r"(?i)\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*\S+", r"(?i)\bbearer\s+\S+",
    )
    return "[REDACTED]\n" if any(re.search(pattern, line) for pattern in patterns) else line


def diff(args: argparse.Namespace) -> dict[str, Any]:
    directory, metadata = _load_experiment(args)
    _registry, _stage, reasons = _current(args, metadata)
    baseline = (directory / metadata["files"]["baseline"]).read_text(encoding="utf-8").splitlines(keepends=True)
    candidate = (directory / metadata["files"]["candidate"]).read_text(encoding="utf-8").splitlines(keepends=True)
    unified = "".join(_redact_line(line) for line in difflib.unified_diff(baseline, candidate, fromfile="baseline_prompt.txt", tofile="candidate_prompt.txt"))
    overlay = _validated_overlay(directory, metadata, _stage["development"]) if _stage and not reasons else _read_object(directory / metadata["files"]["parameters"], "parameter overlay")
    profile = metadata.get("parameter_profile", {})
    current_profile = _stage.get("development", {}).get("parameter_profile", {}) if _stage else {}
    defaults = {key: rule.get("default") for key, rule in current_profile.get("parameters", {}).items() if isinstance(rule, dict)}
    changed = sorted(key for key, value in overlay.items() if defaults.get(key) != value)
    return {"schema": "nmbot.experiment_diff.v1", "status": "orphaned" if reasons else "ready", "id": args.id, "orphan_reasons": reasons, "prompt_diff": unified, "parameter_changed_keys": changed, "hashes": metadata["hashes"], "parameter_profile_fingerprint": profile.get("fingerprint")}


def _actions(root: Path, directory: Path, metadata: dict[str, Any], stage: dict[str, Any], profile: dict[str, Any], full: bool) -> list[dict[str, Any]]:
    candidate = directory / metadata["files"]["candidate"]
    actions = [
        {"kind": "prompt_static", "argv": [sys.executable, "scripts/nmbot_prompt_static_check.py", str(candidate), "--kind", profile["prompt_check"]["kind"]]},
        {"kind": "focused", "argv": [sys.executable, "-m", "pytest", "-q", stage["test"]]},
    ]
    if full:
        actions.append({"kind": "full", "argv": [sys.executable, "scripts/nmbot_check.py", profile["check_scope"], "--json"]})
    return actions


def check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    root = Path(args.root).resolve()
    directory, metadata = _load_experiment(args)
    _registry, stage, reasons = _current(args, metadata)
    if reasons or stage is None:
        return 2, {"schema": CHECK_SCHEMA, "version": 1, "status": "blocked", "id": args.id, "reasons": reasons, "actions": []}
    profile = _validate_development(stage["development"])
    _validated_overlay(directory, metadata, profile)
    actions = _actions(root, directory, metadata, stage, profile, args.full)
    summaries = []
    if args.dry_run:
        summaries = [{"kind": action["kind"], "status": "planned"} for action in actions]
        result = {"schema": CHECK_SCHEMA, "version": 1, "status": "dry-run", "id": args.id, "actions": actions, "summaries": summaries, "safety": {"child_output_saved": False}}
        return 0, result
    code = 0
    for action in actions:
        completed = subprocess.run(action["argv"], cwd=root, check=False, capture_output=True, text=True)
        summary = {"kind": action["kind"], "status": "passed" if completed.returncode == 0 else "failed", "returncode": completed.returncode}
        summaries.append(summary)
        if completed.returncode != 0:
            code = 1
            break
    result = {"schema": CHECK_SCHEMA, "version": 1, "status": "passed" if code == 0 else "failed", "id": args.id, "summaries": summaries, "safety": {"child_output_saved": False}}
    (directory / "check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return code, result


def _forbidden_receipt_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    allowed = {"payload_stage", "prompt_hashes", "raw_prompt_included", "raw_payload_included", "raw_output_included", "raw_contact_included", "raw_token_included", "raw_secret_included"}
    if normalized in allowed or normalized.endswith("_hash") or normalized.endswith("_fingerprint"):
        return False
    exact = {"prompt", "payload", "model_output", "output", "raw", "stdout", "stderr", "token", "api_key", "apikey", "secret", "contact", "env", "environment"}
    return normalized in exact or normalized.startswith(("raw_", "token_", "secret_", "contact_", "api_key_", "env_"))


def validate_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != RECEIPT_SCHEMA or value.get("version") != 1:
        raise ExperimentError("invalid workflow receipt schema")
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or _forbidden_receipt_key(key):
                    raise ExperimentError(f"forbidden receipt key: {key}")
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    flags = value.get("safety", {})
    required = {"raw_prompt_included", "raw_payload_included", "raw_output_included", "raw_contact_included", "raw_token_included", "raw_secret_included"}
    if not isinstance(flags, dict) or any(flags.get(key) is not False for key in required):
        raise ExperimentError("receipt raw-inclusion flags must be false")
    return value


def report(args: argparse.Namespace) -> dict[str, Any]:
    directory, metadata = _load_experiment(args)
    registry, stage, reasons = _current(args, metadata)
    overlay = _validated_overlay(directory, metadata, stage["development"]) if stage and not reasons else _read_object(directory / metadata["files"]["parameters"], "parameter overlay")
    defaults = {}
    if stage:
        defaults = {key: rule["default"] for key, rule in stage["development"]["parameter_profile"]["parameters"].items()}
    changed = sorted(key for key, value in overlay.items() if defaults.get(key) != value)
    check_path = directory / "check.json"
    summaries = _read_object(check_path, "check").get("summaries", []) if check_path.is_file() else []
    receipt = {
        "schema": RECEIPT_SCHEMA, "version": 1, "id": args.id, "status": "orphaned" if reasons else "reported",
        "refs": metadata["refs"], "stage": metadata["stage"], "path_id": metadata["path_id"], "payload_stage": metadata["payload_stage"],
        "stage_map": {"start_fingerprint": metadata["stage_map"]["fingerprint"], "current_fingerprint": registry["_map_hash"]},
        "profile": {"schema": metadata["parameter_profile"]["schema"], "version": metadata["parameter_profile"]["version"], "fingerprint": metadata["parameter_profile"]["fingerprint"], "changed_keys": changed},
        "prompt_hashes": {"baseline": metadata["hashes"]["baseline"], "candidate": metadata["hashes"]["candidate"]},
        "case_set": metadata["case_set"], "check_summaries": summaries, "evidence_scope": "local_offline_static_and_registered_checks",
        "unknowns": reasons or (["checks_not_run"] if not summaries else []),
        "safety": {"local_only": True, "network_used": False, "production_used": False, "raw_prompt_included": False, "raw_payload_included": False, "raw_output_included": False, "raw_contact_included": False, "raw_token_included": False, "raw_secret_included": False},
    }
    validate_receipt(receipt)
    (directory / "workflow_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def compare(args: argparse.Namespace) -> dict[str, Any]:
    receipts = []
    for experiment_id in (args.left, args.right):
        namespace = argparse.Namespace(**vars(args), id=experiment_id)
        directory, _metadata = _load_experiment(namespace)
        path = directory / "workflow_receipt.json"
        receipts.append(validate_receipt(_read_object(path, "workflow receipt")))
    left, right = receipts
    reasons = []
    fields = (("stage",), ("path_id",), ("payload_stage",), ("stage_map", "start_fingerprint"), ("profile", "schema"), ("profile", "version"), ("case_set", "id"), ("case_set", "fingerprint"))
    for path in fields:
        a: Any = left
        b: Any = right
        for key in path:
            a = a.get(key) if isinstance(a, dict) else None
            b = b.get(key) if isinstance(b, dict) else None
        if a != b:
            reasons.append("mismatch:" + ".".join(path))
    return {"schema": COMPARE_SCHEMA, "version": 1, "status": "incomparable" if reasons else "comparable", "left": args.left, "right": args.right, "reasons": reasons, "differences": {} if reasons else {"prompt_candidate_changed": left["prompt_hashes"]["candidate"] != right["prompt_hashes"]["candidate"], "profile_changed_keys": sorted(set(left["profile"]["changed_keys"]) | set(right["profile"]["changed_keys"]))}}


def stages(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    registry = validate_registry(_inside(root, args.stage_map, label="stage map", must_exist=True), root)
    rows = []
    for stage_id, row in registry["stages"].items():
        if "development" in row:
            rows.append({"stage": stage_id, "path_id": _stage_path(registry, stage_id), "payload_stage": row["payload_stage"], "profile": row["development"]})
    return {"schema": DEVELOPMENT_SCHEMA, "version": 1, "stages": rows}


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    common.add_argument("--stage-map", default="config/nmbot_stage_map.json")
    common.add_argument("--store-dir", default="tmp/nmbot_experiments")
    common.add_argument("--json", action="store_true")
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("stages", parents=[common])
    start_parser = subs.add_parser("start", parents=[common])
    start_parser.add_argument("--stage", required=True); start_parser.add_argument("--title", required=True)
    prompt_group = start_parser.add_mutually_exclusive_group(); prompt_group.add_argument("--prompt-file"); prompt_group.add_argument("--prompt")
    start_parser.add_argument("--param", action="append", default=[]); start_parser.add_argument("--id")
    start_parser.add_argument("--hypothesis"); start_parser.add_argument("--prompt-version"); start_parser.add_argument("--model-version")
    for name in ("diff", "report"):
        child = subs.add_parser(name, parents=[common]); child.add_argument("id")
    check_parser = subs.add_parser("check", parents=[common]); check_parser.add_argument("id"); check_parser.add_argument("--dry-run", action="store_true"); check_parser.add_argument("--full", action="store_true")
    compare_parser = subs.add_parser("compare", parents=[common]); compare_parser.add_argument("left"); compare_parser.add_argument("right")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "check":
            code, value = check(args)
        else:
            value = {"stages": stages, "start": start, "diff": diff, "report": report, "compare": compare}[args.command](args)
            code = 0
        if args.json or args.command in {"stages", "start", "check", "report", "compare"}:
            print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        elif args.command == "diff":
            print(value["prompt_diff"], end="" if value["prompt_diff"].endswith("\n") else "\n")
            print(json.dumps({key: value[key] for key in ("status", "orphan_reasons", "parameter_changed_keys", "hashes")}, ensure_ascii=False, sort_keys=True))
        return code
    except ExperimentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
