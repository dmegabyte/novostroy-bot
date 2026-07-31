#!/usr/bin/env python3
"""Offline MCP/search card reformatter and safe corpus runner.

This script is intentionally production-disconnected: it imports existing local
normalization and scenario mechanics, but never calls a model or the network.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nmbot_v2.card_normalizer import normalize_search_result  # noqa: E402
from nmbot_v2.contracts import OptionCard, SearchResult  # noqa: E402
from nmbot_v2.scenario_field_mechanics import build_scenario_context  # noqa: E402


TOP_LEVEL_KEYS = ("facts", "near", "missing", "params")
PRIMARY_SCENARIOS = {"life", "family", "investment", "rental"}
SCENARIOS_WITH_OVERLAY = PRIMARY_SCENARIOS | {"financing"}
CONTACT_OR_URL_RE = re.compile(
    r"https?://|www\.|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}",
    re.I,
)
SENSITIVE_KEY_RE = re.compile(r"url|link|site|phone|телефон|email|mail|contact|client|uid|chat|user_text|original|raw|payload|note|description", re.I)


@dataclass(frozen=True)
class ParsedResponse:
    classification: str
    projected: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class CorpusItem:
    case_id: str
    source_id: str
    source_path: str
    response: Any
    scenario: str = "life"
    original_scenario: str | None = None


def parse_response(value: Any) -> ParsedResponse:
    """Parse a raw MCP/search response without throwing on legacy content."""

    try:
        data = _coerce_jsonish(value)
    except Exception as exc:  # noqa: BLE001 - safe classifier boundary
        return ParsedResponse("parse_error", {}, _short_error(exc))
    if not isinstance(data, Mapping):
        return ParsedResponse("parse_error", {}, "parsed value is not an object")
    projected = _project_top_level(data)
    facts = projected.get("facts") if isinstance(projected.get("facts"), list) else []
    near = projected.get("near") if isinstance(projected.get("near"), list) else []
    card_like = [item for item in [*facts, *near] if isinstance(item, Mapping)]
    if not facts and not near:
        return ParsedResponse("empty", projected)
    if not card_like:
        return ParsedResponse("non_card_facts", projected)
    normalized = normalize_search_result(_adapt_response_aliases(projected))
    if not normalized.facts and not normalized.near:
        return ParsedResponse("empty", projected)
    return ParsedResponse("normalized", projected)


def build_reformat_plan(response: Any, scenario: str = "life") -> dict[str, Any]:
    parsed = parse_response(response)
    plan: dict[str, Any] = {
        "classification": parsed.classification,
        "scenario": _primary_scenario(scenario),
        "overlay": _overlay(scenario),
        "cards": [],
        "benefit_model_input": [],
        "errors": [],
    }
    if parsed.error:
        plan["errors"].append(parsed.error)
    if parsed.classification != "normalized":
        return plan
    normalized = normalize_search_result(_adapt_response_aliases(parsed.projected))
    cards = tuple([*normalized.facts, *normalized.near])[:3]
    scenario_context = build_scenario_context(
        cards=cards,
        primary_scenario=plan["scenario"],
        overlay=plan["overlay"],
        presentation_scope="shortlist",
    )
    ctx_cards = list(scenario_context.get("cards") or [])
    for zero_idx, card in enumerate(cards):
        idx = zero_idx + 1
        ctx = ctx_cards[zero_idx] if zero_idx < len(ctx_cards) and isinstance(ctx_cards[zero_idx], Mapping) else {}
        mandatory = mandatory_text(card)
        item = {
            "idx": idx,
            "name": card.name,
            "mode": "near" if card.is_near else "facts",
            "mandatory_text": mandatory,
            "anchor_fact": str(ctx.get("anchor_fact") or ""),
        }
        plan["cards"].append(item)
        plan["benefit_model_input"].append(_benefit_input(idx, ctx))
    return plan


def assemble_reformatted_answer(plan: Mapping[str, Any], benefits: Any | None = None, final_question: str | None = None) -> str:
    """Assemble final answer from deterministic facts plus optional supplied benefits."""

    cards = [item for item in plan.get("cards", []) if isinstance(item, Mapping)] if isinstance(plan, Mapping) else []
    if not cards:
        return final_question or "Пока не вижу подходящих карточек. Хотите, попробую расширить поиск?"
    benefit_map = _benefit_map(benefits)
    lines = []
    for item in cards:
        idx = int(item.get("idx", len(lines) + 1))
        name = _clean_text(item.get("name") or "Вариант")
        mandatory = _clean_text(item.get("mandatory_text") or "")
        benefit = _clean_text(benefit_map.get(idx) or "")
        sentence = f"{idx}. {name}. {mandatory}".strip()
        if benefit:
            sentence = f"{sentence} {benefit}"
        lines.append(sentence)
    question = final_question or "Хотите, расскажу подробнее про один из этих вариантов?"
    return "\n\n".join([*lines, question])


def run_corpus(manifest_path: str | Path, *, root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root).resolve()
    manifest_file = _safe_project_path(root_path, manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    items = list(_load_manifest_items(manifest, root_path))
    hashes: dict[str, list[CorpusItem]] = {}
    parsed_by_hash: dict[str, ParsedResponse] = {}
    errors: list[dict[str, str]] = []
    for item in items:
        parsed = parse_response(item.response)
        digest = canonical_response_hash(parsed.projected if parsed.projected else {"parse_error": parsed.error or "parse_error"})
        hashes.setdefault(digest, []).append(item)
        parsed_by_hash.setdefault(digest, parsed)
    unique_count = len(hashes)
    counts = {"parse_error": 0, "non_card_facts": 0, "empty": 0, "normalized": 0}
    facts_total = 0
    near_total = 0
    scenario_counts: dict[str, int] = {}
    anchor_counts: dict[str, int] = {}
    mandatory_floor_checks = {"checked": 0, "failed": 0}
    source_files = []
    for src in manifest.get("sources", []):
        if isinstance(src, Mapping):
            source_files.append({"id": str(src.get("id") or ""), "path": str(src.get("path") or "")})
    for digest, group in hashes.items():
        item = group[0]
        parsed = parsed_by_hash[digest]
        counts[parsed.classification] = counts.get(parsed.classification, 0) + 1
        scenario_counts[_scenario_for_report(item.scenario)] = scenario_counts.get(_scenario_for_report(item.scenario), 0) + 1
        if parsed.classification == "normalized":
            plan = build_reformat_plan(parsed.projected, item.scenario)
            normalized = normalize_search_result(_adapt_response_aliases(parsed.projected))
            facts_total += len(normalized.facts)
            near_total += len(normalized.near)
            for card in plan.get("cards", []):
                anchor = str(card.get("anchor_fact") or "none") if isinstance(card, Mapping) else "none"
                anchor_counts[anchor] = anchor_counts.get(anchor, 0) + 1
            checked, failed = _mandatory_floor_check(plan, normalized)
            mandatory_floor_checks["checked"] += checked
            mandatory_floor_checks["failed"] += failed
            if failed:
                errors.append({"case_id": item.case_id, "source_id": item.source_id, "error": "mandatory_floor_failed"})
        elif parsed.error:
            errors.append({"case_id": item.case_id, "source_id": item.source_id, "error": parsed.classification})
    report = {
        "version": "nmbot_card_reformatter_report_v1",
        "source_files": source_files,
        "extracted_count": len(items),
        "unique_count": unique_count,
        "duplicate_count": len(items) - unique_count,
        "classification_counts": counts,
        "normalized_facts_total": facts_total,
        "normalized_near_total": near_total,
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "anchor_counts": dict(sorted(anchor_counts.items())),
        "mandatory_floor_checks": mandatory_floor_checks,
        "safe_errors": errors[:200],
    }
    return _redact_report(report)


def canonical_response_hash(projected: Mapping[str, Any]) -> str:
    canonical = _sanitize_for_canonical(projected)
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def mandatory_text(card: OptionCard) -> str:
    parts: list[str] = []
    if card.location:
        parts.append(f"Локация — {card.location}.")
    if card.price_min is not None:
        parts.append(f"Квартиры в проекте — от {_format_rub(card.price_min)}.")
    elif card.price:
        parts.append(f"Цена проекта — {card.price}.")
    if card.ready:
        ready = card.ready.strip()
        if _is_delivered(ready):
            parts.append("Дом сдан.")
        else:
            parts.append(f"Срок готовности — {ready}.")
    if card.finishing:
        finishing = card.finishing.strip()
        norm = finishing.casefold().replace("ё", "е")
        if norm == "без отделки":
            parts.append("Квартиры передаются без отделки.")
        elif norm == "с отделкой":
            parts.append("Предусмотрена отделка.")
        else:
            parts.append(f"Отделка — {finishing}.")
    return " ".join(parts)


def extract_response(value: Any) -> ParsedResponse:
    return parse_response(value)


def _coerce_jsonish(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        raise ValueError("response is not a string/object")
    text = value.strip()
    if not text:
        raise ValueError("empty response text")
    candidates = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    candidates.extend(fenced)
    candidates.append(text)
    extracted = _extract_balanced_json_objects(text)
    candidates.extend(extracted)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"json parse failed: {last_error}")


def _extract_balanced_json_objects(text: str) -> list[str]:
    out: list[str] = []
    starts = [idx for idx, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        in_str = False
        escape = False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    out.append(text[start : pos + 1])
                    break
    return out


def _project_top_level(data: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in TOP_LEVEL_KEYS:
        if key in data:
            projected[key] = copy.deepcopy(data[key])
    projected.setdefault("facts", [])
    projected.setdefault("near", [])
    projected.setdefault("missing", [])
    projected.setdefault("params", {})
    return projected


def _adapt_response_aliases(projected: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dict(projected))
    for list_key in ("facts", "near"):
        if isinstance(out.get(list_key), list):
            out[list_key] = [_adapt_card_aliases(item) if isinstance(item, Mapping) else item for item in out[list_key]]
    return out


def _adapt_card_aliases(card: Mapping[str, Any]) -> dict[str, Any]:
    adapted = dict(card)
    infra = adapted.get("infrastructure")
    infra_items: list[Any]
    nested_aliases = {
        "schools": ("school", "школа"),
        "kindergartens": ("kindergarten", "детский сад"),
        "parks": ("park_near", "парк"),
        "shops": (None, "магазины"),
        "services": (None, "сервисы"),
    }
    if isinstance(infra, list):
        infra_items = list(infra)
    elif isinstance(infra, str):
        infra_items = [infra]
    elif isinstance(infra, Mapping):
        infra_items = []
        for key, val in infra.items():
            if not val:
                continue
            canonical, label = nested_aliases.get(str(key), (None, str(key)))
            if canonical and canonical not in adapted:
                adapted[canonical] = True
            infra_items.append(label)
    else:
        infra_items = []
    alias_map = {
        "parks": ("park_near", "парк"),
        "schools": ("school", "школа"),
        "kindergartens": ("kindergarten", "детский сад"),
        "daily_services": (None, "магазины и сервисы"),
    }
    for alias, (canonical, label) in alias_map.items():
        if alias in adapted and adapted.get(alias) not in (None, "", False):
            if canonical and canonical not in adapted:
                adapted[canonical] = True
            infra_items.append(str(adapted.get(alias) if not isinstance(adapted.get(alias), bool) else label))
    if infra_items:
        adapted["infrastructure"] = infra_items
    return adapted


def _benefit_input(idx: int, ctx: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": idx,
        "evidence": copy.deepcopy(ctx.get("evidence") or []),
        "communication_goal": str(ctx.get("communication_goal") or ""),
        "allowed_concepts": list(ctx.get("allowed_concepts") or []),
        "forbidden_meanings": list(ctx.get("forbidden_meanings") or []),
    }


def _benefit_map(benefits: Any | None) -> dict[int, str]:
    if benefits is None:
        return {}
    if isinstance(benefits, Mapping):
        source = benefits.get("benefits", benefits)
        if isinstance(source, Mapping):
            return {int(k): str(v) for k, v in source.items() if str(k).isdigit()}
        if isinstance(source, list):
            return _benefit_map(source)
    if isinstance(benefits, list):
        out: dict[int, str] = {}
        for idx, item in enumerate(benefits, start=1):
            if isinstance(item, Mapping):
                raw_idx = item.get("idx", idx)
                text = item.get("benefit") or item.get("text") or ""
                if isinstance(raw_idx, int) and text:
                    out[raw_idx] = str(text)
            elif item:
                out[idx] = str(item)
        return out
    return {}


def _load_manifest_items(manifest: Mapping[str, Any], root: Path) -> Iterable[CorpusItem]:
    for source in manifest.get("sources", []):
        if not isinstance(source, Mapping):
            continue
        source_id = str(source.get("id") or source.get("path") or "source")
        path_text = str(source.get("path") or "")
        loader = str(source.get("loader") or "")
        file_path = _safe_project_path(root, path_text)
        try:
            yield from _load_source_items(source_id, path_text, file_path, loader, source)
        except Exception as exc:  # noqa: BLE001
            yield CorpusItem(f"{source_id}:loader_error", source_id, path_text, {"facts": [], "near": [], "missing": [f"loader_error:{_short_error(exc)}"], "params": {}}, "life")


def _load_source_items(source_id: str, path_text: str, file_path: Path, loader: str, source: Mapping[str, Any]) -> Iterable[CorpusItem]:
    if loader == "quality_scenarios_records_search_output":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        for record in data.get("records", []):
            if isinstance(record, Mapping):
                rid = str(record.get("id") or "record")
                scenario = rid if rid in SCENARIOS_WITH_OVERLAY else "life"
                original = None if rid in SCENARIOS_WITH_OVERLAY else rid
                yield CorpusItem(f"{source_id}:{rid}", source_id, path_text, record.get("search_output"), scenario, original)
        return
    if loader == "dialogue_replay_turn_searches":
        for line_no, row in _jsonl(file_path):
            base_id = str(row.get("id") or f"line{line_no}")
            for turn_idx, turn in enumerate(row.get("turns", []) if isinstance(row.get("turns"), list) else []):
                if not isinstance(turn, Mapping):
                    continue
                scenario = _scenario_from_plan(turn.get("plan"), default="life")
                search = turn.get("search")
                if isinstance(search, Mapping) and isinstance(search.get("attempts"), list):
                    for attempt_idx, attempt in enumerate(search.get("attempts") or []):
                        if isinstance(attempt, Mapping) and "search" in attempt:
                            yield CorpusItem(f"{source_id}:{base_id}:t{turn_idx}:a{attempt_idx}", source_id, path_text, attempt.get("search"), scenario)
                elif search is not None:
                    yield CorpusItem(f"{source_id}:{base_id}:t{turn_idx}", source_id, path_text, search, scenario)
        return
    if loader == "top_level_response":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        scenario = str(source.get("scenario_override") or _scenario_from_params(data.get("params")) or "life")
        yield CorpusItem(f"{source_id}:top", source_id, path_text, data, scenario)
        return
    if loader == "field_sales_coverage_cases":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        for case in data.get("cases", []) if isinstance(data.get("cases"), list) else []:
            if isinstance(case, Mapping):
                scenario, original = _map_coverage_scenario(case.get("scenario"))
                yield CorpusItem(str(case.get("case_id") or f"{source_id}:case"), source_id, path_text, {"facts": [case.get("card")], "near": [], "missing": [], "params": {}}, scenario, original)
        return
    if loader == "live_scenario_pipeline_rows":
        for line_no, row in _jsonl(file_path):
            response = row.get("mcp_response")
            scenario = _scenario_from_serialized(row.get("params")) or _scenario_from_serialized(row.get("mcp_request")) or _scenario_from_case(row.get("case"))
            yield CorpusItem(f"{source_id}:row{line_no}:{row.get('case') or 'case'}", source_id, path_text, response, scenario)
        return
    if loader == "response_eval_cases":
        for line_no, row in _jsonl(file_path):
            yield CorpusItem(str(row.get("case_id") or f"{source_id}:row{line_no}"), source_id, path_text, row.get("search_response"), "life")
        return
    raise ValueError(f"unknown loader: {loader}")


def _jsonl(file_path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for line_no, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield line_no, row


def _safe_project_path(root: Path, path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        candidate = p.resolve()
    else:
        candidate = (root / p).resolve()
    if root not in candidate.parents and candidate != root:
        raise ValueError(f"path escapes project root: {path}")
    return candidate


def _scenario_from_plan(plan: Any, default: str = "life") -> str:
    if not isinstance(plan, Mapping):
        return default
    for key in ("intent", "viewpoint", "purpose"):
        mapped = _map_scenario(plan.get(key))
        if mapped:
            return mapped
    delta = plan.get("constraints_delta")
    if isinstance(delta, Mapping):
        prefs = delta.get("preferences")
        if isinstance(prefs, Mapping):
            mapped = _map_scenario(prefs.get("purpose") or prefs.get("financing"))
            if mapped:
                return mapped
    return default


def _scenario_from_params(params: Any) -> str | None:
    if not isinstance(params, Mapping):
        return None
    return _map_scenario(params.get("purpose") or params.get("scenario"))


def _scenario_from_serialized(value: Any) -> str | None:
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, Mapping):
        return _scenario_from_params(data)
    return None


def _scenario_from_case(value: Any) -> str:
    text = str(value or "").casefold()
    if "family" in text:
        return "family"
    if "rent" in text:
        return "rental"
    if "invest" in text:
        return "investment"
    if "financ" in text or "mortgage" in text:
        return "financing"
    return "life"


def _map_coverage_scenario(value: Any) -> tuple[str, str | None]:
    text = str(value or "").strip().casefold()
    if text in {"family", "investment", "rental"}:
        return text, None
    if text in {"budget", "finance", "financing", "mortgage"}:
        return "financing", text if text != "financing" else None
    if text in {"general", "parking", "life", ""}:
        return "life", text or None
    return "life", text


def _map_scenario(value: Any) -> str | None:
    text = str(value or "").strip().casefold().replace("ё", "е")
    if text in SCENARIOS_WITH_OVERLAY:
        return text
    if text in {"rent", "lease"}:
        return "rental"
    if text in {"mortgage", "finance", "budget"}:
        return "financing"
    if text in {"self_use", "general", "purchase", "search"}:
        return "life"
    return None


def _primary_scenario(scenario: str | None) -> str:
    text = _map_scenario(scenario) or "life"
    return "life" if text == "financing" else text


def _overlay(scenario: str | None) -> str | None:
    return "financing" if (_map_scenario(scenario) == "financing") else None


def _scenario_for_report(scenario: str) -> str:
    return _map_scenario(scenario) or "life"


def _mandatory_floor_check(plan: Mapping[str, Any], normalized: SearchResult) -> tuple[int, int]:
    cards = tuple([*normalized.facts, *normalized.near])[:3]
    plan_cards = [item for item in plan.get("cards", []) if isinstance(item, Mapping)]
    checked = 0
    failed = 0
    for card, item in zip(cards, plan_cards):
        text = str(item.get("mandatory_text") or "")
        required: list[str] = []
        if card.location:
            required.append(str(card.location))
        checked += 1
        if str(item.get("name") or "") != card.name:
            failed += 1
        if card.price_min is not None:
            required.append("Квартиры в проекте")
        elif card.price:
            required.append(str(card.price))
        if card.ready:
            required.append("Дом сдан" if _is_delivered(card.ready) else "Срок готовности")
        if card.finishing:
            required.append("без отделки" if card.finishing.casefold().replace("ё", "е") == "без отделки" else "отдел")
        checked += len(required)
        failed += sum(1 for token in required if token.casefold() not in text.casefold())
    return checked, failed


def _sanitize_for_canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _sanitize_for_canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0])) if not SENSITIVE_KEY_RE.search(str(k))}
    if isinstance(value, list):
        return [_sanitize_for_canonical(item) for item in value]
    if isinstance(value, str):
        return CONTACT_OR_URL_RE.sub("[redacted]", value)
    return value


def _redact_report(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _redact_report(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_report(item) for item in value]
    if isinstance(value, str):
        return CONTACT_OR_URL_RE.sub("[redacted]", value)
    return value


def _format_rub(value: int | float) -> str:
    number = float(value)
    if number >= 1_000_000:
        millions = number / 1_000_000
        text = f"{millions:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{text} млн ₽"
    return f"{int(number):,}".replace(",", " ") + " ₽"


def _is_delivered(value: str) -> bool:
    text = value.casefold().replace("ё", "е")
    return text in {"сдан", "готов", "готовый", "delivered", "ready"} or text.startswith("сдан ") or text.startswith("сдан(") or text.startswith("дом сдан")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _short_error(exc: Exception) -> str:
    return _clean_text(str(exc))[:160]


def _read_optional_json(path: str | None) -> Any:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline nmbot card reformatter/corpus runner")
    parser.add_argument("response_file", nargs="?", help="JSON/prose response file; stdin is used when omitted")
    parser.add_argument("--scenario", default="life")
    parser.add_argument("--corpus-manifest")
    parser.add_argument("--benefits-json")
    parser.add_argument("--benefits-file")
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--final-question", default="Хотите, расскажу подробнее про один из этих вариантов?")
    args = parser.parse_args(argv)

    if args.corpus_manifest:
        print(json.dumps(run_corpus(args.corpus_manifest), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    text = Path(args.response_file).read_text(encoding="utf-8") if args.response_file else sys.stdin.read()
    plan = build_reformat_plan(text, args.scenario)
    if args.assemble:
        benefits = json.loads(args.benefits_json) if args.benefits_json else _read_optional_json(args.benefits_file)
        payload = {"plan": plan, "answer": assemble_reformatted_answer(plan, benefits, args.final_question)}
    else:
        payload = {"plan": plan}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
