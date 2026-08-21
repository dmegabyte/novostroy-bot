from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot.py"


def load_wrapper_module():
    spec = importlib.util.spec_from_file_location("nmbot_wrapper_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def pop_envelope(output: dict) -> dict:
    envelope = output.pop("diagnostic_envelope")
    assert envelope["schema_version"] == "nmbot.diagnostic.v1"
    assert envelope["safety"] == {"read_only": True, "raw_output_included": False}
    assert set(envelope["correlation"]) == {"trace_present", "task_present"}
    return envelope


def test_check_delegates_direct_argv_and_returncode(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["check", "docs", "audit", "--dry-run", "--json"]) == 7
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_check.py", "docs", "audit", "--dry-run", "--json"], "cwd": mod.ROOT, "check": False}]


def test_summary_latency_uses_actual_runtime_timing_ms_total_first() -> None:
    mod = load_wrapper_module()

    assert mod._summary_latency_ms({"runtime_summary": {"timing_ms": {"total": 123}}, "total_ms": 999}) == 123
    assert mod._summary_latency_ms({"total_ms": 456}) == 456
    assert mod._summary_latency_ms({"runtime_summary": {"timing": {"total_ms": 789}}}) == 789


def test_timeline_flattens_bridge_evidence_and_runtime_gateway_attempts() -> None:
    mod = load_wrapper_module()
    payload = {
        "traces": [
            {
                "trace_ref": "trace_abc123abc123",
                "stage": "delivery_complete",
                "evidence": [
                    {"stage": "request_received", "status": "accepted", "duration_ms": 1},
                    {"stage": "upstream_response", "status": "upstream", "duration_ms": 50},
                ],
                "actual": {
                    "runtime_gateway_attempts": [
                        {"stage": "gateway_attempt", "ok": True, "gateway_task_id": "task-1", "duration_ms": 42, "parse_status": "ok", "raw_response": "secret"}
                    ]
                },
            }
        ]
    }

    timeline = mod._build_timeline_from_payload("trace", payload, {})
    steps = timeline["steps"]
    assert [step["stage"] for step in steps] == ["request_received", "upstream_response", "gateway_attempt"]
    assert steps[-1]["gateway_task_id"] == "task-1"
    assert steps[-1]["parse_status"] == "ok"
    assert "raw_response" not in json.dumps(timeline, ensure_ascii=False)


def test_check_quality_delegates_to_canonical_dispatcher(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["check", "quality", "--dry-run"]) == 0
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_check.py", "quality", "--dry-run"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_check_rejects_invalid_scope_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["check", "prod"]) == 2


def test_audit_and_preflight_delegate_without_shell(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["audit", "--human", "--root", "/tmp/example"]) == 0
    assert mod.main(["preflight", "--human"]) == 0
    assert calls == [
        [sys.executable, "scripts/nmbot_project_audit.py", "--human", "--root", "/tmp/example"],
        [sys.executable, "scripts/nmbot_release_preflight.py", "--human"],
    ]


def test_diag_delegates_only_when_invoked_and_does_not_run_real_diag(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 3)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diag", "--local", "--json"]) == 3
    assert calls == [{"argv": ["bash", "scripts/nmbot_diag.sh", "--local", "--json"], "cwd": mod.ROOT, "check": False}]


def test_context_delegates_direct_argv_and_does_not_run_checks(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 5)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["context", "--pack", "prompt/rental", "--human"]) == 5
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_context_pack.py", "--pack", "prompt/rental", "--human"], "cwd": mod.ROOT, "check": False}]


def test_retrieve_delegates_exact_argv_without_parsing_flags(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 8)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["retrieve", "finance disclaimer first list", "--json", "--cards", "5", "--source-cards"]) == 8
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_retrieval.py", "finance disclaimer first list", "--json", "--cards", "5", "--source-cards"], "cwd": mod.ROOT, "check": False}]


def test_navigate_delegates_exact_argv_without_duplicate_parser(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 11)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["navigate", "resolve_response_path", "--json", "--manifest", "config/alt.json"]) == 11
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_navigation.py", "resolve_response_path", "--json", "--manifest", "config/alt.json"], "cwd": mod.ROOT, "check": False}]


def test_context_gate_delegates_exact_argv_without_parsing_flags(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 15)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["context-gate", "v2.search", "--project-id", "nmbot", "--evidence-type", "stage", "--definition-of-done", "source plus test", "--json"]) == 15
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_context_gate.py", "v2.search", "--project-id", "nmbot", "--evidence-type", "stage", "--definition-of-done", "source plus test", "--json"], "cwd": mod.ROOT, "check": False}]


def test_context_gate_strict_target_delegates_exact_argv_without_parsing(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 16)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    argv = ["context-gate", "ignored", "--project-id", "nmbot", "--evidence-type", "docs", "--target-kind", "docs", "--target", "## Contract", "--target-owner", "docs/search.md", "--definition-of-done", "exact docs", "--json"]
    assert mod.main(argv) == 16
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_context_gate.py", *argv[1:]], "cwd": mod.ROOT, "check": False}]


def test_memory_registry_delegates_exact_argv_without_wrapper_parsing(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 17)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["memory-registry", "--project-id", "nmbot", "--json"]) == 17
    assert calls == [{"argv": [sys.executable, "scripts/project_memory_registry.py", "--project-id", "nmbot", "--json"], "cwd": mod.ROOT, "check": False}]


def test_memory_outcomes_delegates_exact_argv_without_wrapper_parsing(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 18)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["memory-outcomes", "--hints", "--project-id", "nmbot", "--policy-version", "nmbot-passive-v1", "--route", "docs", "--evidence-type", "docs", "--json"]) == 18
    assert calls == [{"argv": [sys.executable, "scripts/project_memory_outcomes.py", "--hints", "--project-id", "nmbot", "--policy-version", "nmbot-passive-v1", "--route", "docs", "--evidence-type", "docs", "--json"], "cwd": mod.ROOT, "check": False}]


def test_docs_gate_delegates_exact_argv_without_wrapper_parsing(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 19)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["docs-gate", "--plan", "--update-id", "update-001", "--json"]) == 19
    assert calls == [{"argv": [sys.executable, "scripts/project_documentation_gate.py", "--plan", "--update-id", "update-001", "--json"], "cwd": mod.ROOT, "check": False}]


def test_explain_without_args_delegates_to_response_path_resolver_default(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 9)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["explain"]) == 9
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_response_path.py"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_explain_registry_delegates_without_forcing_version(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 10)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["explain", "--registry", "alternate.json"]) == 10
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_response_path.py", "--registry", "alternate.json"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_explain_version_delegates_exact_argv_and_returncode(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 12)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["explain", "--version", "v2", "--json"]) == 12
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_response_path.py", "--version", "v2", "--json"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_explain_path_id_delegates_exact_argv_and_returncode(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 13)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["explain", "--path-id", "jivo.v2.turn.v1", "--json"]) == 13
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_response_path.py", "--path-id", "jivo.v2.turn.v1", "--json"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_explain_stage_id_delegates_exact_argv_and_returncode(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 14)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["explain", "--stage-id", "v2.search", "--json"]) == 14
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_response_path.py", "--stage-id", "v2.search", "--json"],
            "cwd": mod.ROOT,
            "check": False,
        }
    ]


def test_recipes_overlap_delegates_only_known_subcommand(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 4)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["recipes", "overlap", "--human", "--threshold", "0.9"]) == 4
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_recipe_overlap.py", "--human", "--threshold", "0.9"], "cwd": mod.ROOT, "check": False}]


def test_recipes_pair_delegates_direct_argv(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 6)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["recipes", "pair", "recipe_a", "recipe_b", "--human"]) == 6
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_recipe_overlap.py", "--pair", "recipe_a", "recipe_b", "--human"], "cwd": mod.ROOT, "check": False}]


def test_recipes_pair_rejects_missing_ids_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["recipes", "pair", "recipe_a"]) == 2


def test_recipes_explain_delegates_direct_argv(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 8)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["recipes", "explain", "recipe_a", "recipe_b", "--human"]) == 8
    assert calls == [{"argv": [sys.executable, "scripts/nmbot_recipe_overlap.py", "--explain", "recipe_a", "recipe_b", "--human"], "cwd": mod.ROOT, "check": False}]


def test_recipes_explain_rejects_missing_ids_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["recipes", "explain", "recipe_a"]) == 2
    assert mod.main(["recipes", "explain", "--human", "recipe_b"]) == 2


def test_recipes_rejects_unknown_subcommand_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["recipes", "delete"]) == 2


def test_diagnose_trace_uses_default_local_paths_and_normalizes(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    calls = []

    child_payload = {
        "summary": {"traces": 1},
        "traces": [
            {
                "stage": "delivery_missing",
                "outcome": "upstream_seen_but_no_terminal_delivery",
                "confidence": "high",
                "evidence": [
                    {"http_status": 200, "payload": "must-not-print"},
                    {"http_status": 502, "token": "secret-token"},
                ],
                "audit": [{"model_output": "must-not-print"}],
            }
        ],
    }

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append({"argv": argv, "cwd": cwd, "check": check, "capture_output": capture_output, "text": text})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-123", "--json"]) == 0
    assert calls == [
        {
            "argv": [
                sys.executable,
                "scripts/nmbot_jivo_dialogue_diagnose.py",
                "logs/n8n_bridge_structured.jsonl",
                "--audit-log",
                "logs/dialogue_journal.jsonl",
                "--trace",
                "trace-123",
                "--json",
            ],
            "cwd": tmp_path,
            "check": False,
            "capture_output": True,
            "text": True,
        }
    ]
    output = json.loads(capsys.readouterr().out)
    envelope = pop_envelope(output)
    assert envelope["evidence_scope"] == "local"
    assert envelope["correlation"] == {"trace_present": True, "task_present": False}
    assert output == {
        "schema_version": "nmbot.diagnose.v1",
        "kind": "trace",
        "status": "reported",
        "owner_layer": "jivo_bridge",
        "http_status": 502,
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "bash scripts/nmbot_diag.sh --logs",
        "owner_source": None,
        "owner_symbol": None,
        "contract_doc": None,
        "focused_test": None,
        "next_check": None,
        "owner_confidence": "unknown",
        "stage": "delivery_missing",
        "outcome": "upstream_seen_but_no_terminal_delivery",
        "confidence": "high",
    }
    dumped = json.dumps(output, ensure_ascii=False)
    assert "must-not-print" not in dumped
    assert "secret-token" not in dumped


def test_diagnose_trace_missing_local_log_reports_no_evidence_without_subprocess(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--trace", "trace-1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "no_evidence"
    assert output["error_code"] == "bridge_log_missing"
    assert output["owner_layer"] == "unknown"


def test_diagnose_task_passes_date_logs_dir_and_normalizes(monkeypatch, capsys) -> None:
    mod = load_wrapper_module()
    calls = []
    child_payload = {
        "task_id": "task-abc",
        "status": "failed",
        "layer": "provider",
        "category": "provider_error",
        "error_code": "explicit_provider_error",
        "result": {"prompt": "must-not-print"},
    }

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append({"argv": argv, "cwd": cwd, "check": check, "capture_output": capture_output, "text": text})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--task", "task-abc", "--date", "2026-07-27", "--logs-dir", "/tmp/logs", "--json"]) == 0
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_gateway_task_diag.py", "task-abc", "--json", "--date", "2026-07-27", "--logs-dir", "/tmp/logs"],
            "cwd": mod.ROOT,
            "check": False,
            "capture_output": True,
            "text": True,
        }
    ]
    output = json.loads(capsys.readouterr().out)
    envelope = pop_envelope(output)
    assert envelope["correlation"] == {"trace_present": False, "task_present": True}
    assert output == {
        "schema_version": "nmbot.diagnose.v1",
        "kind": "task",
        "status": "failed",
        "owner_layer": "provider",
        "http_status": None,
        "task_id": "task-abc",
        "provider_error": "explicit_provider_error",
        "parse_status": "not_reported",
        "next_command": "python3 scripts/nmbot_gateway_task_diag.py TASK_ID --scenario --json",
        "owner_source": None,
        "owner_symbol": None,
        "contract_doc": None,
        "focused_test": None,
        "next_check": None,
        "owner_confidence": "unknown",
        "error_code": "explicit_provider_error",
    }
    assert "must-not-print" not in json.dumps(output, ensure_ascii=False)


def test_diagnose_trace_stage_resolves_owner_card_from_local_stage_map(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "nmbot_stage_map.json").write_text(
        json.dumps(
            {
                "stages": {
                    "v2.search": {
                        "source": "scripts/nmbot_runtime_adapter.py",
                        "source_symbol": "search",
                        "doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
                        "test": "tests/test_nmbot_v2_search_contract_runtime.py",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "v2.search", "outcome": "failed", "confidence": "high", "evidence": [{"http_status": 500}], "raw": "must-not-print"}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-v2", "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["stage"] == "v2.search"
    assert output["owner_source"] == "scripts/nmbot_runtime_adapter.py"
    assert output["owner_symbol"] == "search"
    assert output["contract_doc"] == "docs/NMBOT_EXTERNAL_CONTRACTS.md"
    assert output["focused_test"] == "tests/test_nmbot_v2_search_contract_runtime.py"
    assert output["next_check"] == "python3 -m pytest -q tests/test_nmbot_v2_search_contract_runtime.py"
    assert output["owner_confidence"] == "stage"
    assert "must-not-print" not in output_text


def test_diagnose_trace_unknown_stage_returns_null_owner_card(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "nmbot_stage_map.json").write_text(json.dumps({"stages": {"v2.search": {"source": "scripts/x.py"}}}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "unknown.stage", "outcome": "failed", "confidence": "low", "evidence": []}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-unknown", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["stage"] == "unknown.stage"
    assert output["owner_confidence"] == "unknown"
    for key in ("owner_source", "owner_symbol", "contract_doc", "focused_test", "next_check"):
        assert output[key] is None


def test_diagnose_rejects_bad_selectors_and_unknown_options_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--trace", "trace-1", "--task", "task-1"]) == 2
    assert mod.main(["diagnose", "--trace"]) == 2
    assert mod.main(["diagnose", "--trace", "trace-1", "--raw"]) == 2
    assert mod.main(["diagnose", "--unknown"]) == 2


def test_diagnose_implicit_latest_task_routes_without_selector(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(json.dumps({"task_id": "task-implicit", "prompt": "must-not-print"}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_utc_date", lambda: "2026-07-27")
    calls = []
    child_payload = {"task_id": "task-implicit", "status": "failed", "layer": "gateway", "error_code": "gateway_failed", "raw": "must-not-print"}

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append({"argv": argv, "cwd": cwd, "check": check, "capture_output": capture_output, "text": text})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose"]) == 0
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_gateway_task_diag.py", "task-implicit", "--json", "--date", "2026-07-27", "--logs-dir", "logs"],
            "cwd": tmp_path,
            "check": False,
            "capture_output": True,
            "text": True,
        }
    ]
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["kind"] == "task"
    assert output["task_id"] == "task-implicit"
    assert "must-not-print" not in output_text


def test_diagnose_implicit_latest_human_is_one_safe_line(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(json.dumps({"gateway_task_id": "243", "model": "must-not-print"}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {
        "task_id": "243",
        "status": "failed",
        "layer": "provider",
        "category": "provider_error",
        "error_code": "explicit_provider_error",
        "result": {"secret": "must-not-print"},
    }

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="raw child must-not-print")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--human", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    output_text = capsys.readouterr().out
    assert output_text.count("\n") == 1
    assert output_text.startswith("Статус: failed · владелец: provider · HTTP: - · task: 243 · provider: explicit_provider_error · parse: not_reported")
    for secret in ("must-not-print", "model", "result", "raw child"):
        assert secret not in output_text


def test_diagnose_trace_human_includes_compact_owner_card_without_raw_child_fields(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "nmbot_stage_map.json").write_text(
        json.dumps(
            {
                "stages": {
                    "v2.search": {
                        "source": "scripts/nmbot_runtime_adapter.py",
                        "source_symbol": "search",
                        "doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
                        "test": "tests/test_nmbot_v2_search_contract_runtime.py",
                        "raw_child_field": "must-not-print",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "v2.search", "outcome": "failed", "confidence": "high", "evidence": [{"http_status": 500}], "raw_child_field": "must-not-print"}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="raw child must-not-print")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-v2", "--human"]) == 0
    output_text = capsys.readouterr().out
    assert output_text.count("\n") == 1
    assert "owner_source: scripts/nmbot_runtime_adapter.py" in output_text
    assert "owner_symbol: search" in output_text
    assert "focused_test: tests/test_nmbot_v2_search_contract_runtime.py" in output_text
    assert "next_check: python3 -m pytest -q tests/test_nmbot_v2_search_contract_runtime.py" in output_text
    for secret in ("must-not-print", "raw_child_field", "raw child"):
        assert secret not in output_text


def test_diagnose_explicit_task_and_trace_human_format(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    calls = []
    payloads = [
        {"task_id": "task-human", "status": "failed", "layer": "provider", "category": "provider_error", "error_code": "explicit_provider_error"},
        {"traces": [{"stage": "upstream_failure", "outcome": "failed", "confidence": "low", "evidence": [{"http_status": 502}], "secret": "must-not-print"}]},
    ]

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payloads.pop(0)), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--task", "task-human", "--human"]) == 0
    task_line = capsys.readouterr().out
    assert "Статус: failed" in task_line
    assert "task: task-human" in task_line
    assert "provider: explicit_provider_error" in task_line
    assert mod.main(["diagnose", "--trace", "trace-human", "--logs-dir", str(logs), "--human"]) == 0
    trace_line = capsys.readouterr().out
    assert trace_line.count("\n") == 1
    assert "владелец: api_or_upstream" in trace_line
    assert "HTTP: 502" in trace_line
    assert "stage: upstream_failure" in trace_line
    assert "must-not-print" not in trace_line


def test_diagnose_human_and_json_are_mutually_exclusive_before_dispatch(monkeypatch, capsys) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--human", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "mutually exclusive" in captured.err


def test_diagnose_child_malformed_output_is_bounded(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout="not json with raw payload secret", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-1"]) == 3
    output = json.loads(capsys.readouterr().out)
    pop_envelope(output)
    assert output == {
        "schema_version": "nmbot.diagnose.v1",
        "kind": "trace",
        "status": "diagnostic_failed",
        "owner_layer": "unknown",
        "http_status": None,
        "task_id": None,
        "provider_error": None,
        "parse_status": "not_reported",
        "next_command": "check child diagnostic directly",
        "owner_source": None,
        "owner_symbol": None,
        "contract_doc": None,
        "focused_test": None,
        "next_check": None,
        "owner_confidence": "unknown",
        "error_code": "child_json_parse_error",
    }
    assert "raw payload secret" not in json.dumps(output, ensure_ascii=False)


def test_diagnose_nonzero_child_uses_safe_json_stderr(monkeypatch, capsys) -> None:
    mod = load_wrapper_module()
    safe_error = {"status": "diagnostic_failed", "error_code": "missing_gateway_token"}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr=json.dumps(safe_error))

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--task", "task-1"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "diagnostic_failed"
    assert output["error_code"] == "missing_gateway_token"
    assert output["owner_layer"] == "unknown"


def test_diagnose_latest_task_uses_previous_actionable_row_and_safe_output(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    journal = logs / "bot_error_events-2026-07-27.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "task-old", "payload_preview": "must-not-print"}),
                json.dumps({"gateway_task_id": "task-new", "model": "must-not-print", "prompt": "must-not-print"}),
                json.dumps({"status": "newest-without-id", "contacts": "must-not-print", "tokens": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    calls = []
    child_payload = {"task_id": "task-new", "status": "failed", "layer": "gateway", "error_code": "gateway_failed"}

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append({"argv": argv, "cwd": cwd, "check": check, "capture_output": capture_output, "text": text})
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--latest", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    assert calls == [
        {
            "argv": [sys.executable, "scripts/nmbot_gateway_task_diag.py", "task-new", "--json", "--date", "2026-07-27", "--logs-dir", str(logs)],
            "cwd": tmp_path,
            "check": False,
            "capture_output": True,
            "text": True,
        }
    ]
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["kind"] == "task"
    assert output["task_id"] == "task-new"
    for secret in ("must-not-print", "payload_preview", "model", "prompt", "contacts", "tokens"):
        assert secret not in output_text


def test_diagnose_latest_raw_trace_routes_without_printing_trace_id(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(json.dumps({"trace_id": "raw-trace-secret", "exception": "must-not-print"}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    calls = []
    child_payload = {"traces": [{"stage": "upstream_failure", "outcome": "failed", "confidence": "medium", "evidence": [{"http_status": 504}]}]}

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--latest", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    assert calls == [
        [
            sys.executable,
            "scripts/nmbot_jivo_dialogue_diagnose.py",
            str(logs / "n8n_bridge_structured.jsonl"),
            "--audit-log",
            str(logs / "dialogue_journal.jsonl"),
            "--trace",
            "raw-trace-secret",
            "--json",
        ]
    ]
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["kind"] == "trace"
    assert output["owner_layer"] == "api_or_upstream"
    assert "raw-trace-secret" not in output_text
    assert "must-not-print" not in output_text


def test_diagnose_latest_ignores_trace_ref_alone(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(json.dumps({"trace_ref": "not-raw-trace", "session_ref": "ignored"}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--latest", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["kind"] == "latest"
    assert output["status"] == "no_evidence"
    assert output["error_code"] == "actionable_event_missing"


def test_diagnose_latest_missing_and_malformed_journal_are_bounded(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--latest", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["kind"] == "latest"
    assert missing["error_code"] == "error_log_missing"
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        'not json\n[]\n{}\n{"task_id":""}\n{"task_id":false}\n{"trace_id":"   "}\n',
        encoding="utf-8",
    )
    assert mod.main(["diagnose", "--latest", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    malformed = json.loads(capsys.readouterr().out)
    assert malformed["kind"] == "latest"
    assert malformed["status"] == "no_evidence"
    assert malformed["error_code"] == "actionable_event_missing"


def test_diagnose_latest_selector_conflicts_rejected_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--latest", "--trace", "trace-1"]) == 2
    assert mod.main(["diagnose", "--task", "task-1", "--latest"]) == 2
    assert mod.main(["diagnose", "--latest", "--unknown"]) == 2


def test_diagnose_recent_groups_sorts_and_resolves_exact_stage_owner(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "nmbot_stage_map.json").write_text(
        json.dumps(
            {
                "stages": {
                    "v2.search": {
                        "source": "scripts/nmbot_runtime_adapter.py",
                        "source_symbol": "search",
                        "doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
                        "test": "tests/test_nmbot_v2_search_contract_runtime.py",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"error_code": "old_outside_limit", "stage": "v2.search"}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "prompt": "must-not-print"}),
                json.dumps({"error_type": "Provider Error", "stage": "unknown.stage", "model": "must-not-print"}),
                json.dumps({"category": "main_search_timeout", "stage": "v2.search", "trace_id": "must-not-print"}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "task_id": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "4", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["schema_version"] == "nmbot.diagnose.recent.v1"
    assert output["kind"] == "recent"
    assert output["status"] == "reported"
    assert output["requested_limit"] == 4
    assert output["scanned_events"] == 4
    assert output["actionable_events"] == 4
    assert output["runtime_version_scope"] == "historical_event_evidence_not_current_process"
    assert output["runtime_versions"] == [{"runtime_version": "UNKNOWN", "count": 4}]
    assert output["groups"][0] == {
        "error_code": "main_search_timeout",
        "stage": "v2.search",
        "runtime_version": "UNKNOWN",
        "runtime_version_source": "insufficient_event_evidence",
        "count": 3,
        "owner_source": "scripts/nmbot_runtime_adapter.py",
        "owner_symbol": "search",
        "contract_doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
        "focused_test": "tests/test_nmbot_v2_search_contract_runtime.py",
        "next_check": "python3 -m pytest -q tests/test_nmbot_v2_search_contract_runtime.py",
        "owner_confidence": "stage",
    }
    assert output["groups"][1]["error_code"] == "provider_error"
    assert output["groups"][1]["owner_confidence"] == "unknown"
    for raw in ("must-not-print", "task_id", "trace_id", "model", "prompt", "old_outside_limit"):
        assert raw not in output_text


def test_diagnose_recent_ignores_malformed_and_counts_latest_parseable_rows(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        "\n".join(
            [
                "not json",
                json.dumps({"error_code": "old_actionable"}),
                json.dumps(["not-object"]),
                json.dumps({"note": "valid-but-not-actionable", "payload": "must-not-print"}),
                "{broken",
                json.dumps({"error_code": "new_actionable", "stage": "bad stage with spaces", "exception": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "2", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["scanned_events"] == 2
    assert output["actionable_events"] == 1
    assert output["groups"] == [
        {
            "error_code": "new_actionable",
            "stage": None,
            "runtime_version": "UNKNOWN",
            "runtime_version_source": "insufficient_event_evidence",
            "count": 1,
            "owner_source": None,
            "owner_symbol": None,
            "contract_doc": None,
            "focused_test": None,
            "next_check": None,
            "owner_confidence": "unknown",
        }
    ]
    assert "old_actionable" not in output_text
    assert "must-not-print" not in output_text


def test_diagnose_recent_missing_and_no_actionable_are_no_evidence(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "3", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    missing = json.loads(capsys.readouterr().out)
    pop_envelope(missing)
    assert missing == {
        "schema_version": "nmbot.diagnose.recent.v1",
        "kind": "recent",
        "status": "no_evidence",
        "date": "2026-07-27",
        "requested_limit": 3,
        "scanned_events": 0,
        "actionable_events": 0,
        "runtime_version_scope": "historical_event_evidence_not_current_process",
        "runtime_versions": [],
        "groups": [],
        "next_command": "bash scripts/nmbot_diag.sh --logs",
    }
    (logs / "bot_error_events-2026-07-27.jsonl").write_text('{"status":"failed","prompt":"must-not-print"}\n', encoding="utf-8")
    assert mod.main(["diagnose", "--recent", "3", "--date", "2026-07-27", "--logs-dir", str(logs)]) == 0
    no_actionable_text = capsys.readouterr().out
    no_actionable = json.loads(no_actionable_text)
    assert no_actionable["status"] == "no_evidence"
    assert no_actionable["scanned_events"] == 1
    assert no_actionable["runtime_version_scope"] == "historical_event_evidence_not_current_process"
    assert no_actionable["runtime_versions"] == []
    assert no_actionable["groups"] == []
    assert "must-not-print" not in no_actionable_text


def test_diagnose_recent_rejects_invalid_range_selectors_and_plan_before_read_or_dispatch(monkeypatch, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent"]) == 2
    assert mod.main(["diagnose", "--recent", "abc"]) == 2
    assert mod.main(["diagnose", "--recent", "0"]) == 2
    assert mod.main(["diagnose", "--recent", "101"]) == 2
    assert mod.main(["diagnose", "--recent", "1", "--trace", "trace-1"]) == 2
    assert mod.main(["diagnose", "--latest", "--recent", "1"]) == 2
    assert mod.main(["diagnose", "--recent", "1", "--plan"]) == 2
    assert mod.main(["diagnose", "--recent", "1", "--human", "--json"]) == 2


def test_diagnose_recent_human_one_safe_line_with_next_command(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"error_code": "main_search_timeout", "runtime_version": "v0", "prompt": "must-not-print"}),
                json.dumps({"error_code": "main_search_timeout", "runtime_version": "v2", "trace_id": "must-not-print"}),
                json.dumps({"error_type": "gateway_failed", "runtime_version": "V3", "model": "must-not-print"}),
                json.dumps({"error_type": "gateway_failed", "exception": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "4", "--date", "2026-07-27", "--logs-dir", str(logs), "--human"]) == 0
    output_text = capsys.readouterr().out
    assert output_text.count("\n") == 1
    assert "main_search_timeout [V0] (1; owner: unknown)" in output_text
    assert "main_search_timeout [V2] (1; owner: unknown)" in output_text
    assert "gateway_failed [V3] (1; owner: unknown)" in output_text
    assert "gateway_failed [UNKNOWN] (1; owner: unknown)" in output_text
    assert output_text.startswith("Последние ошибки: ")
    assert output_text.rstrip().endswith("Дальше: bash scripts/nmbot_diag.sh --logs")
    for raw in ("must-not-print", "trace_id", "model", "prompt", "exception"):
        assert raw not in output_text


def test_diagnose_recent_groups_runtime_versions_per_historical_event(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_plan_stage_map(tmp_path)
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"error_code": "old_outside_limit", "runtime_version": "V2"}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "runtime_version": "V0", "prompt": "must-not-print"}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "runtime_version": "v2", "trace_id": "must-not-print"}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "runtime_version": "V3", "task": {"id": "must-not-print"}}),
                json.dumps({"error_code": "main_search_timeout", "stage": "v2.search", "payload": "must-not-print"}),
                json.dumps({"error_code": "gateway_failed", "stage": "unknown.stage", "runtime_version": "V0", "model": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "5", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    main_groups = [group for group in output["groups"] if group["error_code"] == "main_search_timeout"]
    assert len(main_groups) == 4
    assert output["runtime_versions"] == [
        {"runtime_version": "V0", "count": 2},
        {"runtime_version": "V2", "count": 1},
        {"runtime_version": "V3", "count": 1},
        {"runtime_version": "UNKNOWN", "count": 1},
    ]
    assert output["runtime_version_scope"] == "historical_event_evidence_not_current_process"
    by_version = {group["runtime_version"]: group for group in main_groups}
    assert set(by_version) == {"V0", "V2", "V3", "UNKNOWN"}
    assert by_version["V2"]["runtime_version_source"] == "journal_event"
    assert by_version["UNKNOWN"]["runtime_version_source"] == "insufficient_event_evidence"
    assert by_version["V2"]["owner_symbol"] == "search"
    unknown_stage_group = next(group for group in output["groups"] if group["error_code"] == "gateway_failed")
    assert unknown_stage_group["runtime_version"] == "V0"
    assert unknown_stage_group["owner_confidence"] == "unknown"
    for raw in ("must-not-print", '"task"', "trace_id", "model", "prompt", "payload", "old_outside_limit"):
        assert raw not in output_text


def test_diagnose_recent_invalid_runtime_variants_normalize_unknown_without_raw_leak(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"error_code": "bad_runtime", "runtime_version": "V1", "raw": "must-not-print"}),
                json.dumps({"error_code": "bad_runtime", "runtime_version": 2, "exception": "must-not-print"}),
                json.dumps({"error_code": "bad_runtime", "runtime_version": ["V2"], "prompt": "must-not-print"}),
                json.dumps({"error_code": "bad_runtime", "runtime_version": {"value": "V2"}, "model": "must-not-print"}),
                json.dumps({"error_code": "bad_runtime", "runtime_version": "", "payload": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--recent", "5", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["runtime_versions"] == [{"runtime_version": "UNKNOWN", "count": 5}]
    assert output["groups"] == [
        {
            "error_code": "bad_runtime",
            "stage": None,
            "runtime_version": "UNKNOWN",
            "runtime_version_source": "insufficient_event_evidence",
            "count": 5,
            "owner_source": None,
            "owner_symbol": None,
            "contract_doc": None,
            "focused_test": None,
            "next_check": None,
            "owner_confidence": "unknown",
        }
    ]
    for raw in ("must-not-print", "runtime_version", "V1", "exception", "prompt", "model", "payload"):
        if raw == "runtime_version":
            continue
        assert raw not in output_text


def _write_plan_stage_map(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "nmbot_runtime_adapter.py").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "NMBOT_EXTERNAL_CONTRACTS.md").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_nmbot_v2_search_contract_runtime.py").write_text("", encoding="utf-8")
    (tmp_path / "config" / "nmbot_stage_map.json").write_text(
        json.dumps(
            {
                "stages": {
                    "v2.search": {
                        "source": "scripts/nmbot_runtime_adapter.py",
                        "source_symbol": "search",
                        "doc": "docs/NMBOT_EXTERNAL_CONTRACTS.md",
                        "test": "tests/test_nmbot_v2_search_contract_runtime.py",
                        "raw_child_field": "must-not-print",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def _write_trace_logs(tmp_path: Path) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "n8n_bridge_structured.jsonl").write_text("", encoding="utf-8")
    (logs / "dialogue_journal.jsonl").write_text("", encoding="utf-8")
    return logs


def test_diagnose_plan_json_ready_for_known_v2_search_trace(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    _write_trace_logs(tmp_path)
    _write_plan_stage_map(tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "v2.search", "outcome": "failed", "confidence": "high", "evidence": [{"http_status": 500}], "raw": "must-not-print"}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-v2", "--plan", "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["edit_plan"] == {
        "schema_version": "nmbot.diagnose.edit_plan.v1",
        "status": "ready",
        "diagnostic_stage": "v2.search",
        "problem_code": None,
        "read_first": [
            "scripts/nmbot_runtime_adapter.py",
            "docs/NMBOT_EXTERNAL_CONTRACTS.md",
            "tests/test_nmbot_v2_search_contract_runtime.py",
        ],
        "suggested_change_surface": ["scripts/nmbot_runtime_adapter.py", "tests/test_nmbot_v2_search_contract_runtime.py"],
        "verification_command": "python3 -m pytest -q tests/test_nmbot_v2_search_contract_runtime.py",
        "requires_impact_chain": True,
        "documentation_after_verify": True,
        "safety": "manual_edit_only_no_auto_fix",
        "next_action": "read impact chain before editing",
    }
    assert output["next_command"] == "bash scripts/nmbot_diag.sh --logs"
    assert "must-not-print" not in output_text


def test_diagnose_without_plan_lacks_edit_plan_for_backcompat(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    _write_trace_logs(tmp_path)
    _write_plan_stage_map(tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "v2.search", "outcome": "failed", "confidence": "high", "evidence": []}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-v2", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert "edit_plan" not in output


def test_diagnose_unknown_stage_plan_is_blocked_with_empty_surface(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    _write_trace_logs(tmp_path)
    _write_plan_stage_map(tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "unknown.stage", "outcome": "failed", "confidence": "low", "evidence": []}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-unknown", "--plan", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["edit_plan"]["status"] == "blocked"
    assert output["edit_plan"]["diagnostic_stage"] is None
    assert output["edit_plan"]["suggested_change_surface"] == []
    assert output["edit_plan"]["verification_command"] is None


def test_diagnose_plan_human_one_safe_line_has_plan_fields(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    _write_trace_logs(tmp_path)
    _write_plan_stage_map(tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {"traces": [{"stage": "v2.search", "outcome": "failed", "confidence": "high", "evidence": [{"http_status": 500}], "raw_child_field": "must-not-print"}]}

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="raw child must-not-print")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-v2", "--plan", "--human"]) == 0
    output_text = capsys.readouterr().out
    assert output_text.count("\n") == 1
    assert "plan_status: ready" in output_text
    assert "surface: scripts/nmbot_runtime_adapter.py,tests/test_nmbot_v2_search_contract_runtime.py" in output_text
    assert "verify: python3 -m pytest -q tests/test_nmbot_v2_search_contract_runtime.py" in output_text
    for secret in ("must-not-print", "raw_child_field", "raw child"):
        assert secret not in output_text


def test_diagnose_plan_with_implicit_latest_runs_only_existing_diagnoser(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(json.dumps({"task_id": "task-implicit"}), encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_utc_date", lambda: "2026-07-27")
    calls = []
    child_payload = {"task_id": "task-implicit", "status": "failed", "layer": "gateway", "error_code": "gateway_failed"}

    def fake_run(argv, cwd, check, capture_output, text):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--plan"]) == 0
    assert calls == [[sys.executable, "scripts/nmbot_gateway_task_diag.py", "task-implicit", "--json", "--date", "2026-07-27", "--logs-dir", "logs"]]
    output = json.loads(capsys.readouterr().out)
    assert output["edit_plan"]["status"] == "blocked"


def test_tools_registry_json_and_human_mark_legacy_without_dispatch(monkeypatch, capsys) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["tools", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "nmbot.diagnostic_tools.v1"
    by_name = {tool["name"]: tool for tool in output["tools"]}
    for legacy in ("nmbot_health.py", "nmbot_env_check.py", "nmbot_deploy_smoke.py", "find_dialog.py"):
        assert by_name[legacy]["status"] == "legacy"
        assert by_name[legacy]["canonical_wrapper"] is None
    assert mod.main(["tools", "--human"]) == 0
    human = capsys.readouterr().out
    assert "nmbot_health.py: legacy legacy" in human


def test_tools_registry_fail_closed_when_missing(monkeypatch, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    assert mod.main(["tools"]) == 2


def test_namespace_aliases_delegate_direct_argv_and_reject_unknown(monkeypatch) -> None:
    mod = load_wrapper_module()
    calls = []

    def fake_run(argv, cwd, check):
        calls.append({"argv": argv, "cwd": cwd, "check": check})
        return subprocess.CompletedProcess(argv, 23)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["trace", "analyze", "log.jsonl", "--strict"]) == 23
    assert mod.main(["trace", "dialogue", "bridge.jsonl", "--json"]) == 23
    assert mod.main(["dialogue", "report", "--session", "s1"]) == 23
    assert mod.main(["planner", "find", "--q", "abc"]) == 23
    assert mod.main(["runtime", "compare", "--json"]) == 23
    assert mod.main(["release", "identity", "read"]) == 23
    assert mod.main(["contour", "recon", "--contour", "primary"]) == 23
    assert mod.main(["architecture", "--strict"]) == 23
    assert calls == [
        {"argv": [sys.executable, "scripts/nmbot_jivo_trace_analyze.py", "log.jsonl", "--strict"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_jivo_dialogue_diagnose.py", "bridge.jsonl", "--json"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_dialogue_report.py", "--session", "s1"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/find_planner_trace.py", "--q", "abc"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_v2_version_compare.py", "--json"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_release_identity.py", "read"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_contour_recon.py", "--contour", "primary"], "cwd": mod.ROOT, "check": False},
        {"argv": [sys.executable, "scripts/nmbot_architecture_preflight.py", "--strict"], "cwd": mod.ROOT, "check": False},
    ]
    calls.clear()
    assert mod.main(["release", "status"]) == 2
    assert mod.main(["contour", "status"]) == 2
    assert mod.main(["release", "identity"]) == 2
    assert mod.main(["release", "identity", "create", "--release-id", "x", "--write"]) == 2
    assert mod.main(["release", "identity", "read", "--json"]) == 2
    assert mod.main(["trace", "find_dialog"]) == 2
    assert calls == []


def test_tools_registry_validator_rejects_malformed_rows(monkeypatch, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    root = tmp_path
    scripts = root / "scripts"
    config = root / "config"
    scripts.mkdir()
    config.mkdir()
    (scripts / "tool.py").write_text("# test tool\n", encoding="utf-8")

    def write_registry(tool: dict) -> None:
        (config / "nmbot_diagnostic_tools.json").write_text(
            json.dumps({"schema_version": "nmbot.diagnostic_tools.v1", "tools": [tool]}),
            encoding="utf-8",
        )

    base = {
        "name": "tool.py",
        "status": "current",
        "path": "scripts/tool.py",
        "purpose": "test",
        "canonical_wrapper": "python3 scripts/tool.py",
        "evidence_scope": "local",
        "network": False,
        "side_effects": False,
        "replacement": None,
        "notes": None,
    }
    monkeypatch.setattr(mod, "ROOT", root)
    write_registry(base)
    assert mod.main(["tools", "--json"]) == 0

    bad = dict(base, status="old")
    write_registry(bad)
    assert mod.main(["tools"]) == 2
    bad = dict(base, path="../scripts/tool.py")
    write_registry(bad)
    assert mod.main(["tools"]) == 2
    bad = dict(base, notes={"not": "scalar"})
    write_registry(bad)
    assert mod.main(["tools"]) == 2
    bad = dict(base, status="legacy", canonical_wrapper="python3 scripts/tool.py")
    write_registry(bad)
    assert mod.main(["tools"]) == 2


def test_diagnose_timeline_adds_bounded_steps_without_raw_ids(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    _write_trace_logs(tmp_path)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    child_payload = {
        "traces": [
            {"stage": "main_search", "status": "ok", "duration_ms": 11, "trace_id": "must-not-print"},
            {"stage": "delivery_missing", "outcome": "failed", "duration_ms": 22, "error_code": "Delivery Missing", "payload": "must-not-print"},
        ]
    }

    def fake_run(argv, cwd, check, capture_output, text):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(child_payload), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["diagnose", "--trace", "trace-secret", "--timeline", "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    timeline = output["timeline"]
    assert timeline["schema_version"] == "nmbot.diagnose.timeline.v1"
    assert timeline["first_failed_stage"] == "delivery_missing"
    assert timeline["last_successful_stage"] == "main_search"
    assert timeline["correlation_coverage"] == {"trace": True, "task": False}
    assert timeline["steps"] == [
        {"stage": "main_search", "status": "ok", "duration_ms": 11, "owner_layer": "runtime", "error_code": None},
        {"stage": "delivery_missing", "status": "failed", "duration_ms": 22, "owner_layer": "jivo_bridge", "error_code": "delivery_missing"},
    ]
    for raw in ("trace-secret", "trace_id", "payload", "must-not-print"):
        assert raw not in output_text


def test_diagnose_timeline_conflicts_rejected_before_dispatch(monkeypatch) -> None:
    mod = load_wrapper_module()

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--timeline", "--recent", "1"]) == 2
    assert mod.main(["diagnose", "--timeline", "--plan"]) == 2
    assert mod.main(["diagnose", "--timeline", "--trace", "t", "--task", "x"]) == 2


def test_diagnose_summary_1h_uses_max_timestamp_window_and_safe_fields(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "dialogue_journal.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-07-27T09:59:00Z", "role": "user", "text": "must-not-print", "runtime_version": "V0", "total_ms": 100}),
                json.dumps({"timestamp": "2026-07-27T10:30:00Z", "role": "user", "text": "must-not-print", "runtime_version": "V2", "total_ms": 200}),
                json.dumps({"timestamp": "2026-07-27T10:40:00Z", "role": "bot", "fallback": True, "answer": "must-not-print", "runtime_version": "V3", "total_ms": 300}),
                json.dumps({"timestamp": "2026-07-27T11:00:00Z", "role": "bot", "runtime_version": "bad", "total_ms": 400, "task_id": "must-not-print"}),
            ]
        ),
        encoding="utf-8",
    )
    (logs / "bot_error_events-2026-07-27.jsonl").write_text(
        json.dumps({"timestamp": "2026-07-27T10:50:00Z", "error_code": "gateway_failed", "prompt": "must-not-print", "runtime_version": "V2"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--summary", "1h", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["schema_version"] == "nmbot.diagnose.summary.v1"
    assert output["counts"] == {"user_turns": 1, "bot_turns": 2, "actionable_errors": 1}
    assert output["error_rate"] == 1.0
    assert output["fallback"] == {"count": 1, "rate": 0.5}
    assert output["latency_ms"] == {"count": 3, "p50": 300, "p95": 300, "p99": 300}
    assert output["runtime_versions"] == {"V0": 0, "V2": 2, "V3": 1, "UNKNOWN": 1}
    assert output["saturation"] == {"status": "unavailable", "reason": "not_present_in_local_journals"}
    for raw in ("must-not-print", "task_id", "prompt", "answer", "text"):
        assert raw not in output_text


def test_diagnose_summary_uses_bounded_latency_and_fallback_fields(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "dialogue_journal.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-07-27T10:10:00Z", "role": "bot", "answer_kind": "safe_upstream_fallback", "runtime_summary": {"timing": {"total_ms": 125}, "other": {"total_ms": 999}}, "total": {"ms": 777}}),
                json.dumps({"timestamp": "2026-07-27T10:20:00Z", "role": "bot", "error_summary": {"code": "UPSTREAM_FALLBACK_USED"}, "runtime_summary": {"timing": {"total_ms": 250}}}),
                json.dumps({"timestamp": "2026-07-27T10:30:00Z", "role": "bot", "error_code": "safe-fallback", "total_ms": 500, "raw_output": "must-not-print fallback"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--summary", "1h", "--date", "2026-07-27", "--logs-dir", str(logs), "--json"]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["fallback"] == {"count": 3, "rate": 1.0}
    assert output["latency_ms"] == {"count": 3, "p50": 250, "p95": 250, "p99": 250}
    assert "must-not-print" not in output_text


def test_diagnose_summary_conflicts_and_no_timestamps_are_bounded(monkeypatch, capsys, tmp_path: Path) -> None:
    mod = load_wrapper_module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "dialogue_journal.jsonl").write_text('{"role":"user","text":"must-not-print"}\nnot json\n', encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def fail_run(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    assert mod.main(["diagnose", "--summary", "2h"]) == 2
    assert mod.main(["diagnose", "--summary", "1h", "--latest"]) == 2
    assert mod.main(["diagnose", "--summary", "1h", "--logs-dir", str(logs)]) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)
    assert output["status"] == "no_evidence"
    assert output["saturation"]["reason"] == "not_present_in_local_journals"
    assert "must-not-print" not in output_text
