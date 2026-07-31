from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("publish_live_run_rows_to_sheet_test", SCRIPT_DIR / "publish_live_run_rows_to_sheet.py")
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

google_spec = importlib.util.spec_from_file_location("nmbot_google_sheets_publish_test", SCRIPT_DIR / "nmbot_google_sheets.py")
google_mod = importlib.util.module_from_spec(google_spec)
assert google_spec and google_spec.loader
sys.modules[google_spec.name] = google_mod
google_spec.loader.exec_module(google_mod)


def _rows_file(tmp_path: Path) -> Path:
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps({"version": "v2", "command": "python3 scripts/chat_cli.py тест", "case": "c1"}, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_publisher_dry_run_does_not_build_google_service(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setattr(mod, "build_sheets_service", lambda: (_ for _ in ()).throw(AssertionError("no service in dry-run")))
    monkeypatch.setattr(mod, "resolve_sheet_title", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no title in dry-run")))
    monkeypatch.setattr(mod, "append_rows", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no append in dry-run")))

    assert mod.main([str(_rows_file(tmp_path)), "--gid", "123"]) == 0

    out = capsys.readouterr().out
    assert "mode=dry-run" in out
    assert "target=gid:123" in out


class _Exec:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def execute(self) -> dict[str, Any]:
        return self.value


class _Values:
    def __init__(self) -> None:
        self.appends: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> _Exec:
        self.appends.append(kwargs)
        return _Exec({"updates": {"updatedRange": "Лист!A2:H2", "updatedRows": 1}})


class _Sheets:
    def __init__(self) -> None:
        self._values = _Values()

    def values(self) -> _Values:
        return self._values

    def append(self, **kwargs: Any) -> _Exec:  # pragma: no cover - guard
        raise AssertionError("append must go through values()")


class _Service:
    def __init__(self) -> None:
        self.sheets = _Sheets()

    def spreadsheets(self) -> _Sheets:
        return self.sheets


def test_generic_append_rows_mapping_with_fake_service() -> None:
    service = _Service()

    result = google_mod.append_rows(service, spreadsheet_id="sheet", range_name="'Лист'!A:H", values=[["a", "b"]])

    assert result["updates"]["updatedRows"] == 1
    assert service.sheets._values.appends == [
        {
            "spreadsheetId": "sheet",
            "range": "'Лист'!A:H",
            "valueInputOption": "USER_ENTERED",
            "insertDataOption": "INSERT_ROWS",
            "body": {"values": [["a", "b"]]},
        }
    ]
