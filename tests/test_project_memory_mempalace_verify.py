from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "project_memory_mempalace_verify.py"


def load_verify_module():
    spec = importlib.util.spec_from_file_location("project_memory_mempalace_verify_test", VERIFY_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def make_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample (value) VALUES ('ok')")


def ok_runner(args: Sequence[str], cwd: Path | None, timeout: int):
    mod = load_verify_module()
    assert args[0] == "mempalace"
    assert cwd is not None and cwd.exists()
    assert timeout == 30
    return mod.RunnerResult(returncode=0, stdout="ok", stderr="")


def test_verify_success_with_mocked_cli_and_temp_sqlite(tmp_path: Path) -> None:
    mod = load_verify_module()
    make_sqlite(tmp_path / "chroma.sqlite3")
    make_sqlite(tmp_path / "knowledge_graph.sqlite3")

    payload = mod.verify_mempalace_post_repair(tmp_path, runner=ok_runner)

    assert payload["schema"] == "project_memory_mempalace_verify.v1"
    assert payload["ok"] is True
    assert payload["selector_enabled"] is False
    assert payload["project_fact_source_enabled"] is False
    assert payload["allowed_after_success"] == ["agent_diary", "meta_memory"]
    assert payload["behavior_activation_permitted"] is False
    assert payload["write_performed"] is False
    assert [step["name"] for step in payload["checks"]["cli"]] == ["status", "repair-status", "semantic_search", "wake_up"]


def test_verify_fails_on_sqlite_integrity_error(tmp_path: Path) -> None:
    mod = load_verify_module()
    (tmp_path / "chroma.sqlite3").write_text("not sqlite", encoding="utf-8")

    payload = mod.verify_mempalace_post_repair(tmp_path, runner=ok_runner)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "post_repair_verification_failed"
    assert payload["checks"]["sqlite"][0]["status"] == "error"


def test_verify_fails_when_primary_db_missing(tmp_path: Path) -> None:
    mod = load_verify_module()
    make_sqlite(tmp_path / "knowledge_graph.sqlite3")

    payload = mod.verify_mempalace_post_repair(tmp_path, runner=ok_runner)

    primary = payload["checks"]["sqlite"][0]
    assert payload["ok"] is False
    assert primary["name"] == "chroma.sqlite3"
    assert primary["present"] is False
    assert primary["required"] is True


def test_verify_fails_on_cli_step_failure(tmp_path: Path) -> None:
    mod = load_verify_module()
    make_sqlite(tmp_path / "chroma.sqlite3")

    def failing_runner(args: Sequence[str], cwd: Path | None, timeout: int):
        if "repair-status" in args:
            return mod.RunnerResult(returncode=1, stdout="", stderr="repair incomplete")
        return mod.RunnerResult(returncode=0, stdout="ok", stderr="")

    payload = mod.verify_mempalace_post_repair(tmp_path, runner=failing_runner)

    assert payload["ok"] is False
    failed = [step for step in payload["checks"]["cli"] if not step["pass"]]
    assert len(failed) == 1
    assert failed[0]["name"] == "repair-status"
    assert "repair incomplete" in failed[0]["stderr_preview"]
