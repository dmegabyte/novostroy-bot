#!/usr/bin/env python3
"""Read-only resolver for compact nmbot response path registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "config" / "nmbot_stage_map.json"


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_response_path(version: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    normalized = str(version or "").strip().lower()
    path_id = (registry.get("active_by_version") or {}).get(normalized)
    if not path_id:
        raise SystemExit(f"Unsupported version: {version}")
    return resolve_path_id(path_id, registry=registry)


def resolve_path_id(path_id: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    return _expand_path(str(path_id or "").strip(), registry, stack=())


def _stage_card(stage_id: str, stages: dict[str, Any]) -> dict[str, Any]:
    stage = stages.get(stage_id) if isinstance(stages.get(stage_id), dict) else None
    if stage is None:
        raise SystemExit(f"Unknown stage_id: {stage_id}")
    return {
        "stage_id": stage_id,
        "purpose": stage.get("purpose"),
        "owner": stage.get("owner"),
        "source": stage.get("source"),
        "source_symbol": stage.get("source_symbol"),
        "prompt": stage.get("prompt"),
        "payload_stage": stage.get("payload_stage"),
        "doc": stage.get("doc"),
        "test": stage.get("test"),
    }


def resolve_stage_id(stage_id: str, *, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    normalized = str(stage_id or "").strip()
    if not normalized:
        raise SystemExit("Missing stage_id")
    stages = registry.get("stages") if isinstance(registry.get("stages"), dict) else {}
    return {
        "schema": registry.get("schema"),
        "stage_id": normalized,
        "lookup": _stage_card(normalized, stages),
    }


def _expand_path(path_id: str, registry: dict[str, Any], *, stack: tuple[str, ...]) -> dict[str, Any]:
    paths = registry.get("paths") if isinstance(registry.get("paths"), dict) else {}
    stages = registry.get("stages") if isinstance(registry.get("stages"), dict) else {}
    path = paths.get(path_id) if isinstance(paths.get(path_id), dict) else None
    if not path:
        raise SystemExit(f"Unknown path_id: {path_id}")
    if path_id in stack:
        raise SystemExit(f"Extends cycle: {' -> '.join(stack + (path_id,))}")
    stage_ids: list[str] = []
    parent_id = path.get("extends")
    if parent_id:
        stage_ids.extend(_expand_path(str(parent_id), registry, stack=stack + (path_id,))["stage_ids"])
    stage_ids.extend(str(item) for item in path.get("stage_ids", []) if isinstance(item, str))
    duplicate_stage_ids = sorted({item for item in stage_ids if stage_ids.count(item) > 1})
    if duplicate_stage_ids:
        raise SystemExit(f"Duplicate stage_ids in path {path_id}: {', '.join(duplicate_stage_ids)}")
    return {
        "schema": registry.get("schema"),
        "path_id": path_id,
        "purpose": path.get("purpose"),
        "boundary": path.get("boundary"),
        "correlation_limit": path.get("correlation_limit"),
        "stage_ids": stage_ids,
        "lookup": [
            _stage_card(stage_id, stages)
            for stage_id in stage_ids
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve active nmbot response path from registry only.")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--path-id")
    parser.add_argument("--stage-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)
    registry = load_registry(args.registry)
    if args.stage_id and args.path_id:
        raise SystemExit("Use only one of --stage-id or --path-id")
    if args.stage_id:
        result = resolve_stage_id(args.stage_id, registry=registry)
    else:
        result = resolve_path_id(args.path_id, registry=registry) if args.path_id else resolve_response_path(args.version, registry=registry)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.stage_id:
        item = result["lookup"]
        print(f"stage_id: {result['stage_id']}")
        print(f"- source={item.get('source') or item.get('owner')} prompt={item.get('prompt')} test={item.get('test')}")
    else:
        print(f"path_id: {result['path_id']}")
        for item in result["lookup"]:
            print(f"- {item['stage_id']} source={item.get('source') or item.get('owner')} test={item.get('test')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
