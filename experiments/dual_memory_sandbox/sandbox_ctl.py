#!/usr/bin/env python3
"""Safe local control surface for the dual memory sandbox.

Default commands are structural only. score and distill are guarded and are not
called by validation or tests except for argument/guard inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from telemetry.compare import compare_paired_summaries
from telemetry.session_metrics import collect_session_metrics

ROOT = Path(__file__).resolve().parent
RUNS_ROOT = Path("/tmp/opencode/nmbot_dual_memory_runs")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
FORBIDDEN_PUBLIC_FIELDS = {"expected_answer", "root_cause_code", "hidden_assertions", "success_criteria", "pattern_id", "verifier_id"}
FORBIDDEN_MEMORY_SNAPSHOT_KEYS = {
    "label", "labels", "private_label", "private_labels", "expected", "expected_answer", "hidden", "hidden_assertions",
    "assertions", "holdout", "holdout_outcome", "holdout_outcomes", "outcome", "outcomes", "raw", "raw_prompt",
    "prompt", "code", "source_code", "body", "file_body", "log", "logs", "raw_log", "private", "secret", "secrets",
    "token", "api_key", "password", "credential", "customer_data", "transcript", "transcript_body",
}
MEMORY_SNAPSHOT_ALLOWED_FIELDS = {
    "schema_version",
    "sealed",
    "derived_from_phase",
    "support_task_ids",
    "advisory_patterns",
    "forbidden_source_task_ids",
    "contains_raw_logs",
    "contains_raw_prompts",
    "contains_private_labels",
    "seal_hash",
}
MEMORY_ADVISORY_PATTERN_ALLOWED_FIELDS = {"advice_code", "support_task_ids"}
IMMUTABLE_MANIFEST_EXCLUDES = {"runtime_templates/episodes.jsonl", "runtime_templates/patterns.jsonl", "locks/hash_manifest.json"}
def experiment_config(root: Path = ROOT) -> dict:
    return json.loads((root / "experiment.json").read_text(encoding="utf-8"))


HOLDOUT_TASK_IDS = list(experiment_config().get("holdout_task_ids", []))
LEARNING_TASK_IDS = list(experiment_config().get("learning_task_ids", []))
OBSERVER_VERSION = "prepared-observer-v1"
QUALITY_RESULT_KEYS = {"schema_version", "task_id", "pass", "false_success", "safety_regression", "budget_regression", "scorer_id", "sealed"}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def contained(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{rel(path)}:{line_no}: invalid jsonl: {exc}") from exc
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256_tree(path: Path) -> str:
    h = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(item.relative_to(path).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(item).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def immutable_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        r = rel(path)
        if r in IMMUTABLE_MANIFEST_EXCLUDES:
            continue
        if r.startswith("__pycache__/") or "/__pycache__/" in r:
            continue
        files.append(path)
    return sorted(files, key=rel)


def expected_manifest() -> dict:
    return {
        "schema_version": 1,
        "project_id": "nmbot",
        "status": "PREPARED_NOT_RUN",
        "hash_algorithm": "sha256",
        "excludes": sorted(IMMUTABLE_MANIFEST_EXCLUDES),
        "files": {rel(path): sha256_file(path) for path in immutable_files()},
    }


def validate_hash_manifest() -> None:
    manifest_path = ROOT / "locks" / "hash_manifest.json"
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = expected_manifest()
    if actual != expected:
        raise ValueError("locks/hash_manifest.json does not match immutable inputs")


def validate_layout(root: Path = ROOT, validate_hashes: bool = True) -> dict:
    tasks = read_jsonl(root / "public" / "tasks.jsonl")
    labels = read_jsonl(root / "private" / "labels.jsonl")
    experiment = experiment_config(root)
    if len(tasks) != 15:
        raise ValueError(f"expected 15 public tasks, got {len(tasks)}")
    label_by_id = {row["id"]: row for row in labels}
    if set(label_by_id) != {row["id"] for row in tasks}:
        raise ValueError("private labels must match public task ids exactly")

    families: dict[str, dict[str, int]] = {}
    seen = set()
    learning_ids = set(experiment.get("learning_task_ids", []))
    holdout_ids = set(experiment.get("holdout_task_ids", []))
    for row in tasks:
        if row["id"] in seen:
            raise ValueError(f"duplicate task id {row['id']}")
        seen.add(row["id"])
        if FORBIDDEN_PUBLIC_FIELDS.intersection(row):
            raise ValueError(f"public task {row['id']} contains private fields")
        if row["partition"] not in {"learning", "holdout"}:
            raise ValueError(f"bad partition for {row['id']}")
        if row["partition"] == "learning" and row["id"] not in learning_ids:
            raise ValueError(f"learning task id not listed in experiment manifest: {row['id']}")
        if row["partition"] == "holdout" and row["id"] not in holdout_ids:
            raise ValueError(f"holdout task id not listed in experiment manifest: {row['id']}")
        fam = families.setdefault(row["family_id"], {"learning": 0, "holdout": 0})
        fam[row["partition"]] += 1
        fixture = root / row["fixture_ref"]
        if not contained(fixture, root / "public" / "fixtures") or not fixture.is_dir():
            raise ValueError(f"bad fixture path for {row['id']}")
        if sha256_tree(fixture) != row["fixture_hash"]:
            raise ValueError(f"fixture hash mismatch for {row['id']}")
        allowed = row.get("allowed_paths")
        if allowed != ["src/subject.py"]:
            raise ValueError(f"unexpected allowed paths for {row['id']}")

    if len(families) != 3 or any(v != {"learning": 3, "holdout": 2} for v in families.values()):
        raise ValueError(f"family partition mismatch: {families}")

    for label in labels:
        if label.get("expected_changed_paths") != ["src/subject.py"]:
            raise ValueError(f"bad expected paths for {label['id']}")
        if not label.get("root_cause_code") or not label.get("pattern_id") or not label.get("verifier_id"):
            raise ValueError(f"incomplete private label for {label['id']}")

    for runtime_name in ["episodes.jsonl", "patterns.jsonl"]:
        runtime_file = root / "runtime_templates" / runtime_name
        if runtime_file.read_text(encoding="utf-8") != "":
            raise ValueError(f"runtime template must be empty: {runtime_name}")

    if experiment.get("runtime") != "current_opencode_task_subagents" or experiment.get("status") != "PREPARED_NOT_RUN":
        raise ValueError("experiment manifest must be prepared for current OpenCode task subagents")
    if learning_ids != {row["id"] for row in tasks if row["partition"] == "learning"}:
        raise ValueError("experiment learning_task_ids must match public learning partition")
    if holdout_ids != {row["id"] for row in tasks if row["partition"] == "holdout"}:
        raise ValueError("experiment holdout_task_ids must match public holdout partition")
    agent_allowed = json.dumps(experiment["input_sets"]["agent_allowed"])
    if "private" in agent_allowed:
        raise ValueError("agent input set must not include private files")
    scorer_allowed = json.dumps(experiment["input_sets"]["scorer_allowed"])
    if "private/labels.jsonl" not in scorer_allowed or "private/verifiers" not in scorer_allowed:
        raise ValueError("scorer input set must include private scorer inputs")

    if validate_hashes:
        validate_hash_manifest()

    return {"ok": True, "tasks": len(tasks), "families": families, "status": "PREPARED_NOT_RUN", "telemetry": "prepared"}


def safe_id(value: str, name: str) -> str:
    if not SAFE_ID.match(value):
        raise ValueError(f"unsafe {name}: {value!r}")
    return value


def selected_task(task_id: str) -> dict:
    for row in read_jsonl(ROOT / "public" / "tasks.jsonl"):
        if row["id"] == task_id:
            return row
    raise ValueError(f"unknown task id: {task_id}")


def task_fingerprint(task: dict) -> str:
    h = hashlib.sha256()
    public_task = json.dumps(task, sort_keys=True, separators=(",", ":")).encode("utf-8")
    h.update(public_task)
    h.update(b"\0")
    h.update(task["fixture_hash"].encode("ascii"))
    return h.hexdigest()


def validate_memory_snapshot(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_memory_snapshot_schema(data)
    if data.get("schema_version") != 1 or data.get("sealed") is not True:
        raise ValueError("memory snapshot must be sealed schema_version=1")
    if data.get("derived_from_phase") != "L" or sorted(data.get("support_task_ids", [])) != sorted(LEARNING_TASK_IDS):
        raise ValueError("memory snapshot must derive only from the nine learning task ids")
    validate_advisory_patterns(data.get("advisory_patterns"))
    if set(data.get("forbidden_source_task_ids", [])) & set(HOLDOUT_TASK_IDS):
        raise ValueError("memory snapshot explicitly references forbidden holdout sources")
    if data.get("contains_raw_logs") or data.get("contains_raw_prompts") or data.get("contains_private_labels"):
        raise ValueError("memory snapshot contains forbidden raw/private material")
    if data.get("seal_hash") != memory_snapshot_seal_hash(data):
        raise ValueError("memory snapshot seal_hash does not match canonical safe payload")
    return data


def normalized_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def validate_memory_snapshot_schema(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = normalized_field_name(key)
            if normalized in FORBIDDEN_MEMORY_SNAPSHOT_KEYS:
                raise ValueError(f"memory snapshot contains forbidden field at {path}.{key}")
            if path.startswith("$.advisory_patterns[") and key not in MEMORY_ADVISORY_PATTERN_ALLOWED_FIELDS:
                raise ValueError(f"memory snapshot advisory pattern contains unknown field at {path}.{key}")
            validate_memory_snapshot_schema(child, f"{path}.{key}")
        if path == "$":
            unknown = set(value) - MEMORY_SNAPSHOT_ALLOWED_FIELDS
            if unknown:
                raise ValueError(f"memory snapshot contains unknown fields: {sorted(unknown)}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            validate_memory_snapshot_schema(child, f"{path}[{idx}]")
    elif not isinstance(value, (str, int, bool, type(None))):
        raise ValueError(f"memory snapshot contains unsupported value type at {path}")


def validate_advisory_patterns(value: object) -> None:
    if not isinstance(value, list):
        raise ValueError("memory snapshot advisory_patterns must be a list")
    learning = set(LEARNING_TASK_IDS)
    for idx, pattern in enumerate(value):
        if not isinstance(pattern, dict) or set(pattern) != MEMORY_ADVISORY_PATTERN_ALLOWED_FIELDS:
            raise ValueError(f"memory snapshot advisory_patterns[{idx}] must contain only advice_code and support_task_ids")
        advice_code = pattern.get("advice_code")
        if not isinstance(advice_code, str) or not SAFE_ID.match(advice_code):
            raise ValueError(f"memory snapshot advisory_patterns[{idx}].advice_code must be a safe controlled ID")
        support_ids = pattern.get("support_task_ids")
        if not isinstance(support_ids, list) or not all(isinstance(item, str) for item in support_ids):
            raise ValueError(f"memory snapshot advisory_patterns[{idx}].support_task_ids must be learning ID strings")
        if len(set(support_ids)) != len(support_ids) or len(set(support_ids)) < 3:
            raise ValueError(f"memory snapshot advisory_patterns[{idx}] needs at least three distinct learning support IDs")
        if not set(support_ids).issubset(learning):
            raise ValueError(f"memory snapshot advisory_patterns[{idx}] references non-learning support IDs")


def memory_snapshot_safe_payload(data: dict) -> dict:
    payload = {key: data[key] for key in sorted(MEMORY_SNAPSHOT_ALLOWED_FIELDS - {"seal_hash"}) if key in data}
    if set(payload) != MEMORY_SNAPSHOT_ALLOWED_FIELDS - {"seal_hash"}:
        missing = sorted((MEMORY_SNAPSHOT_ALLOWED_FIELDS - {"seal_hash"}) - set(payload))
        raise ValueError(f"memory snapshot missing required fields: {missing}")
    if not all(isinstance(item, str) for item in payload["support_task_ids"]):
        raise ValueError("memory snapshot support_task_ids must be strings")
    if not all(isinstance(item, str) for item in payload["forbidden_source_task_ids"]):
        raise ValueError("memory snapshot forbidden_source_task_ids must be strings")
    validate_advisory_patterns(payload["advisory_patterns"])
    for key in ["contains_raw_logs", "contains_raw_prompts", "contains_private_labels", "sealed"]:
        if not isinstance(payload[key], bool):
            raise ValueError(f"memory snapshot {key} must be boolean")
    return payload


def memory_snapshot_seal_hash(data: dict) -> str:
    payload = memory_snapshot_safe_payload(data)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def copytree_public(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def command_prepare(args: argparse.Namespace) -> int:
    validate_layout()
    task_id = safe_id(args.task_id, "task-id")
    run_id = safe_id(args.run_id, "run-id")
    if args.mode not in {"baseline", "learning", "memory"}:
        raise ValueError("mode must be baseline, learning, or memory")
    expected_phase = {"baseline": "B0", "learning": "L", "memory": "M1"}[args.mode]
    if args.phase != expected_phase:
        raise ValueError(f"mode {args.mode} requires phase {expected_phase}")
    task = selected_task(task_id)
    if args.mode in {"baseline", "memory"} and task_id not in HOLDOUT_TASK_IDS:
        raise ValueError(f"mode {args.mode} requires one of the six holdout task ids")
    if args.mode == "learning" and task_id not in LEARNING_TASK_IDS:
        raise ValueError("learning phase requires one of the nine learning task ids")
    run_dir = RUNS_ROOT / run_id
    agent_dir = run_dir / "agent"
    observer_dir = run_dir / "observer"
    if run_dir.exists() and not args.force:
        raise FileExistsError(f"refuse overwrite without --force: {run_dir}")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    agent_dir.mkdir(parents=True)
    observer_dir.mkdir(parents=True)

    fixture_src = ROOT / task["fixture_ref"]
    copytree_public(fixture_src, agent_dir)
    (agent_dir / "task_card.json").write_text(json.dumps(task, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    memory_dir = agent_dir / "advisory_memory"
    memory_dir.mkdir()
    if args.mode in {"baseline", "learning"}:
        if args.memory_snapshot:
            raise ValueError(f"{args.mode} mode must receive empty memory and no snapshot")
        shutil.copy2(ROOT / "runtime_templates" / "episodes.jsonl", memory_dir / "episodes.jsonl")
        shutil.copy2(ROOT / "runtime_templates" / "patterns.jsonl", memory_dir / "patterns.jsonl")
        memory_snapshot_hash = None
    else:
        if not args.memory_snapshot:
            raise ValueError("memory mode requires --memory-snapshot sealed learning-derived snapshot")
        snapshot_path = Path(args.memory_snapshot).resolve()
        snapshot = validate_memory_snapshot(snapshot_path)
        shutil.copy2(snapshot_path, memory_dir / "sealed_learning_memory_snapshot.json")
        memory_snapshot_hash = sha256_file(snapshot_path)

    if any("private" in p.relative_to(agent_dir).parts for p in agent_dir.rglob("*")):
        raise RuntimeError("private path leaked into agent view")
    allowlist = {
        "schema_version": 1,
        "task_id": task_id,
        "run_id": run_id,
        "mode": args.mode,
        "agent_dir": str(agent_dir),
        "agent_allowed_paths": sorted(p.relative_to(agent_dir).as_posix() for p in agent_dir.rglob("*") if p.is_file()),
        "agent_tree_hash": sha256_tree(agent_dir),
        "observer_dir": str(observer_dir),
        "observer_outside_agent": not contained(observer_dir, agent_dir),
        "memory_snapshot_hash": memory_snapshot_hash,
        "fresh_subagent_session_required": True,
        "opencode_launch_profiles_copied": False,
        "private_copied": False,
    }
    (run_dir / "allowlist_manifest.json").write_text(json.dumps(allowlist, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "status": "PREPARED_NOT_RUN",
        "run_id": run_id,
        "task_id": task_id,
        "mode": args.mode,
        "phase": args.phase,
        "immutable_code_data_hash": sha256_file(ROOT / "locks" / "hash_manifest.json"),
        "task_fingerprint": task_fingerprint(task),
        "fixture_hash": task["fixture_hash"],
        "memory_snapshot_hash": memory_snapshot_hash,
        "collector_contract": "read_only_opencode_db_aggregates_after_future_task_subagent_session",
        "future_runtime": "current_opencode_task_subagents",
        "task_api_model_selector": "not_available_here_actual_model_must_be_verified_from_db_after_run",
        "fresh_subagent_session_required": True,
        "actual_agent": None,
        "actual_model_identity": None,
        "parent_id": None,
        "session_id": None,
        "host_observer_version": OBSERVER_VERSION,
        "start_utc": None,
        "end_utc": None,
        "clean_workspace_hash": sha256_tree(agent_dir),
        "coverage": {"present": [], "missing": ["tokens", "model_calls", "tools", "retrieval", "memory", "wall"], "not_evaluable_until_session_collect": True, "reason": "PREPARED_NOT_RUN; future collector reads OpenCode DB aggregates only after a task-subagent session exists"},
        "host_only_boundary": "observer stays outside agent view; no callback plugin, Docker, OCI or relay proof is claimed",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    (observer_dir / "run_metadata_stub.json").write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "run_dir": str(run_dir), "allowlist_hash": sha256_file(run_dir / "allowlist_manifest.json")}, sort_keys=True))
    return 0


def require_ack(args: argparse.Namespace) -> None:
    if not getattr(args, "ack_run_experiment", False):
        raise ValueError("guarded future command requires --ack-run-experiment")


def load_run_metadata(run_dir: Path) -> dict:
    metadata_path = run_dir / "observer" / "run_metadata_stub.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    return data


def prepared_run_dir(value: str) -> Path:
    run_dir = Path(value).resolve()
    runs_root = RUNS_ROOT.resolve()
    if not contained(run_dir, runs_root):
        raise ValueError("run-dir must be under RUNS_ROOT")
    if not (run_dir / "agent").is_dir() or not (run_dir / "observer" / "run_metadata_stub.json").is_file():
        raise ValueError("run-dir is not a prepared sandbox run")
    return run_dir


def validate_session_summary(summary: dict, metadata: dict) -> dict:
    for key in ["task_id", "mode", "phase"]:
        if summary.get(key) != metadata.get(key):
            raise ValueError(f"session summary {key} mismatch")
    if summary.get("fresh_subagent_session") is not True:
        raise ValueError("session summary must be a fresh subagent session")
    for key in ["session_id", "parent_id", "actual_agent", "actual_model_identity"]:
        if not isinstance(summary.get(key), str) or not summary.get(key):
            raise ValueError(f"session summary missing {key}")
    if not isinstance(summary.get("resources"), dict) or not isinstance(summary.get("coverage"), dict):
        raise ValueError("session summary missing resources/coverage")
    return summary


def validate_quality_result(path: Path, task_id: str) -> dict:
    quality = json.loads(path.read_text(encoding="utf-8"))
    if set(quality) != QUALITY_RESULT_KEYS:
        raise ValueError("quality_result has unexpected fields")
    if quality.get("schema_version") != 1 or quality.get("task_id") != task_id or quality.get("sealed") is not True:
        raise ValueError("quality_result identity/seal mismatch")
    for key in ["pass", "false_success", "safety_regression", "budget_regression"]:
        if type(quality.get(key)) is not bool:
            raise ValueError(f"quality_result {key} must be bool")
    if not isinstance(quality.get("scorer_id"), str) or not SAFE_ID.match(quality["scorer_id"]):
        raise ValueError("quality_result scorer_id must be safe string")
    return {key: quality[key] for key in ["pass", "false_success", "safety_regression", "budget_regression"]}


def command_score(args: argparse.Namespace) -> int:
    require_ack(args)
    run_dir = Path(args.run_dir).resolve()
    private_root = Path(args.private_root).resolve()
    agent_dir = run_dir / "agent"
    if not agent_dir.is_dir() or not private_root.is_dir():
        raise ValueError("run dir and private root must exist")
    for path in agent_dir.rglob("*"):
        if "private" in path.parts or path.name == "labels.jsonl":
            raise RuntimeError("labels/private files are visible in agent view")
    print(json.dumps({"ok": False, "status": "ACKED_BUT_SCORER_NOT_IMPLEMENTED_IN_PREP_TASK"}, sort_keys=True))
    return 2


def command_distill(args: argparse.Namespace) -> int:
    require_ack(args)
    if args.threshold < 3:
        raise ValueError("Layer B threshold must be at least 3")
    print(json.dumps({"ok": False, "status": "ACKED_BUT_DISTILL_NOT_IMPLEMENTED_IN_PREP_TASK", "threshold": args.threshold}, sort_keys=True))
    return 2


def command_seal_run(args: argparse.Namespace) -> int:
    require_ack(args)
    run_dir = prepared_run_dir(args.run_dir)
    observer = run_dir / "observer"
    metadata_path = observer / "run_metadata_stub.json"
    session_path = observer / "session_summary.json"
    if not session_path.is_file():
        raise ValueError("session_summary.json is required before seal-run")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    required = ["run_id", "task_id", "mode", "phase", "task_fingerprint", "fixture_hash", "memory_snapshot_hash", "host_observer_version", "clean_workspace_hash"]
    missing = [key for key in required if metadata.get(key) in {None, ""}]
    if metadata.get("mode") in {"baseline", "learning"}:
        missing = [key for key in missing if key != "memory_snapshot_hash"]
    if missing:
        raise ValueError(f"metadata missing completed fields: {missing}")
    if metadata.get("mode") in {"baseline", "learning"} and metadata.get("memory_snapshot_hash") is not None:
        raise ValueError("baseline/learning run must have empty memory_snapshot_hash")
    if metadata.get("mode") == "memory" and not metadata.get("memory_snapshot_hash"):
        raise ValueError("memory run requires memory_snapshot_hash")
    session = validate_session_summary(json.loads(session_path.read_text(encoding="utf-8")), metadata)
    quality = validate_quality_result(Path(args.quality_result).resolve(), metadata["task_id"])
    summary = {
        "schema_version": 1,
        "sealed": True,
        "run_id": metadata["run_id"],
        "task_id": metadata["task_id"],
        "mode": metadata["mode"],
        "phase": metadata["phase"],
        "task_fingerprint": metadata["task_fingerprint"],
        "fixture_hash": metadata["fixture_hash"],
        "memory_snapshot_hash": metadata.get("memory_snapshot_hash"),
        "session_id": session["session_id"],
        "parent_id": session["parent_id"],
        "actual_agent": session["actual_agent"],
        "actual_model_identity": session["actual_model_identity"],
        "fresh_subagent_session": True,
        "diagnostics": session.get("diagnostics", {}),
        "quality": quality,
        "coverage": session["coverage"],
        "resources": session["resources"],
        "session_summary_sha256": sha256_file(session_path),
        "quality_result_sha256": sha256_file(Path(args.quality_result).resolve()),
    }
    summary_path = observer / "run_summary.json"
    atomic_write_json(summary_path, summary)
    seal = {"schema_version": 1, "sealed": True, "run_summary_sha256": sha256_file(summary_path), "session_summary_sha256": summary["session_summary_sha256"], "quality_result_sha256": summary["quality_result_sha256"]}
    atomic_write_json(observer / "run_seal.json", seal)
    print(json.dumps({"ok": True, "run_summary": str(summary_path), "run_seal": str(observer / "run_seal.json")}, sort_keys=True))
    return 0


def command_compare(args: argparse.Namespace) -> int:
    require_ack(args)
    baseline = [load_sealed_summary(Path(p).resolve()) for p in args.baseline_run_dir]
    memory = [load_sealed_summary(Path(p).resolve()) for p in args.memory_run_dir]
    result = compare_paired_summaries(baseline, memory, expected_holdout_ids=HOLDOUT_TASK_IDS)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("overall_resource_claim") == "evaluable" else 2


def command_collect_session(args: argparse.Namespace) -> int:
    run_dir = prepared_run_dir(args.run_dir)
    metadata = load_run_metadata(run_dir)
    summary = collect_session_metrics(
        session_id=args.session_id,
        task_id=metadata["task_id"],
        mode=metadata["mode"],
        phase=metadata["phase"],
        expected_parent_id=args.expected_parent_id,
        db_path=Path(args.db_path).resolve() if args.db_path else None,
    )
    validate_session_summary(summary, metadata)
    output = run_dir / "observer" / "session_summary.json"
    atomic_write_json(output, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


def load_sealed_summary(run_dir: Path) -> dict:
    observer = run_dir / "observer"
    seal = json.loads((observer / "run_seal.json").read_text(encoding="utf-8"))
    summary = json.loads((observer / "run_summary.json").read_text(encoding="utf-8"))
    if summary.get("sealed") is not True or seal.get("run_summary_sha256") != sha256_file(observer / "run_summary.json"):
        raise ValueError(f"unsealed run_summary: {run_dir}")
    return summary


def command_seal_manifest(args: argparse.Namespace) -> int:
    manifest = expected_manifest()
    if args.write:
        path = ROOT / "locks" / "hash_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-layout")
    prep = sub.add_parser("prepare")
    prep.add_argument("--task-id", required=True)
    prep.add_argument("--run-id", required=True)
    prep.add_argument("--mode", choices=["baseline", "learning", "memory"], required=True)
    prep.add_argument("--phase", choices=["B0", "L", "M1"], default="B0")
    prep.add_argument("--memory-snapshot")
    prep.add_argument("--force", action="store_true")
    score = sub.add_parser("score")
    score.add_argument("--run-dir", required=True)
    score.add_argument("--private-root", required=True)
    score.add_argument("--ack-run-experiment", action="store_true")
    distill = sub.add_parser("distill")
    distill.add_argument("--run-dir", required=True)
    distill.add_argument("--threshold", type=int, default=3)
    distill.add_argument("--ack-run-experiment", action="store_true")
    seal_run = sub.add_parser("seal-run")
    seal_run.add_argument("--run-dir", required=True)
    seal_run.add_argument("--quality-result", required=True)
    seal_run.add_argument("--ack-run-experiment", action="store_true")
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline-run-dir", action="append", required=True)
    compare.add_argument("--memory-run-dir", action="append", required=True)
    compare.add_argument("--ack-run-experiment", action="store_true")
    collect = sub.add_parser("collect-session")
    collect.add_argument("--run-dir", required=True)
    collect.add_argument("--session-id", required=True)
    collect.add_argument("--expected-parent-id")
    collect.add_argument("--db-path")
    seal = sub.add_parser("seal-manifest")
    seal.add_argument("--write", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-layout":
            print(json.dumps(validate_layout(), sort_keys=True))
            return 0
        if args.command == "prepare":
            return command_prepare(args)
        if args.command == "score":
            return command_score(args)
        if args.command == "distill":
            return command_distill(args)
        if args.command == "seal-run":
            return command_seal_run(args)
        if args.command == "compare":
            return command_compare(args)
        if args.command == "collect-session":
            return command_collect_session(args)
        if args.command == "seal-manifest":
            return command_seal_manifest(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
