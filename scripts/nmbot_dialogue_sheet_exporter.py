#!/usr/bin/env python3
"""Export redacted NMBot dialogue journal to a Google Sheet tab.

The source journal is the canonical append-only, already-redacted JSONL file.
This exporter never reads raw Jivo payloads/prompts and upserts rows by stable
opaque dialogue/segment IDs so repeated hourly runs do not create doubles.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dialogue_journal import journal_path
from nmbot_google_sheets import GoogleSheetsConfig, SchemaError, _quote_sheet_range, append_rows, build_sheets_service


SPREADSHEET_ID = "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ"
TAB_NAME = "Диалоги"
LEGACY_HEADERS = [
    "ID диалога",
    "Дата и время",
    "Статус",
    "Диалог",
    "Модель ответа",
    "Модель поиска",
    "Дата и время последнего обновления",
    "Ошибка обработки",
]
RUNTIME_VERSION_HEADER = "Версия runtime"
BACKEND_MCP_SUMMARY_HEADER = "Backend/MCP summary"
MEMORY_STATE_SUMMARY_HEADER = "Memory/state summary"
EXECUTION_HEADER = "Исполнение"
DIAGNOSTIC_OUTCOME_HEADER = "Итог диагностики"
ANALYTICS_HEADER = "Аналитика"
HEADERS = [
    *LEGACY_HEADERS,
    RUNTIME_VERSION_HEADER,
    BACKEND_MCP_SUMMARY_HEADER,
    MEMORY_STATE_SUMMARY_HEADER,
    EXECUTION_HEADER,
    DIAGNOSTIC_OUTCOME_HEADER,
    ANALYTICS_HEADER,
]
OLD_RUNTIME_HEADERS = [*LEGACY_HEADERS, RUNTIME_VERSION_HEADER]
OLD_DIAGNOSTIC_BASE_HEADERS = [*LEGACY_HEADERS, RUNTIME_VERSION_HEADER, BACKEND_MCP_SUMMARY_HEADER, MEMORY_STATE_SUMMARY_HEADER]
NO_DATA = "нет данных"
VALID_RUNTIME_VERSIONS = ("V0", "V1", "V2", "V3")
ISO_MINUTE_TIMESTAMP_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[T ](?P<hour>\d{2}):(?P<minute>\d{2})"
    r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)
HUMAN_MINUTE_TIMESTAMP_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")


@dataclass(frozen=True)
class DialogueRow:
    dialogue_id: str
    created_at: str
    status: str
    dialogue: str
    answer_model: str
    search_model: str
    updated_at: str
    processing_error: str
    runtime_version: str = NO_DATA
    backend_mcp_summary: str = NO_DATA
    memory_state_summary: str = NO_DATA
    execution: str = NO_DATA
    diagnostic_outcome: str = NO_DATA
    analytics: str = NO_DATA

    def values(self) -> list[str]:
        return [
            self.dialogue_id,
            self.created_at,
            self.status,
            self.dialogue,
            self.answer_model,
            self.search_model,
            self.updated_at,
            self.processing_error,
            self.runtime_version,
            self.backend_mcp_summary,
            self.memory_state_summary,
            self.execution,
            self.diagnostic_outcome,
            self.analytics,
        ]


def load_journal_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return events
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def aggregate_dialogues(events: list[dict[str, Any]]) -> list[DialogueRow]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        conversation_ref = _safe_conversation_ref(event.get("conversation_ref"))
        if not conversation_ref:
            continue
        grouped.setdefault(conversation_ref, []).append(event)

    rows: list[DialogueRow] = []
    for conversation_ref, items in grouped.items():
        for segment_index, segment_events in enumerate(_split_start_reset_segments(items)):
            rows.append(_row_from_events(_segment_dialogue_id(conversation_ref, segment_index), segment_events))
    return sorted(rows, key=lambda row: (_sheet_timestamp_sort_key(row.created_at), row.dialogue_id))


def _safe_conversation_ref(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("sha256:") and 10 <= len(text) <= 80:
        return text
    return ""


def _split_start_reset_segments(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(events, key=lambda item: str(item.get("ts") or ""))
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for event in ordered:
        if _is_start_reset_boundary(event) and current:
            segments.append(current)
            current = []
        current.append(event)
    if current:
        segments.append(current)
    return segments


def _is_start_reset_boundary(event: dict[str, Any]) -> bool:
    event_type = str(event.get("event_type") or "").strip().lower()
    answer_kind = str(event.get("answer_kind") or "").strip().lower()
    return event_type == "lifecycle" and answer_kind == "start_reset"


def _segment_dialogue_id(conversation_ref: str, segment_index: int) -> str:
    if segment_index <= 0:
        return conversation_ref
    return f"{conversation_ref}#s{segment_index:03d}"


def _row_from_events(dialogue_id: str, events: list[dict[str, Any]]) -> DialogueRow:
    ordered = sorted(events, key=lambda item: str(item.get("ts") or ""))
    created_at = _format_sheet_timestamp(ordered[0].get("ts") if ordered else None)
    updated_at = _format_sheet_timestamp(ordered[-1].get("ts") if ordered else None) if ordered else created_at
    answer_models: list[str] = []
    search_models: list[str] = []
    errors: list[str] = []
    runtime_versions: list[str] = []
    call_counts: dict[str, int] = {}
    field_names: list[str] = []
    memory_state_parts: list[str] = []
    lines: list[str] = []
    any_failed = False
    any_handoff = False

    for event in ordered:
        role = str(event.get("role") or "").strip().lower()
        event_type = str(event.get("event_type") or "turn").strip().lower()
        answer_kind = str(event.get("answer_kind") or "").strip().lower()
        text = str(event.get("text") or "").strip()
        label = "Бот" if role == "bot" else "Пользователь" if role == "user" else role or "Событие"
        if event_type == "handoff" or answer_kind == "callback_queued":
            any_handoff = True
        if event_type == "handoff":
            text = text or "Передача оператору"
        if text:
            lines.append(f"{label}: {text}")

        runtime_summary = event.get("runtime_summary") if isinstance(event.get("runtime_summary"), dict) else {}
        _extend_runtime_versions(runtime_versions, event.get("runtime_version"))
        _extend_runtime_versions(runtime_versions, runtime_summary.get("runtime_version"))
        _extend_models(answer_models, runtime_summary.get("model_usage"), "answer")
        _extend_models(search_models, runtime_summary.get("model_usage"), "search")
        _extend_models_from_attempts(answer_models, runtime_summary.get("gateway_attempt_details"), "answer")
        _extend_models_from_attempts(search_models, runtime_summary.get("gateway_attempt_details"), "search")
        _merge_safe_call_counts(call_counts, runtime_summary.get("call_counts"))
        _extend_safe_field_names(field_names, runtime_summary.get("field_trace"))
        _extend_safe_memory_state_parts(memory_state_parts, "before", runtime_summary.get("state_before"))
        _extend_safe_memory_state_parts(memory_state_parts, "after", runtime_summary.get("state_after"))

        error_summary = event.get("error_summary") if isinstance(event.get("error_summary"), dict) else {}
        status = str(error_summary.get("status") or "").strip().lower()
        if status in {"failed", "degraded"}:
            any_failed = any_failed or status == "failed"
            errors.append(_format_error_summary(error_summary))

    last_role = str(ordered[-1].get("role") or "").strip().lower() if ordered else ""
    if any_failed:
        status_text = "ошибка обработки"
    elif last_role == "user":
        status_text = "незавершён"
    elif any_handoff:
        status_text = "передан оператору"
    else:
        status_text = "активен"

    return DialogueRow(
        dialogue_id=dialogue_id,
        created_at=created_at,
        status=status_text,
        dialogue="\n".join(lines) or NO_DATA,
        answer_model=", ".join(answer_models) if answer_models else NO_DATA,
        search_model=", ".join(search_models) if search_models else NO_DATA,
        updated_at=updated_at,
        processing_error="; ".join(dict.fromkeys(errors)) if errors else "",
        runtime_version=_format_runtime_versions(runtime_versions),
        backend_mcp_summary=_format_backend_mcp_summary(call_counts, field_names),
        memory_state_summary=_format_memory_state_summary(memory_state_parts),
        execution=_format_execution_summary(runtime_versions, answer_models, search_models, call_counts),
        diagnostic_outcome=_format_diagnostic_outcome(status_text, errors),
        analytics=_format_analytics(errors),
    )


def _format_sheet_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return NO_DATA
    if HUMAN_MINUTE_TIMESTAMP_RE.match(text):
        return text
    match = ISO_MINUTE_TIMESTAMP_RE.match(text)
    if not match:
        return NO_DATA
    return f"{match.group('day')}.{match.group('month')}.{match.group('year')} {match.group('hour')}:{match.group('minute')}"


def _sheet_timestamp_sort_key(value: str) -> str:
    text = str(value or "").strip()
    if HUMAN_MINUTE_TIMESTAMP_RE.match(text):
        return f"{text[6:10]}-{text[3:5]}-{text[0:2]}T{text[11:16]}"
    return text


def _extend_models(target: list[str], model_usage: Any, role: str) -> None:
    if not isinstance(model_usage, dict):
        return
    raw = model_usage.get(role)
    items = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    for item in items:
        model = str(item or "").strip()
        if model and model not in target:
            target.append(model[:80])


def _extend_models_from_attempts(target: list[str], attempts: Any, role: str) -> None:
    if not isinstance(attempts, list):
        return
    for item in attempts:
        if not isinstance(item, dict):
            continue
        if str(item.get("stage") or "") != "gateway_attempt":
            continue
        if str(item.get("model_role") or "").strip().lower() != role:
            continue
        model = str(item.get("model") or "").strip()
        if model and model not in target:
            target.append(model[:80])


def _extend_runtime_versions(target: list[str], value: Any) -> None:
    version = _normalize_runtime_version(value)
    if version and version not in target:
        target.append(version)


def _normalize_runtime_version(value: Any) -> str | None:
    version = str(value or "").strip().upper()
    return version if version in VALID_RUNTIME_VERSIONS else None


def _format_runtime_versions(values: list[str]) -> str:
    present = set(values)
    ordered = [version for version in VALID_RUNTIME_VERSIONS if version in present]
    return "/".join(ordered) if ordered else NO_DATA


SAFE_SUMMARY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
UNSAFE_SUMMARY_KEY_PARTS = ("phone", "тел", "email", "client", "session", "chat", "token", "secret", "payload", "prompt", "raw", "object", "card_name", "+7", "7999")


def _safe_summary_key(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not SAFE_SUMMARY_KEY_RE.fullmatch(text):
        return None
    if any(part in text for part in UNSAFE_SUMMARY_KEY_PARTS):
        return None
    return text


def _merge_safe_call_counts(target: dict[str, int], value: Any) -> None:
    if not isinstance(value, dict):
        return
    for key in ("search", "selected_enrichment"):
        try:
            number = int(value.get(key))
        except (TypeError, ValueError):
            continue
        target[key] = target.get(key, 0) + max(0, min(number, 100))


def _extend_safe_field_names(target: list[str], value: Any) -> None:
    if not isinstance(value, dict):
        return
    cards = value.get("cards") if isinstance(value.get("cards"), list) else []
    for card in cards[:10]:
        if not isinstance(card, dict):
            continue
        for field_list_key in ("raw_fields", "normalized_fields"):
            fields = card.get(field_list_key) if isinstance(card.get(field_list_key), list) else []
            for item in fields:
                field = _safe_summary_key(item)
                if field and field not in target:
                    target.append(field)


def _format_backend_mcp_summary(call_counts: dict[str, int], field_names: list[str]) -> str:
    parts: list[str] = []
    if "search" in call_counts:
        parts.append(f"search_calls={call_counts['search']}")
    if "selected_enrichment" in call_counts:
        parts.append(f"enrichment_calls={call_counts['selected_enrichment']}")
    if field_names:
        parts.append("fields=" + ",".join(sorted(field_names)[:30]))
    return "; ".join(parts) if parts else NO_DATA


def _extend_safe_memory_state_parts(target: list[str], prefix: str, value: Any) -> None:
    if prefix not in {"before", "after"} or not isinstance(value, dict):
        return
    keys = value.get("param_keys") if isinstance(value.get("param_keys"), list) else []
    safe_keys = sorted(dict.fromkeys(key for key in (_safe_summary_key(item) for item in keys) if key))[:20]
    if safe_keys:
        target.append(f"{prefix}_keys=" + ",".join(safe_keys))
    visible_count = value.get("visible_options_count")
    if isinstance(visible_count, int):
        target.append(f"{prefix}_visible_options_count={max(0, min(visible_count, 100))}")
    if isinstance(value.get("selected_present"), bool):
        target.append(f"{prefix}_selected_present={'true' if value.get('selected_present') else 'false'}")


def _format_memory_state_summary(parts: list[str]) -> str:
    return "\n".join(dict.fromkeys(parts)) if parts else NO_DATA


def _format_error_summary(error_summary: dict[str, Any]) -> str:
    status = str(error_summary.get("status") or "").strip().lower() or "unknown"
    codes = [value for value in (_safe_summary_key(x) for x in error_summary.get("codes", [])) if value] if isinstance(error_summary.get("codes"), list) else []
    stages = [value for value in (_safe_summary_key(x) for x in error_summary.get("stages", [])) if value] if isinstance(error_summary.get("stages"), list) else []
    parts = [status]
    if codes:
        parts.append("codes=" + ",".join(codes[:8]))
    if stages:
        parts.append("stages=" + ",".join(stages[:4]))
    if error_summary.get("fallback"):
        parts.append("fallback=true")
    return " ".join(parts)


def _format_execution_summary(runtime_versions: list[str], answer_models: list[str], search_models: list[str], call_counts: dict[str, int]) -> str:
    parts: list[str] = []
    runtime = _format_runtime_versions(runtime_versions)
    if runtime != NO_DATA:
        parts.append(f"runtime={runtime}")
    if answer_models:
        parts.append("answer=" + ",".join(answer_models[:3]))
    if search_models:
        parts.append("search=" + ",".join(search_models[:3]))
    if "search" in call_counts:
        parts.append(f"search_calls={call_counts['search']}")
    if "selected_enrichment" in call_counts:
        parts.append(f"enrichment_calls={call_counts['selected_enrichment']}")
    return "; ".join(parts) if parts else NO_DATA


def _format_diagnostic_outcome(status_text: str, errors: list[str]) -> str:
    if errors:
        if any(error.startswith("failed") for error in errors):
            return "Есть ошибка обработки"
        if any(error.startswith("degraded") for error in errors):
            return "Есть деградация обработки"
        return "Есть диагностическое предупреждение"
    if status_text == "незавершён":
        return "Диалог ждёт ответа бота"
    if status_text == "передан оператору":
        return "Передан оператору"
    return "Ошибок в экспорте не зафиксировано"


def _format_analytics(errors: list[str]) -> str:
    if not errors:
        return "Причину нельзя локализовать по данным экспорта: ошибок в экспортируемых событиях нет."
    evidence = "; ".join(dict.fromkeys(errors))[:500]
    return f"Причину нельзя локализовать по данным экспорта; зафиксированы только признаки: {evidence}"


def ensure_tab_and_headers(service: Any, *, spreadsheet_id: str, tab_name: str) -> None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)").execute()
    titles = [str((sheet.get("properties") or {}).get("title") or "") for sheet in meta.get("sheets", []) if isinstance(sheet, dict)] if isinstance(meta, dict) else []
    if tab_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)").execute()
    sheet_id = _find_sheet_id(meta, tab_name)
    values = service.spreadsheets().values()
    result = values.get(spreadsheetId=spreadsheet_id, range=_quote_sheet_range(tab_name, "A1:N1")).execute()
    rows = result.get("values", []) if isinstance(result, dict) else []
    first = list(rows[0]) if rows else []
    if not any(str(cell or "").strip() for cell in first):
        values.update(
            spreadsheetId=spreadsheet_id,
            range=_quote_sheet_range(tab_name, "A1:N1"),
            valueInputOption="USER_ENTERED",
            body={"values": [HEADERS]},
        ).execute()
        format_dialogue_sheet(service, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id)
        return
    normalized = [str(cell or "") for cell in first]
    if normalized[: len(HEADERS)] == HEADERS:
        return
    if normalized[: len(LEGACY_HEADERS)] == LEGACY_HEADERS and not any(str(cell or "").strip() for cell in first[len(LEGACY_HEADERS) :]):
        values.update(
            spreadsheetId=spreadsheet_id,
            range=_quote_sheet_range(tab_name, "I1:N1"),
            valueInputOption="USER_ENTERED",
            body={"values": [[RUNTIME_VERSION_HEADER, BACKEND_MCP_SUMMARY_HEADER, MEMORY_STATE_SUMMARY_HEADER, EXECUTION_HEADER, DIAGNOSTIC_OUTCOME_HEADER, ANALYTICS_HEADER]]},
        ).execute()
        format_dialogue_sheet(service, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id)
        return
    if normalized[: len(OLD_RUNTIME_HEADERS)] == OLD_RUNTIME_HEADERS and not any(str(cell or "").strip() for cell in first[len(OLD_RUNTIME_HEADERS) :]):
        values.update(
            spreadsheetId=spreadsheet_id,
            range=_quote_sheet_range(tab_name, "J1:N1"),
            valueInputOption="USER_ENTERED",
            body={"values": [[BACKEND_MCP_SUMMARY_HEADER, MEMORY_STATE_SUMMARY_HEADER, EXECUTION_HEADER, DIAGNOSTIC_OUTCOME_HEADER, ANALYTICS_HEADER]]},
        ).execute()
        format_dialogue_sheet(service, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id)
        return
    if normalized[: len(OLD_DIAGNOSTIC_BASE_HEADERS)] == OLD_DIAGNOSTIC_BASE_HEADERS and not any(str(cell or "").strip() for cell in first[len(OLD_DIAGNOSTIC_BASE_HEADERS) :]):
        values.update(
            spreadsheetId=spreadsheet_id,
            range=_quote_sheet_range(tab_name, "L1:N1"),
            valueInputOption="USER_ENTERED",
            body={"values": [[EXECUTION_HEADER, DIAGNOSTIC_OUTCOME_HEADER, ANALYTICS_HEADER]]},
        ).execute()
        format_dialogue_sheet(service, spreadsheet_id=spreadsheet_id, sheet_id=sheet_id)
        return
    if normalized[: len(HEADERS)] != HEADERS:
        raise SchemaError("dialogue_sheet_header_mismatch")


def _find_sheet_id(meta: Any, tab_name: str) -> int | None:
    if not isinstance(meta, dict):
        return None
    for sheet in meta.get("sheets", []):
        properties = sheet.get("properties") if isinstance(sheet, dict) else None
        if not isinstance(properties, dict) or str(properties.get("title") or "") != tab_name:
            continue
        sheet_id = properties.get("sheetId")
        return sheet_id if isinstance(sheet_id, int) else None
    return None


def format_dialogue_sheet(service: Any, *, spreadsheet_id: str, sheet_id: int | None) -> None:
    if sheet_id is None:
        return
    requests = [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(HEADERS)}, "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}}, "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(HEADERS)}, "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}}, "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 4}, "properties": {"pixelSize": 420}, "fields": "pixelSize"}},
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 11, "endIndex": 14}, "properties": {"pixelSize": 260}, "fields": "pixelSize"}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 12, "endColumnIndex": 13}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "ошибка"}]}, "format": {"backgroundColor": {"red": 0.96, "green": 0.8, "blue": 0.8}}}}, "index": 0}},
        {"addConditionalFormatRule": {"rule": {"ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 12, "endColumnIndex": 13}], "booleanRule": {"condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": "деградация"}]}, "format": {"backgroundColor": {"red": 1.0, "green": 0.92, "blue": 0.7}}}}, "index": 0}},
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def upsert_rows(service: Any, *, spreadsheet_id: str, tab_name: str, rows: list[DialogueRow]) -> dict[str, int]:
    ensure_tab_and_headers(service, spreadsheet_id=spreadsheet_id, tab_name=tab_name)
    values = service.spreadsheets().values()
    existing = values.get(spreadsheetId=spreadsheet_id, range=_quote_sheet_range(tab_name, "A2:A")).execute()
    existing_rows = existing.get("values", []) if isinstance(existing, dict) else []
    row_by_id = {str(row[0]): idx + 2 for idx, row in enumerate(existing_rows) if row and str(row[0]).strip()}
    updated_values: list[dict[str, Any]] = []
    appended_values: list[list[str]] = []
    for row in rows:
        values_row = row.values()
        row_num = row_by_id.get(row.dialogue_id)
        if row_num:
            updated_values.append({"range": _quote_sheet_range(tab_name, f"A{row_num}:N{row_num}"), "values": [values_row]})
        else:
            appended_values.append(values_row)
    if updated_values:
        values.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updated_values},
        ).execute()
    if appended_values:
        append_rows(service, spreadsheet_id=spreadsheet_id, range_name=_quote_sheet_range(tab_name, "A:N"), values=appended_values, value_input_option="RAW")
    return {"updated": len(updated_values), "appended": len(appended_values), "total": len(rows)}


def build_dialogue_export_service(spreadsheet_id: str, tab_name: str) -> Any:
    creds_value = str(os.getenv("NMBOT_DIALOGUE_EXPORT_GOOGLE_CREDENTIALS") or os.getenv("NMBOT_CALLBACK_GOOGLE_CREDENTIALS") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    config = GoogleSheetsConfig(spreadsheet_id=spreadsheet_id, tab_name=tab_name, credentials_path=Path(creds_value).expanduser() if creds_value else None)
    return build_sheets_service(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export redacted NMBot dialogues to Google Sheets")
    parser.add_argument("--journal", default=str(journal_path()))
    parser.add_argument("--sheet-id", default=os.getenv("NMBOT_DIALOGUE_EXPORT_SHEET_ID", SPREADSHEET_ID))
    parser.add_argument("--tab", default=os.getenv("NMBOT_DIALOGUE_EXPORT_SHEET_TAB", TAB_NAME))
    parser.add_argument("--write", action="store_true", help="perform Google Sheets write; default is dry-run")
    parser.add_argument("--verify-readback", action="store_true", help="read headers and id column after write")
    args = parser.parse_args(argv)

    rows = aggregate_dialogues(load_journal_events(Path(args.journal).expanduser()))
    if not args.write:
        print(f"mode=dry-run tab={args.tab} rows={len(rows)} headers={json.dumps(HEADERS, ensure_ascii=False)}")
        if rows:
            print(f"first_id={rows[0].dialogue_id} last_id={rows[-1].dialogue_id}")
        return 0

    service = build_dialogue_export_service(str(args.sheet_id), str(args.tab))
    result = upsert_rows(service, spreadsheet_id=str(args.sheet_id), tab_name=str(args.tab), rows=rows)
    print(f"mode=write tab={args.tab} total={result['total']} updated={result['updated']} appended={result['appended']}")
    if args.verify_readback:
        header_result = service.spreadsheets().values().get(spreadsheetId=str(args.sheet_id), range=_quote_sheet_range(str(args.tab), "A1:N1")).execute()
        header = (header_result.get("values") or [[]])[0] if isinstance(header_result, dict) else []
        if [str(cell or "") for cell in header[: len(HEADERS)]] != HEADERS:
            raise SchemaError("dialogue_sheet_readback_header_mismatch")
        id_result = service.spreadsheets().values().get(spreadsheetId=str(args.sheet_id), range=_quote_sheet_range(str(args.tab), "A2:A2")).execute()
        first_id = "present" if isinstance(id_result, dict) and id_result.get("values") else "empty"
        print(f"verify=ok headers=ok first_id={first_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
