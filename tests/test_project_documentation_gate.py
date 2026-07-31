from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_documentation_gate.py"


def load_gate_module():
    spec = importlib.util.spec_from_file_location("project_documentation_gate_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_repo(tmp_path: Path, monkeypatch):
    mod = load_gate_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    for name in (
        "CURRENT_ARCHITECTURE.md",
        "PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md",
        "NOTEBOOKLM_SUMMARY_MIGRATION_WRITE_PLAN.md",
        "EXPERIMENTS.md",
        "NMBOT_RUNBOOK.md",
        "NMBOT_RUNTIME_VERSIONS.md",
        "NMBOT_EXTERNAL_CONTRACTS.md",
        "MARSHRUT_K_DOKAZATELSTVU.md",
        "QAPAIRS_RETRIEVAL.md",
        "CC2_RETRIEVAL.md",
        "MPN_RETRIEVAL.md",
    ):
        (tmp_path / "docs" / name).write_text("# doc\n", encoding="utf-8")
    (tmp_path / "scripts" / "owner.py").write_text("# owner\n", encoding="utf-8")
    owners = {
        "schema": "project_documentation_owners.v1",
        "projects": {
            "nmbot": {
                "architecture": "docs/CURRENT_ARCHITECTURE.md",
                "retrieval": "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md",
                "migration": "docs/NOTEBOOKLM_SUMMARY_MIGRATION_WRITE_PLAN.md",
                "experiments": "docs/EXPERIMENTS.md",
                "operations": "docs/NMBOT_RUNBOOK.md",
                "runtime_versions": "docs/NMBOT_RUNTIME_VERSIONS.md",
                "external_contracts": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
                "methodology": "docs/MARSHRUT_K_DOKAZATELSTVU.md",
            },
            "qapairs": {"retrieval": "docs/QAPAIRS_RETRIEVAL.md"},
            "cc2": {"retrieval": "docs/CC2_RETRIEVAL.md"},
            "mpn": {"retrieval": "docs/MPN_RETRIEVAL.md"},
        },
    }
    write_json(tmp_path / "owners.json", owners)
    return mod


def event(update_id: str = "upd-001", **overrides):
    payload = {
        "schema": "project_documentation_update.v1",
        "update_id": update_id,
        "project_id": "nmbot",
        "topic": "architecture",
        "change_type": "addition",
        "status": "pending",
        "fact": "The local documentation gate queues only verified documentation facts.",
        "evidence_refs": [{"kind": "doc", "ref": "docs/CURRENT_ARCHITECTURE.md:1", "sha256": None}],
        "verification": {"type": "source_readback", "result": "pending", "verified_at": None},
        "supersedes_anchor": None,
        "human_approved": False,
        "docs_write_performed": False,
        "notebook_write_authorized": False,
        "runtime_change_authorized": False,
        "production_claim_authorized": False,
        "created_at": "2026-07-27T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def verified_event(update_id: str = "upd-verified", **overrides):
    payload = event(
        update_id,
        status="verified",
        human_approved=True,
        verification={"type": "focused_test", "result": "passed", "verified_at": "2026-07-27T00:01:00Z"},
    )
    payload.update(overrides)
    return payload


def receipt(receipt_id: str = "receipt-001", **overrides):
    payload = {
        "schema": "project_documentation_verify_receipt.v1",
        "receipt_id": receipt_id,
        "project_id": "nmbot",
        "topic": "architecture",
        "change_type": "addition",
        "fact": "The receipt capture path records verified documentation facts without publishing them.",
        "evidence_refs": [{"kind": "doc", "ref": "docs/CURRENT_ARCHITECTURE.md:1", "sha256": None}],
        "verification": {"type": "focused_test", "result": "passed", "verified_at": "2026-07-27T00:02:00Z"},
        "supersedes_anchor": None,
        "created_at": "2026-07-27T00:03:00Z",
    }
    payload.update(overrides)
    return payload


def test_owner_map_validation_default_config() -> None:
    mod = load_gate_module()
    result = mod.validate_owner_map("config/project_documentation_owners.json")
    assert result["valid"] is True
    assert result["owner_count"] >= 11


def test_pending_enqueue_then_plan_denied(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "pending.json", event())
    result = mod.append_event("pending.json", "data/queue.jsonl", "owners.json")
    assert result["appended"] is True
    plan = mod.plan_update("upd-001", "data/queue.jsonl", "owners.json")
    assert plan["ok"] is False
    assert {err["code"] for err in plan["errors"]} >= {"plan_requires_verified", "plan_requires_approval"}


def test_verified_approved_enqueue_returns_safe_plan(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "verified.json", verified_event())
    assert mod.append_event("verified.json", "data/queue.jsonl", "owners.json")["appended"] is True
    plan = mod.plan_update("upd-verified", "data/queue.jsonl", "owners.json")
    assert plan["ok"] is True
    assert plan["target_doc"] == "docs/CURRENT_ARCHITECTURE.md"
    assert plan["allowed_action"] == "prepare_human_patch_only"
    assert plan["docs_write_performed"] is False


def test_verified_requires_evidence_but_can_wait_for_human_approval(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    no_evidence = verified_event("upd-no-evidence", evidence_refs=[])
    invalid = mod.validate_event(no_evidence, "owners.json")
    assert invalid["valid"] is False
    assert "verified_requires_evidence" in {item["code"] for item in invalid["errors"]}

    awaiting_approval = verified_event("upd-awaiting-approval", human_approved=False)
    assert mod.validate_event(awaiting_approval, "owners.json")["valid"] is True
    write_json(tmp_path / "awaiting.json", awaiting_approval)
    assert mod.append_event("awaiting.json", "data/queue.jsonl", "owners.json")["appended"] is True
    plan = mod.plan_update("upd-awaiting-approval", "data/queue.jsonl", "owners.json")
    assert plan["ok"] is False
    assert "plan_requires_approval" in {item["code"] for item in plan["errors"]}


def test_correct_owner_routing_for_non_nmbot_projects(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "q.json", verified_event("upd-q", project_id="qapairs", topic="retrieval"))
    mod.append_event("q.json", "data/queue.jsonl", "owners.json")
    assert mod.plan_update("upd-q", "data/queue.jsonl", "owners.json")["target_doc"] == "docs/QAPAIRS_RETRIEVAL.md"


def test_correction_requires_stable_anchor(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    bad = event(change_type="correction", supersedes_anchor=None)
    result = mod.validate_event(bad, "owners.json")
    assert result["valid"] is False
    assert "correction_anchor" in {err["code"] for err in result["errors"]}
    good = event(change_type="correction", supersedes_anchor="#stable-anchor")
    assert mod.validate_event(good, "owners.json")["valid"] is True


def test_secret_raw_and_leaky_key_denied_without_echo(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    bad = event(payload="sk-secret-token-value")
    result = mod.validate_event(bad, "owners.json")
    rendered = json.dumps(result, ensure_ascii=False)
    assert result["valid"] is False
    assert "sk-secret-token-value" not in rendered
    assert {err["code"] for err in result["errors"]} & {"forbidden_field", "secretish_value", "event_keys"}


def test_absolute_traversal_and_symlink_escape_are_rejected(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    abs_event = event(evidence_refs=[{"kind": "doc", "ref": "/tmp/x.md:1", "sha256": None}])
    trav_event = event(evidence_refs=[{"kind": "doc", "ref": "docs/../secret.md:1", "sha256": None}])
    assert mod.validate_event(abs_event, "owners.json")["valid"] is False
    assert mod.validate_event(trav_event, "owners.json")["valid"] is False
    outside = tmp_path.parent / "outside_doc.md"
    outside.write_text("# outside\n", encoding="utf-8")
    os.symlink(outside, tmp_path / "docs" / "ESCAPE.md")
    owners = {"schema": "project_documentation_owners.v1", "projects": {"nmbot": {"architecture": "docs/ESCAPE.md"}}}
    write_json(tmp_path / "bad_owners.json", owners)
    assert mod.validate_owner_map("bad_owners.json")["valid"] is False


def test_cli_rejects_owner_and_store_overrides() -> None:
    for flag in ("--owners", "--store"):
        result = subprocess.run([sys.executable, "scripts/project_documentation_gate.py", "--validate", flag, "x"], cwd=ROOT, check=False, capture_output=True, text=True)
        assert result.returncode == 2
        assert "unrecognized arguments" in result.stderr


def test_cli_requires_exactly_one_command() -> None:
    missing = subprocess.run([sys.executable, "scripts/project_documentation_gate.py", "--json"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert missing.returncode == 2
    both = subprocess.run([sys.executable, "scripts/project_documentation_gate.py", "--validate", "--list"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert both.returncode == 2


def test_append_store_path_fail_closed(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "verified.json", verified_event())
    for bad_store in ("docs/x.md", "config/x.jsonl", "data/not-json.txt"):
        result = mod.append_event("verified.json", bad_store, "owners.json")
        assert result["appended"] is False
    os.symlink(tmp_path / "docs", tmp_path / "data")
    symlink_result = mod.append_event("verified.json", "data/queue.jsonl", "owners.json")
    assert symlink_result["appended"] is False
    assert "path_symlink" in {err["code"] for err in symlink_result["errors"]}


def test_append_store_file_symlink_fail_closed(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "verified.json", verified_event())
    (tmp_path / "data").mkdir()
    outside = tmp_path.parent / "outside_queue.jsonl"
    outside.write_text("", encoding="utf-8")
    os.symlink(outside, tmp_path / "data" / "queue.jsonl")
    result = mod.append_event("verified.json", "data/queue.jsonl", "owners.json")
    assert result["appended"] is False
    assert "path_symlink" in {err["code"] for err in result["errors"]}


def test_in_repo_owner_doc_symlink_rejected(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    os.symlink(tmp_path / "docs" / "CURRENT_ARCHITECTURE.md", tmp_path / "docs" / "ALIAS.md")
    owners = {"schema": "project_documentation_owners.v1", "projects": {"nmbot": {"architecture": "docs/ALIAS.md"}}}
    write_json(tmp_path / "bad_owners.json", owners)
    result = mod.validate_owner_map("bad_owners.json")
    assert result["valid"] is False
    assert "path_symlink" in {err["code"] for err in result["errors"]}


def test_evidence_paths_are_kind_scoped_and_secret_safe(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    (tmp_path / "tests" / "test_owner.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SAFE_SHAPE=hidden\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.py").write_text("# runtime log artifact\n", encoding="utf-8")
    bad_refs = [
        {"kind": "doc", "ref": ".env:1", "sha256": None},
        {"kind": "source", "ref": "logs/app.py:1", "sha256": None},
        {"kind": "source", "ref": "docs/CURRENT_ARCHITECTURE.md:1", "sha256": None},
        {"kind": "test", "ref": "scripts/owner.py:1", "sha256": None},
        {"kind": "doc", "ref": "docs/CURRENT_ARCHITECTURE.txt:1", "sha256": None},
    ]
    for ref in bad_refs:
        result = mod.validate_event(event(evidence_refs=[ref]), "owners.json")
        assert result["valid"] is False

    good = event(
        evidence_refs=[
            {"kind": "source", "ref": "scripts/owner.py:1", "sha256": None},
            {"kind": "test", "ref": "tests/test_owner.py:1", "sha256": None},
            {"kind": "doc", "ref": "docs/CURRENT_ARCHITECTURE.md:1", "sha256": None},
        ]
    )
    assert mod.validate_event(good, "owners.json")["valid"] is True


def test_duplicate_update_id_and_fingerprint_denied(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "one.json", event("dup-1"))
    write_json(tmp_path / "two.json", event("dup-1", fact="A different safe fact."))
    write_json(tmp_path / "three.json", event("dup-2"))
    assert mod.append_event("one.json", "data/queue.jsonl", "owners.json")["appended"] is True
    dup_id = mod.append_event("two.json", "data/queue.jsonl", "owners.json")
    assert dup_id["errors"][0]["code"] == "duplicate_update_id"
    dup_fp = mod.append_event("three.json", "data/queue.jsonl", "owners.json")
    assert dup_fp["errors"][0]["code"] == "duplicate_fingerprint"


def test_passed_receipt_captures_verified_unapproved_and_plan_denied(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "receipt.json", receipt())
    captured = mod.capture_receipt("receipt.json", "data/queue.jsonl", "owners.json")
    assert captured == {
        "schema": "project_documentation_capture_result.v1",
        "appended": True,
        "valid": True,
        "receipt_id": "receipt-001",
        "update_id": "receipt-001",
        "status": "verified",
        "project_id": "nmbot",
        "topic": "architecture",
        "target_doc": "docs/CURRENT_ARCHITECTURE.md",
    }
    stored = [item for _line, item in mod.iter_store_records("data/queue.jsonl")][0]
    assert stored["update_id"] == "receipt-001"
    assert stored["status"] == "verified"
    assert stored["human_approved"] is False
    assert stored["docs_write_performed"] is False
    plan = mod.plan_update("receipt-001", "data/queue.jsonl", "owners.json")
    assert plan["ok"] is False
    assert "plan_requires_approval" in {item["code"] for item in plan["errors"]}


def test_pending_and_failed_receipts_capture_pending(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    for receipt_id, result in (("receipt-pending", "pending"), ("receipt-failed", "failed")):
        write_json(tmp_path / f"{receipt_id}.json", receipt(receipt_id, verification={"type": "focused_test", "result": result, "verified_at": None}))
        captured = mod.capture_receipt(f"{receipt_id}.json", "data/queue.jsonl", "owners.json")
        assert captured["appended"] is True
        assert captured["status"] == "pending"
    records = [item for _line, item in mod.iter_store_records("data/queue.jsonl")]
    assert [item["status"] for item in records] == ["pending", "pending"]


def test_receipt_invalid_verification_timestamps_fail_closed_without_echo(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    cases = [
        receipt("bad-passed", verification={"type": "focused_test", "result": "passed", "verified_at": None}),
        receipt("bad-pending", verification={"type": "focused_test", "result": "pending", "verified_at": "2026-07-27T00:02:00Z"}),
    ]
    for index, payload in enumerate(cases):
        write_json(tmp_path / f"bad-{index}.json", payload)
        captured = mod.capture_receipt(f"bad-{index}.json", "data/queue.jsonl", "owners.json")
        assert captured["appended"] is False
        assert captured["valid"] is False
    assert not (tmp_path / "data" / "queue.jsonl").exists()


def test_receipt_extra_authorization_and_target_fields_fail_closed(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    for key, value in (("target_doc", "docs/OTHER.md"), ("human_approved", True), ("docs_write_performed", True), ("notebook_write_authorized", True)):
        payload = receipt(f"bad-{key}")
        payload[key] = value
        write_json(tmp_path / f"bad-{key}.json", payload)
        captured = mod.capture_receipt(f"bad-{key}.json", "data/queue.jsonl", "owners.json")
        rendered = json.dumps(captured, ensure_ascii=False)
        assert captured["appended"] is False
        assert "receipt_keys" in rendered
        assert "docs/OTHER.md" not in rendered


def test_receipt_secret_raw_customer_data_and_unknown_owner_fail_without_echo(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    cases = [
        receipt("bad-secret", fact="The token is sk-secret-token-value-forbidden."),
        receipt("bad-raw", fact="raw payload should never be captured"),
        receipt("bad-customer", fact="Customer phone +7 999 123 45 67 must not be captured"),
        receipt("bad-owner", project_id="unknown_project"),
    ]
    for payload in cases:
        write_json(tmp_path / f"{payload['receipt_id']}.json", payload)
        captured = mod.capture_receipt(f"{payload['receipt_id']}.json", "data/queue.jsonl", "owners.json")
        rendered = json.dumps(captured, ensure_ascii=False)
        assert captured["appended"] is False
        assert "sk-secret-token-value-forbidden" not in rendered
        assert "+7 999 123 45 67" not in rendered
        assert "raw payload should never" not in rendered


def test_receipt_duplicates_fail_closed(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "one.json", receipt("dup-receipt"))
    write_json(tmp_path / "two.json", receipt("dup-receipt", fact="Another safe fact."))
    write_json(tmp_path / "three.json", receipt("dup-fingerprint"))
    assert mod.capture_receipt("one.json", "data/queue.jsonl", "owners.json")["appended"] is True
    duplicate_id = mod.capture_receipt("two.json", "data/queue.jsonl", "owners.json")
    duplicate_fp = mod.capture_receipt("three.json", "data/queue.jsonl", "owners.json")
    assert duplicate_id["appended"] is False
    assert duplicate_id["errors"][0]["code"] == "duplicate_update_id"
    assert duplicate_fp["appended"] is False
    assert duplicate_fp["errors"][0]["code"] == "duplicate_fingerprint"


def test_capture_flushes_and_fsyncs(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(mod.os, "fsync", lambda fileno: calls.append(fileno))
    write_json(tmp_path / "receipt.json", receipt())
    assert mod.capture_receipt("receipt.json", "data/queue.jsonl", "owners.json")["appended"] is True
    assert calls


def test_cli_capture_exact_safe_metadata(tmp_path, monkeypatch, capsys) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "DEFAULT_STORE", Path("data/queue.jsonl"))
    monkeypatch.setattr(mod, "DEFAULT_OWNERS", Path("owners.json"))
    write_json(tmp_path / "receipt.json", receipt(fact="A safe but hidden capture fact."))
    code = mod.main(["--capture", "--input", "receipt.json", "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert code == 0
    assert payload == {
        "schema": "project_documentation_capture_result.v1",
        "appended": True,
        "valid": True,
        "receipt_id": "receipt-001",
        "update_id": "receipt-001",
        "status": "verified",
        "project_id": "nmbot",
        "topic": "architecture",
        "target_doc": "docs/CURRENT_ARCHITECTURE.md",
    }
    assert "fact" not in output
    assert "evidence_refs" not in output
    assert "patch" not in output.lower()


def test_malformed_queue_and_input_json_fail_closed(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "queue.jsonl").write_text("{bad\n", encoding="utf-8")
    result = mod.validate_store("data/queue.jsonl", "owners.json")
    assert result["valid"] is False
    assert "malformed_queue" in {err["code"] for err in result["errors"]}
    (tmp_path / "bad.json").write_text("{bad\n", encoding="utf-8")
    try:
        mod.append_event("bad.json", "data/other.jsonl", "owners.json")
    except mod.GateError as exc:
        assert exc.code == "malformed_json"


def test_append_flushes_and_fsyncs(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(mod.os, "fsync", lambda fileno: calls.append(fileno))
    write_json(tmp_path / "verified.json", verified_event())
    assert mod.append_event("verified.json", "data/queue.jsonl", "owners.json")["appended"] is True
    assert calls


def test_no_dangerous_imports_or_docs_writes_static() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    forbidden_imports = ["import subprocess", "from subprocess", "import urllib", "from urllib", "import requests", "import socket", "import importlib", "__import__"]
    assert not any(item in source for item in forbidden_imports)
    assert "eval(" not in source
    assert "exec(" not in source
    assert "target_doc" in source
    assert "open(\"docs/" not in source


def test_list_metadata_never_includes_fact(tmp_path, monkeypatch) -> None:
    mod = make_repo(tmp_path, monkeypatch)
    write_json(tmp_path / "verified.json", verified_event(fact="A safe but hidden fact."))
    mod.append_event("verified.json", "data/queue.jsonl", "owners.json")
    listed = mod.list_updates("data/queue.jsonl", "owners.json")
    rendered = json.dumps(listed, ensure_ascii=False)
    assert listed["ok"] is True
    assert "fact" not in rendered
    assert "A safe but hidden fact" not in rendered


def test_cli_py_compile_and_config_json_validate() -> None:
    compile_result = subprocess.run([sys.executable, "-m", "py_compile", "scripts/project_documentation_gate.py"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert compile_result.returncode == 0, compile_result.stderr
    data = json.loads((ROOT / "config" / "project_documentation_owners.json").read_text(encoding="utf-8"))
    assert data["schema"] == "project_documentation_owners.v1"
