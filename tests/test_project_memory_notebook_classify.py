from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFY_SCRIPT = ROOT / "scripts" / "project_memory_notebook_classify.py"
TREE_CONFIG = ROOT / "config" / "project_memory_tree.json"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_classify_test", CLASSIFY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def inventory(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_inventory.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "content_printed": False,
        "migration_performed": False,
        "automatic_routing_changed": False,
        "requires_owner_confirmation": True,
        "record_count": len(records),
        "records": records,
    }


def record(record_id: str, sha: str, notebook: str = "alpha", kind: str = "note") -> dict[str, object]:
    return {
        "notebook": notebook,
        "kind": kind,
        "id": record_id,
        "content_sha256": sha,
        "body_bytes": 12,
    }


def evidence_rule(record_id: str = "r1", sha: str = "sha-1", owner_path: str = "ecosystem/nmbot") -> dict[str, object]:
    return {
        "notebook": "alpha",
        "kind": "note",
        "record_id": record_id,
        "metadata_sha256": sha,
        "owner_path": owner_path,
        "node_class": "project",
        "lifecycle": "historical_not_current",
        "confidence": "explicit",
        "evidence_refs": ["docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md:354-385"],
    }


def evidence_map(rules: list[dict[str, object]]) -> dict[str, object]:
    record_count = 1
    rule_count = len(rules)
    unresolved_count = record_count - rule_count
    return {
        "schema": "project_memory_classification_evidence.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "content_printed": False,
        "title_or_notebook_name_matching_used": False,
        "matched_text_printed": False,
        "sensitive_match_details_printed": False,
        "body_scanned_internal_only": True,
        "generator": "project_memory_notebook_evidence.py",
        "scanner_contract": "body_sha_verified_exact_owner_path.v1",
        "storage_scope": "local_notebook_store",
        "inventory_schema": "project_memory_notebook_inventory.v1",
        "inventory_record_count": record_count,
        "record_count": record_count,
        "rule_count": rule_count,
        "unresolved_count": unresolved_count,
        "sensitive_exclusion_count": 0,
        "sensitive_unresolved_count": 0,
        "ambiguous_count": 0,
        "no_match_count": unresolved_count,
        "rules": rules,
        "unresolved_summary": {
            "no_match": unresolved_count,
            "ambiguous_multiple_owners": 0,
            "sensitive_without_unique_owner": 0,
        },
        "errors": [],
    }


def test_success_explicit_mapping() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree, evidence_map([evidence_rule()]))

    assert payload["ok"] is True
    assert payload["classified_count"] == 1
    item = payload["records"][0]
    assert item["owner_path"] == "ecosystem/nmbot"
    assert item["node_class"] == "project"
    assert item["lifecycle"] == "historical_not_current"
    assert item["confidence"] == "explicit"
    assert item["evidence_refs"] == ["docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md:354-385"]
    assert payload["read_only"] is True
    assert payload["write_performed"] is False
    assert payload["notebook_mutation_performed"] is False
    assert payload["automatic_routing_changed"] is False
    assert payload["production_verified"] is False
    assert payload["requires_owner_confirmation"] is True


def test_default_unresolved_without_exact_rule() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree)

    assert payload["ok"] is True
    assert payload["classified_count"] == 0
    assert payload["unresolved_count"] == 1
    item = payload["records"][0]
    assert item["owner_path"] is None
    assert item["node_class"] == "unresolved"
    assert item["lifecycle"] == "unresolved"
    assert item["confidence"] == "unresolved"
    assert item["evidence_refs"] == []


def test_fingerprint_mismatch_fails_closed_to_unresolved() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(inventory([record("r1", "sha-actual")]), tree, evidence_map([evidence_rule(sha="sha-other")]))

    assert payload["ok"] is True
    assert payload["classified_count"] == 0
    assert payload["records"][0]["owner_path"] is None
    assert payload["records"][0]["confidence"] == "unresolved"


def test_invalid_owner_path_denied() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree, evidence_map([evidence_rule(owner_path="ecosystem/missing")]))

    assert payload["ok"] is False
    assert payload["denied_reason"] == "invalid_rule_owner_path"


def test_prohibited_title_body_leak(tmp_path: Path) -> None:
    inv = inventory([record("r1", "sha-1")])
    inv["records"][0]["body"] = "BODY SECRET MUST NOT LEAK"
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(inv), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(CLASSIFY_SCRIPT), "--inventory", str(inventory_path), "--tree", str(TREE_CONFIG), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    assert "SECRET TITLE" not in run.stdout
    assert "BODY SECRET" not in run.stdout
    payload = json.loads(run.stdout)
    assert payload["ok"] is False
    assert payload["denied_reason"] == "inventory_record_contains_unsafe_key"
    assert "title" not in json.dumps(payload).lower()
    assert "body" not in json.dumps(payload).lower()


def test_inventory_rejects_all_unsafe_record_keys() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    for key in ["title", "body", "content", "note", "raw", "transcript", "log"]:
        rec = record("r1", "sha-1")
        rec[key] = "SECRET_MUST_NOT_LEAK"
        payload = mod.classify_inventory(inventory([rec]), tree)
        assert payload["ok"] is False, key
        assert payload["denied_reason"] == "inventory_record_contains_unsafe_key"
        assert "SECRET_MUST_NOT_LEAK" not in json.dumps(payload, ensure_ascii=False)


def test_evidence_map_contract_denies_unsafe_or_inconsistent_shapes() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    inv = inventory([record("r1", "sha-1")])

    cases = [
        ({"schema": "project_memory_classification_evidence.v1", "rules": []}, "evidence_not_ok"),
        (evidence_map([evidence_rule()]) | {"ok": False}, "evidence_not_ok"),
        (evidence_map([evidence_rule()]) | {"read_only": False}, "evidence_flag_mismatch:read_only"),
        (evidence_map([evidence_rule()]) | {"content_printed": True}, "evidence_flag_mismatch:content_printed"),
        (evidence_map([evidence_rule()]) | {"generator": "other.py"}, "evidence_generator_mismatch"),
        (evidence_map([evidence_rule()]) | {"scanner_contract": "other.v1"}, "evidence_scanner_contract_mismatch"),
        (evidence_map([evidence_rule()]) | {"inventory_schema": "wrong"}, "evidence_inventory_schema_mismatch"),
        (evidence_map([evidence_rule()]) | {"inventory_record_count": 2}, "evidence_inventory_record_count_mismatch"),
        (evidence_map([evidence_rule()]) | {"rule_count": 0}, "evidence_rule_count_mismatch"),
        (evidence_map([evidence_rule()]) | {"unresolved_count": 1}, "evidence_unresolved_count_mismatch"),
        (evidence_map([evidence_rule()]) | {"sensitive_exclusion_count": 2}, "evidence_counter_logically_inconsistent"),
        (evidence_map([]) | {"ambiguous_count": 1, "no_match_count": 1}, "evidence_unresolved_breakdown_inconsistent"),
    ]

    for evidence, reason in cases:
        payload = mod.classify_inventory(inv, tree, evidence)
        assert payload["ok"] is False, reason
        assert payload["denied_reason"] == reason


def test_properly_shaped_scanner_style_evidence_map_is_accepted() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree, evidence_map([evidence_rule()]))

    assert payload["ok"] is True
    assert payload["classified_count"] == 1


def test_stable_sort() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    payload = mod.classify_inventory(
        inventory([record("b", "sha-b", notebook="z"), record("a", "sha-a", notebook="a", kind="source")]),
        tree,
    )
    assert [(item["record_ref"]["notebook"], item["record_ref"]["kind"], item["record_ref"]["id"]) for item in payload["records"]] == [
        ("a", "source", "a"),
        ("z", "note", "b"),
    ]


def test_duplicate_rule_and_record_denied() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    duplicate_record = mod.classify_inventory(inventory([record("r1", "sha-1"), record("r1", "sha-1")]), tree)
    assert duplicate_record["ok"] is False
    assert duplicate_record["denied_reason"] == "duplicate_inventory_record"

    duplicate_evidence = evidence_map([evidence_rule(), evidence_rule()])
    duplicate_evidence.update({"inventory_record_count": 2, "record_count": 2, "unresolved_count": 0, "no_match_count": 0})
    duplicate_evidence["unresolved_summary"]["no_match"] = 0
    duplicate_rule = mod.classify_inventory(inventory([record("r1", "sha-1"), record("r2", "sha-2")]), tree, duplicate_evidence)
    assert duplicate_rule["ok"] is False
    assert duplicate_rule["denied_reason"] == "duplicate_evidence_rule"


def test_production_current_forbidden_without_fresh_evidence() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    rule = evidence_rule()
    rule["lifecycle"] = "production_current"
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree, evidence_map([rule]))
    assert payload["ok"] is False
    assert payload["denied_reason"] == "rule_lifecycle_not_allowed"
    assert payload["production_verified"] is False


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
    source = CLASSIFY_SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)


def test_project_tree_expected_paths_and_classes() -> None:
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    nodes = {item["path"]: item for item in tree["nodes"]}
    expected = {
        "ecosystem/nmbot": "project",
        "ecosystem/nmbot/projects/v0": "subproject",
        "ecosystem/nmbot/projects/v2": "subproject",
        "ecosystem/nmbot/projects/v3": "subproject",
        "ecosystem/nmbot/shared-boundary": "shared",
        "ecosystem/mpn-daemon": "project",
        "ecosystem/cc-daemons": "project",
        "ecosystem/cc-daemons/projects/mpn": "subproject",
        "ecosystem/cc-daemons/shared-core": "shared",
        "ecosystem/cc-daemons/projects/qapairs": "project",
        "ecosystem/cc-daemons/projects/qapairs/current/autonomous-v1": "subproject",
        "ecosystem/cc-daemons/projects/qapairs/recovery/structured-facts-v2": "subproject",
        "ecosystem/cc-daemons/projects/qapairs/legacy/embedded-poller": "subproject",
        "ecosystem/cc-daemons/projects/qapairs/legacy/standalone-poller": "subproject",
        "ecosystem/N8N_AUDIT": "project",
        "ecosystem/N8N_AUDIT/projects/call-center-2": "subproject",
        "ecosystem/N8N_AUDIT/projects/qapairs": "subproject",
        "ecosystem/N8N_AUDIT/projects/mpn": "subproject",
        "ecosystem/N8N_AUDIT/projects/prompt_workflow_mvp": "subproject",
        "ecosystem/N8N_AUDIT/shared/gas-owner-zones": "shared",
        "ecosystem/opencode": "project",
        "ecosystem/NOVOSTROY_M": "project",
        "ecosystem/NOVOSTROY_AI": "project",
        "ecosystem/NOVOSTROY_AI/projects/nmbot": "subproject",
        "ecosystem/rules-v2": "project",
    }
    assert {path: nodes[path]["class"] for path in expected} == expected
    assert tree["canonical_decisions"]["qapairs"] == "canonical_owner_cc-daemons_all_qapairs_contours_nested_standalone_poller_legacy"
    assert tree["canonical_decisions"]["rules-v2"] == "unresolved"
    assert tree["canonical_decisions"]["n8n_audit_notebook"] == "n8n_audit"


def test_project_tree_evidence_refs_are_source_line_ranges_without_placeholders() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    assert mod.validate_tree(tree)
    serialized = json.dumps(tree, ensure_ascii=False).lower()
    forbidden = ["user_context:", "memory:", "model_assertion:", "fixture:", "placeholder:"]
    assert not any(token in serialized for token in forbidden)
    for node in tree["nodes"]:
        for ref in node["evidence_refs"]:
            assert mod.EVIDENCE_REF_RE.match(ref), ref


def test_placeholder_evidence_ref_denied() -> None:
    mod = load_module()
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    tree["nodes"][0]["evidence_refs"] = ["user_context:confirmed_roots"]
    payload = mod.classify_inventory(inventory([record("r1", "sha-1")]), tree)
    assert payload["ok"] is False
    assert payload["denied_reason"] == "placeholder_evidence_ref_forbidden"
