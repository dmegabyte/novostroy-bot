from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT = SCRIPT_DIR / "nmbot_dialogue_sheet_exporter.py"
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location("nmbot_dialogue_sheet_exporter_test", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["nmbot_dialogue_sheet_exporter_test"] = mod
spec.loader.exec_module(mod)


def test_aggregate_dialogues_uses_only_recorded_stage_model_facts() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-29T10:00:00Z",
            "role": "user",
            "event_type": "turn",
            "conversation_ref": "sha256:new-dialogue",
            "client_id_ref": "sha256:must-not-export",
            "text": "Нужна квартира",
        },
        {
            "ts": "2026-07-29T10:00:02Z",
            "role": "bot",
            "event_type": "turn",
            "conversation_ref": "sha256:new-dialogue",
            "text": "Сейчас подберу варианты",
            "runtime_summary": {
                "model_usage": {"answer": ["openai_gpt-4.1"], "search": ["google_gemini"]},
                "gateway_attempt_details": [
                    {"stage": "gateway_attempt", "model_role": "answer", "model": "openai_gpt-4.1"},
                    {"stage": "gateway_attempt", "model_role": "search", "model": "google_gemini"},
                ],
            },
        },
        {
            "ts": "2026-07-29T09:00:00Z",
            "role": "bot",
            "event_type": "turn",
            "conversation_ref": "sha256:old-dialogue",
            "text": "Старый ответ",
        },
    ])

    by_id = {row.dialogue_id: row for row in rows}
    assert by_id["sha256:new-dialogue"].answer_model == "openai_gpt-4.1"
    assert by_id["sha256:new-dialogue"].search_model == "google_gemini"
    assert by_id["sha256:old-dialogue"].answer_model == mod.NO_DATA
    assert by_id["sha256:old-dialogue"].search_model == mod.NO_DATA
    dumped = "\n".join("\t".join(row.values()) for row in rows)
    assert "client_id" not in dumped
    assert "must-not-export" not in dumped


def test_sheet_date_fields_are_human_minutes_and_dialogue_has_no_timestamps() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-29T12:07:30Z",
            "role": "user",
            "event_type": "turn",
            "conversation_ref": "sha256:date-format",
            "text": "Первое сообщение",
        },
        {
            "ts": "2026-07-29T12:08:45+00:00",
            "role": "bot",
            "event_type": "turn",
            "conversation_ref": "sha256:date-format",
            "text": "Ответ без времени в колонке диалога",
        },
    ])

    row = rows[0]
    assert row.created_at == "29.07.2026 12:07"
    assert row.updated_at == "29.07.2026 12:08"
    assert "Пользователь: Первое сообщение" in row.dialogue
    assert "Бот: Ответ без времени в колонке диалога" in row.dialogue
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}|\[", row.dialogue)


def test_callback_queued_marks_dialogue_as_transferred_to_operator() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-30T20:00:00Z",
            "role": "user",
            "event_type": "turn",
            "conversation_ref": "sha256:callback-final",
            "text": "Позови оператора",
        },
        {
            "ts": "2026-07-30T20:00:01Z",
            "role": "bot",
            "event_type": "turn",
            "answer_kind": "callback_queued",
            "conversation_ref": "sha256:callback-final",
            "text": "Заявка передана оператору.",
        },
    ])

    assert rows[0].status == "передан оператору"
    assert "Бот: Заявка передана оператору." in rows[0].dialogue


def test_aggregate_dialogues_can_use_gateway_attempt_roles_without_guessing() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-29T10:00:00Z",
            "role": "bot",
            "conversation_ref": "sha256:roles-dialogue",
            "text": "Ответ",
            "runtime_summary": {
                "gateway_attempt_details": [
                    {"stage": "gateway_attempt", "model_role": "answer", "model": "answer_model"},
                    {"stage": "gateway_attempt", "model_role": "search", "model": "search_model"},
                    {"stage": "gateway_attempt", "model": "unknown_role_model"},
                ],
            },
        }
    ])

    assert rows[0].answer_model == "answer_model"
    assert rows[0].search_model == "search_model"
    assert "unknown_role_model" not in rows[0].values()


def test_runtime_version_column_uses_only_observed_event_versions() -> None:
    rows = mod.aggregate_dialogues([
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "conversation_ref": "sha256:runtime-one", "text": "one", "runtime_version": "v0"},
        {"ts": "2026-07-29T10:00:01Z", "role": "bot", "conversation_ref": "sha256:runtime-one", "text": "two", "runtime_version": "V0"},
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "conversation_ref": "sha256:runtime-mixed", "text": "one", "runtime_version": "V2"},
        {"ts": "2026-07-29T10:00:01Z", "role": "bot", "conversation_ref": "sha256:runtime-mixed", "text": "two", "runtime_version": "V1"},
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "conversation_ref": "sha256:runtime-missing", "text": "one"},
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "conversation_ref": "sha256:runtime-invalid", "text": "one", "runtime_version": "V9"},
        {"ts": "2026-07-29T10:00:01Z", "role": "bot", "conversation_ref": "sha256:runtime-summary", "text": "two", "runtime_summary": {"runtime_version": "V3"}},
    ])

    by_id = {row.dialogue_id: row for row in rows}
    assert by_id["sha256:runtime-one"].runtime_version == "V0"
    assert by_id["sha256:runtime-mixed"].runtime_version == "V1/V2"
    assert by_id["sha256:runtime-missing"].runtime_version == mod.NO_DATA
    assert by_id["sha256:runtime-invalid"].runtime_version == mod.NO_DATA
    assert by_id["sha256:runtime-summary"].runtime_version == "V3"
    assert by_id["sha256:runtime-one"].values()[8] == "V0"


def test_memory_state_summary_uses_newlines_and_filters_prohibited_values() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-29T10:00:00Z",
            "role": "bot",
            "conversation_ref": "sha256:memory-state",
            "text": "Ответ",
            "runtime_summary": {
                "state_before": {
                    "param_keys": ["rooms", "client_phone", "prompt_text"],
                    "visible_options_count": 0,
                    "selected_present": False,
                    "client_phone": "+79991234567",
                },
                "state_after": {
                    "param_keys": ["rooms", "budget", "email"],
                    "visible_options_count": 2,
                    "selected_present": True,
                    "selected_value": "secret apartment name",
                },
            },
        }
    ])

    summary = rows[0].memory_state_summary
    assert summary == "\n".join([
        "before_keys=rooms",
        "before_visible_options_count=0",
        "before_selected_present=false",
        "after_keys=budget,rooms",
        "after_visible_options_count=2",
        "after_selected_present=true",
    ])
    assert "; " not in summary
    dumped = "\n".join(rows[0].values())
    for prohibited in ("client_phone", "prompt_text", "+79991234567", "email", "secret apartment name"):
        assert prohibited not in dumped


def test_start_reset_boundaries_split_dialogue_segments_and_assign_all_events_once() -> None:
    events = [
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "event_type": "turn", "conversation_ref": "sha256:reset-dialogue", "text": "old user"},
        {"ts": "2026-07-29T10:00:01Z", "role": "bot", "event_type": "turn", "conversation_ref": "sha256:reset-dialogue", "text": "old bot"},
        {"ts": "2026-07-29T10:01:00Z", "role": "bot", "event_type": "lifecycle", "answer_kind": "start_reset", "conversation_ref": "sha256:reset-dialogue", "text": "new start"},
        {"ts": "2026-07-29T10:01:02Z", "role": "user", "event_type": "turn", "conversation_ref": "sha256:reset-dialogue", "text": "new user"},
        {"ts": "2026-07-29T10:02:00Z", "role": "bot", "event_type": "lifecycle", "answer_kind": "start_reset", "conversation_ref": "sha256:reset-dialogue", "text": "third start"},
    ]

    rows = mod.aggregate_dialogues(events)

    assert [row.dialogue_id for row in rows] == ["sha256:reset-dialogue", "sha256:reset-dialogue#s001", "sha256:reset-dialogue#s002"]
    assert "old user" in rows[0].dialogue and "old bot" in rows[0].dialogue
    assert "new start" in rows[1].dialogue and "new user" in rows[1].dialogue
    assert "third start" in rows[2].dialogue
    rendered = "\n".join(row.dialogue for row in rows)
    for text in ("old user", "old bot", "new start", "new user", "third start"):
        assert rendered.count(text) == 1


def test_start_reset_first_event_stays_compatible_with_existing_dialogue_id() -> None:
    rows = mod.aggregate_dialogues([
        {"ts": "2026-07-29T10:00:00Z", "role": "bot", "event_type": "lifecycle", "answer_kind": "start_reset", "conversation_ref": "sha256:first-reset", "text": "new start"},
        {"ts": "2026-07-29T10:00:01Z", "role": "user", "event_type": "turn", "conversation_ref": "sha256:first-reset", "text": "hello"},
    ])

    assert [row.dialogue_id for row in rows] == ["sha256:first-reset"]
    assert "new start" in rows[0].dialogue


def test_no_reset_keeps_legacy_single_row_id() -> None:
    rows = mod.aggregate_dialogues([
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "event_type": "turn", "conversation_ref": "sha256:no-reset", "text": "one"},
        {"ts": "2026-07-29T10:00:01Z", "role": "bot", "event_type": "turn", "conversation_ref": "sha256:no-reset", "text": "two"},
    ])

    assert [row.dialogue_id for row in rows] == ["sha256:no-reset"]


def test_segment_ids_are_stable_when_journal_is_appended() -> None:
    base = [
        {"ts": "2026-07-29T10:00:00Z", "role": "user", "conversation_ref": "sha256:append-stable", "text": "one"},
        {"ts": "2026-07-29T10:01:00Z", "role": "bot", "event_type": "lifecycle", "answer_kind": "start_reset", "conversation_ref": "sha256:append-stable", "text": "two"},
    ]
    appended = [*base, {"ts": "2026-07-29T10:02:00Z", "role": "bot", "event_type": "turn", "conversation_ref": "sha256:append-stable", "text": "three"}]

    assert [row.dialogue_id for row in mod.aggregate_dialogues(base)] == ["sha256:append-stable", "sha256:append-stable#s001"]
    assert [row.dialogue_id for row in mod.aggregate_dialogues(appended)] == ["sha256:append-stable", "sha256:append-stable#s001"]


def test_historical_many_start_resets_fixture_splits_without_arbitrary_truncation() -> None:
    events: list[dict[str, Any]] = []
    for idx in range(78):
        events.extend([
            {"ts": f"2026-07-29T10:{idx:02d}:00Z", "role": "bot", "event_type": "lifecycle", "answer_kind": "start_reset", "conversation_ref": "sha256:many-resets", "text": f"start {idx}"},
            {"ts": f"2026-07-29T10:{idx:02d}:01Z", "role": "user", "event_type": "turn", "conversation_ref": "sha256:many-resets", "text": f"turn {idx}"},
        ])

    rows = mod.aggregate_dialogues(events)

    assert len(rows) == 78
    assert rows[0].dialogue_id == "sha256:many-resets"
    assert rows[-1].dialogue_id == "sha256:many-resets#s077"
    assert all(f"turn {idx}" in rows[idx].dialogue for idx in range(78))


class _Call:
    def __init__(self, result: dict[str, Any] | None = None, hook=None) -> None:
        self.result = result or {}
        self.hook = hook

    def execute(self) -> dict[str, Any]:
        if self.hook:
            self.hook()
        return self.result


class _Values:
    def __init__(self, service: "_SheetsService") -> None:
        self.service = service

    def get(self, *, spreadsheetId: str, range: str) -> _Call:  # noqa: A002 - Google API name
        if range.endswith("A1:H1") or range.endswith("A1:I1") or range.endswith("A1:K1") or range.endswith("A1:N1"):
            return _Call({"values": [self.service.headers] if self.service.headers else []})
        if range.endswith("A2:A"):
            return _Call({"values": [[row[0]] for row in self.service.rows]})
        return _Call({"values": []})

    def update(self, *, spreadsheetId: str, range: str, valueInputOption: str, body: dict[str, Any]) -> _Call:  # noqa: A002
        self.service.value_input_options.append((range, valueInputOption))

        def hook() -> None:
            values = body["values"]
            if range.endswith("A1:H1") or range.endswith("A1:I1") or range.endswith("A1:K1") or range.endswith("A1:N1"):
                self.service.headers = list(values[0])
                return
            if range.endswith("I1:I1"):
                while len(self.service.headers) < len(mod.LEGACY_HEADERS):
                    self.service.headers.append("")
                if len(self.service.headers) == len(mod.LEGACY_HEADERS):
                    self.service.headers.append(str(values[0][0]))
                else:
                    self.service.headers[len(mod.LEGACY_HEADERS)] = str(values[0][0])
                return
            if range.endswith("I1:K1"):
                while len(self.service.headers) < len(mod.LEGACY_HEADERS):
                    self.service.headers.append("")
                row_values = [str(item) for item in values[0]]
                self.service.headers[len(mod.LEGACY_HEADERS) : len(mod.LEGACY_HEADERS) + len(row_values)] = row_values
                return
            if range.endswith("J1:K1"):
                while len(self.service.headers) < len(mod.OLD_RUNTIME_HEADERS):
                    self.service.headers.append("")
                row_values = [str(item) for item in values[0]]
                self.service.headers[len(mod.OLD_RUNTIME_HEADERS) : len(mod.OLD_RUNTIME_HEADERS) + len(row_values)] = row_values
                return
            if range.endswith("I1:N1"):
                while len(self.service.headers) < len(mod.LEGACY_HEADERS):
                    self.service.headers.append("")
                row_values = [str(item) for item in values[0]]
                self.service.headers[len(mod.LEGACY_HEADERS) : len(mod.LEGACY_HEADERS) + len(row_values)] = row_values
                return
            if range.endswith("J1:N1"):
                while len(self.service.headers) < len(mod.OLD_RUNTIME_HEADERS):
                    self.service.headers.append("")
                row_values = [str(item) for item in values[0]]
                self.service.headers[len(mod.OLD_RUNTIME_HEADERS) : len(mod.OLD_RUNTIME_HEADERS) + len(row_values)] = row_values
                return
            if range.endswith("L1:N1"):
                while len(self.service.headers) < len(mod.OLD_DIAGNOSTIC_BASE_HEADERS):
                    self.service.headers.append("")
                row_values = [str(item) for item in values[0]]
                self.service.headers[len(mod.OLD_DIAGNOSTIC_BASE_HEADERS) : len(mod.OLD_DIAGNOSTIC_BASE_HEADERS) + len(row_values)] = row_values
                return
            row_num = int(range.split("!A", 1)[1].split(":", 1)[0])
            self.service.rows[row_num - 2] = list(values[0])

        return _Call({}, hook)

    def batchUpdate(self, *, spreadsheetId: str, body: dict[str, Any]) -> _Call:  # noqa: N802
        self.service.value_input_options.append(("batchUpdate", str(body.get("valueInputOption") or "")))

        def hook() -> None:
            for item in body["data"]:
                row_num = int(item["range"].split("!A", 1)[1].split(":", 1)[0])
                self.service.rows[row_num - 2] = list(item["values"][0])

        return _Call({}, hook)

    def append(self, *, spreadsheetId: str, range: str, valueInputOption: str, insertDataOption: str, body: dict[str, Any]) -> _Call:  # noqa: A002
        self.service.value_input_options.append((range, valueInputOption))

        def hook() -> None:
            self.service.rows.extend([list(row) for row in body["values"]])

        return _Call({"updates": {"updatedRange": range}}, hook)


class _Spreadsheets:
    def __init__(self, service: "_SheetsService") -> None:
        self.service = service

    def get(self, *, spreadsheetId: str, fields: str) -> _Call:
        sheets = [{"properties": {"title": title, "sheetId": idx + 100}} for idx, title in enumerate(self.service.tabs)]
        return _Call({"sheets": sheets})

    def batchUpdate(self, *, spreadsheetId: str, body: dict[str, Any]) -> _Call:  # noqa: N802
        self.service.batch_update_requests.append(body)

        def hook() -> None:
            for request in body["requests"]:
                if "addSheet" in request:
                    title = request["addSheet"]["properties"]["title"]
                    if title not in self.service.tabs:
                        self.service.tabs.append(title)

        return _Call({}, hook)

    def values(self) -> _Values:
        return _Values(self.service)


class _SheetsService:
    def __init__(self) -> None:
        self.tabs: list[str] = []
        self.headers: list[str] = []
        self.rows: list[list[str]] = [["sha256:existing", "old"]]
        self.value_input_options: list[tuple[str, str]] = []
        self.batch_update_requests: list[dict[str, Any]] = []

    def spreadsheets(self) -> _Spreadsheets:
        return _Spreadsheets(self)


def test_upsert_rows_creates_tab_headers_updates_existing_and_appends_new() -> None:
    service = _SheetsService()
    rows = [
        mod.DialogueRow("sha256:existing", "t1", "активен", "dialogue 1", "нет данных", "нет данных", "t2", ""),
        mod.DialogueRow("sha256:new", "t3", "активен", "dialogue 2", "answer", "search", "t4", ""),
    ]

    result = mod.upsert_rows(service, spreadsheet_id="sheet", tab_name="Диалоги", rows=rows)

    assert result == {"updated": 1, "appended": 1, "total": 2}
    assert "Диалоги" in service.tabs
    assert service.headers == mod.HEADERS
    assert [row[0] for row in service.rows] == ["sha256:existing", "sha256:new"]
    assert service.rows[0] == rows[0].values()
    assert service.rows[1] == rows[1].values()
    assert len(rows[0].values()) == 14
    assert rows[0].values()[11:] == [mod.NO_DATA, mod.NO_DATA, mod.NO_DATA]
    assert any(range_name.endswith("A1:N1") and option == "USER_ENTERED" for range_name, option in service.value_input_options)
    assert [option for range_name, option in service.value_input_options if not range_name.endswith("A1:N1")] == ["RAW", "RAW"]


def test_segmented_upsert_updates_legacy_row_and_does_not_duplicate_on_rerun() -> None:
    service = _SheetsService()
    service.rows = [["sha256:existing", "2026-07-29T10:00:00Z", "legacy"]]
    rows = [
        mod.DialogueRow("sha256:existing", "29.07.2026 10:00", "активен", "segment 0", "нет данных", "нет данных", "29.07.2026 10:01", ""),
        mod.DialogueRow("sha256:existing#s001", "29.07.2026 10:02", "активен", "segment 1", "нет данных", "нет данных", "29.07.2026 10:03", ""),
    ]

    first = mod.upsert_rows(service, spreadsheet_id="sheet", tab_name="Диалоги", rows=rows)
    second = mod.upsert_rows(service, spreadsheet_id="sheet", tab_name="Диалоги", rows=rows)

    assert first == {"updated": 1, "appended": 1, "total": 2}
    assert second == {"updated": 2, "appended": 0, "total": 2}
    assert [row[0] for row in service.rows] == ["sha256:existing", "sha256:existing#s001"]
    assert service.rows[0] == rows[0].values()
    assert service.rows[1] == rows[1].values()


def test_existing_legacy_header_is_extended_without_overwriting_a_to_h() -> None:
    service = _SheetsService()
    service.tabs = ["Диалоги"]
    service.headers = list(mod.LEGACY_HEADERS)

    mod.ensure_tab_and_headers(service, spreadsheet_id="sheet", tab_name="Диалоги")

    assert service.headers == mod.HEADERS
    assert service.value_input_options == [("'Диалоги'!I1:N1", "USER_ENTERED")]


def test_existing_runtime_header_is_extended_without_overwriting_a_to_i() -> None:
    service = _SheetsService()
    service.tabs = ["Диалоги"]
    service.headers = list(mod.OLD_RUNTIME_HEADERS)

    mod.ensure_tab_and_headers(service, spreadsheet_id="sheet", tab_name="Диалоги")

    assert service.headers == mod.HEADERS
    assert service.value_input_options == [("'Диалоги'!J1:N1", "USER_ENTERED")]


def test_existing_k_header_is_extended_to_diagnostics_without_overwriting_a_to_k() -> None:
    service = _SheetsService()
    service.tabs = ["Диалоги"]
    service.headers = list(mod.OLD_DIAGNOSTIC_BASE_HEADERS)

    mod.ensure_tab_and_headers(service, spreadsheet_id="sheet", tab_name="Диалоги")

    assert service.headers == mod.HEADERS
    assert service.value_input_options == [("'Диалоги'!L1:N1", "USER_ENTERED")]


def test_diagnostic_columns_are_deterministic_privacy_safe_and_do_not_claim_cause() -> None:
    rows = mod.aggregate_dialogues([
        {
            "ts": "2026-07-29T10:00:00Z",
            "role": "bot",
            "conversation_ref": "sha256:diagnostic-row",
            "text": "Ответ",
            "runtime_version": "V3",
            "runtime_summary": {
                "model_usage": {"answer": ["answer_model"], "search": ["search_model"]},
                "call_counts": {"search": 2, "selected_enrichment": 1},
            },
            "error_summary": {"status": "failed", "codes": ["gateway_timeout"], "stages": ["answer"]},
        }
    ])

    row = rows[0]
    assert row.execution == "runtime=V3; answer=answer_model; search=search_model; search_calls=2; enrichment_calls=1"
    assert row.diagnostic_outcome == "Есть ошибка обработки"
    assert row.analytics == "Причину нельзя локализовать по данным экспорта; зафиксированы только признаки: failed codes=gateway_timeout stages=answer"
    assert row.values()[11:] == [row.execution, row.diagnostic_outcome, row.analytics]


def test_formatting_payload_freezes_wraps_widths_and_formats_only_diagnostic_outcome() -> None:
    service = _SheetsService()
    service.tabs = ["Диалоги"]
    service.headers = list(mod.OLD_DIAGNOSTIC_BASE_HEADERS)

    mod.ensure_tab_and_headers(service, spreadsheet_id="sheet", tab_name="Диалоги")

    format_requests = [body for body in service.batch_update_requests if any("repeatCell" in request or "addConditionalFormatRule" in request for request in body.get("requests", []))]
    assert format_requests
    requests = format_requests[-1]["requests"]
    assert any(request.get("updateSheetProperties", {}).get("properties", {}).get("gridProperties", {}).get("frozenRowCount") == 1 for request in requests)
    assert any(request.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("wrapStrategy") == "WRAP" for request in requests)
    width_ranges = [request.get("updateDimensionProperties", {}).get("range", {}) for request in requests]
    assert {item.get("startIndex") for item in width_ranges if item.get("dimension") == "COLUMNS"} >= {3, 11}
    conditional_ranges = [request["addConditionalFormatRule"]["rule"]["ranges"][0] for request in requests if "addConditionalFormatRule" in request]
    assert conditional_ranges
    assert all(item["startColumnIndex"] == 12 and item["endColumnIndex"] == 13 for item in conditional_ranges)


def test_unexpected_header_is_rejected_instead_of_overwritten() -> None:
    service = _SheetsService()
    service.tabs = ["Диалоги"]
    service.headers = [*mod.LEGACY_HEADERS[:-1], "Другая колонка"]

    try:
        mod.ensure_tab_and_headers(service, spreadsheet_id="sheet", tab_name="Диалоги")
    except mod.SchemaError as exc:
        assert str(exc) == "dialogue_sheet_header_mismatch"
    else:
        raise AssertionError("SchemaError expected")
