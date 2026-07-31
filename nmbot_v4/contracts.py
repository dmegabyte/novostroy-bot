from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


V4_MODEL = "google/gemini-3.1-flash-lite-preview"
V4_PAYLOAD_STAGE = "nmbot_v4_one_prompt"
V4_MCP_SERVER = "novostroym"
V4_PROMPT_SOURCE = "prompts/v4_flat_search.txt"
V4_FAIL_CLOSED_MESSAGE = "Сейчас не получилось безопасно выполнить подбор. Попробуйте написать ещё раз — я вернусь с вариантами, когда поиск будет доступен."
V4_FAIL_CLOSED_OBJECT: dict[str, Any] = {"data": [], "message": V4_FAIL_CLOSED_MESSAGE}


class V4Error(ValueError):
    pass


@dataclass(frozen=True)
class V4State:
    revision: int = 1
    last_valid_ids: tuple[int, ...] = ()
    last_message_summary: str = ""
    pending_followup: str | None = None
    contact_name: str = ""
    contact_phone_redacted: str = ""
    contact_consent: bool = False
    callback_ref: str = ""

    @classmethod
    def clean(cls) -> "V4State":
        return cls()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "V4State":
        data = value if isinstance(value, Mapping) else {}
        ids_raw = data.get("last_valid_ids")
        ids: list[int] = []
        if isinstance(ids_raw, list):
            for item in ids_raw[:20]:
                if isinstance(item, int) and not isinstance(item, bool) and item > 0:
                    ids.append(item)
        summary = _safe_v4_text(data.get("last_message_summary"), limit=240)
        pending_raw = str(data.get("pending_followup") or "").strip()
        pending = pending_raw if pending_raw in {"contact_name", "contact_phone"} else None
        return cls(
            revision=1,
            last_valid_ids=tuple(ids),
            last_message_summary=summary,
            pending_followup=pending,
            contact_name=_safe_v4_text(data.get("contact_name"), limit=80),
            contact_phone_redacted=_safe_v4_redacted_contact(data.get("contact_phone_redacted")),
            contact_consent=bool(data.get("contact_consent", False)),
            callback_ref=_safe_v4_ref(data.get("callback_ref")),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "revision": 1,
            "last_valid_ids": list(self.last_valid_ids[:20]),
            "last_message_summary": self.last_message_summary[:240],
        }
        if self.pending_followup in {"contact_name", "contact_phone"}:
            data["pending_followup"] = self.pending_followup
        if self.contact_name:
            data["contact_name"] = _safe_v4_text(self.contact_name, limit=80)
        if self.contact_phone_redacted:
            redacted = _safe_v4_redacted_contact(self.contact_phone_redacted)
            if redacted:
                data["contact_phone_redacted"] = redacted
        if self.contact_consent:
            data["contact_consent"] = True
        if self.callback_ref:
            ref = _safe_v4_ref(self.callback_ref)
            if ref:
                data["callback_ref"] = ref
        return data

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "revision": 1,
            "last_valid_ids": list(self.last_valid_ids[:20]),
            "last_message_summary": self.last_message_summary[:240],
        }


def _safe_v4_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?<!\d)(?:\+?\d[\s().-]*){10,15}(?!\d)", "[redacted-contact]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted-email]", text)
    return text[:limit]


def _safe_v4_redacted_contact(value: Any) -> str:
    text = _safe_v4_text(value, limit=80)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 7:
        return ""
    return text


def _safe_v4_ref(value: Any) -> str:
    text = str(value or "").strip()
    return text[:120] if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", text) else ""
