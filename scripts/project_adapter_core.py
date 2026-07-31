#!/usr/bin/env python3
"""Static project adapter loader for local developer navigation.

Adapters are code-level allowlisted. The CLI never accepts arbitrary absolute
roots. All adapter paths are relative to the declared root and symlink/path
escapes are rejected before reads.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import NamedTuple
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


_ROOTS: dict[str, Path] = {
    "project": PROJECT_ROOT,
    "cc-daemons": Path("/home/ser/projects/cc-daemons"),
    "mpn-daemon": Path("/home/ser/projects/mpn-daemon"),
}


class AdapterError(ValueError):
    """Adapter loading or path safety failed."""


class ProjectAdapter(NamedTuple):
    project_id: str
    root: Path
    manifest_path: Path | None = None
    stage_map_path: Path | None = None
    diagnostics_path: Path | None = None
    canonical_notebook: str = ""
    output_schema_prefix: str = "project"


def _adapter(
    *,
    project_id: str,
    root_key: str,
    manifest_path: Path | None = None,
    stage_map_path: Path | None = None,
    diagnostics_path: Path | None = None,
    canonical_notebook: str,
    output_schema_prefix: str,
) -> ProjectAdapter:
    root = _ROOTS.get(root_key)
    if root is None:
        raise AdapterError(f"unknown adapter root key: {root_key}")
    return ProjectAdapter(
        project_id=project_id,
        root=root,
        manifest_path=manifest_path,
        stage_map_path=stage_map_path,
        diagnostics_path=diagnostics_path,
        canonical_notebook=canonical_notebook,
        output_schema_prefix=output_schema_prefix,
    )


_ADAPTERS: dict[str, ProjectAdapter] = {
    "nmbot": _adapter(
        project_id="nmbot",
        root_key="project",
        manifest_path=Path("config/nmbot_retrieval_sources.json"),
        stage_map_path=Path("config/nmbot_stage_map.json"),
        canonical_notebook="nmbot",
        output_schema_prefix="nmbot",
    ),
    "qapairs": _adapter(
        project_id="qapairs",
        root_key="cc-daemons",
        manifest_path=PROJECT_ROOT / "config/qapairs_retrieval_sources.json",
        stage_map_path=PROJECT_ROOT / "config/qapairs_stage_map.json",
        diagnostics_path=PROJECT_ROOT / "config/qapairs_diagnostic_codes.json",
        canonical_notebook="cc-daemons",
        output_schema_prefix="qapairs",
    ),
    "cc-daemons": _adapter(
        project_id="cc-daemons",
        root_key="cc-daemons",
        canonical_notebook="cc-daemons",
        output_schema_prefix="cc_daemons",
    ),
    "cc2": _adapter(
        project_id="cc2",
        root_key="cc-daemons",
        manifest_path=PROJECT_ROOT / "config/cc2_retrieval_sources.json",
        stage_map_path=PROJECT_ROOT / "config/cc2_stage_map.json",
        diagnostics_path=PROJECT_ROOT / "config/cc2_diagnostic_codes.json",
        canonical_notebook="cc2",
        output_schema_prefix="cc2",
    ),
    "mpn": _adapter(
        project_id="mpn",
        root_key="mpn-daemon",
        manifest_path=PROJECT_ROOT / "config/mpn_retrieval_sources.json",
        stage_map_path=PROJECT_ROOT / "config/mpn_stage_map.json",
        diagnostics_path=PROJECT_ROOT / "config/mpn_diagnostic_codes.json",
        canonical_notebook="mpn",
        output_schema_prefix="mpn",
    ),
}


def load_adapter(project_id: str) -> ProjectAdapter:
    """Return one allowlisted adapter by id; no caller-supplied roots."""
    adapter = _ADAPTERS.get(str(project_id or "").strip())
    if adapter is None:
        allowed = ", ".join(sorted(_ADAPTERS))
        raise AdapterError(f"unsupported project_id; allowed: {allowed}")
    root = adapter.root.resolve()
    if not root.is_dir():
        raise AdapterError(f"adapter root is unavailable: {adapter.project_id}")
    return ProjectAdapter(
        project_id=adapter.project_id,
        root=root,
        manifest_path=adapter.manifest_path,
        stage_map_path=adapter.stage_map_path,
        diagnostics_path=adapter.diagnostics_path,
        canonical_notebook=adapter.canonical_notebook,
        output_schema_prefix=adapter.output_schema_prefix,
    )


def safe_join(root: Path, rel_path: str, *, must_exist: bool = True) -> Path:
    raw = str(rel_path or "").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise AdapterError(f"path must be relative and stay inside adapter root: {rel_path}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise AdapterError(f"path escapes adapter root: {rel_path}") from exc
    if must_exist and not resolved.is_file():
        raise AdapterError(f"adapter path does not exist: {rel_path}")
    return resolved


def load_nmbot_navigation() -> Any:
    path = PROJECT_ROOT / "scripts" / "nmbot_navigation.py"
    spec = importlib.util.spec_from_file_location("project_adapter_nmbot_navigation", path)
    if spec is None or spec.loader is None:
        raise AdapterError("cannot load nmbot navigation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_STATIC_VALIDATORS: dict[str, str] = {
    "qapairs": "qapairs_context_manifest.py",
    "cc2": "cc2_context_manifest.py",
    "mpn": "mpn_context_manifest.py",
}


def load_project_manifest_validator(project_id: str) -> Any:
    """Load one checked-in static validator by allowlisted project id only."""
    filename = _STATIC_VALIDATORS.get(str(project_id or "").strip())
    if filename is None:
        allowed = ", ".join(sorted(_STATIC_VALIDATORS))
        raise AdapterError(f"unsupported static validator; allowed: {allowed}")
    path = PROJECT_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(f"project_adapter_{project_id}_manifest", path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"cannot load {project_id} static validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_qapairs_manifest_validator() -> Any:
    return load_project_manifest_validator("qapairs")
