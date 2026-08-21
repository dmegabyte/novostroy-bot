#!/usr/bin/env python3
"""Safe local worker for callback summary + Google Sheets append orchestration."""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nmbot_callback_summary import build_sanitized_summary_input, deterministic_summary_fallback, SummaryProvider
from nmbot_crm_outbox import LocalCallbackOutbox
from nmbot_callback_crm import CallbackCRMAdapter
from nmbot_google_sheets import AppendResult, CallbackSheetAdapter, ConfigurationError, GoogleSheetsCallbackAdapter, GoogleSheetsConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CallbackSheetWorker:
    def __init__(
        self,
        *,
        outbox: LocalCallbackOutbox,
        summary_provider: SummaryProvider,
        sheet_adapter: CallbackSheetAdapter,
        owner: str = "worker",
        max_attempts: int = 3,
        retry_base_seconds: int = 30,
        crm_adapter: CallbackCRMAdapter | None = None,
    ) -> None:
        self.outbox = outbox
        self.summary_provider = summary_provider
        self.sheet_adapter = sheet_adapter
        self.owner = owner
        self.max_attempts = max(1, max_attempts)
        self.retry_base_seconds = max(0, retry_base_seconds)
        self.crm_adapter = crm_adapter

    def _retry_delay(self, attempts: int) -> int:
        return self.retry_base_seconds * (2 ** max(0, attempts - 1))

    def _process_sheet_once(self) -> dict[str, Any]:
        due = self.outbox.iter_due_records(owner=self.owner)
        if not due:
            return {"processed": 0, "status": "idle"}
        lead_ref = str(due[0].get("lead_ref") or "")
        lease = self.outbox.lease_record(lead_ref=lead_ref, owner=self.owner)
        if lease.status != "leased" or not lease.record:
            return {"processed": 0, "status": lease.status, "lead_ref": lead_ref}
        record = lease.record
        delivery = record.setdefault("sheet_delivery", {})
        attempts = int(delivery.get("attempts") or 0)

        snapshot = build_sanitized_summary_input(record)
        summary = record.setdefault("summary", {})
        if not str(summary.get("text") or "").strip():
            try:
                text = str(self.summary_provider.summarize(snapshot) or "").strip()
                if not text:
                    raise RuntimeError("empty_summary")
                summary.update({"status": "ready", "text": text[:1000], "attempts": int(summary.get("attempts") or 0) + 1})
            except Exception:
                if attempts + 1 < self.max_attempts:
                    self.outbox.schedule_retry(record, error_class="summary_retryable", delay_seconds=self._retry_delay(attempts + 1))
                    return {"processed": 1, "status": "retrying", "stage": "summary", "lead_ref": lead_ref}
                summary.update({"status": "fallback", "text": deterministic_summary_fallback(snapshot), "attempts": int(summary.get("attempts") or 0) + 1})
            self.outbox.update_record(record)

        try:
            lookup = self.sheet_adapter.lookup_delivery(lead_ref=lead_ref)
            if lookup.delivered:
                self.outbox.mark_delivered(record, row_ref=lookup.row_ref)
                return {"processed": 1, "status": "already_delivered", "lead_ref": lead_ref}

            ensure = self.sheet_adapter.ensure_headers()
            if ensure.status != "ok":
                if ensure.retryable and attempts + 1 < self.max_attempts:
                    self.outbox.schedule_retry(record, error_class=ensure.error_class or "header_retryable", delay_seconds=self._retry_delay(attempts + 1))
                    return {"processed": 1, "status": "retrying", "stage": "sheet_headers", "lead_ref": lead_ref}
                self.outbox.mark_failed(record, error_class=ensure.error_class or "sheet_schema_error")
                return {"processed": 1, "status": "failed", "stage": "sheet_headers", "lead_ref": lead_ref}

            contact = record.get("contact") if isinstance(record.get("contact"), dict) else {}
            result = self.sheet_adapter.append_callback(
                created_at_msk=str(record.get("created_at_msk") or ""),
                phone=str(contact.get("phone") or record.get("phone") or ""),
                name=str(contact.get("name") or ""),
                summary=str(record.get("summary", {}).get("text") or ""),
                lead_ref=lead_ref,
            )
        except ConfigurationError as exc:
            self.outbox.mark_failed(record, error_class=str(exc)[:80] or "adapter_configuration_error")
            return {"processed": 1, "status": "failed", "stage": "sheet_config", "lead_ref": lead_ref}
        except Exception:
            if attempts + 1 < self.max_attempts:
                self.outbox.schedule_retry(record, error_class="adapter_exception", delay_seconds=self._retry_delay(attempts + 1))
                return {"processed": 1, "status": "retrying", "stage": "sheet_exception", "lead_ref": lead_ref}
            self.outbox.mark_failed(record, error_class="adapter_exception")
            return {"processed": 1, "status": "failed", "stage": "sheet_exception", "lead_ref": lead_ref}
        if result.status == "ok" and result.row_ref:
            try:
                self.sheet_adapter.record_delivery(lead_ref=lead_ref, row_ref=result.row_ref, delivered_at=_now_iso())
            except Exception:
                self.outbox.mark_append_uncertain(record, row_ref=result.row_ref)
                return {"processed": 1, "status": "append_uncertain", "lead_ref": lead_ref}
            self.outbox.mark_delivered(record, row_ref=result.row_ref)
            return {"processed": 1, "status": "sheet_delivered", "lead_ref": lead_ref}
        if result.uncertain:
            # Do not append again in the same cycle. A later retry first checks the private ledger.
            self.outbox.schedule_retry(record, error_class=result.error_class or "append_uncertain", delay_seconds=self._retry_delay(attempts + 1))
            return {"processed": 1, "status": "retrying", "stage": "sheet_uncertain", "lead_ref": lead_ref}
        if result.retryable and attempts + 1 < self.max_attempts:
            self.outbox.schedule_retry(record, error_class=result.error_class or "append_retryable", delay_seconds=self._retry_delay(attempts + 1))
            return {"processed": 1, "status": "retrying", "stage": "sheet", "lead_ref": lead_ref}
        self.outbox.mark_failed(record, error_class=result.error_class or "append_failed")
        return {"processed": 1, "status": "failed", "lead_ref": lead_ref}

    def _process_crm_once(self) -> dict[str, Any]:
        if self.crm_adapter is None:
            return {"processed": 0, "status": "idle"}
        due = self.outbox.iter_due_records(owner=self.owner, sink="crm")
        if not due:
            return {"processed": 0, "status": "idle"}
        lead_ref = str(due[0].get("lead_ref") or "")
        lease = self.outbox.lease_record(lead_ref=lead_ref, owner=self.owner, sink="crm")
        if lease.status != "leased" or not lease.record:
            return {"processed": 0, "status": lease.status, "lead_ref": lead_ref}
        record = lease.record
        delivery = record.setdefault("crm_delivery", {})
        attempts = int(delivery.get("attempts") or 0)
        summary = record.setdefault("summary", {})
        if not str(summary.get("text") or "").strip():
            snapshot = build_sanitized_summary_input(record)
            try:
                text = str(self.summary_provider.summarize(snapshot) or "").strip()
                if not text:
                    raise RuntimeError("empty_summary")
                summary.update({"status": "ready", "text": text[:1000], "attempts": int(summary.get("attempts") or 0) + 1})
            except Exception:
                summary.update({"status": "fallback", "text": deterministic_summary_fallback(snapshot), "attempts": int(summary.get("attempts") or 0) + 1})
            self.outbox.update_record(record)
        contact = record.get("contact") if isinstance(record.get("contact"), dict) else {}
        result = self.crm_adapter.send_callback(
            phone=str(contact.get("phone") or record.get("phone") or ""),
            name=str(contact.get("name") or ""),
            summary=str(summary.get("text") or ""),
        )
        if result.status == "ok":
            self.outbox.mark_crm_delivered(record, receipt=result.receipt)
            return {"processed": 1, "status": "crm_delivered", "lead_ref": lead_ref}
        if result.uncertain:
            self.outbox.mark_crm_uncertain(record, error_class=result.error_class)
            return {"processed": 1, "status": "crm_uncertain", "lead_ref": lead_ref}
        if result.retryable and attempts + 1 < self.max_attempts:
            self.outbox.schedule_retry(record, error_class=result.error_class or "crm_retryable", delay_seconds=self._retry_delay(attempts + 1), sink="crm")
            return {"processed": 1, "status": "retrying", "stage": "crm", "lead_ref": lead_ref}
        self.outbox.mark_failed(record, error_class=result.error_class or "crm_failed", sink="crm")
        return {"processed": 1, "status": "failed", "stage": "crm", "lead_ref": lead_ref}

    def process_once(self) -> dict[str, Any]:
        sheet_result = self._process_sheet_once()
        crm_result = self._process_crm_once()
        return sheet_result if sheet_result.get("processed") else crm_result


def build_worker_from_env() -> CallbackSheetWorker:
    outbox_value = str(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR") or "").strip()
    if not outbox_value:
        raise ConfigurationError("missing_callback_outbox_dir")
    outbox_dir = Path(outbox_value).expanduser()
    config = GoogleSheetsConfig.from_env()
    adapter = GoogleSheetsCallbackAdapter(config)
    provider_name = str(os.getenv("NMBOT_CALLBACK_SUMMARY_PROVIDER") or "deterministic").strip().lower()
    if provider_name in ("", "deterministic"):
        from nmbot_callback_summary import DeterministicSummaryProvider

        summary_provider: SummaryProvider = DeterministicSummaryProvider()
    elif provider_name == "gateway":
        from nmbot_callback_summary import GatewayOvermindSummaryProvider

        summary_provider = GatewayOvermindSummaryProvider()
    else:
        raise ConfigurationError("invalid_callback_summary_provider")

    return CallbackSheetWorker(
        outbox=LocalCallbackOutbox(outbox_dir),
        summary_provider=summary_provider,
        sheet_adapter=adapter,
        crm_adapter=CallbackCRMAdapter(),
        max_attempts=int(os.getenv("NMBOT_CALLBACK_MAX_ATTEMPTS", "3") or "3"),
        retry_base_seconds=int(os.getenv("NMBOT_CALLBACK_RETRY_BASE_SECONDS", "30") or "30"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NMBOT callback sheet worker")
    parser.add_argument("--diagnose", action="store_true", help="validate config only; no network")
    parser.add_argument("--loop", action="store_true", help="poll the private outbox until stopped")
    parser.add_argument("--poll-seconds", type=int, default=5, help="outbox polling interval for --loop")
    args = parser.parse_args(argv)
    try:
        if args.diagnose:
            GoogleSheetsConfig.from_env()
            outbox_value = str(os.getenv("NMBOT_CALLBACK_OUTBOX_DIR") or "").strip()
            if not outbox_value:
                raise ConfigurationError("missing_callback_outbox_dir")
            print("OK: callback worker configuration is syntactically valid; google network not used")
            return 0
        if args.poll_seconds < 1:
            raise ConfigurationError("invalid_callback_poll_seconds")
        worker = build_worker_from_env()
        if args.loop:
            while True:
                result = worker.process_once()
                print({k: v for k, v in result.items() if k != "summary"}, flush=True)
                time.sleep(args.poll_seconds)
        result = worker.process_once()
        print({k: v for k, v in result.items() if k != "summary"})
        return 0
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
