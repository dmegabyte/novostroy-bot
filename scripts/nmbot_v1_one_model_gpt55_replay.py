#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Iterable, Mapping

ROOT = Path(os.getenv("NMBOT_REPLAY_ROOT", "/tmp/opencode-run-nmbot/project"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.nmbot_runtime_adapter import _extract_phone_v2  # noqa: E402


MODEL = "openai/gpt-5.5"
TEMPERATURE = 0.3
MAX_TOKENS = 1800
TIMEOUT_SECONDS = 90
SOURCE_FIXTURE = ROOT / "data/v0_answer_writer_replay/cases.v1.jsonl"
TARGET_FIXTURE = ROOT / "data/v1_one_model_gpt55/target_regression.v1.jsonl"
PROMPT_SOURCE = "prompts/candidates/v1_one_model_gpt55_experiment_v1.txt"
PROMPT_PATH = ROOT / PROMPT_SOURCE
DEFAULT_OUT_DIR = ROOT / "tmp/v1_one_model_gpt55"
QUERY_MARKER = "V1_ONE_MODEL_INPUT="
PAYLOAD_STAGE = "v1_one_model_gpt55_replay_experiment"
NEXT_ACTIONS = {"none", "inspect_option", "clarify_search", "offer_operator", "request_phone"}
REQUIRED_PROMPT_MARKERS = (
    "V1_ONE_MODEL_INPUT=",
    "Любое утверждение об объекте",
    "Если клиент спрашивает об ипотеке",
    "Не обрабатывай телефон сам",
    '"visible_options"',
)
INTERNAL_TERMS_RE = re.compile(r"(?i)\b(?:json|markdown|html|developer prompt|system prompt|planner|mcp|search evidence|raw_search_response|payload|schema|next_action|visible_options)\b")
QUESTION_RE = re.compile(r"[?？]")
MONEY_RE = re.compile(r"(?:\b\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)|\b\d+[,.]\d+\s*млн)", re.IGNORECASE)
MORTGAGE_INTENT_RE = re.compile(r"(?i)(ипотек|семеукна|семейн|первоначальн|\bпв\b|взнос|оплат|рассроч)")
MORTGAGE_FIELD_RE = re.compile(r"(?i)(mortgage|ипотек|payment|оплат|рассроч|down[_ -]?payment|первоначальн|\bпв\b|взнос)")
MORTGAGE_CONFIRM_RE = re.compile(r"(?i)(семейн\w*\s+ипотек\w*\s+(?:возмож|доступ|есть|подход)|ипотек\w*\s+(?:возмож|доступн|есть|одобрен|подходит)|можно\s+(?:взять|оформить).*ипотек|доступна\s+.*ипотек)")
OPTION_FOUND_RE = re.compile(r"(?i)(нашла|подобрала|есть|покажу|вариант[а-я]*)")
PROJECT_PREFIX_RE = re.compile(r"(?iu)\b(?:ЖК|жилой\s+комплекс|жилой\s+квартал|жилой\s+район|апарт-отель|МФК)\s+[«\"“„]?([А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]*(?:\s+[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z0-9-]*){0,4})[»\"“”]?")
TITLE_CASE_PROJECT_RE = re.compile(r"(?u)(?<![\w-])([А-ЯЁA-Z][а-яёa-z0-9-]{2,}(?:\s+[А-ЯЁA-Z][а-яёa-z0-9-]{2,}){1,3})(?![\w-])")
CARD_LIKE_RE = re.compile(r"(?im)(?:^\s*\d+[\).]\s+|\b(?:от\s+)?\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)|\b(?:студия|квартира|евро\d|комнат|м²|кв\.?)\b)")
QUOTE_TRANS = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "`": "'"})
ALLOWED_FACTUAL_GEOGRAPHIES = {"москва", "новая москва", "московская область", "санкт-петербург"}
LOCATION_KEYS = {"location", "location_id", "district", "area", "region", "city", "metro", "property_metro", "address", "street"}
LOCATION_LIKE_RE = re.compile(
    r"(?iu)\b(?:москв|област|район|округ|дегунино|метро|улиц|шоссе|проспект|переулок|санкт-петербург|петербург|спб)\b"
)
GENERIC_TITLE_STARTS = {
    "В текущих",
    "В базе",
    "В данных",
    "В Москве",
    "В Новой",
    "По Москве",
    "По Санкт",
    "Санкт-Петербург",
    "Московская область",
    "Новая Москва",
    "Оператор может",
    "Проверить другой",
    "Есть подтвержд",
    "Рассмотреть Москву",
}
LEADING_CANDIDATE_WORD_RE = re.compile(r"(?iu)^(?:есть|нашла|подобрала|покажу)\s+")
GEOGRAPHY_WORD_RE = re.compile(r"(?iu)^(?:москва|москву|москве|новая|новой|московская|московскую|область|области|санкт-петербург|петербург|петербургу|спб|или)$")
CARD_CONTEXT_RE = re.compile(
    r"(?iu)(?:^\s*\d+[\).]\s+|[—–-]\s*(?:от\s+)?\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)|\b(?:цена|стоимость|проект|жк|квартира|студия|м²|кв\.?)\b)"
)


@dataclass(frozen=True)
class Case:
    case_id: str
    record: dict[str, Any]
    evidence: dict[str, Any]
    source_refs: list[str]
    corpus: str


def prompt_identity() -> dict[str, Any]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return {"source": PROMPT_SOURCE, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def prompt_contract() -> dict[str, Any]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_PROMPT_MARKERS if marker not in text]
    return {"ok": not missing, "missing_markers": missing}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid jsonl") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no}: row must be object")
        rows.append(row)
    return rows


def _parse_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("raw_search_response")
    if isinstance(raw, dict):
        data = dict(raw)
    else:
        try:
            data = json.loads(str(raw or "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{record.get('case_id')}: raw_search_response invalid json") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{record.get('case_id')}: evidence must be object")
    facts = data.get("facts")
    near = data.get("near")
    if facts is None:
        data["facts"] = []
    if near is None:
        data["near"] = []
    if not isinstance(data["facts"], list) or not isinstance(data["near"], list):
        raise ValueError(f"{record.get('case_id')}: facts/near must be lists")
    data.setdefault("missing", [])
    data.setdefault("params", {})
    return data


def _source_refs(record: Mapping[str, Any], fallback_path: Path) -> list[str]:
    refs = record.get("source_refs")
    if isinstance(refs, list):
        return [str(ref) for ref in refs if str(ref).strip()]
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    path = str(source.get("path") or fallback_path.relative_to(ROOT))
    line = source.get("line")
    return [f"{path}:{line}" if line is not None else path]


def load_cases(case_id: str | None = None) -> list[Case]:
    real_rows = _load_jsonl(SOURCE_FIXTURE)
    target_rows = _load_jsonl(TARGET_FIXTURE)
    if len(real_rows) != 10:
        raise ValueError(f"expected exactly 10 real rows, got {len(real_rows)}")
    if len(target_rows) != 1:
        raise ValueError(f"expected exactly 1 target regression row, got {len(target_rows)}")
    cases: list[Case] = []
    for row in real_rows:
        cases.append(Case(str(row.get("case_id") or ""), row, _parse_evidence(row), _source_refs(row, SOURCE_FIXTURE), "real_v0_replay"))
    for row in target_rows:
        cases.append(Case(str(row.get("case_id") or ""), row, _parse_evidence(row), _source_refs(row, TARGET_FIXTURE), "derived_target_regression"))
    if any(not c.case_id for c in cases):
        raise ValueError("case_id is required")
    if len({c.case_id for c in cases}) != len(cases):
        raise ValueError("duplicate case_id")
    if case_id:
        cases = [c for c in cases if c.case_id == case_id]
        if not cases:
            raise ValueError(f"unknown case_id={case_id}")
    return cases


def _safe_state_summary(record: Mapping[str, Any]) -> Any:
    value = record.get("state_summary")
    if value is not None:
        return value
    assignment = record.get("assignment") if isinstance(record.get("assignment"), dict) else {}
    response_job = assignment.get("response_job") if isinstance(assignment.get("response_job"), dict) else {}
    return {"source_case_id": record.get("case_id"), "response_job_kind": response_job.get("kind"), "note": "saved replay context only"}


def model_input(case: Case) -> dict[str, Any]:
    return {
        "client_message": str(case.record.get("client_message") or case.record.get("user_text") or ""),
        "previous_assistant_message": str(case.record.get("previous_assistant_message") or ""),
        "state_summary": _safe_state_summary(case.record),
        "evidence": case.evidence,
    }


def request_payload(case: Case, *, prompt: str | None = None) -> dict[str, Any]:
    return {
        "_payload_stage": PAYLOAD_STAGE,
        "service": "openrouter",
        "model": MODEL,
        "system_prompt": prompt if prompt is not None else PROMPT_PATH.read_text(encoding="utf-8"),
        "query": QUERY_MARKER + json.dumps(model_input(case), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "parameters": {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
        **({"external_api_key": os.getenv("OPENROUTER_API_KEY", "")} if os.getenv("OPENROUTER_API_KEY") else {}),
    }


def _evidence_items(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for group in ("facts", "near"):
        values = evidence.get(group)
        if isinstance(values, list):
            out.extend(item for item in values if isinstance(item, dict))
    return out


def evidence_names(evidence: Mapping[str, Any]) -> set[str]:
    return {str(item.get("name") or "").strip() for item in _evidence_items(evidence) if str(item.get("name") or "").strip()}


def evidence_text(evidence: Mapping[str, Any], client_message: str = "") -> str:
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n" + client_message


def _norm_mention(value: str) -> str:
    text = str(value or "").translate(QUOTE_TRANS).replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip().strip('"\'.,:;!?()[]{}')
    text = re.sub(r"^(?:жк|жилой комплекс|жилой квартал|жилой район|апарт-отель|мфк)\s+", "", text, flags=re.IGNORECASE)
    return text.casefold()


def _project_aliases(item: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for key in ("name", "project", "title", "complex", "alias", "aliases"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            aliases.add(value)
        elif isinstance(value, list):
            aliases.update(str(v) for v in value if str(v).strip())
    return aliases


def _field_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item
            elif isinstance(item, Mapping):
                for nested_value in item.values():
                    yield from _field_values(nested_value)
    elif isinstance(value, Mapping):
        for nested_value in value.values():
            yield from _field_values(nested_value)


def _grounded_non_project_terms(evidence: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for item in _evidence_items(evidence):
        for key, value in item.items():
            key_text = str(key or "").casefold()
            is_location_field = key_text in LOCATION_KEYS or any(part in key_text for part in ("location", "district", "metro", "address", "street"))
            if not is_location_field:
                continue
            for candidate in _field_values(value):
                text = str(candidate or "").strip()
                if not text:
                    continue
                if key_text == "location" or LOCATION_LIKE_RE.search(text):
                    norm = _norm_mention(text)
                    if norm:
                        terms.add(norm)
    return terms


def _grounded_project_terms(evidence: Mapping[str, Any], client_message: str = "") -> tuple[set[str], set[str]]:
    allowed: set[str] = set()
    named: set[str] = set()
    for item in _evidence_items(evidence):
        for alias in _project_aliases(item):
            norm = _norm_mention(alias)
            if norm:
                allowed.add(norm)
                named.add(norm)
    support = evidence_text(evidence, client_message)
    for match in PROJECT_PREFIX_RE.finditer(support):
        norm = _norm_mention(match.group(1))
        if norm:
            allowed.add(norm)
    for geo in ALLOWED_FACTUAL_GEOGRAPHIES:
        if geo in _norm_mention(support):
            allowed.add(geo)
    allowed.update(_grounded_non_project_terms(evidence))
    return allowed, named


def _title_case_in_card_context(response: str, start: int, end: int) -> bool:
    line_start = response.rfind("\n", 0, start) + 1
    line_end = response.find("\n", end)
    if line_end == -1:
        line_end = len(response)
    line = response[line_start:line_end]
    relative_start = start - line_start
    before = line[:relative_start]
    after = line[relative_start:]
    nearby = line[max(0, relative_start - 80) : min(len(line), relative_start + 120)]
    return bool(
        re.search(r"^\s*\d+[\).]\s*$", before)
        or re.search(r"^\s*[-*•]\s*$", before)
        or CARD_CONTEXT_RE.search(nearby)
        or re.match(r"(?iu)\s*[—–-]\s*(?:от\s+)?\d+[\d\s]*(?:[,.]\d+)?\s*(?:млн|руб|₽)", after)
    )


def _response_project_mentions(response: str) -> set[str]:
    mentions: set[str] = set()
    for fragment in re.findall(r"[«\"“„]([^»\"“”]{3,80})[»\"“”]", response):
        norm = _norm_mention(fragment)
        if norm:
            mentions.add(norm)
    for match in PROJECT_PREFIX_RE.finditer(response):
        norm = _norm_mention(match.group(1))
        if norm:
            mentions.add(norm)
    for match in TITLE_CASE_PROJECT_RE.finditer(response):
        if not _title_case_in_card_context(response, match.start(1), match.end(1)):
            continue
        fragment = re.sub(r"\s+(?:подтверждённых|подтвержденных|данных|вариантов|условия|ипотеке).*$", "", match.group(1), flags=re.IGNORECASE).strip()
        fragment = LEADING_CANDIDATE_WORD_RE.sub("", fragment).strip()
        norm = _norm_mention(fragment)
        if not norm or norm in ALLOWED_FACTUAL_GEOGRAPHIES:
            continue
        if all(GEOGRAPHY_WORD_RE.match(word) for word in fragment.split()):
            continue
        if any(fragment.startswith(start) for start in GENERIC_TITLE_STARTS):
            continue
        mentions.add(norm)
    return mentions


def evidence_has_mortgage(evidence: Mapping[str, Any]) -> bool:
    for item in _evidence_items(evidence):
        for key, value in item.items():
            if MORTGAGE_FIELD_RE.search(str(key)) or MORTGAGE_FIELD_RE.search(str(value)):
                return True
    params = evidence.get("params")
    if isinstance(params, dict):
        return any(MORTGAGE_FIELD_RE.search(str(k)) or MORTGAGE_FIELD_RE.search(str(v)) for k, v in params.items())
    return False


def _strict_json_object(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(str(raw or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(data, dict):
        raise ValueError("not_object")
    if set(data) != {"response", "visible_options", "next_action"}:
        raise ValueError("wrong_keys")
    return data


def _question_count(text: str) -> int:
    return len(QUESTION_RE.findall(text))


def validate_answer(data: Mapping[str, Any], case: Case) -> list[str]:
    errors: list[str] = []
    response = data.get("response")
    visible = data.get("visible_options")
    next_action = data.get("next_action")
    if not isinstance(response, str) or not response.strip():
        errors.append("response_empty")
        response = ""
    if isinstance(response, str) and len(response) > 1800:
        errors.append("response_too_long")
    if next_action not in NEXT_ACTIONS:
        errors.append("next_action_invalid")
    if not isinstance(visible, list):
        errors.append("visible_options_not_list")
        visible = []
    if isinstance(visible, list) and len(visible) > 3:
        errors.append("visible_options_too_many")
    names = evidence_names(case.evidence)
    visible_names: list[str] = []
    for idx, item in enumerate(visible if isinstance(visible, list) else []):
        if not isinstance(item, dict) or set(item) != {"name"} or not isinstance(item.get("name"), str):
            errors.append(f"visible_option_{idx}_bad_shape")
            continue
        name = item["name"].strip()
        visible_names.append(name)
        if name not in names:
            errors.append(f"visible_option_{idx}_not_in_evidence")
        if name and name not in str(response):
            errors.append(f"visible_option_{idx}_not_mentioned")
    if _question_count(str(response)) > 1:
        errors.append("too_many_questions")
    if INTERNAL_TERMS_RE.search(str(response)):
        errors.append("internal_terms_visible")
    if re.search(r"<[^>]+>|```|^\s*[-*#]", str(response), re.MULTILINE):
        errors.append("markup_visible")
    support = evidence_text(case.evidence, str(case.record.get("client_message") or ""))
    for money in MONEY_RE.findall(str(response)):
        if money.strip() and money.strip() not in support:
            errors.append(f"money_not_grounded:{money.strip()[:40]}")
            break
    quoted = re.findall(r"[«\"]([^»\"]{3,80})[»\"]", str(response))
    for fragment in quoted:
        full_candidates = {fragment, f"ЖК «{fragment}»", f"Жилой квартал «{fragment}»"}
        if not (full_candidates & names) and fragment not in support:
            errors.append(f"quoted_name_not_grounded:{fragment[:40]}")
            break
    allowed_mentions, evidence_project_names = _grounded_project_terms(case.evidence, str(case.record.get("client_message") or ""))
    response_mentions = _response_project_mentions(str(response))
    unknown_mentions = sorted(mention for mention in response_mentions if mention not in allowed_mentions)
    if unknown_mentions:
        errors.append(f"unknown_project_mention:{unknown_mentions[0][:40]}")
    visible_norms = {_norm_mention(name) for name in visible_names if _norm_mention(name)}
    if visible_names and evidence_project_names:
        missing_visible = sorted(mention for mention in response_mentions if mention in evidence_project_names and mention not in visible_norms)
        if missing_visible:
            errors.append(f"project_mention_not_visible:{missing_visible[0][:40]}")
    if not visible_names and CARD_LIKE_RE.search(str(response)) and response_mentions:
        errors.append("card_like_output_without_visible_options")
    facts = case.evidence.get("facts") if isinstance(case.evidence.get("facts"), list) else []
    near = case.evidence.get("near") if isinstance(case.evidence.get("near"), list) else []
    if not facts and not near and OPTION_FOUND_RE.search(str(response)):
        errors.append("empty_evidence_option_claim")
    client = str(case.record.get("client_message") or "")
    if MORTGAGE_INTENT_RE.search(client + "\n" + str(response)) and not evidence_has_mortgage(case.evidence):
        if MORTGAGE_CONFIRM_RE.search(str(response)):
            errors.append("unsupported_mortgage_confirmation")
        if visible_names:
            errors.append("mortgage_followup_repeated_cards_without_terms")
    return errors


def parse_and_validate(raw: str, case: Case) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = _strict_json_object(raw)
    except ValueError as exc:
        return None, [str(exc)]
    errors = validate_answer(data, case)
    return (dict(data) if not errors else None), errors


def fallback_answer(case: Case, errors: Iterable[str]) -> dict[str, Any]:
    client = str(case.record.get("client_message") or "")
    facts = case.evidence.get("facts") if isinstance(case.evidence.get("facts"), list) else []
    near = case.evidence.get("near") if isinstance(case.evidence.get("near"), list) else []
    if MORTGAGE_INTENT_RE.search(client) and not evidence_has_mortgage(case.evidence):
        return {
            "response": "В текущих данных условия по ипотеке не подтверждены. Оператор может проверить их по выбранным или по всем текущим вариантам — проверить?",
            "visible_options": [],
            "next_action": "offer_operator",
        }
    if not facts and not near:
        return {"response": "В текущих данных подтверждённых вариантов нет. Проверить другой район или бюджет?", "visible_options": [], "next_action": "clarify_search"}
    return {"response": "Не хочу рисковать неподтверждёнными деталями. Оператор может аккуратно проверить варианты по текущему запросу — передать?", "visible_options": [], "next_action": "offer_operator"}


def phone_bypass_result(case: Case) -> dict[str, Any] | None:
    phone = _extract_phone_v2(str(case.record.get("client_message") or ""))
    if not phone:
        return None
    answer = {
        "response": "Симуляция: валидный номер перехвачен кодом; callback не создавался.",
        "visible_options": [],
        "next_action": "none",
    }
    return {
        "case_id": case.case_id,
        "corpus": case.corpus,
        "source_refs": case.source_refs,
        "published": False,
        "provider_called": False,
        "callback_simulated_only": True,
        "result": answer,
        "checks": {"status": "phone_bypass_simulated", "validation_errors": []},
        "duration_ms": 0,
    }


def dry_row(case: Case) -> dict[str, Any]:
    payload = request_payload(case, prompt=PROMPT_PATH.read_text(encoding="utf-8"))
    return {
        "case_id": case.case_id,
        "corpus": case.corpus,
        "source_refs": case.source_refs,
        "published": False,
        "provider_called": False,
        "callback_simulated_only": False,
        "result": fallback_answer(case, ["dry_run"]),
        "checks": {
            "status": "dry_run",
            "model": payload.get("model"),
            "payload_stage": payload.get("_payload_stage"),
            "query_marker": str(payload.get("query") or "").startswith(QUERY_MARKER),
            "has_mcp_servers": "mcp_servers" in payload,
            "temperature": payload.get("parameters", {}).get("temperature"),
        },
        "duration_ms": 0,
    }


async def run_case(case: Case, gateway: Any | None = None) -> dict[str, Any]:
    bypass = phone_bypass_result(case)
    if bypass:
        return bypass
    start = monotonic()
    client = gateway
    close_client = False
    try:
        if client is None:
            from scripts.nmbot_gateway_client import OvermindClient  # noqa: WPS433

            client = OvermindClient()
            close_client = True
        headers = {"Authorization": f"Bearer {os.getenv('GATEWAY_POLL_TOKEN') or os.getenv('OVERMIND_TOKEN') or ''}"}
        raw, meta = await client._run_gateway_request(request_payload(case), headers, TIMEOUT_SECONDS)
        provider_called = True
        if isinstance(meta, dict) and meta.get("_safe_fallback"):
            raise RuntimeError("safe_fallback")
        parsed, errors = parse_and_validate(raw, case)
    except Exception as exc:  # do not expose provider/raw body
        provider_called = True
        parsed = None
        errors = [f"provider_or_parse_error:{exc.__class__.__name__}"]
    finally:
        if close_client and client is not None and hasattr(client, "close"):
            await client.close()
    duration_ms = int((monotonic() - start) * 1000)
    published = parsed is not None
    result = parsed if parsed is not None else fallback_answer(case, errors)
    return {
        "case_id": case.case_id,
        "corpus": case.corpus,
        "source_refs": case.source_refs,
        "published": published,
        "provider_called": provider_called,
        "callback_simulated_only": False,
        "result": result,
        "checks": {"status": "passed" if published else "fallback", "validation_errors": list(errors)[:8]},
        "duration_ms": duration_ms,
    }


async def run_all(cases: list[Case], *, parallelism: int, gateway: Any | None = None) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(parallelism)

    async def one(case: Case) -> dict[str, Any]:
        async with sem:
            return await run_case(case, gateway=gateway)

    tasks = [asyncio.create_task(one(case)) for case in cases]
    return list(await asyncio.gather(*tasks))


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        return {str(k)[:80]: _bounded(v, depth=depth + 1) for k, v in list(value.items())[:80] if "raw" not in str(k).lower() and "secret" not in str(k).lower() and "token" not in str(k).lower()}
    if isinstance(value, list):
        return [_bounded(v, depth=depth + 1) for v in value[:80]]
    if isinstance(value, str):
        return value[:1800]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def assert_no_secrets(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, default=str)
    for key in ("OPENROUTER_API_KEY", "OVERMIND_TOKEN", "GATEWAY_POLL_TOKEN"):
        secret = os.getenv(key)
        if secret and secret in text:
            raise RuntimeError(f"secret leaked: {key}")


def write_outputs(rows: list[dict[str, Any]], *, out_dir: Path, stem: str, mode: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"{stem}_results.jsonl"
    md_path = out_dir / f"{stem}_report.md"
    safe_rows = [_bounded(row) for row in rows]
    assert_no_secrets(safe_rows)
    jsonl_path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in safe_rows) + "\n", encoding="utf-8")
    lines = [f"# V1 one-model GPT-5.5 replay — {mode}", "", f"Prompt: `{PROMPT_SOURCE}`", f"Model: `{MODEL}`", "", "| case | source | old response | new response | checks | duration ms |", "|---|---|---|---|---|---|"]
    by_id = {row["case_id"]: row for row in safe_rows}
    for case in load_cases():
        if case.case_id not in by_id:
            continue
        row = by_id[case.case_id]
        old = str(case.record.get("old_response_text") or "").replace("|", "\\|").replace("\n", "<br>")[:500]
        new = str(row.get("result", {}).get("response") or "").replace("|", "\\|").replace("\n", "<br>")[:500]
        checks = json.dumps(row.get("checks", {}), ensure_ascii=False, sort_keys=True).replace("|", "\\|")[:500]
        refs = ", ".join(case.source_refs).replace("|", "\\|")
        lines.append(f"| `{case.case_id}` | {refs} | {old} | {new} | {checks} | {row.get('duration_ms', 0)} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl_path, md_path


def validate_command(case_id: str | None = None) -> tuple[int, dict[str, Any]]:
    cases = load_cases(case_id)
    prompt = prompt_contract()
    payload = request_payload(cases[0]) if cases else {}
    errors: list[str] = []
    if not prompt["ok"]:
        errors.append("prompt_contract")
    if payload.get("model") != MODEL:
        errors.append("model_pin")
    if "mcp_servers" in payload:
        errors.append("mcp_servers_present")
    for case in cases:
        if not isinstance(case.evidence.get("facts"), list) or not isinstance(case.evidence.get("near"), list):
            errors.append(f"bad_evidence:{case.case_id}")
    target_count = sum(1 for c in cases if c.corpus == "derived_target_regression")
    real_count = sum(1 for c in cases if c.corpus == "real_v0_replay")
    report = {
        "schema": "nmbot.v1_one_model_gpt55_replay.validate.v1",
        "status": "passed" if not errors else "failed",
        "model": MODEL,
        "prompt": prompt_identity(),
        "prompt_contract": prompt,
        "case_counts": {"real": real_count, "target_regression": target_count, "total": len(cases)},
        "request_contract": {"query_marker": str(payload.get("query") or "").startswith(QUERY_MARKER), "has_mcp_servers": "mcp_servers" in payload, "temperature": payload.get("parameters", {}).get("temperature")},
        "errors": errors[:20],
    }
    assert_no_secrets(report)
    return (0 if not errors else 2), report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-only isolated V1 one-model GPT-5.5 saved-evidence replay")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "dry-run", "run"):
        p = sub.add_parser(name)
        p.add_argument("--case-id")
        p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
        p.add_argument("--parallelism", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= int(args.parallelism) <= 11:
        print(json.dumps({"status": "failed", "error": "parallelism must be 1..11"}, ensure_ascii=False))
        return 2
    if args.command == "validate":
        code, report = validate_command(args.case_id)
        print(json.dumps(_bounded(report), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return code
    try:
        cases = load_cases(args.case_id)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:200]}, ensure_ascii=False))
        return 2
    if args.command == "dry-run":
        rows = [dry_row(case) for case in cases]
    else:
        rows = asyncio.run(run_all(cases, parallelism=int(args.parallelism)))
    jsonl_path, md_path = write_outputs(rows, out_dir=args.out_dir, stem=args.command.replace("-", "_"), mode=args.command)
    non_failing_statuses = {"dry_run", "phone_bypass_simulated"}
    failed = [row for row in rows if not row.get("published") and row.get("checks", {}).get("status") not in non_failing_statuses]
    report = {"status": "failed" if failed else "passed", "mode": args.command, "rows": len(rows), "failed": len(failed), "results": str(jsonl_path), "report": str(md_path), "provider_called": any(row.get("provider_called") for row in rows)}
    assert_no_secrets(report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
