from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCRIPT = ROOT / "scripts" / "project_memory_notebook_evidence.py"
CLASSIFY_SCRIPT = ROOT / "scripts" / "project_memory_notebook_classify.py"
TREE_CONFIG = ROOT / "config" / "project_memory_tree.json"
MARKERS_CONFIG = ROOT / "config" / "project_memory_ownership_markers.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def record_body_path(root: Path, notebook: str = "alpha", kind: str = "note", filename: str = "r1.json") -> Path:
    dirname = "notes" if kind == "note" else "sources"
    return root / "workspaces" / "default" / "notebooks" / notebook / dirname / filename


def make_storage(tmp_path: Path, body: str, *, title: str = "SECRET TITLE", record_id: str = "r1", kind: str = "note") -> Path:
    root = tmp_path / "knowledge-mcp"
    key = "note" if kind == "note" else "content"
    id_key = "note_id" if kind == "note" else "source_id"
    write_json(record_body_path(root, kind=kind), {id_key: record_id, "title": title, key: body})
    return root


def inventory_for(body: str, *, record_id: str = "r1", kind: str = "note", sha: str | None = None) -> dict[str, object]:
    return {
        "schema": "project_memory_notebook_inventory.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "content_printed": False,
        "migration_performed": False,
        "automatic_routing_changed": False,
        "requires_owner_confirmation": True,
        "record_count": 1,
        "records": [
            {
                "notebook": "alpha",
                "kind": kind,
                "id": record_id,
                "content_sha256": sha or hashlib.sha256(body.encode()).hexdigest(),
                "body_bytes": len(body.encode()),
            }
        ],
    }


def marker_config(markers: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "project_memory_ownership_markers.v1",
        "version": 1,
        "read_only": True,
        "write_performed": False,
        "notebook_mutation_performed": False,
        "automatic_routing_changed": False,
        "production_verified": False,
        "requires_owner_confirmation": True,
        "markers": markers,
    }


def marker(marker_id: str, token: str, owner_path: str = "ecosystem/nmbot", node_class: str = "project") -> dict[str, object]:
    return {
        "marker_id": marker_id,
        "match_type": "exact_token",
        "token": token,
        "owner_path": owner_path,
        "node_class": node_class,
        "evidence_refs": ["config/project_memory_registry.json:5-33"],
        "lifecycle": "historical_not_current",
    }


def test_unique_match_creates_classifier_rule(tmp_path: Path) -> None:
    token = "/tmp/opencode-run-nmbot/project"
    body = f"historical local source at {token}"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_unique")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, marker_config([marker("m1", token)]))

    assert payload["ok"] is True
    assert payload["generator"] == "project_memory_notebook_evidence.py"
    assert payload["scanner_contract"] == "body_sha_verified_exact_owner_path.v1"
    assert payload["storage_scope"] == "local_notebook_store"
    assert payload["inventory_schema"] == "project_memory_notebook_inventory.v1"
    assert payload["inventory_record_count"] == 1
    assert payload["rule_count"] == 1
    rule = payload["rules"][0]
    assert rule["notebook"] == "alpha"
    assert rule["kind"] == "note"
    assert rule["record_id"] == "r1"
    assert rule["owner_path"] == "ecosystem/nmbot"
    assert rule["node_class"] == "project"
    assert rule["lifecycle"] == "historical_not_current"
    assert rule["confidence"] == "explicit"
    assert rule["sensitive_exclusion"] is False


def test_no_match_is_unresolved_without_rule(tmp_path: Path) -> None:
    body = "historical body without safe marker"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_no_match")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, marker_config([marker("m1", "/safe/unique/root")]))

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["unresolved_summary"]["no_match"] == 1


def test_ambiguous_multiple_owners_is_unresolved(tmp_path: Path) -> None:
    body = "both /tmp/opencode-run-nmbot/project and /home/ser/projects/N8N_AUDIT appear"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_ambiguous")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    config = marker_config(
        [
            marker("m1", "/tmp/opencode-run-nmbot/project"),
            marker("m2", "/home/ser/projects/N8N_AUDIT", "ecosystem/N8N_AUDIT", "project"),
        ]
    )

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, config)

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["ambiguous_count"] == 1
    assert payload["unresolved_summary"]["ambiguous_multiple_owners"] == 1


def test_sensitive_internal_only_emits_boolean_only(tmp_path: Path) -> None:
    token = "/tmp/opencode-run-nmbot/project"
    secret = "api_key=SECRET_VALUE_MUST_NOT_LEAK"
    body = f"{token}\n{secret}"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_sensitive")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, marker_config([marker("m1", token)]))
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["ok"] is True
    assert payload["rules"][0]["sensitive_exclusion"] is True
    assert payload["sensitive_exclusion_count"] == 1
    assert "SECRET_VALUE_MUST_NOT_LEAK" not in serialized
    assert "api_key" not in serialized.lower()
    assert token not in serialized


def test_sensitive_without_unique_owner_does_not_assign(tmp_path: Path) -> None:
    body = "password=SECRET_VALUE_MUST_NOT_LEAK with no owner marker"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_sensitive_unresolved")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, marker_config([marker("m1", "/safe/unique/root")]))

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["sensitive_unresolved_count"] == 1
    assert payload["unresolved_summary"]["sensitive_without_unique_owner"] == 1


def test_sha_mismatch_fails_closed_exit_2(tmp_path: Path) -> None:
    body = "body /tmp/opencode-run-nmbot/project"
    root = make_storage(tmp_path, body)
    inv_path = tmp_path / "inventory.json"
    markers_path = tmp_path / "markers.json"
    inv_path.write_text(json.dumps(inventory_for(body, sha="wrong-sha")), encoding="utf-8")
    markers_path.write_text(json.dumps(marker_config([marker("m1", "/tmp/opencode-run-nmbot/project")])), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(EVIDENCE_SCRIPT), "--inventory", str(inv_path), "--storage-root", str(root), "--markers", str(markers_path), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 2
    assert json.loads(run.stdout)["denied_reason"] == "hash_mismatch"


def test_missing_malformed_and_path_escape_fail_closed(tmp_path: Path) -> None:
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_failures")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    config = marker_config([marker("m1", "/tmp/opencode-run-nmbot/project")])

    missing_root = tmp_path / "missing"
    missing = mod.build_evidence_map(inventory_for("x"), missing_root, "default", tree, config)
    assert missing["ok"] is False
    assert missing["denied_reason"] == "storage_root_missing"
    assert str(missing_root) not in json.dumps(missing, ensure_ascii=False)
    assert missing["storage_scope"] == "local_notebook_store"
    assert "storage_root" not in missing

    root = tmp_path / "knowledge-mcp"
    bad = record_body_path(root)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not-json SECRET_MUST_NOT_LEAK", encoding="utf-8")
    malformed = mod.build_evidence_map(inventory_for("x"), root, "default", tree, config)
    assert malformed["ok"] is False
    assert malformed["denied_reason"] == "malformed_json"
    assert "SECRET_MUST_NOT_LEAK" not in json.dumps(malformed)
    assert str(root) not in json.dumps(malformed, ensure_ascii=False)

    escaped = mod.build_evidence_map(inventory_for("x"), root, "../default", tree, config)
    assert escaped["ok"] is False
    assert escaped["denied_reason"] == "invalid_workspace"


def test_no_output_leaks_title_body_or_matched_token(tmp_path: Path) -> None:
    token = "/tmp/opencode-run-nmbot/project"
    body = f"BODY_SECRET_MUST_NOT_LEAK {token}"
    root = make_storage(tmp_path, body, title="TITLE_SECRET_MUST_NOT_LEAK")
    inv_path = tmp_path / "inventory.json"
    markers_path = tmp_path / "markers.json"
    inv_path.write_text(json.dumps(inventory_for(body)), encoding="utf-8")
    markers_path.write_text(json.dumps(marker_config([marker("m1", token)])), encoding="utf-8")

    run = subprocess.run(
        [sys.executable, str(EVIDENCE_SCRIPT), "--inventory", str(inv_path), "--storage-root", str(root), "--markers", str(markers_path), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    assert "BODY_SECRET_MUST_NOT_LEAK" not in run.stdout
    assert "TITLE_SECRET_MUST_NOT_LEAK" not in run.stdout
    assert token not in run.stdout
    payload = json.loads(run.stdout)
    assert payload["content_printed"] is False
    assert payload["matched_text_printed"] is False
    assert payload["title_or_notebook_name_matching_used"] is False


def test_rejects_unsafe_inventory_record_keys(tmp_path: Path) -> None:
    body = "historical local source at /tmp/opencode-run-nmbot/project"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_unsafe_keys")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    config = marker_config([marker("m1", "/tmp/opencode-run-nmbot/project")])

    for key in ["title", "body", "content", "note", "raw", "transcript", "log"]:
        inv = inventory_for(body)
        inv["records"][0][key] = "SECRET_MUST_NOT_LEAK"
        payload = mod.build_evidence_map(inv, root, "default", tree, config)
        assert payload["ok"] is False, key
        assert payload["denied_reason"] == "inventory_record_contains_unsafe_key"
        assert "SECRET_MUST_NOT_LEAK" not in json.dumps(payload, ensure_ascii=False)


def test_stable_rules_sort(tmp_path: Path) -> None:
    root = tmp_path / "knowledge-mcp"
    body_b = "/tmp/opencode-run-nmbot/project beta"
    body_a = "/tmp/opencode-run-nmbot/project alpha"
    write_json(record_body_path(root, "zeta", "note", "b.json"), {"note_id": "b", "note": body_b, "title": "b"})
    write_json(record_body_path(root, "alpha", "source", "a.json"), {"source_id": "a", "content": body_a, "title": "a"})
    inv = {
        "schema": "project_memory_notebook_inventory.v1",
        "ok": True,
        "read_only": True,
        "write_performed": False,
        "records": [
            {"notebook": "zeta", "kind": "note", "id": "b", "content_sha256": hashlib.sha256(body_b.encode()).hexdigest()},
            {"notebook": "alpha", "kind": "source", "id": "a", "content_sha256": hashlib.sha256(body_a.encode()).hexdigest()},
        ],
    }
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_stable")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inv, root, "default", tree, marker_config([marker("m1", "/tmp/opencode-run-nmbot/project")]))

    assert [(item["notebook"], item["kind"], item["record_id"]) for item in payload["rules"]] == [("alpha", "source", "a"), ("zeta", "note", "b")]


def test_exact_marker_not_loose_title_or_notebook_name(tmp_path: Path) -> None:
    token = "/tmp/opencode-run-nmbot/project"
    body = "qapairs mpn nmbot words and no exact safe path"
    root = make_storage(tmp_path, body, title=f"title has {token}")
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_exact")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, marker_config([marker("m1", token)]))

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["no_match_count"] == 1

    loose = marker_config([marker("loose", "qapairs")])
    denied = mod.build_evidence_map(inventory_for(body), root, "default", tree, loose)
    assert denied["ok"] is False
    assert denied["denied_reason"] == "unsafe_marker_token"


def test_real_marker_config_validates_and_has_no_generic_cc_daemons_parent_marker(tmp_path: Path) -> None:
    root = tmp_path / "knowledge-mcp"
    (root / "workspaces" / "default").mkdir(parents=True)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_real_markers_valid")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    markers = json.loads(MARKERS_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for("unused") | {"record_count": 0, "records": []}, root, "default", tree, markers)

    assert payload["ok"] is True
    assert payload["rule_count"] == 0
    tokens = [item["token"] for item in markers["markers"]]
    assert "/home/ser/projects/cc-daemons" not in tokens
    assert all(item["match_type"] == "exact_token" for item in markers["markers"])
    assert all(item["lifecycle"] == "historical_not_current" for item in markers["markers"])
    assert not any(item["token"].lower() in {"qapairs", "mpn", "nmbot"} for item in markers["markers"])


def test_real_cc_daemons_exact_child_markers_map_to_specific_owners(tmp_path: Path) -> None:
    cases = [
        ("v1a", "/home/ser/projects/cc-daemons/tools/issue_qa_autonomous.py", "ecosystem/cc-daemons/projects/qapairs/current/autonomous-v1"),
        ("v1b", "/home/ser/projects/cc-daemons/tools/issue_qa_orchestrator.py", "ecosystem/cc-daemons/projects/qapairs/current/autonomous-v1"),
        ("v2", "/home/ser/projects/cc-daemons/tools/qapairs_gemma_facts_to_pairs.py", "ecosystem/cc-daemons/projects/qapairs/recovery/structured-facts-v2"),
        ("legacy", "/home/ser/projects/cc-daemons/projects/qapairs", "ecosystem/cc-daemons/projects/qapairs/legacy/embedded-poller"),
        ("standalone", "/home/ser/projects/qapairs-daemon", "ecosystem/cc-daemons/projects/qapairs/legacy/standalone-poller"),
        ("core", "/home/ser/projects/cc-daemons/core", "ecosystem/cc-daemons/shared-core"),
        ("mpn", "/home/ser/projects/cc-daemons/projects/mpn", "ecosystem/cc-daemons/projects/mpn"),
    ]
    root = tmp_path / "knowledge-mcp"
    records = []
    for record_id, body, _owner in cases:
        write_json(record_body_path(root, filename=f"{record_id}.json"), {"note_id": record_id, "title": "hidden", "note": body})
        records.append(
            {
                "notebook": "alpha",
                "kind": "note",
                "id": record_id,
                "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "body_bytes": len(body.encode()),
            }
        )
    inv = inventory_for("unused") | {"record_count": len(records), "records": records}
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_real_markers_children")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    markers = json.loads(MARKERS_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inv, root, "default", tree, markers)

    assert payload["ok"] is True
    assert payload["rule_count"] == len(cases)
    owners_by_id = {item["record_id"]: item["owner_path"] for item in payload["rules"]}
    assert owners_by_id == {record_id: owner for record_id, _body, owner in cases}


def test_real_relative_source_path_markers_map_to_specific_owners(tmp_path: Path) -> None:
    cases = [
        ("v1a", "tools/issue_qa_autonomous.py", "ecosystem/cc-daemons/projects/qapairs/current/autonomous-v1"),
        ("v1b", "tools/issue_qa_orchestrator.py", "ecosystem/cc-daemons/projects/qapairs/current/autonomous-v1"),
        ("v2", "tools/qapairs_gemma_facts_to_pairs.py", "ecosystem/cc-daemons/projects/qapairs/recovery/structured-facts-v2"),
        ("legacy", "projects/qapairs/daemon.py", "ecosystem/cc-daemons/projects/qapairs/legacy/embedded-poller"),
        ("core", "core/daemon_engine.py", "ecosystem/cc-daemons/shared-core"),
        ("cc_mpn", "projects/mpn/daemon.py", "ecosystem/cc-daemons/projects/mpn"),
        ("mpn_daemon", "mpn_local_pipeline.py", "ecosystem/mpn-daemon"),
    ]
    root = tmp_path / "knowledge-mcp"
    records = []
    for record_id, body, _owner in cases:
        write_json(record_body_path(root, filename=f"{record_id}.json"), {"note_id": record_id, "title": "hidden", "note": body})
        records.append(
            {
                "notebook": "alpha",
                "kind": "note",
                "id": record_id,
                "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "body_bytes": len(body.encode()),
            }
        )
    inv = inventory_for("unused") | {"record_count": len(records), "records": records}
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_real_relative_markers")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    markers = json.loads(MARKERS_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inv, root, "default", tree, markers)

    assert payload["ok"] is True
    assert payload["rule_count"] == len(cases)
    owners_by_id = {item["record_id"]: item["owner_path"] for item in payload["rules"]}
    assert owners_by_id == {record_id: owner for record_id, _body, owner in cases}


def test_real_markers_do_not_classify_loose_business_words_or_titles(tmp_path: Path) -> None:
    token = "/home/ser/projects/cc-daemons/tools/issue_qa_autonomous.py"
    body = "qapairs mpn nmbot cc-daemons title words only, no exact source path"
    root = make_storage(tmp_path, body, title=f"hidden title has {token}")
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_real_markers_loose")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    markers = json.loads(MARKERS_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, markers)

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["no_match_count"] == 1


def test_real_markers_multiple_different_owner_relative_paths_remain_unresolved(tmp_path: Path) -> None:
    body = "tools/issue_qa_autonomous.py and core/daemon_engine.py"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_real_relative_ambiguous")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    markers = json.loads(MARKERS_CONFIG.read_text(encoding="utf-8"))

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, markers)

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["ambiguous_count"] == 1
    assert payload["unresolved_summary"]["ambiguous_multiple_owners"] == 1


def test_parent_child_overlap_with_multiple_owners_remains_unresolved(tmp_path: Path) -> None:
    body = "/home/ser/projects/cc-daemons/core/shared.py"
    root = make_storage(tmp_path, body)
    mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_parent_child_overlap")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    config = marker_config(
        [
            marker("parent", "/home/ser/projects/cc-daemons", "ecosystem/cc-daemons", "project"),
            marker("child", "/home/ser/projects/cc-daemons/core", "ecosystem/cc-daemons/shared-core", "shared"),
        ]
    )

    payload = mod.build_evidence_map(inventory_for(body), root, "default", tree, config)

    assert payload["ok"] is True
    assert payload["rules"] == []
    assert payload["ambiguous_count"] == 1
    assert payload["unresolved_summary"]["ambiguous_multiple_owners"] == 1


def test_forbidden_imports_network_subprocess_runtime() -> None:
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
    source = EVIDENCE_SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)


def test_generated_evidence_is_accepted_by_classifier(tmp_path: Path) -> None:
    token = "/tmp/opencode-run-nmbot/project"
    body = f"historical local source at {token}"
    root = make_storage(tmp_path, body)
    evidence_mod = load_module(EVIDENCE_SCRIPT, "project_memory_notebook_evidence_classifier_evidence")
    classify_mod = load_module(CLASSIFY_SCRIPT, "project_memory_notebook_evidence_classifier")
    tree = json.loads(TREE_CONFIG.read_text(encoding="utf-8"))
    inv = inventory_for(body)

    evidence = evidence_mod.build_evidence_map(inv, root, "default", tree, marker_config([marker("m1", token)]))
    manifest = classify_mod.classify_inventory(inv, tree, evidence)

    assert evidence["ok"] is True
    assert manifest["ok"] is True
    assert manifest["classified_count"] == 1
    assert manifest["records"][0]["owner_path"] == "ecosystem/nmbot"
