#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MODULE_FILES = [
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
]
ALL_JSON_FILES = [
    "schema.json",
    *MODULE_FILES,
    "combinations.json",
    "brief_schema.json",
    "adaptation_schema.json",
    "example_input.json",
    "example_brief.json",
    "coverage_corpus.json",
    "coverage_report.json",
    "structured_finance_schema.json",
    "example_structured_finance_input.json",
    "example_structured_finance_output.json",
    "answer_composer_input_schema.json",
    "answer_composer_candidate_schema.json",
    "example_answer_composer_input.json",
    "example_answer_composer_candidate.json",
    "example_answer_composer_result.json",
    "answer_composer_matrix.json",
    "answer_composer_matrix_report.json",
]

REQUIRED_CARD_KEYS = {
    "field_id",
    "domain",
    "source_fields",
    "canonical_fact",
    "client_label",
    "value_type",
    "literal_meaning",
    "sales_strength",
    "freshness",
    "required_evidence",
    "scenario_angles",
    "combines_with",
    "forbidden_claims",
    "rendering_rules",
}
VALUE_TYPES = {"text", "number", "money", "boolean", "list", "area", "floor", "link", "inventory", "percentage", "months"}
STRENGTHS = {"strong", "supporting", "neutral", "weak"}
SCENARIOS = {"family", "commute", "budget", "comfort", "safety", "investment", "parking", "readiness", "general"}
FRESHNESS_POLICIES = {"static_until_mcp_changes", "dynamic_mcp_required"}

# Deny technical/runtime/search envelope fields.  Dotted ads.status is allowed for
# lot_status only because it is rendered only after canonical handling.
DENY_SOURCE_EXACT = {
    "diagnostics",
    "params",
    "missing",
    "near",
    "trace",
    "validation",
    "planner",
    "pending",
    "callback",
    "contact",
    "seller",
    "seller_name",
    "seller_phone",
    "agent_phone",
    "agent_name",
    "prompt",
    "model",
    "payload",
    "raw",
    "action",
    "state",
    "status",
}
DENY_SOURCE_SUBSTRINGS = (
    "diagnostic",
    "params",
    "prompt",
    "model",
    "trace",
    "validation",
    "planner",
    "pending_",
    "callback",
    "seller_",
    "agent_phone",
    "raw_",
    "payload",
)

DYNAMIC_FIELDS = {
    "apartment_price",
    "room_formats",
    "area",
    "apartment_inventory",
    "finishing",
    "parking_price",
    "parking_inventory",
    "mortgage_rate",
    "down_payment",
    "installment_months",
    "discount",
    "sales_count",
    "ads_count",
    "lot_full_price",
    "lot_area",
    "lot_floor",
    "lot_rooms",
    "lot_renovation",
    "lot_status",
    "house_link",
}

BRIEF_REQUIRED_KEYS = {
    "schema_version",
    "scenario",
    "object_name",
    "fresh_mcp",
    "fields",
    "combinations",
    "constraints",
    "diagnostics",
}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_json(path: Path, errors: list[str]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001 - compact CLI validator
        fail(errors, f"{path.name}: JSON parse failed: {exc}")
        return None


def require_non_empty_list(card: dict[str, Any], key: str, errors: list[str]) -> None:
    value = card.get(key)
    if not isinstance(value, list) or not value:
        fail(errors, f"{card.get('field_id', '<unknown>')}: {key} must be a non-empty list")
        return
    if any(not isinstance(item, str) or not item.strip() for item in value):
        fail(errors, f"{card.get('field_id', '<unknown>')}: {key} must contain non-empty strings")


def validate_freshness(card: dict[str, Any], errors: list[str]) -> None:
    field_id = str(card.get("field_id", ""))
    freshness = card.get("freshness")
    if not isinstance(freshness, dict):
        fail(errors, f"{field_id}: freshness must be an object")
        return
    policy = freshness.get("policy")
    if policy not in FRESHNESS_POLICIES:
        fail(errors, f"{field_id}: invalid freshness policy {policy!r}")
    if not isinstance(freshness.get("cache_static"), bool):
        fail(errors, f"{field_id}: freshness.cache_static must be boolean")
    if not str(freshness.get("reason", "")).strip():
        fail(errors, f"{field_id}: freshness.reason is required")
    if field_id in DYNAMIC_FIELDS:
        if policy != "dynamic_mcp_required" or freshness.get("cache_static") is not False:
            fail(errors, f"{field_id}: dynamic field must require fresh MCP and cache_static=false")


def validate_angles(card: dict[str, Any], errors: list[str]) -> None:
    field_id = str(card.get("field_id", ""))
    angles = card.get("scenario_angles")
    if not isinstance(angles, list) or not angles:
        fail(errors, f"{field_id}: scenario_angles must be non-empty")
        return
    for index, angle in enumerate(angles):
        if not isinstance(angle, dict):
            fail(errors, f"{field_id}: angle #{index} must be object")
            continue
        if angle.get("scenario") not in SCENARIOS:
            fail(errors, f"{field_id}: angle #{index} invalid scenario")
        if angle.get("strength") not in STRENGTHS:
            fail(errors, f"{field_id}: angle #{index} invalid strength")
        if not str(angle.get("benefit", "")).strip():
            fail(errors, f"{field_id}: angle #{index} benefit is required")
        for key in ("requires_all", "requires_any", "do_not_say"):
            if not isinstance(angle.get(key), list):
                fail(errors, f"{field_id}: angle #{index} {key} must be list")
        if isinstance(angle.get("do_not_say"), list) and not angle["do_not_say"]:
            fail(errors, f"{field_id}: angle #{index} do_not_say must be non-empty")


def validate_sources(card: dict[str, Any], errors: list[str]) -> None:
    field_id = str(card.get("field_id", ""))
    sources = card.get("source_fields")
    if not isinstance(sources, list):
        return
    for source in sources:
        low = str(source).strip().lower()
        if low in DENY_SOURCE_EXACT and not (field_id == "lot_status" and low == "ads.status"):
            fail(errors, f"{field_id}: denied technical/raw source field {source!r}")
        if any(token in low for token in DENY_SOURCE_SUBSTRINGS):
            fail(errors, f"{field_id}: denied technical/raw source field {source!r}")


def validate_card(card: Any, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(card, dict):
        fail(errors, "card must be object")
        return None
    field_id = str(card.get("field_id", "<unknown>"))
    extra = set(card) - REQUIRED_CARD_KEYS
    missing = REQUIRED_CARD_KEYS - set(card)
    if extra:
        fail(errors, f"{field_id}: unexpected keys: {', '.join(sorted(extra))}")
    if missing:
        fail(errors, f"{field_id}: missing keys: {', '.join(sorted(missing))}")
    if card.get("value_type") not in VALUE_TYPES:
        fail(errors, f"{field_id}: invalid value_type")
    if card.get("sales_strength") not in STRENGTHS:
        fail(errors, f"{field_id}: invalid sales_strength")
    for key in ("source_fields", "required_evidence", "forbidden_claims", "rendering_rules"):
        require_non_empty_list(card, key, errors)
    if not isinstance(card.get("combines_with"), list):
        fail(errors, f"{field_id}: combines_with must be list")
    validate_freshness(card, errors)
    validate_angles(card, errors)
    validate_sources(card, errors)
    return card


def validate_combinations(data: Any, ids: set[str], errors: list[str]) -> int:
    if not isinstance(data, dict) or not isinstance(data.get("combinations"), list):
        fail(errors, "combinations.json: combinations list is required")
        return 0
    seen: set[str] = set()
    for item in data["combinations"]:
        if not isinstance(item, dict):
            fail(errors, "combinations.json: item must be object")
            continue
        combo_id = str(item.get("id", ""))
        if not combo_id:
            fail(errors, "combinations.json: id is required")
        if combo_id in seen:
            fail(errors, f"combinations.json: duplicate id {combo_id}")
        seen.add(combo_id)
        for key in ("client_meaning", "safe_phrasing", "forbidden_leap"):
            if not str(item.get(key, "")).strip():
                fail(errors, f"{combo_id}: {key} is required")
        cards = item.get("required_cards")
        evidence = item.get("required_evidence")
        if not isinstance(cards, list) or not cards:
            fail(errors, f"{combo_id}: required_cards must be non-empty")
        else:
            for ref in cards:
                if ref not in ids:
                    fail(errors, f"{combo_id}: unknown required card {ref}")
        if not isinstance(evidence, list) or not evidence:
            fail(errors, f"{combo_id}: required_evidence must be non-empty")
    return len(seen)


def validate_brief_schema(data: Any, errors: list[str]) -> None:
    if not isinstance(data, dict):
        fail(errors, "brief_schema.json: root object is required")
        return
    if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail(errors, "brief_schema.json: must declare JSON Schema draft 2020-12")
    if "properties" not in data or "$defs" not in data:
        fail(errors, "brief_schema.json: properties and $defs are required")


def validate_adaptation_schema(data: Any, errors: list[str]) -> None:
    if not isinstance(data, dict):
        fail(errors, "adaptation_schema.json: root object is required")
        return
    if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail(errors, "adaptation_schema.json: must declare JSON Schema draft 2020-12")
    required = data.get("required")
    if set(required or []) != {"schema_version", "object_name", "facts", "lot_index", "diagnostics"}:
        fail(errors, "adaptation_schema.json: root required keys mismatch")
    diagnostics = data.get("properties", {}).get("diagnostics", {}) if isinstance(data.get("properties"), dict) else {}
    diag_required = diagnostics.get("required") if isinstance(diagnostics, dict) else None
    expected_diag = {"unmapped_field_ids", "omitted_field_ids", "lot_examples_available", "lot_selection", "house_link_available"}
    if set(diag_required or []) != expected_diag:
        fail(errors, "adaptation_schema.json: diagnostics required keys mismatch")


def validate_structured_finance_schema(data: Any, errors: list[str]) -> None:
    if not isinstance(data, dict):
        fail(errors, "structured_finance_schema.json: root object is required")
        return
    if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail(errors, "structured_finance_schema.json: must declare JSON Schema draft 2020-12")
    if set(data.get("required") or []) != {"schema_version", "object_name", "fresh_mcp", "facts"}:
        fail(errors, "structured_finance_schema.json: root required keys mismatch")
    if data.get("additionalProperties") is not False:
        fail(errors, "structured_finance_schema.json: root must be strict")
    properties = data.get("properties") if isinstance(data.get("properties"), dict) else {}
    facts = properties.get("facts") if isinstance(properties, dict) else None
    fact_properties = facts.get("properties") if isinstance(facts, dict) and isinstance(facts.get("properties"), dict) else {}
    expected_refs = {
        "mortgage_rate": "#/$defs/mortgage_rate_fact",
        "down_payment": "#/$defs/down_payment_fact",
        "installment_months": "#/$defs/months_fact",
    }
    for field_id, expected_ref in expected_refs.items():
        if fact_properties.get(field_id, {}).get("$ref") != expected_ref:
            fail(errors, f"structured_finance_schema.json: {field_id} ref mismatch")
    defs = data.get("$defs") if isinstance(data.get("$defs"), dict) else {}
    expected_sources = {
        "mortgage_rate_fact": {"mortgage_calc.min_percent", "mortgage.year_percent"},
        "down_payment_fact": {"mortgage_calc.min_fee", "mortgage.min_fee"},
    }
    for def_name, sources in expected_sources.items():
        definition = defs.get(def_name) if isinstance(defs, dict) else None
        source_schema = definition.get("properties", {}).get("source_field", {}) if isinstance(definition, dict) else {}
        if set(source_schema.get("enum") or []) != sources:
            fail(errors, f"structured_finance_schema.json: {def_name} source allowlist mismatch")


def validate_answer_composer_schemas(parsed: dict[str, Any], errors: list[str]) -> None:
    input_schema = parsed.get("answer_composer_input_schema.json")
    candidate_schema = parsed.get("answer_composer_candidate_schema.json")
    for name, data in (
        ("answer_composer_input_schema.json", input_schema),
        ("answer_composer_candidate_schema.json", candidate_schema),
    ):
        if not isinstance(data, dict):
            fail(errors, f"{name}: root object is required")
            continue
        if data.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            fail(errors, f"{name}: must declare JSON Schema draft 2020-12")
        if data.get("additionalProperties") is not False:
            fail(errors, f"{name}: root must be strict")
    if isinstance(input_schema, dict):
        if set(input_schema.get("required") or []) != {"schema_version", "answer_goal", "cta_template", "brief"}:
            fail(errors, "answer_composer_input_schema.json: required keys mismatch")
        props = input_schema.get("properties", {}) if isinstance(input_schema.get("properties"), dict) else {}
        if props.get("answer_goal", {}).get("const") != "present_selected":
            fail(errors, "answer_composer_input_schema.json: answer_goal const mismatch")
    if isinstance(candidate_schema, dict):
        expected = {"intro", "fact_summary", "benefit", "caveat", "final_question", "used_field_ids", "used_combination_ids"}
        if set(candidate_schema.get("required") or []) != expected:
            fail(errors, "answer_composer_candidate_schema.json: required keys mismatch")


def validate_example_consistency(parsed: dict[str, Any], errors: list[str]) -> None:
    example_input = parsed.get("example_input.json")
    example_brief = parsed.get("example_brief.json")
    if not isinstance(example_input, dict):
        fail(errors, "example_input.json: root object is required")
        return
    if not isinstance(example_brief, dict):
        fail(errors, "example_brief.json: root object is required")
        return
    try:
        from brief_builder import build_compact_brief

        regenerated = build_compact_brief(
            example_input,
            "family",
            fresh_mcp=True,
            requested_fields=("school", "kindergarten"),
            max_fields=5,
            object_name="Синтетический ЖК",
        )
    except Exception as exc:  # noqa: BLE001 - validator must report compactly
        fail(errors, f"example_brief.json: regeneration failed: {exc}")
        return
    if regenerated != example_brief:
        fail(errors, "example_brief.json: does not match build_compact_brief(example_input)")
    missing = BRIEF_REQUIRED_KEYS - set(example_brief)
    extra = set(example_brief) - BRIEF_REQUIRED_KEYS
    if missing:
        fail(errors, f"example_brief.json: missing keys: {', '.join(sorted(missing))}")
    if extra:
        fail(errors, f"example_brief.json: unexpected keys: {', '.join(sorted(extra))}")
    if example_brief.get("scenario") not in SCENARIOS:
        fail(errors, "example_brief.json: invalid scenario")
    diagnostics = example_brief.get("diagnostics")
    if not isinstance(diagnostics, dict):
        fail(errors, "example_brief.json: diagnostics object is required")
    else:
        for key in ("unknown_field_ids", "omitted_field_ids"):
            if not isinstance(diagnostics.get(key), list):
                fail(errors, f"example_brief.json: diagnostics.{key} must be list")


def validate_structured_finance_example_consistency(parsed: dict[str, Any], errors: list[str]) -> None:
    example_input = parsed.get("example_structured_finance_input.json")
    example_output = parsed.get("example_structured_finance_output.json")
    if not isinstance(example_input, dict):
        fail(errors, "example_structured_finance_input.json: root object is required")
        return
    if not isinstance(example_output, dict):
        fail(errors, "example_structured_finance_output.json: root object is required")
        return
    try:
        from structured_finance_adapter import adapt_structured_finance

        regenerated = adapt_structured_finance(example_input)
    except Exception as exc:  # noqa: BLE001 - validator must report compactly
        fail(errors, f"example_structured_finance_output.json: regeneration failed: {exc}")
        return
    if regenerated != example_output:
        fail(errors, "example_structured_finance_output.json: does not match adapt_structured_finance(example_input)")


def validate_answer_composer_example_consistency(parsed: dict[str, Any], errors: list[str]) -> None:
    example_input = parsed.get("example_answer_composer_input.json")
    example_candidate = parsed.get("example_answer_composer_candidate.json")
    example_result = parsed.get("example_answer_composer_result.json")
    if not isinstance(example_input, dict):
        fail(errors, "example_answer_composer_input.json: root object is required")
        return
    if not isinstance(example_candidate, dict):
        fail(errors, "example_answer_composer_candidate.json: root object is required")
        return
    if not isinstance(example_result, dict):
        fail(errors, "example_answer_composer_result.json: root object is required")
        return
    try:
        from answer_composer_simulator import simulate, validate_candidate

        regenerated = simulate(example_input, example_candidate)
        validation_errors = validate_candidate(example_input, example_candidate)
    except Exception as exc:  # noqa: BLE001 - validator must report compactly
        fail(errors, f"example_answer_composer_result.json: regeneration failed: {exc}")
        return
    if validation_errors:
        fail(errors, f"example_answer_composer_candidate.json: validator errors: {', '.join(validation_errors)}")
    if regenerated != example_result:
        fail(errors, "example_answer_composer_result.json: does not match answer_composer_simulator.simulate(input, candidate)")
    if example_result.get("manual_review_required") is not True:
        fail(errors, "example_answer_composer_result.json: manual_review_required must be true")
    if not example_result.get("valid") or example_result.get("errors") != []:
        fail(errors, "example_answer_composer_result.json: committed example must be valid")


def validate_answer_composer_matrix_consistency(parsed: dict[str, Any], errors: list[str]) -> None:
    matrix = parsed.get("answer_composer_matrix.json")
    stored_report = parsed.get("answer_composer_matrix_report.json")
    if not isinstance(matrix, list):
        fail(errors, "answer_composer_matrix.json: root list is required")
        return
    if not isinstance(stored_report, list):
        fail(errors, "answer_composer_matrix_report.json: root list is required")
        return
    try:
        from run_answer_composer_matrix import generate_report, validate_or_raise

        regenerated = generate_report(matrix)
        validate_or_raise(regenerated, matrix)
    except Exception as exc:  # noqa: BLE001 - validator must report compactly
        fail(errors, f"answer_composer_matrix_report.json: regeneration failed: {exc}")
        return
    if len(matrix) != 5:
        fail(errors, "answer_composer_matrix.json: expected exactly 5 cases")
    if regenerated != stored_report:
        fail(errors, "answer_composer_matrix_report.json: does not match run_answer_composer_matrix.generate_report()")


def validate_coverage_consistency(parsed: dict[str, Any], cards: list[dict[str, Any]], errors: list[str]) -> None:
    corpus = parsed.get("coverage_corpus.json")
    stored_report = parsed.get("coverage_report.json")
    if not isinstance(corpus, dict):
        fail(errors, "coverage_corpus.json: root object is required")
        return
    if corpus.get("version") != "v1" or not isinstance(corpus.get("cases"), list) or not corpus["cases"]:
        fail(errors, "coverage_corpus.json: version and non-empty cases are required")
    if not isinstance(stored_report, dict):
        fail(errors, "coverage_report.json: root object is required")
        return
    try:
        from coverage_audit import EXPECTED_UNREACHABLE, generate_report

        regenerated = generate_report()
    except Exception as exc:  # noqa: BLE001 - validator must report compactly
        fail(errors, f"coverage_report.json: regeneration failed: {exc}")
        return
    registry_ids = {card["field_id"] for card in cards}
    if len(registry_ids) != stored_report.get("registry_field_count"):
        fail(errors, "coverage_report.json: registry_field_count mismatch")
    if set(stored_report.get("expected_unreachable", {})) != set(EXPECTED_UNREACHABLE):
        fail(errors, "coverage_report.json: expected_unreachable mismatch")
    if regenerated != stored_report:
        fail(errors, "coverage_report.json: does not match coverage_audit.generate_report()")


def main() -> int:
    errors: list[str] = []
    parsed = {name: load_json(ROOT / name, errors) for name in ALL_JSON_FILES}
    cards: list[dict[str, Any]] = []
    ids: set[str] = set()

    for name in MODULE_FILES:
        data = parsed.get(name)
        if not isinstance(data, dict):
            fail(errors, f"{name}: module object is required")
            continue
        if data.get("module") != name.removesuffix(".json"):
            fail(errors, f"{name}: module name must match file name")
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list) or not raw_cards:
            fail(errors, f"{name}: cards must be non-empty list")
            continue
        for raw_card in raw_cards:
            card = validate_card(raw_card, errors)
            if card is None:
                continue
            field_id = str(card.get("field_id", ""))
            if field_id in ids:
                fail(errors, f"duplicate field_id: {field_id}")
            ids.add(field_id)
            cards.append(card)

    for card in cards:
        field_id = card["field_id"]
        for ref in card.get("combines_with", []):
            if ref not in ids:
                fail(errors, f"{field_id}: combines_with references unknown field_id {ref}")

    combo_count = validate_combinations(parsed.get("combinations.json"), ids, errors)
    validate_brief_schema(parsed.get("brief_schema.json"), errors)
    validate_adaptation_schema(parsed.get("adaptation_schema.json"), errors)
    validate_structured_finance_schema(parsed.get("structured_finance_schema.json"), errors)
    validate_answer_composer_schemas(parsed, errors)
    validate_example_consistency(parsed, errors)
    validate_structured_finance_example_consistency(parsed, errors)
    validate_answer_composer_example_consistency(parsed, errors)
    validate_answer_composer_matrix_consistency(parsed, errors)
    validate_coverage_consistency(parsed, cards, errors)

    if errors:
        print(f"FAIL files={len(ALL_JSON_FILES)} cards={len(cards)} combinations={combo_count} errors={len(errors)}")
        for error in errors:
            print(f"- {error}")
        return 1

    dynamic_count = sum(1 for card in cards if card["field_id"] in DYNAMIC_FIELDS)
    print(f"OK files={len(ALL_JSON_FILES)} modules={len(MODULE_FILES)} cards={len(cards)} dynamic={dynamic_count} combinations={combo_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
