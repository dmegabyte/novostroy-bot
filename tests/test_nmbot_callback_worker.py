from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

outbox_spec = importlib.util.spec_from_file_location("nmbot_crm_outbox_worker_test", SCRIPT_DIR / "nmbot_crm_outbox.py")
outbox_mod = importlib.util.module_from_spec(outbox_spec)
assert outbox_spec and outbox_spec.loader
sys.modules[outbox_spec.name] = outbox_mod
outbox_spec.loader.exec_module(outbox_mod)

summary_spec = importlib.util.spec_from_file_location("nmbot_callback_summary_worker_test", SCRIPT_DIR / "nmbot_callback_summary.py")
summary_mod = importlib.util.module_from_spec(summary_spec)
assert summary_spec and summary_spec.loader
sys.modules[summary_spec.name] = summary_mod
summary_spec.loader.exec_module(summary_mod)

google_spec = importlib.util.spec_from_file_location("nmbot_google_sheets_worker_test", SCRIPT_DIR / "nmbot_google_sheets.py")
google_mod = importlib.util.module_from_spec(google_spec)
assert google_spec and google_spec.loader
sys.modules[google_spec.name] = google_mod
google_spec.loader.exec_module(google_mod)

worker_spec = importlib.util.spec_from_file_location("nmbot_callback_sheet_worker_test", SCRIPT_DIR / "nmbot_callback_sheet_worker.py")
worker_mod = importlib.util.module_from_spec(worker_spec)
assert worker_spec and worker_spec.loader
sys.modules[worker_spec.name] = worker_mod
worker_spec.loader.exec_module(worker_mod)


def make_record(tmp_path: Path, *, name: str = "Иван", phone: str = "+79991234567") -> tuple[Any, str]:
    outbox = outbox_mod.LocalCallbackOutbox(tmp_path / "outbox")
    result = outbox.enqueue_callback(
        session_key="jivo:secret-chat",
        event_id="evt-1",
        contact_name=name,
        normalized_phone=phone,
        context={
            "params": {"rooms": 2, "budget": 12000000},
            "selected_option": {"name": "ЖК Видимый"},
            "last_bot_question": "Хотите, чтобы специалист проверил условия?",
            "client_id": "raw-client",
        },
    )
    return outbox, result.lead_ref


class StaticSummary:
    def __init__(self, text: str = "Безопасное саммари") -> None:
        self.text = text
        self.seen: list[dict[str, Any]] = []

    def summarize(self, snapshot: dict[str, Any]) -> str:
        self.seen.append(snapshot)
        return self.text


class FailingSummary:
    def summarize(self, snapshot: dict[str, Any]) -> str:
        raise RuntimeError("llm unavailable with secret +79991234567")


def read_record(tmp_path: Path) -> dict[str, Any]:
    path = next((tmp_path / "outbox").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


class _Exec:
    def __init__(self, value: dict[str, Any] | None = None, exc: BaseException | None = None) -> None:
        self.value = value or {}
        self.exc = exc

    def execute(self) -> dict[str, Any]:
        if self.exc:
            raise self.exc
        return self.value


class _FakeGoogleValues:
    def __init__(self, *, header: list[str] | None = None, append_exc: BaseException | None = None) -> None:
        self.header = header
        self.append_exc = append_exc
        self.updates: list[dict[str, Any]] = []
        self.appends: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> _Exec:
        return _Exec({"values": [self.header]} if self.header is not None else {})

    def update(self, **kwargs: Any) -> _Exec:
        self.updates.append(kwargs)
        self.header = list(kwargs["body"]["values"][0])
        return _Exec({"updatedRange": kwargs["range"]})

    def append(self, **kwargs: Any) -> _Exec:
        self.appends.append(kwargs)
        return _Exec({"updates": {"updatedRange": "Лист1!A2:D2"}}, self.append_exc)


class _FakeGoogleService:
    def __init__(self, values: _FakeGoogleValues) -> None:
        self._values = values

    def spreadsheets(self) -> "_FakeGoogleService":
        return self

    def values(self) -> _FakeGoogleValues:
        return self._values


class _FakeHttpError(Exception):
    def __init__(self, status: int) -> None:
        self.resp = type("Resp", (), {"status": status})()


class LedgerFailingAdapter(google_mod.FakeCallbackSheetAdapter):
    def record_delivery(self, *, lead_ref: str, row_ref: str, delivered_at: str) -> None:
        raise OSError("ledger unavailable")


def test_worker_success_appends_four_columns_with_moscow_timestamp(tmp_path: Path) -> None:
    outbox, lead_ref = make_record(tmp_path)
    summary = StaticSummary("Проверить условия по ЖК.")
    sheet = google_mod.FakeCallbackSheetAdapter()
    worker = worker_mod.CallbackSheetWorker(outbox=outbox, summary_provider=summary, sheet_adapter=sheet, retry_base_seconds=0)

    result = worker.process_once()

    assert result["status"] == "sheet_delivered"
    assert sheet.rows == [[read_record(tmp_path)["created_at_msk"], "+79991234567", "Иван", "Проверить условия по ЖК."]]
    assert len(sheet.rows[0]) == 4
    assert "+03:00" in sheet.rows[0][0]
    assert sheet.deliveries[lead_ref] == "A1:D1"


def test_google_headers_are_initialized_once_on_empty_sheet(tmp_path: Path) -> None:
    values = _FakeGoogleValues(header=None)
    adapter = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "ledger"),
        client=_FakeGoogleService(values),
    )

    first = adapter.ensure_headers()
    second = adapter.ensure_headers()

    assert first.status == "ok"
    assert second.status == "ok"
    assert len(values.updates) == 1
    assert values.updates[0]["body"] == {"values": [google_mod.EXPECTED_HEADERS]}


def test_mismatched_headers_are_nonretryable_and_prevent_append(tmp_path: Path) -> None:
    outbox, _ = make_record(tmp_path)
    values = _FakeGoogleValues(header=["wrong", "Телефон", "Имя", "Саммари диалога"])
    adapter = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "ledger"),
        client=_FakeGoogleService(values),
    )
    worker = worker_mod.CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=adapter, retry_base_seconds=0)

    result = worker.process_once()

    assert result["status"] == "failed"
    assert result["stage"] == "sheet_headers"
    assert values.appends == []


def test_real_fake_google_append_uses_user_entered_insert_rows_and_four_columns(tmp_path: Path) -> None:
    values = _FakeGoogleValues(header=google_mod.EXPECTED_HEADERS)
    adapter = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "ledger"),
        client=_FakeGoogleService(values),
    )

    result = adapter.append_callback(created_at_msk="2026-07-16 16:30:00 МСК", phone="+79991234567", name="Иван", summary="Саммари", lead_ref="cb_test")

    assert result.status == "ok"
    assert len(values.appends) == 1
    call = values.appends[0]
    assert call["valueInputOption"] == "USER_ENTERED"
    assert call["insertDataOption"] == "INSERT_ROWS"
    assert call["body"] == {"values": [["2026-07-16 16:30:00 МСК", "+79991234567", "Иван", "Саммари"]]}
    assert len(call["body"]["values"][0]) == 4


def test_local_delivery_ledger_is_private_and_idempotent(tmp_path: Path) -> None:
    ledger = google_mod.LocalDeliveryLedger(tmp_path / "ledger")
    assert ledger.lookup_delivery(lead_ref="cb_opaque").delivered is False

    ledger.record_delivery(lead_ref="cb_opaque", row_ref="Лист1!A2:D2", delivered_at="2026-07-16T12:00:00Z")

    assert ledger.lookup_delivery(lead_ref="cb_opaque").row_ref == "Лист1!A2:D2"
    assert oct(os.stat(tmp_path / "ledger").st_mode & 0o777) == "0o700"
    path = tmp_path / "ledger" / "cb_opaque.json"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_google_http_429_is_retryable_and_403_is_failed(tmp_path: Path) -> None:
    adapter_429 = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "l1"),
        client=_FakeGoogleService(_FakeGoogleValues(header=google_mod.EXPECTED_HEADERS, append_exc=_FakeHttpError(429))),
    )
    retry = adapter_429.append_callback(created_at_msk="t", phone="p", name="n", summary="s", lead_ref="cb_retry")
    assert retry.retryable is True
    assert retry.error_class == "google_http_429"

    adapter_403 = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "l2"),
        client=_FakeGoogleService(_FakeGoogleValues(header=google_mod.EXPECTED_HEADERS, append_exc=_FakeHttpError(403))),
    )
    failed = adapter_403.append_callback(created_at_msk="t", phone="p", name="n", summary="s", lead_ref="cb_failed")
    assert failed.retryable is False
    assert failed.error_class == "google_http_403"


def test_google_timeout_is_uncertain_retryable(tmp_path: Path) -> None:
    adapter = google_mod.GoogleSheetsCallbackAdapter(
        google_mod.GoogleSheetsConfig("1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ", "Лист1", ledger_dir=tmp_path / "ledger"),
        client=_FakeGoogleService(_FakeGoogleValues(header=google_mod.EXPECTED_HEADERS, append_exc=TimeoutError("lost response"))),
    )

    result = adapter.append_callback(created_at_msk="t", phone="p", name="n", summary="s", lead_ref="cb_timeout")

    assert result.uncertain is True
    assert result.retryable is True
    assert result.error_class == "transport_timeout"


def test_diagnose_validates_without_building_google_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CALLBACK_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("NMBOT_CALLBACK_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_ID", "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ")
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_TAB", "Лист1")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps({"client_email": "sa@example.test", "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n"}))
    monkeypatch.setattr(google_mod.GoogleSheetsCallbackAdapter, "__init__", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not instantiate")))

    assert worker_mod.main(["--diagnose"]) == 0


def test_summary_failure_retries_then_uses_deterministic_fallback(tmp_path: Path) -> None:
    outbox, _ = make_record(tmp_path)
    sheet = google_mod.FakeCallbackSheetAdapter()
    worker = worker_mod.CallbackSheetWorker(outbox=outbox, summary_provider=FailingSummary(), sheet_adapter=sheet, max_attempts=1, retry_base_seconds=0)

    result = worker.process_once()

    assert result["status"] == "sheet_delivered"
    assert sheet.rows[0][3]
    assert "secret" not in sheet.rows[0][3]
    assert "+799" not in sheet.rows[0][3]


def test_retry_and_lease_behavior_is_deterministic(tmp_path: Path) -> None:
    outbox, lead_ref = make_record(tmp_path)
    busy = outbox.lease_record(lead_ref=lead_ref, owner="w1", ttl_seconds=60)
    assert busy.status == "leased"
    assert outbox.lease_record(lead_ref=lead_ref, owner="w2", ttl_seconds=60).status == "busy"
    worker = worker_mod.CallbackSheetWorker(
        outbox=outbox,
        summary_provider=StaticSummary(),
        sheet_adapter=google_mod.FakeCallbackSheetAdapter(fail=google_mod.AppendResult(status="timeout", retryable=True, error_class="timeout")),
        owner="w1",
        retry_base_seconds=0,
    )
    result = worker.process_once()
    assert result["status"] == "retrying"
    record = read_record(tmp_path)
    assert record["sheet_delivery"]["attempts"] == 1
    assert record["sheet_delivery"]["last_error_class"] == "timeout"


def test_uncertain_append_retry_does_not_duplicate_when_ledger_has_delivery(tmp_path: Path) -> None:
    outbox, lead_ref = make_record(tmp_path)
    sheet = google_mod.FakeCallbackSheetAdapter(fail=google_mod.AppendResult(status="timeout", uncertain=True, retryable=True, error_class="timeout"))
    worker = worker_mod.CallbackSheetWorker(outbox=outbox, summary_provider=StaticSummary(), sheet_adapter=sheet, retry_base_seconds=0)
    first = worker.process_once()
    assert first["status"] == "retrying"
    assert sheet.rows == []
    sheet.fail = None
    sheet.deliveries[lead_ref] = "A7:D7"
    second = worker.process_once()
    assert second["status"] == "already_delivered"
    assert sheet.rows == []
    assert read_record(tmp_path)["sheet_delivery"]["sheet_row_ref"] == "A7:D7"


def test_ledger_failure_after_append_stops_automatic_retry(tmp_path: Path) -> None:
    outbox, _ = make_record(tmp_path)
    worker = worker_mod.CallbackSheetWorker(
        outbox=outbox,
        summary_provider=StaticSummary(),
        sheet_adapter=LedgerFailingAdapter(),
        retry_base_seconds=0,
    )

    result = worker.process_once()

    assert result["status"] == "append_uncertain"
    assert read_record(tmp_path)["sheet_delivery"]["status"] == "append_uncertain"
    assert worker.process_once()["status"] == "idle"


def test_diagnose_rejects_blank_outbox_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_CALLBACK_OUTBOX_DIR", raising=False)
    monkeypatch.setenv("NMBOT_CALLBACK_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_ID", "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ")
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_TAB", "Лист1")

    assert worker_mod.main(["--diagnose"]) == 2


def test_worker_rejects_invalid_poll_interval_before_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NMBOT_CALLBACK_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("NMBOT_CALLBACK_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_ID", "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ")
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_TAB", "Лист1")

    assert worker_mod.main(["--poll-seconds", "0"]) == 2


def test_redaction_of_phone_name_and_jivo_identifiers_from_summary_input(tmp_path: Path) -> None:
    outbox, _ = make_record(tmp_path, name="Мария", phone="+79990001122")
    record = read_record(tmp_path)
    snapshot = summary_mod.build_sanitized_summary_input(record)
    text = repr(snapshot)
    assert "+79990001122" not in text
    assert "9990001122" not in text
    assert "Мария" not in text
    assert "raw-client" not in text
    assert "secret-chat" not in text


class FakeAsyncSummaryClient:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.prompts: list[str] = []
        self.closed = False

    async def summarize_client_card(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.prompts.append(str(kwargs["prompt"]))
        return self.raw, {"model": "fake"}

    async def close(self) -> None:
        self.closed = True


def test_gateway_summary_provider_uses_sanitized_prompt_and_renders_json() -> None:
    raw = json.dumps(
        {
            "client_request_summary": "Клиент хочет подобрать квартиру и просит звонок",
            "client_criteria": ["rooms: 2", "budget: 12000000"],
            "selected_complex": "ЖК Видимый",
            "discussed_options": ["ЖК Видимый — основной интерес"],
            "operator_tasks": ["Проверить наличие и условия"],
            "important_context": ["Клиент нажал callback"],
            "unknowns": ["Актуальная цена"],
        },
        ensure_ascii=False,
    )
    fake = FakeAsyncSummaryClient(raw)
    provider = summary_mod.GatewayOvermindSummaryProvider(client_factory=lambda: fake, timeout=7)
    snapshot = {
        "params": {"rooms": 2, "budget": 12000000},
        "selected_option": {"name": "ЖК Видимый", "client_id": "raw-client"},
        "dialog_window": [
            {"role": "user", "text": "Меня зовут Мария, телефон +7 999 000-11-22"},
            {"role": "bot", "text": "Хотите звонок?"},
        ],
        "contact": {"name": "Мария", "phone": "+79990001122"},
        "payload": {"token": "secret-token", "chat_id": "secret-chat"},
    }

    text = provider.summarize(snapshot)

    assert fake.closed is True
    assert fake.prompts
    prompt = fake.prompts[0]
    assert "+7 999" not in prompt
    assert "+79990001122" not in prompt
    assert "Мария" not in prompt
    assert "raw-client" not in prompt
    assert "secret-token" not in prompt
    assert "secret-chat" not in prompt
    assert "Клиент хочет подобрать" in text
    assert "Интерес: ЖК Видимый" in text
    assert "client_request_summary" not in text
    assert "{" not in text


def test_gateway_summary_provider_falls_back_on_empty_model_text() -> None:
    fake = FakeAsyncSummaryClient("")
    provider = summary_mod.GatewayOvermindSummaryProvider(client_factory=lambda: fake)

    text = provider.summarize({"selected_option": {"name": "ЖК Видимый"}})

    assert "ЖК Видимый" in text
    assert fake.closed is True


def test_deterministic_summary_uses_canonical_snapshot_context() -> None:
    text = summary_mod.deterministic_summary_fallback(
        {
            "runtime": "v0",
            "params": {"rooms": 2, "budget": 12000000},
            "selected_option": {"name": "ЖК Выбранный"},
            "current_options": [{"name": "ЖК Первый"}, {"name": "ЖК Второй"}],
            "last_bot_question": "Хотите, чтобы специалист проверил условия?",
        }
    )

    assert "ЖК Выбранный" in text
    assert "rooms: 2" in text
    assert "budget: 12000000" in text
    assert "Хотите, чтобы специалист" in text


def test_gateway_summary_provider_rejects_nested_event_loop() -> None:
    fake = FakeAsyncSummaryClient("{}")
    provider = summary_mod.GatewayOvermindSummaryProvider(client_factory=lambda: fake)

    async def scenario() -> None:
        try:
            provider.summarize({})
        except RuntimeError as exc:
            assert "sync_context" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("expected RuntimeError")

    asyncio.run(scenario())
    assert fake.prompts == []


def _set_worker_env(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("NMBOT_CALLBACK_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("NMBOT_CALLBACK_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_ID", "1lE_aDxYGVtsl3SgFE9pZY-7ByxDu6zNkyZ1Qr1F0-EQ")
    monkeypatch.setenv("NMBOT_CALLBACK_SHEET_TAB", "Лист1")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", json.dumps({"client_email": "sa@example.test", "private_key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n"}))
    monkeypatch.setattr(worker_mod, "GoogleSheetsCallbackAdapter", lambda _config: google_mod.FakeCallbackSheetAdapter())


def test_build_worker_from_env_keeps_deterministic_default(tmp_path: Path, monkeypatch) -> None:
    _set_worker_env(monkeypatch, tmp_path)
    monkeypatch.delenv("NMBOT_CALLBACK_SUMMARY_PROVIDER", raising=False)

    worker = worker_mod.build_worker_from_env()

    assert worker.summary_provider.__class__.__name__ == "DeterministicSummaryProvider"


def test_build_worker_from_env_selects_gateway_only_by_switch(tmp_path: Path, monkeypatch) -> None:
    _set_worker_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NMBOT_CALLBACK_SUMMARY_PROVIDER", "gateway")

    worker = worker_mod.build_worker_from_env()

    assert worker.summary_provider.__class__.__name__ == "GatewayOvermindSummaryProvider"


def test_build_worker_from_env_rejects_unknown_summary_provider(tmp_path: Path, monkeypatch) -> None:
    _set_worker_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NMBOT_CALLBACK_SUMMARY_PROVIDER", "llm")

    try:
        worker_mod.build_worker_from_env()
    except worker_mod.ConfigurationError as exc:
        assert "invalid_callback_summary_provider" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ConfigurationError")
