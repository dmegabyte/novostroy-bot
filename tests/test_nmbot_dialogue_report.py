from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_dialogue_report.py"
BACKFILL_SCRIPT = SCRIPT_DIR / "backfill_dialogue_runtime_versions.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_dialogue_report_test", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

backfill_spec = importlib.util.spec_from_file_location("backfill_dialogue_runtime_versions_test", BACKFILL_SCRIPT)
backfill_mod = importlib.util.module_from_spec(backfill_spec)
assert backfill_spec and backfill_spec.loader
backfill_spec.loader.exec_module(backfill_mod)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_report_preserves_runtime_and_release_id_defaults_missing_to_unknown(tmp_path: Path, capsys) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rows = [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет", "runtime_version": "V2", "release_id": "rel-1"},
        {"ts": "2026-07-21T10:00:01Z", "role": "bot", "session_key_ref": "sha256:abc", "text": "Здравствуйте"},
    ]
    write_jsonl(log_dir / "dialogue_journal.jsonl", rows)

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=True)
    report = mod.build_report(log_dir, args)

    timeline = report["reports"][0]["timeline"]
    assert timeline[0]["runtime_version"] == "V2"
    assert timeline[0]["runtime_version_source"] == "existing"
    assert timeline[1]["runtime_version"] == "UNKNOWN"
    assert timeline[1]["runtime_version_source"] == "insufficient_history"
    assert timeline[0]["release_id"] == "rel-1"
    assert timeline[1]["release_id"] == "UNKNOWN"

    mod.print_human(report)
    out = capsys.readouterr().out
    assert "runtime_version=V2" in out
    assert "runtime_version=UNKNOWN" in out
    assert "release_id=rel-1" in out
    assert "release_id=UNKNOWN" in out


def test_report_preserves_sanitized_execution_path(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rows = [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет"},
        {
            "ts": "2026-07-21T10:00:01Z",
            "role": "bot",
            "session_key_ref": "sha256:abc",
            "text": "Ответ",
            "execution_path": {
                "schema": "nmbot.execution_path.v1",
                "path_id": "jivo.v2.turn.v1",
                "stages": [
                    {"stage_id": "v2.runtime_finalize", "status": "completed", "payload": "secret"},
                    {"stage_id": "jivo.api.prepare", "status": "completed"},
                ],
            },
        },
    ]
    write_jsonl(log_dir / "dialogue_journal.jsonl", rows)

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=False)
    report = mod.build_report(log_dir, args)

    execution_path = report["reports"][0]["timeline"][1]["execution_path"]
    assert execution_path["path_id"] == "jivo.v2.turn.v1"
    assert execution_path["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}
    assert "secret" not in json.dumps(report, ensure_ascii=False)


def test_report_preserves_sanitized_v1_execution_path(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rows = [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет", "runtime_version": "V1"},
        {
            "ts": "2026-07-21T10:00:01Z",
            "role": "bot",
            "session_key_ref": "sha256:abc",
            "text": "Ответ",
            "runtime_version": "V1",
            "execution_path": {
                "schema": "nmbot.execution_path.v1",
                "path_id": "jivo.v1.turn.v1",
                "stages": [
                    {"stage_id": "v1.planner", "status": "completed", "payload": "secret"},
                    {"stage_id": "v1.transition", "status": "completed"},
                    {"stage_id": "v1.search", "status": "completed"},
                    {"stage_id": "v1.response_plan", "status": "completed"},
                    {"stage_id": "v1.deterministic_render", "status": "completed"},
                    {"stage_id": "v1.presenter", "status": "skipped"},
                    {"stage_id": "v1.runtime_finalize", "status": "completed"},
                    {"stage_id": "jivo.api.prepare", "status": "completed"},
                ],
            },
        },
    ]
    write_jsonl(log_dir / "dialogue_journal.jsonl", rows)

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=False)
    report = mod.build_report(log_dir, args)

    timeline = report["reports"][0]["timeline"]
    assert timeline[0]["runtime_version"] == "V1"
    execution_path = timeline[1]["execution_path"]
    assert execution_path["path_id"] == "jivo.v1.turn.v1"
    assert execution_path["stages"][-1] == {"stage_id": "jivo.api.prepare", "status": "completed"}
    assert "secret" not in json.dumps(report, ensure_ascii=False)


def test_backfill_explicit_start_event_and_forward_override(tmp_path: Path) -> None:
    rows = [
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "role": "user", "text": "/start_0"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "role": "bot", "text": "hello"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e2", "role": "user", "text": "дальше"},
    ]
    journal_rows = [{"line_no": i, "row": row, "invalid": False} for i, row in enumerate(rows, 1)]

    sidecar, counts = backfill_mod.build_sidecar_rows(journal_rows)

    assert [(r["runtime_version"], r["source"]) for r in sidecar] == [("V0", "explicit_start"), ("V0", "explicit_start"), ("V0", "session_override")]
    assert counts["explicit_start"] == 2
    assert counts["session_override"] == 1


def test_backfill_lifecycle_event_without_explicit_start(tmp_path: Path) -> None:
    rows = [
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "role": "user", "text": "/start"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "role": "bot", "text": "Сейчас активна версия: V3."},
    ]
    journal_rows = [{"line_no": i, "row": row, "invalid": False} for i, row in enumerate(rows, 1)]

    sidecar, _ = backfill_mod.build_sidecar_rows(journal_rows)

    assert [(r["runtime_version"], r["source"]) for r in sidecar] == [("V3", "lifecycle_text"), ("V3", "lifecycle_text")]


def test_backfill_plain_start_clears_forward_propagation_but_keeps_lifecycle_for_event(tmp_path: Path) -> None:
    rows = [
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "text": "/start_2"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e2", "text": "/start"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e2", "text": "Сейчас активна версия: V0."},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e3", "text": "ordinary"},
    ]
    journal_rows = [{"line_no": i, "row": row, "invalid": False} for i, row in enumerate(rows, 1)]

    sidecar, _ = backfill_mod.build_sidecar_rows(journal_rows)

    assert [(r["runtime_version"], r["source"]) for r in sidecar] == [
        ("V2", "explicit_start"),
        ("V0", "lifecycle_text"),
        ("V0", "lifecycle_text"),
        ("UNKNOWN", "insufficient_history"),
    ]


def test_backfill_existing_ordinary_version_does_not_propagate(tmp_path: Path) -> None:
    rows = [
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e1", "text": "ordinary", "runtime_version": "V3"},
        {"session_key_ref": "sha256:s", "event_id_ref": "sha256:e2", "text": "later"},
    ]
    journal_rows = [{"line_no": i, "row": row, "invalid": False} for i, row in enumerate(rows, 1)]

    sidecar, _ = backfill_mod.build_sidecar_rows(journal_rows)

    assert [(r["runtime_version"], r["source"]) for r in sidecar] == [("V3", "existing"), ("UNKNOWN", "insufficient_history")]


def test_backfill_eventless_start_does_not_establish_override() -> None:
    rows = [
        {"session_key_ref": "sha256:s", "text": "/start_0"},
        {"session_key_ref": "sha256:s", "text": "ordinary bot response"},
        {"session_key_ref": "sha256:s", "text": "Сейчас активна версия: V0."},
    ]
    journal_rows = [{"line_no": i, "row": row, "invalid": False} for i, row in enumerate(rows, 1)]

    sidecar, _ = backfill_mod.build_sidecar_rows(journal_rows)

    assert [(r["runtime_version"], r["source"]) for r in sidecar] == [
        ("UNKNOWN", "insufficient_history"),
        ("UNKNOWN", "insufficient_history"),
        ("V0", "lifecycle_text"),
    ]


def test_backfill_ambiguous_unknown_and_malformed_line(tmp_path: Path) -> None:
    journal = tmp_path / "dialogue_journal.jsonl"
    journal.write_text('{"session_key_ref":"sha256:s","text":"ordinary"}\nnot json\n', encoding="utf-8")

    rows, total, invalid = backfill_mod.read_journal(journal)
    sidecar, counts = backfill_mod.build_sidecar_rows(rows)

    assert total == 2
    assert invalid == 1
    assert sidecar == [
        {"line_no": 1, "runtime_version": "UNKNOWN", "source": "insufficient_history", "session_key_ref": "sha256:s"},
        {"line_no": 2, "runtime_version": "UNKNOWN", "source": "insufficient_history"},
    ]
    assert counts["UNKNOWN"] == 2


def test_backfill_atomic_sidecar_write_mode_0600(tmp_path: Path) -> None:
    output = tmp_path / "sidecar.jsonl"
    rows = [{"line_no": 1, "runtime_version": "V2", "source": "existing"}]

    backfill_mod.write_jsonl_atomic(output, rows)

    assert json.loads(output.read_text(encoding="utf-8")) == rows[0]
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))


def test_report_direct_field_precedence_over_sidecar(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "dialogue_journal.jsonl", [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет", "runtime_version": "V3"},
    ])
    write_jsonl(log_dir / "dialogue_runtime_versions_backfill.jsonl", [
        {"line_no": 1, "runtime_version": "V0", "source": "explicit_start"},
    ])

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=True, runtime_backfill=None)
    report = mod.build_report(log_dir, args)

    turn = report["reports"][0]["timeline"][0]
    assert turn["runtime_version"] == "V3"
    assert turn["runtime_version_source"] == "existing"


def test_report_sidecar_merge_by_line_no(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "dialogue_journal.jsonl", [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет"},
    ])
    write_jsonl(log_dir / "dialogue_runtime_versions_backfill.jsonl", [
        {"line_no": 1, "runtime_version": "V2", "source": "session_override", "event_id_ref": "sha256:e"},
    ])

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=True, runtime_backfill=None)
    report = mod.build_report(log_dir, args)

    turn = report["reports"][0]["timeline"][0]
    assert turn["runtime_version"] == "V2"
    assert turn["runtime_version_source"] == "session_override"


def test_report_without_sidecar_shows_unknown(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "dialogue_journal.jsonl", [
        {"ts": "2026-07-21T10:00:00Z", "role": "user", "session_key_ref": "sha256:abc", "text": "Привет"},
    ])

    args = argparse.Namespace(date=None, q=["Привет"], any=False, session=None, limit=1, show_text=True, runtime_backfill=None)
    report = mod.build_report(log_dir, args)

    turn = report["reports"][0]["timeline"][0]
    assert turn["runtime_version"] == "UNKNOWN"
    assert turn["runtime_version_source"] == "insufficient_history"


def test_report_keeps_only_safe_terminal_error_summary(tmp_path: Path, capsys) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "dialogue_journal.jsonl", [
        {
            "ts": "2026-07-23T10:00:00Z", "role": "bot", "session_key_ref": "sha256:abc",
            "text": "Ответ", "error_summary": {
                "status": "degraded",
                "codes": ["composer_validation_failed", "invalid_json", "raw_secret_7999"],
                "stages": ["composer", "raw_payload"],
                "fallback": True,
                "raw_exception": "secret",
            },
        },
    ])
    args = argparse.Namespace(date=None, q=["Ответ"], any=False, session=None, limit=1, show_text=True, runtime_backfill=None)
    report = mod.build_report(log_dir, args)
    turn = report["reports"][0]["timeline"][0]

    assert turn["error_summary"] == {
        "status": "degraded",
        "codes": ["composer_validation_failed", "invalid_json"],
        "stages": ["composer"],
        "fallback": True,
    }
    mod.print_human(report)
    out = capsys.readouterr().out
    assert "Errors:" in out
    assert "raw_secret" not in out
    assert "secret" not in out
