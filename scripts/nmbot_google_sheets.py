#!/usr/bin/env python3
"""Google Sheets callback adapter and private delivery ledger.

The module is intentionally import-safe: Google client libraries are imported
only when credentials/client construction is explicitly requested.
"""

from __future__ import annotations

import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
LEAD_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
EXPECTED_HEADERS = ["Дата и время", "Телефон", "Имя", "Саммари диалога"]


@dataclass(frozen=True)
class AppendResult:
    status: str
    row_ref: str = ""
    uncertain: bool = False
    retryable: bool = False
    error_class: str = ""


@dataclass(frozen=True)
class DeliveryLookup:
    delivered: bool
    row_ref: str = ""


class CallbackSheetAdapter(Protocol):
    def ensure_headers(self) -> AppendResult: ...
    def append_callback(self, *, created_at_msk: str, phone: str, name: str, summary: str, lead_ref: str) -> AppendResult: ...
    def lookup_delivery(self, *, lead_ref: str) -> DeliveryLookup: ...
    def record_delivery(self, *, lead_ref: str, row_ref: str, delivered_at: str) -> None: ...


class ConfigurationError(RuntimeError):
    pass


class SchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoogleSheetsConfig:
    spreadsheet_id: str
    tab_name: str
    credentials_path: Path | None = None
    ledger_dir: Path | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "GoogleSheetsConfig":
        env = environ or os.environ
        sheet_id = str(env.get("NMBOT_CALLBACK_SHEET_ID") or "").strip()
        tab = str(env.get("NMBOT_CALLBACK_SHEET_TAB") or "").strip()
        creds_value = str(env.get("NMBOT_CALLBACK_GOOGLE_CREDENTIALS") or env.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        creds = Path(creds_value).expanduser() if creds_value else None
        ledger_value = str(env.get("NMBOT_CALLBACK_LEDGER_DIR") or "").strip()
        ledger_dir = Path(ledger_value).expanduser() if ledger_value else None
        if not SHEET_ID_RE.match(sheet_id):
            raise ConfigurationError("invalid_or_missing_sheet_id")
        if not tab or any(ch in tab for ch in "\n\r\t"):
            raise ConfigurationError("invalid_or_missing_sheet_tab")
        if ledger_dir is None:
            raise ConfigurationError("missing_callback_ledger_dir")
        inline = env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or env.get("GOOGLE_SHEETS_CREDENTIALS") or env.get("GOOGLE_CREDENTIALS")
        if inline:
            try:
                data = json.loads(inline)
            except json.JSONDecodeError as exc:
                raise ConfigurationError("invalid_inline_google_credentials_json") from exc
            if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
                raise ConfigurationError("invalid_inline_google_credentials_shape")
        elif creds is not None:
            if not creds.exists() or not creds.is_file():
                raise ConfigurationError("missing_google_credentials_file")
            if creds.stat().st_mode & 0o077:
                raise ConfigurationError("unsafe_google_credentials_permissions")
        else:
            # ADC may be available without an explicit credentials file.  Do not
            # call Google APIs or refresh credentials here; just allow the real
            # build step to use google.auth.default(scopes=SCOPES).
            pass
        return cls(spreadsheet_id=sheet_id, tab_name=tab, credentials_path=creds, ledger_dir=ledger_dir)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _quote_sheet_range(tab_name: str, cells: str) -> str:
    escaped = tab_name.replace("'", "''")
    return f"'{escaped}'!{cells}"


class LocalDeliveryLedger:
    """Private local delivery ledger keyed by opaque lead_ref."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, lead_ref: str) -> Path:
        if not LEAD_REF_RE.match(lead_ref):
            raise ConfigurationError("invalid_lead_ref")
        safe = lead_ref.replace("/", "_")
        return self.root / f"{safe}.json"

    def lookup_delivery(self, *, lead_ref: str) -> DeliveryLookup:
        try:
            data = json.loads(self._path(lead_ref).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return DeliveryLookup(delivered=False)
        row_ref = str(data.get("row_ref") or "") if isinstance(data, dict) else ""
        return DeliveryLookup(delivered=bool(row_ref), row_ref=row_ref)

    def record_delivery(self, *, lead_ref: str, row_ref: str, delivered_at: str) -> None:
        record = {
            "schema": "nmbot.callback_sheet_delivery.v1",
            "lead_ref": lead_ref,
            "row_ref": str(row_ref or "")[:200],
            "delivered_at": str(delivered_at or _utc_now_iso()),
        }
        path = self._path(lead_ref)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.chmod(tmp_name, 0o600)
                json.dump(record, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
            os.chmod(path, 0o600)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def _credentials_from_env_or_adc(credentials_path: Path | None):
    inline = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GOOGLE_SHEETS_CREDENTIALS") or os.environ.get("GOOGLE_CREDENTIALS")
    if inline:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_info(json.loads(inline), scopes=SCOPES)
    if credentials_path is not None:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(str(credentials_path), scopes=SCOPES)
    import google.auth

    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


def _build_sheets_service(config: GoogleSheetsConfig):
    return build_sheets_service(config)


def build_sheets_service(config: GoogleSheetsConfig | None = None):
    from googleapiclient.discovery import build

    credentials_path = config.credentials_path if config is not None else None
    return build("sheets", "v4", credentials=_credentials_from_env_or_adc(credentials_path), cache_discovery=False)


def resolve_sheet_title(service: Any, *, spreadsheet_id: str, gid: int) -> str:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title)",
    ).execute()
    for sheet in meta.get("sheets", []) if isinstance(meta, dict) else []:
        props = sheet.get("properties", {}) if isinstance(sheet, dict) else {}
        if props.get("sheetId") == gid:
            return str(props.get("title") or "")
    raise ConfigurationError(f"sheet_gid_not_found:{gid}")


def append_rows(service: Any, *, spreadsheet_id: str, range_name: str, values: list[list[Any]], value_input_option: str = "USER_ENTERED", insert_data_option: str = "INSERT_ROWS") -> dict[str, Any]:
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption=value_input_option,
        insertDataOption=insert_data_option,
        body={"values": values},
    ).execute()
    return result if isinstance(result, dict) else {}


def _http_status_from_exception(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


class GoogleSheetsCallbackAdapter:
    """Real Google Sheets adapter plus protected local delivery ledger."""

    def __init__(self, config: GoogleSheetsConfig, *, client: object | None = None) -> None:
        self.config = config
        self.client = client if client is not None else _build_sheets_service(config)
        if config.ledger_dir is None:
            raise ConfigurationError("missing_callback_ledger_dir")
        self.ledger = LocalDeliveryLedger(config.ledger_dir)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "GoogleSheetsCallbackAdapter":
        return cls(GoogleSheetsConfig.from_env(environ))

    def ensure_headers(self) -> AppendResult:
        try:
            values = self.client.spreadsheets().values()
            result = values.get(
                spreadsheetId=self.config.spreadsheet_id,
                range=_quote_sheet_range(self.config.tab_name, "A1:D1"),
            ).execute()
            rows = result.get("values", []) if isinstance(result, dict) else []
            first = list(rows[0]) if rows else []
            if not any(str(cell or "").strip() for cell in first):
                values.update(
                    spreadsheetId=self.config.spreadsheet_id,
                    range=_quote_sheet_range(self.config.tab_name, "A1:D1"),
                    valueInputOption="USER_ENTERED",
                    body={"values": [EXPECTED_HEADERS]},
                ).execute()
                return AppendResult(status="ok", row_ref=_quote_sheet_range(self.config.tab_name, "A1:D1"))
            normalized = [str(cell or "") for cell in first[:4]]
            if normalized != EXPECTED_HEADERS:
                return AppendResult(status="schema_error", retryable=False, error_class="sheet_header_mismatch")
            return AppendResult(status="ok", row_ref=_quote_sheet_range(self.config.tab_name, "A1:D1"))
        except Exception as exc:
            return self._classify_exception(exc)

    def lookup_delivery(self, *, lead_ref: str) -> DeliveryLookup:
        return self.ledger.lookup_delivery(lead_ref=lead_ref)

    def append_callback(self, *, created_at_msk: str, phone: str, name: str, summary: str, lead_ref: str) -> AppendResult:
        delivered = self.lookup_delivery(lead_ref=lead_ref)
        if delivered.delivered:
            return AppendResult(status="ok", row_ref=delivered.row_ref)
        try:
            result = append_rows(
                self.client,
                spreadsheet_id=self.config.spreadsheet_id,
                range_name=_quote_sheet_range(self.config.tab_name, "A:D"),
                values=[[str(created_at_msk or ""), str(phone or ""), str(name or ""), str(summary or "")]],
            )
            updates = result.get("updates", {}) if isinstance(result, dict) else {}
            row_ref = str(updates.get("updatedRange") or "")
            return AppendResult(status="ok", row_ref=row_ref or _quote_sheet_range(self.config.tab_name, "A:D"))
        except Exception as exc:
            return self._classify_exception(exc)

    def record_delivery(self, *, lead_ref: str, row_ref: str, delivered_at: str) -> None:
        self.ledger.record_delivery(lead_ref=lead_ref, row_ref=row_ref, delivered_at=delivered_at)

    def _classify_exception(self, exc: BaseException) -> AppendResult:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return AppendResult(status="timeout", uncertain=True, retryable=True, error_class="transport_timeout")
        status = _http_status_from_exception(exc)
        if status == 429 or (status is not None and 500 <= status <= 599):
            return AppendResult(status="retryable_error", retryable=True, error_class=f"google_http_{status}")
        if status in {401, 403, 404}:
            return AppendResult(status="failed", retryable=False, error_class=f"google_http_{status}")
        return AppendResult(status="failed", retryable=False, error_class="google_adapter_exception")


class FakeCallbackSheetAdapter:
    def __init__(self, *, fail: AppendResult | None = None) -> None:
        self.rows: list[list[str]] = []
        self.deliveries: dict[str, str] = {}
        self.fail = fail

    def lookup_delivery(self, *, lead_ref: str) -> DeliveryLookup:
        row_ref = self.deliveries.get(lead_ref, "")
        return DeliveryLookup(delivered=bool(row_ref), row_ref=row_ref)

    def ensure_headers(self) -> AppendResult:
        return AppendResult(status="ok", row_ref="A1:D1")

    def append_callback(self, *, created_at_msk: str, phone: str, name: str, summary: str, lead_ref: str) -> AppendResult:
        if self.fail is not None:
            return self.fail
        self.rows.append([created_at_msk, phone, name, summary])
        return AppendResult(status="ok", row_ref=f"A{len(self.rows)}:D{len(self.rows)}")

    def record_delivery(self, *, lead_ref: str, row_ref: str, delivered_at: str) -> None:
        self.deliveries[lead_ref] = row_ref
