from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def prompt_identity(*, prompt_id: str, source: str, path: Path, usage: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return {
        "id": prompt_id,
        "source": source,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "usage": usage,
    }


def build_prompt_provenance(identity: dict[str, Any], *, coverage: str = "complete") -> dict[str, Any]:
    safe = {
        "id": str(identity.get("id") or "")[:80],
        "source": str(identity.get("source") or "")[:160],
        "sha256": str(identity.get("sha256") or "")[:64],
        "usage": str(identity.get("usage") or "")[:40],
    }
    return {"schema_version": 1, "owner": "nmbot_v4", "coverage": coverage, "prompts": [safe]}
