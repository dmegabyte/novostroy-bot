from __future__ import annotations

"""V1-owned safe prompt identity helpers.

Only stable prompt labels and SHA-256 identities are exposed. Prompt bodies,
provider payloads, raw responses and secrets must never leave this module.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "nmbot.prompt_provenance.v1"
_USAGES = {"invoked", "configured"}
_COVERAGES = {"complete", "partial", "configured_only"}
_MAX_PROMPTS = 12
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_STAGE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SAFE_SOURCE_RE = re.compile(r"^[A-Za-z0-9_./:-]{1,160}$")
_FORBIDDEN_SOURCE_PARTS = {"", ".", ".."}


def identity_from_text(stage: Any, source: Any, text: str, usage: str = "invoked") -> dict[str, Any]:
    safe_stage = _safe_stage(stage)
    safe_source = _safe_source(source)
    safe_usage = str(usage or "").strip().lower()
    if safe_usage not in _USAGES:
        safe_usage = "invoked"
    digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    return {"stage": safe_stage, "source": safe_source, "sha256": digest, "prompt_id": "p_" + digest[:12], "usage": safe_usage}


def identity_from_path(stage: Any, source: Any, path: str | Path | None = None, usage: str = "invoked") -> dict[str, Any]:
    prompt_path = Path(path if path is not None else source)
    return identity_from_text(stage, source, prompt_path.read_text(encoding="utf-8"), usage=usage)


def build_prompt_provenance(items: Any, coverage: str = "complete") -> dict[str, Any]:
    safe_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items if isinstance(items, (list, tuple)) else []:
        prompt = _sanitize_prompt_item(item)
        if not prompt:
            continue
        key = (prompt["stage"], prompt["source"], prompt["sha256"])
        if key in seen:
            continue
        seen.add(key)
        safe_items.append(prompt)
        if len(safe_items) >= _MAX_PROMPTS:
            break
    safe_items.sort(key=lambda p: (p["stage"], p["source"], p["sha256"]))
    canonical = [{"stage": p["stage"], "source": p["source"], "sha256": p["sha256"]} for p in safe_items]
    set_hash = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    safe_coverage = str(coverage or "").strip().lower()
    if safe_coverage not in _COVERAGES:
        safe_coverage = "partial"
    return {"schema": SCHEMA, "prompt_set_id": "ps_" + set_hash[:12], "set_sha256": set_hash, "coverage": safe_coverage, "prompts": safe_items}


def merge_prompt_provenance(*values: Any, coverage: str | None = None) -> dict[str, Any] | None:
    prompts: list[dict[str, Any]] = []
    coverages: list[str] = []
    for value in values:
        safe = sanitize_prompt_provenance(value)
        if not safe:
            continue
        coverages.append(str(safe.get("coverage") or "partial"))
        prompts.extend(safe.get("prompts") or [])
    if not prompts:
        return None
    final_coverage = coverage or ("partial" if "partial" in coverages else "configured_only" if coverages and all(c == "configured_only" for c in coverages) else "complete")
    return build_prompt_provenance(prompts, coverage=final_coverage)


def sanitize_prompt_provenance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        return None
    prompts = value.get("prompts")
    coverage = str(value.get("coverage") or "").strip().lower()
    if not isinstance(prompts, list) or coverage not in _COVERAGES:
        return None
    rebuilt = build_prompt_provenance(prompts, coverage=coverage)
    if str(value.get("set_sha256") or "") != rebuilt["set_sha256"]:
        return None
    if str(value.get("prompt_set_id") or "") != rebuilt["prompt_set_id"]:
        return None
    return rebuilt


def _sanitize_prompt_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    stage = _safe_stage(value.get("stage"))
    source = _safe_source(value.get("source"))
    sha = str(value.get("sha256") or "").strip().lower()
    usage = str(value.get("usage") or "").strip().lower()
    if not stage or stage == "unknown" or not source or source == "unknown" or not _HEX64_RE.fullmatch(sha):
        return None
    if usage not in _USAGES:
        return None
    return {"stage": stage, "source": source, "sha256": sha, "prompt_id": "p_" + sha[:12], "usage": usage}


def _safe_stage(value: Any) -> str:
    text = str(value or "").strip()
    return text[:80] if _SAFE_STAGE_RE.fullmatch(text) else "unknown"


def _safe_source(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text.startswith("/") or "\x00" in text or not _SAFE_SOURCE_RE.fullmatch(text):
        return "unknown"
    if any(part in _FORBIDDEN_SOURCE_PARTS for part in Path(text).parts):
        return "unknown"
    if any(word in text.lower() for word in ("token", "secret", "payload", "response_body", "prompt_body")):
        return "unknown"
    return text[:160]
