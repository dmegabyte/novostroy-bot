#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
REPORTS_ROOT = PROJECT_ROOT / "reports"
CORPUS_PATH = ROOT / "coverage_corpus.json"
JSON_REPORT_PATH = ROOT / "coverage_report.json"
MD_REPORT_PATH = REPORTS_ROOT / "FIELD_SALES_REGISTRY_COVERAGE_20260721.md"
REGISTRY_VERSION = "v1"
SCHEMA_VERSION = 1
MODULE_FILES = (
    "project.json",
    "apartments.json",
    "readiness.json",
    "transport.json",
    "family.json",
    "yard_safety.json",
    "parking.json",
    "financing.json",
    "investment.json",
    "lots.json",
)
EXPECTED_UNREACHABLE = {
    "down_payment": "structured_finance_missing",
    "house_link": "provenance_only",
    "installment_months": "structured_finance_missing",
    "mortgage_rate": "structured_finance_missing",
}
DENIED_KEY_RE = re.compile(r"(?:phone|email|contact|seller|callback|prompt|model|payload|user_text)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\s().-]*){10,}")
RAW_FINANCE_RE = re.compile(r"\b(?:\d+(?:[,.]\d+)?\s*%|\d+\s*(?:месяц|месяцев|мес\.?)|взнос)\b", re.IGNORECASE)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_registry_cards() -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for filename in MODULE_FILES:
        data = _load_json(ROOT / filename)
        for card in data.get("cards", []):
            field_id = card.get("field_id") if isinstance(card, dict) else None
            if not isinstance(field_id, str):
                raise ValueError(f"{filename}: card without field_id")
            if field_id in cards:
                raise ValueError(f"duplicate field_id: {field_id}")
            cards[field_id] = card
    return dict(sorted(cards.items()))


def _walk(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    out = [(path, value)]
    if isinstance(value, Mapping):
        for key, child in value.items():
            out.extend(_walk(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            out.extend(_walk(child, f"{path}[{index}]"))
    return out


def assert_no_denied_corpus_content(corpus: Mapping[str, Any]) -> None:
    errors: list[str] = []
    for path, value in _walk(corpus):
        if isinstance(value, Mapping):
            for key in value:
                if DENIED_KEY_RE.search(str(key)):
                    errors.append(f"denied key at {path}: {key}")
        if isinstance(value, str):
            if EMAIL_RE.search(value) or PHONE_RE.search(value):
                errors.append(f"PII-like value at {path}")
    if errors:
        raise ValueError("corpus boundary failed: " + "; ".join(errors[:10]))


def assert_no_report_leakage(data: Any) -> None:
    dumped = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if EMAIL_RE.search(dumped) or PHONE_RE.search(dumped):
        raise ValueError("report contains PII-like contact value")
    if RAW_FINANCE_RE.search(dumped):
        raise ValueError("report contains raw finance terms")


def _case_result(case: Mapping[str, Any], adapter: Any) -> dict[str, Any]:
    wrapper = adapter.build_brief_from_option_card(
        case["card"],
        case["scenario"],
        fresh_mcp=bool(case.get("fresh_mcp")),
        requested_fields=tuple(case.get("requested_fields") or ()),
        max_fields=int(case.get("max_fields", 5)),
        lot_index=case.get("lot_index"),
    )
    adaptation = wrapper["adaptation"]
    brief = wrapper["brief"]
    diagnostics = adaptation["diagnostics"]
    return {
        "case_id": case["case_id"],
        "scenario": case["scenario"],
        "fresh_mcp": bool(case.get("fresh_mcp")),
        "lot_index": adaptation["lot_index"],
        "adapted_field_ids": sorted(adaptation["facts"]),
        "selected_brief_field_ids": [field["field_id"] for field in brief["fields"]],
        "brief_descriptors": [
            {
                "field_id": field["field_id"],
                "label": field["label"],
                "allowed_benefit": field.get("allowed_benefit"),
            }
            for field in brief["fields"]
        ],
        "combination_ids": [combo["id"] for combo in brief["combinations"]],
        "safe_combinations": [
            {"id": combo["id"], "safe_phrasing": combo["safe_phrasing"]}
            for combo in brief["combinations"]
        ],
        "adapter_diagnostics": {
            "unmapped_field_ids": sorted(diagnostics.get("unmapped_field_ids", [])),
            "omitted_field_ids": sorted(
                diagnostics.get("omitted_field_ids", []),
                key=lambda item: (item.get("reason", ""), item.get("field_id", "")),
            ),
            "lot_examples_available": diagnostics.get("lot_examples_available"),
            "lot_selection": diagnostics.get("lot_selection"),
            "house_link_available": diagnostics.get("house_link_available"),
        },
    }


def _domain_coverage(cards: Mapping[str, Mapping[str, Any]], observed: set[str], reachable: set[str]) -> dict[str, Any]:
    domains: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"registry": set(), "reachable": set(), "observed": set(), "expected_unreachable": set()})
    for field_id, card in cards.items():
        domain = str(card.get("domain"))
        domains[domain]["registry"].add(field_id)
        if field_id in reachable:
            domains[domain]["reachable"].add(field_id)
        if field_id in observed:
            domains[domain]["observed"].add(field_id)
        if field_id in EXPECTED_UNREACHABLE:
            domains[domain]["expected_unreachable"].add(field_id)
    out: dict[str, Any] = {}
    for domain in sorted(domains):
        item = domains[domain]
        reachable_count = len(item["reachable"])
        observed_count = len(item["observed"])
        out[domain] = {
            "registry_count": len(item["registry"]),
            "reachable_count": reachable_count,
            "observed_count": observed_count,
            "expected_unreachable_field_ids": sorted(item["expected_unreachable"]),
            "reachable_coverage_percent": round((observed_count / reachable_count * 100) if reachable_count else 100.0, 1),
        }
    return out


def generate_report() -> dict[str, Any]:
    cards = load_registry_cards()
    registry_ids = set(cards)
    adapter = _load_module("field_sales_registry_v1_option_card_adapter", ROOT / "option_card_adapter.py")
    corpus = _load_json(CORPUS_PATH)
    if corpus.get("version") != REGISTRY_VERSION or not isinstance(corpus.get("cases"), list):
        raise ValueError("coverage_corpus.json: invalid version or cases")
    assert_no_denied_corpus_content(corpus)

    reachable = set(adapter.reachable_field_ids())
    expected_unreachable = set(EXPECTED_UNREACHABLE)
    if reachable != registry_ids - expected_unreachable:
        raise ValueError("adapter reachable_field_ids does not equal registry minus expected unreachable")
    if len(registry_ids) != 35:
        raise ValueError(f"registry field count changed: {len(registry_ids)}")
    if len(expected_unreachable) != 4:
        raise ValueError(f"expected gap count changed: {len(expected_unreachable)}")

    case_results = [_case_result(case, adapter) for case in sorted(corpus["cases"], key=lambda item: item["case_id"])]
    observed = {field_id for case in case_results for field_id in case["adapted_field_ids"]}
    emitted = set(observed)
    unexpected = sorted(emitted - registry_ids)
    unexpected_uncovered = sorted(reachable - observed)
    missing_partition = registry_ids - observed - expected_unreachable
    if missing_partition:
        raise ValueError(f"registry partition failed: {sorted(missing_partition)}")
    if unexpected or unexpected_uncovered:
        raise ValueError(f"coverage failed unexpected={unexpected} uncovered={unexpected_uncovered}")

    report = {
        "schema_version": SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "registry_field_count": len(registry_ids),
        "reachable_contract_field_ids": sorted(reachable),
        "observed_corpus_field_ids": sorted(observed),
        "expected_unreachable": dict(sorted(EXPECTED_UNREACHABLE.items())),
        "unexpected_uncovered_field_ids": unexpected_uncovered,
        "unexpected_field_ids": unexpected,
        "reachable_coverage_percent": round(len(observed & reachable) / len(reachable) * 100, 1),
        "registry_reachability_percent": round(len(reachable) / len(registry_ids) * 100, 1),
        "domain_coverage": _domain_coverage(cards, observed, reachable),
        "cases": case_results,
        "source_refs": [
            "field_sales_registry/v1/coverage_corpus.json",
            "field_sales_registry/v1/coverage_audit.py",
            "field_sales_registry/v1/option_card_adapter.py",
            "field_sales_registry/v1/brief_builder.py",
            "field_sales_registry/v1/{project,apartments,readiness,transport,family,yard_safety,parking,financing,investment,lots}.json",
        ],
    }
    assert_no_report_leakage(report)
    return report


def markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# Field Sales Registry v1 — offline coverage audit",
        "",
        "## Summary",
        "",
        f"- Registry fields: {report['registry_field_count']}",
        f"- Adapter-reachable fields: {len(report['reachable_contract_field_ids'])}",
        f"- Observed corpus fields: {len(report['observed_corpus_field_ids'])}",
        f"- Reachable coverage: {report['reachable_coverage_percent']:.1f}%",
        f"- Registry reachability: {report['registry_reachability_percent']:.1f}%",
        "- Runtime/deploy/API/selector/services are not imported or called.",
        "",
        "## Expected gaps",
        "",
    ]
    for field_id, reason in report["expected_unreachable"].items():
        lines.append(f"- `{field_id}` — `{reason}`")
    lines.extend(["", "## Domain coverage", ""])
    lines.append("| Domain | Registry | Reachable | Observed | Expected unreachable | Reachable coverage |")
    lines.append("|---|---:|---:|---:|---|---:|")
    for domain, item in report["domain_coverage"].items():
        gaps = ", ".join(f"`{field}`" for field in item["expected_unreachable_field_ids"]) or "—"
        lines.append(
            f"| `{domain}` | {item['registry_count']} | {item['reachable_count']} | {item['observed_count']} | {gaps} | {item['reachable_coverage_percent']:.1f}% |"
        )
    lines.extend(["", "## Case examples", ""])
    for case in report["cases"]:
        selected = ", ".join(f"`{field}`" for field in case["selected_brief_field_ids"]) or "—"
        combos = ", ".join(f"`{combo}`" for combo in case["combination_ids"]) or "—"
        unmapped = ", ".join(f"`{field}`" for field in case["adapter_diagnostics"]["unmapped_field_ids"]) or "—"
        house = "yes" if case["adapter_diagnostics"]["house_link_available"] else "no"
        lines.extend(
            [
                f"### `{case['case_id']}`",
                "",
                f"- Selected field IDs: {selected}",
                f"- Safe combination IDs: {combos}",
                f"- Adapter unmapped names: {unmapped}",
                f"- Lot selection: `{case['adapter_diagnostics']['lot_selection']}`, house linkage diagnostic only: `{house}`",
                "",
            ]
        )
        for descriptor in case["brief_descriptors"]:
            benefit = descriptor.get("allowed_benefit") or "только буквальный факт, без сценарной выгоды"
            lines.append(
                f"- Brief descriptor `{descriptor['field_id']}` — {descriptor['label']}: {benefit}"
            )
        for combo in case["safe_combinations"]:
            lines.append(f"- Safe phrasing `{combo['id']}`: {combo['safe_phrasing']}")
        lines.append("")
    lines.extend(
        [
            "## Boundaries",
            "",
            "- Corpus is synthetic, PII-free and excludes denied outreach/runtime envelope categories.",
            "- Finance text is used only to prove unmapped structured names; reports keep names/reasons only.",
            "- `house_link` remains provenance-only and appears only as a boolean diagnostic about linkage availability.",
            "- Reports are deterministic: sorted IDs, sorted domains and stable case ordering.",
            "",
            "## Source refs",
            "",
        ]
    )
    for source in report["source_refs"]:
        lines.append(f"- `{source}`")
    text = "\n".join(lines) + "\n"
    assert_no_report_leakage(text)
    return text


def write_reports(report: Mapping[str, Any]) -> None:
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    JSON_REPORT_PATH.write_text(_dump_json(report), encoding="utf-8")
    MD_REPORT_PATH.write_text(markdown_report(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic offline coverage audit for field_sales_registry/v1")
    parser.add_argument("--write", action="store_true", help="regenerate coverage_report.json and Markdown report")
    parser.add_argument("--json", action="store_true", help="print full JSON report to stdout")
    args = parser.parse_args(argv)
    try:
        report = generate_report()
        if args.write:
            write_reports(report)
        if args.json:
            print(_dump_json(report), end="")
        else:
            print(
                "OK "
                f"registry={report['registry_field_count']} "
                f"reachable={len(report['reachable_contract_field_ids'])} "
                f"observed={len(report['observed_corpus_field_ids'])} "
                f"reachable_coverage={report['reachable_coverage_percent']:.1f}% "
                f"registry_reachability={report['registry_reachability_percent']:.1f}%"
            )
        return 0
    except Exception as exc:  # noqa: BLE001 - compact CLI failure
        print(f"FAIL coverage audit: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
