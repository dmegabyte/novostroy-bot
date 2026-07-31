from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_backlog_disposition.py"
REGISTRY = ROOT / "config" / "project_memory_registry.json"
TREE = ROOT / "config" / "project_memory_tree.json"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_backlog_disposition_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ref(notebook: str, record_id: str, kind: str = "note") -> dict[str, str]:
    return {"notebook": notebook, "kind": kind, "id": record_id}


def classification_record(notebook: str, record_id: str, sha: str, *, sensitive: bool = False) -> dict[str, object]:
    return {
        "record_ref": ref(notebook, record_id),
        "metadata_sha256": sha,
        "confidence": "unresolved",
        "owner_path": None,
        "node_class": "unresolved",
        "lifecycle": "unresolved",
        "sensitive_exclusion": sensitive,
        "evidence_refs": ["fixture:1-1"] if sensitive else [],
    }


def classification(records: list[dict[str, object]]) -> dict[str, object]:
    sensitive = sum(1 for item in records if item.get("sensitive_exclusion") is True)
    explicit = sum(1 for item in records if item.get("confidence") == "explicit" and item.get("owner_path"))
    return {
        "schema": "project_memory_notebook_classification_manifest.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "record_count": len(records),
        "classified_count": explicit + sensitive,
        "unresolved_count": len(records) - explicit - sensitive,
        "sensitive_exclusion_count": sensitive,
        "records": records,
        "errors": [],
    }


def pre_manifest_for(class_payload: dict[str, object], class_sha: str, selected_ids: set[str]) -> dict[str, object]:
    rows = []
    for item in class_payload["records"]:  # type: ignore[index]
        rec = item["record_ref"]  # type: ignore[index]
        disposition = "held_unresolved_no_migration"
        if item.get("sensitive_exclusion") is True:
            disposition = "excluded_sensitive_no_migration"
        elif rec["id"] in selected_ids:  # type: ignore[index]
            disposition = "selected_for_summary_plan"
        rows.append(
            {
                "record_ref": dict(rec),
                "metadata_sha256": item["metadata_sha256"],
                "disposition": disposition,
                "source_owner_path": item.get("owner_path"),
                "target_project_id": "fixture" if disposition == "selected_for_summary_plan" else None,
                "target_canonical_notebook": "fixture" if disposition == "selected_for_summary_plan" else None,
                "migration_blocker": None if disposition == "selected_for_summary_plan" else "not_selected",
                "provenance": {
                    "classification_schema": "project_memory_notebook_classification_manifest.v1",
                    "classification_sha256": class_sha,
                    "record_metadata_sha256_verified": True,
                },
            }
        )
    selected = sum(1 for row in rows if row["disposition"] == "selected_for_summary_plan")
    sensitive = sum(1 for row in rows if row["disposition"] == "excluded_sensitive_no_migration")
    held = len(rows) - selected - sensitive
    return {
        "schema": "project_memory_pre_migration_manifest.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "migration_performed": False,
        "execution_blocked": True,
        "blocked_reason": "blocked_until_separately_authorized_write_plan_and_owner_gate",
        "execution_gate": {
            "data_migration_authorized": False,
            "notebook_write_authorized": False,
            "owner_gate_required": True,
            "write_plan_required": True,
            "human_owner": "TBD",
            "rollback_owner": "TBD",
        },
        "record_count": len(rows),
        "selected_count": selected,
        "held_unresolved_count": held,
        "sensitive_excluded_count": sensitive,
        "disposition_counts": {"selected_for_summary_plan": selected, "held_unresolved": held, "excluded_sensitive": sensitive},
        "records": rows,
        "errors": [],
    }


def build(records: list[dict[str, object]], selected_ids: set[str] | None = None) -> dict[str, object]:
    mod = load_module()
    class_payload = classification(records)
    class_sha = "classification-sha"
    return mod.build_disposition(
        class_payload,
        pre_manifest_for(class_payload, class_sha, selected_ids or set()),
        json.loads(REGISTRY.read_text(encoding="utf-8")),
        json.loads(TREE.read_text(encoding="utf-8")),
        classification_sha256=class_sha,
        pre_manifest_sha256="pre-sha",
    )


def rows_by_id(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    return {row["record_ref"]["id"]: row for row in payload["records"]}  # type: ignore[index]


def test_dispositions_cover_sensitive_selected_canonical_unmanaged_and_held() -> None:
    payload = build(
        [
            classification_record("nmbot", "canonical-nmbot", "a" * 64),
            classification_record("n8n_audit", "canonical-n8n", "b" * 64),
            classification_record("english_lesson", "nonproject", "c" * 64),
            classification_record("cc-daemons", "mixed-held", "d" * 64),
            classification_record("qapairs-daemon", "legacy-held", "e" * 64),
            classification_record("novostroy-ai", "unknown-held", "f" * 64),
            classification_record("cc-daemons", "sensitive", "1" * 64, sensitive=True),
            classification_record("qapairs-daemon", "selected", "2" * 64),
        ],
        selected_ids={"selected"},
    )

    assert payload["ok"] is True
    by_id = rows_by_id(payload)
    assert by_id["sensitive"]["disposition"] == "excluded_sensitive_no_migration"
    assert by_id["selected"]["disposition"] == "selected_for_summary_plan"
    assert by_id["canonical-nmbot"]["disposition"] == "retained_in_canonical_notebook_no_migration"
    assert by_id["canonical-n8n"]["disposition"] == "retained_in_canonical_notebook_no_migration"
    assert by_id["nonproject"]["disposition"] == "retained_unmanaged_notebook_no_migration"
    assert by_id["mixed-held"]["disposition"] == "held_unresolved_no_migration"
    assert by_id["legacy-held"]["disposition"] == "held_unresolved_no_migration"
    assert by_id["unknown-held"]["disposition"] == "held_unresolved_no_migration"
    assert payload["execution_gate"]["notebook_write_authorized"] is False  # type: ignore[index]
    assert payload["write_performed"] is False


def test_output_contains_no_body_title_path_owner_or_write_authorization() -> None:
    payload = build([classification_record("nmbot", "r1", "a" * 64)])
    dumped = json.dumps(payload, ensure_ascii=False).lower()
    for token in ['"title":', '"body":', '"content":', '"note":', '"raw":', '"transcript":', '"log":', '"storage_root":', '"path":', 'owner_path', 'source_owner_path', 'target_canonical_notebook']:
        assert token not in dumped
    assert "secret" not in dumped
    assert "authorized\": true" not in dumped


def test_bad_inputs_fail_closed() -> None:
    mod = load_module()
    class_payload = classification([classification_record("nmbot", "r1", "a" * 64)])
    pre_payload = pre_manifest_for(class_payload, "classification-sha", set())
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    tree = json.loads(TREE.read_text(encoding="utf-8"))

    leaky = json.loads(json.dumps(class_payload))
    leaky["records"][0]["title"] = "SECRET MUST NOT LEAK"
    assert mod.build_disposition(leaky, pre_payload, registry, tree, classification_sha256="classification-sha", pre_manifest_sha256="pre-sha")["ok"] is False

    bad_counts = json.loads(json.dumps(class_payload))
    bad_counts["record_count"] = 2
    assert mod.build_disposition(bad_counts, pre_payload, registry, tree, classification_sha256="classification-sha", pre_manifest_sha256="pre-sha")["ok"] is False

    stale_pre = json.loads(json.dumps(pre_payload))
    stale_pre["records"][0]["provenance"]["classification_sha256"] = "old-sha"
    payload = mod.build_disposition(class_payload, stale_pre, registry, tree, classification_sha256="classification-sha", pre_manifest_sha256="pre-sha")
    assert payload["ok"] is False
    assert payload["denied_reason"] == "classification_sha_identity_mismatch"


def test_cli_requires_explicit_inputs_and_writes_requested_output(tmp_path: Path) -> None:
    class_payload = classification([classification_record("nmbot", "r1", "a" * 64)])
    class_path = tmp_path / "classification.json"
    class_text = json.dumps(class_payload, sort_keys=True)
    class_path.write_text(class_text, encoding="utf-8")
    pre_payload = pre_manifest_for(class_payload, sha_text(class_text), set())
    pre_path = tmp_path / "pre.json"
    pre_path.write_text(json.dumps(pre_payload, sort_keys=True), encoding="utf-8")
    out_path = tmp_path / "out.json"

    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--classification", str(class_path), "--pre-manifest", str(pre_path), "--registry", str(REGISTRY), "--tree", str(TREE), "--output", str(out_path), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    assert not run.stdout
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["schema"] == "project_memory_notebook_backlog_disposition.v1"


def test_no_dangerous_imports() -> None:
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
        "import nmbot_context_gate",
        "from nmbot_context_gate",
        "import nmbot_runtime",
        "from nmbot_runtime",
    ]
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
