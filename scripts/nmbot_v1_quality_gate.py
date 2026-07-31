from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "nmbot_v1_quality_scenarios.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nmbot_v1.contracts import V1Error, V1IntentPlan  # noqa: E402
from nmbot_v1.runtime import run_turn_sync  # noqa: E402
from nmbot_v1.search_contract import V1OptionCard  # noqa: E402


SCHEMA_VERSION = 1
ROOT_KEYS = {"schema_version", "suite", "records"}
RECORD_KEYS = {"id", "title", "classes", "turns"}
TURN_KEYS = {"id", "user", "plan", "search", "expect"}
PLAN_KEYS = {
    "goal",
    "viewpoint",
    "constraints_delta",
    "selected_option_ref",
    "selected_lot_ref",
    "requested_facts",
    "operator_intent",
    "clarification",
    "contact_name",
    "contact_phone",
    "confidence",
}
EXPECT_KEYS = {
    "stage",
    "action",
    "answer_kind",
    "state_subset",
    "state_unchanged",
    "search_calls_total",
    "selected_project_ref",
    "selected_lot_ref",
    "requested_facts",
    "response_required",
    "response_forbidden",
    "exact_max",
    "near_max",
    "question_count",
    "trace_safe_code",
}
SEARCH_KEYS = {"cards", "attempts", "raises"}
CARD_KEYS = {"ref", "name", "facts", "evidence"}
_BLOCKED_WORDS = (
    "sec" + "ret",
    "tok" + "en",
    "api[_-]?" + "key",
    "b" + "(?:earer)",
    "p" + "(?:assword)",
    "raw[_-]?" + "payload",
    "provider " + "payload",
    "ji" + "vo",
    "v" + "ps",
    "prompt" + "foo",
    "@",
)
SENSITIVE_RE = re.compile("(" + "|".join(_BLOCKED_WORDS) + ")", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
REQUIRED_CLASSES = {
    "first_search",
    "refinement_preserving_prior_hard_constraints",
    "expand_search_truthful_no_repeat_claim",
    "current_options_without_search",
    "selected_project",
    "lot_funnel",
    "fact_check",
    "exact_result",
    "near_only_result",
    "empty_inventory",
    "operator_accept",
    "operator_decline",
    "contact_privacy",
    "off_topic",
    "prompt_injection_safe_plan",
    "provider_error_fails_closed",
}


class QualityGateError(ValueError):
    pass


class FixtureValidationError(QualityGateError):
    pass


def _reject_unknown(obj: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = set(obj) - allowed
    if extra:
        raise FixtureValidationError(f"{label} unknown keys: {','.join(sorted(extra))}")


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureValidationError(f"{label} must be object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise FixtureValidationError(f"{label} must be list")
    return value


def _non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FixtureValidationError(f"{label} must be non-empty string")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FixtureValidationError(f"{label} must be non-negative int")
    return value


def _list_of_strings(value: Any, label: str) -> list[str]:
    items = _require_list(value, label)
    if any(not isinstance(item, str) or not item for item in items):
        raise FixtureValidationError(f"{label} must be list of strings")
    return items


def _synthetic_plan_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "goal": plan["goal"],
        "viewpoint": plan.get("viewpoint", "buyer"),
        "constraints_delta": plan.get("constraints_delta", {"hard": {}, "preferences": {}}),
        "selected_option_ref": plan.get("selected_option_ref"),
        "selected_lot_ref": plan.get("selected_lot_ref"),
        "requested_facts": plan.get("requested_facts", []),
        "operator_intent": plan.get("operator_intent", "none"),
        "clarification": plan.get("clarification"),
        "contact_name": plan.get("contact_name"),
        "contact_phone": plan.get("contact_phone"),
        "confidence": plan.get("confidence", 1),
    }


def load_fixture(path: Path = FIXTURE_PATH) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FixtureValidationError("fixture_read_error") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureValidationError("fixture_json_error") from exc
    validate_fixture(data)
    return data, digest


def validate_fixture(data: Mapping[str, Any]) -> None:
    data = _require_dict(data, "root")
    _reject_unknown(data, ROOT_KEYS, "root")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise FixtureValidationError("root.schema_version must be 1")
    if data.get("suite") != "nmbot_v1_quality_stage_c":
        raise FixtureValidationError("root.suite mismatch")
    records = _require_list(data.get("records"), "records")
    if not records:
        raise FixtureValidationError("records must be non-empty")
    seen: set[str] = set()
    classes: set[str] = set()
    for idx, raw_record in enumerate(records):
        record = _require_dict(raw_record, f"records[{idx}]")
        _reject_unknown(record, RECORD_KEYS, f"records[{idx}]")
        rid = record.get("id")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9_]{3,80}", rid):
            raise FixtureValidationError(f"records[{idx}].id must be safe unique id")
        if rid in seen:
            raise FixtureValidationError(f"duplicate record id: {rid}")
        seen.add(rid)
        if not isinstance(record.get("title"), str) or not record["title"].strip():
            raise FixtureValidationError(f"{rid}.title must be string")
        rec_classes = _require_list(record.get("classes"), f"{rid}.classes")
        if not rec_classes or any(not isinstance(c, str) or c not in REQUIRED_CLASSES for c in rec_classes):
            raise FixtureValidationError(f"{rid}.classes contains unknown class")
        classes.update(rec_classes)
        turns = _require_list(record.get("turns"), f"{rid}.turns")
        if not turns:
            raise FixtureValidationError(f"{rid}.turns must be non-empty")
        turn_ids: set[str] = set()
        for tdx, raw_turn in enumerate(turns):
            _validate_turn(rid, tdx, raw_turn, turn_ids)
    missing = REQUIRED_CLASSES - classes
    if missing:
        raise FixtureValidationError("missing Stage C classes: " + ",".join(sorted(missing)))


def _validate_turn(rid: str, tdx: int, raw_turn: Any, turn_ids: set[str]) -> None:
    turn = _require_dict(raw_turn, f"{rid}.turns[{tdx}]")
    _reject_unknown(turn, TURN_KEYS, f"{rid}.turns[{tdx}]")
    tid = turn.get("id")
    if not isinstance(tid, str) or not re.fullmatch(r"[a-z0-9_]{2,60}", tid) or tid in turn_ids:
        raise FixtureValidationError(f"{rid}.turns[{tdx}].id invalid/duplicate")
    turn_ids.add(tid)
    if not isinstance(turn.get("user"), str) or not turn["user"].strip():
        raise FixtureValidationError(f"{rid}.{tid}.user must be string")
    plan = _require_dict(turn.get("plan"), f"{rid}.{tid}.plan")
    _reject_unknown(plan, PLAN_KEYS, f"{rid}.{tid}.plan")
    if "goal" not in plan:
        raise FixtureValidationError(f"{rid}.{tid}.plan.goal required")
    try:
        V1IntentPlan.from_dict(_synthetic_plan_payload(plan))
    except V1Error as exc:
        raise FixtureValidationError(f"{rid}.{tid}.plan invalid") from exc
    if "search" in turn:
        search = _require_dict(turn["search"], f"{rid}.{tid}.search")
        _reject_unknown(search, SEARCH_KEYS, f"{rid}.{tid}.search")
        if search.get("raises") is not None and search.get("raises") != "provider_error":
            raise FixtureValidationError(f"{rid}.{tid}.search.raises unsupported")
        if "cards" in search:
            cards = _require_list(search.get("cards"), f"{rid}.{tid}.search.cards")
        elif search.get("raises") == "provider_error":
            cards = []
        else:
            raise FixtureValidationError(f"{rid}.{tid}.search.cards must be list")
        if search.get("raises") == "provider_error" and cards:
            raise FixtureValidationError(f"{rid}.{tid}.search cannot mix provider_error and cards")
        attempts = search.get("attempts", [])
        if "attempts" in search:
            attempts = _require_list(attempts, f"{rid}.{tid}.search.attempts")
            if any(not isinstance(item, dict) for item in attempts):
                raise FixtureValidationError(f"{rid}.{tid}.search.attempts must be list of objects")
        for cdx, card in enumerate(cards):
            card = _require_dict(card, f"{rid}.{tid}.search.cards[{cdx}]")
            _reject_unknown(card, CARD_KEYS, f"{rid}.{tid}.search.cards[{cdx}]")
            for key in ("ref", "name", "facts", "evidence"):
                if key not in card:
                    raise FixtureValidationError(f"{rid}.{tid}.search.cards[{cdx}].{key} required")
            try:
                V1OptionCard.from_dict(card)
            except V1Error as exc:
                raise FixtureValidationError(f"{rid}.{tid}.search.cards[{cdx}] invalid") from exc
    expect = _require_dict(turn.get("expect"), f"{rid}.{tid}.expect")
    _reject_unknown(expect, EXPECT_KEYS, f"{rid}.{tid}.expect")
    if "stage" not in expect or "action" not in expect or "answer_kind" not in expect:
        raise FixtureValidationError(f"{rid}.{tid}.expect requires stage/action/answer_kind")
    for key in ("stage", "action", "answer_kind"):
        _non_empty_str(expect[key], f"{rid}.{tid}.expect.{key}")
    if "state_subset" in expect:
        _require_dict(expect["state_subset"], f"{rid}.{tid}.expect.state_subset")
    if "state_unchanged" in expect and not isinstance(expect["state_unchanged"], bool):
        raise FixtureValidationError(f"{rid}.{tid}.expect.state_unchanged must be bool")
    for key in ("search_calls_total", "exact_max", "near_max", "question_count"):
        if key in expect:
            _non_negative_int(expect[key], f"{rid}.{tid}.expect.{key}")
    for key in ("selected_project_ref", "selected_lot_ref", "trace_safe_code"):
        if key in expect:
            _non_empty_str(expect[key], f"{rid}.{tid}.expect.{key}")
    for key in ("requested_facts", "response_required", "response_forbidden"):
        if key in expect:
            _list_of_strings(expect[key], f"{rid}.{tid}.expect.{key}")


class SyntheticPlanner:
    def __init__(self, plan: Mapping[str, Any]):
        self.plan_data = plan

    def plan(self, _planner_input: Mapping[str, Any]) -> dict[str, Any]:
        return _synthetic_plan_payload(self.plan_data)


class SyntheticSearch:
    def __init__(self, spec: Mapping[str, Any] | None):
        self.spec = spec
        self.requests: list[Any] = []

    def search(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.spec and self.spec.get("raises") == "provider_error":
            raise RuntimeError("provider failed")
        cards = [] if not self.spec else self.spec.get("cards", [])
        attempts = [] if not self.spec else self.spec.get("attempts", [{"status": "ok"}])
        return {"schema_version": 1, "cards": cards, "attempts": attempts}


def run_case(record: Mapping[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] | None = None
    search_calls_total = 0
    safe_turns: list[dict[str, Any]] = []
    for turn in record["turns"]:
        before_state = copy.deepcopy(state)
        search_port = SyntheticSearch(turn.get("search")) if "search" in turn else None
        result = run_turn_sync(turn["user"], state, SyntheticPlanner(turn["plan"]), search_port=search_port)
        search_calls_total += len(search_port.requests) if search_port else 0
        _assert_turn(record["id"], turn["id"], turn["expect"], result, before_state, search_calls_total, search_port)
        _assert_no_leak(result)
        state = result.state
        safe_turns.append({"id": turn["id"], "stage": result.stage, "action": result.action, "answer_kind": result.answer_kind})
    return {"id": record["id"], "turns": safe_turns}


def _assert_turn(rid: str, tid: str, expect: Mapping[str, Any], result: Any, before_state: Any, search_calls_total: int, search_port: SyntheticSearch | None) -> None:
    label = f"{rid}.{tid}"
    for attr in ("stage", "action", "answer_kind"):
        if getattr(result, attr) != expect[attr]:
            raise QualityGateError(f"{label} expected {attr}={expect[attr]} got {getattr(result, attr)}")
    if expect.get("state_unchanged") and result.state != (before_state or _clean_state()):
        raise QualityGateError(f"{label} expected state unchanged")
    if "state_subset" in expect:
        _assert_subset(label, result.state, expect["state_subset"])
    if "search_calls_total" in expect and search_calls_total != expect["search_calls_total"]:
        raise QualityGateError(f"{label} expected search_calls_total={expect['search_calls_total']} got {search_calls_total}")
    if search_port and search_port.requests:
        request = search_port.requests[-1]
        if "selected_project_ref" in expect and request.selected_project_ref != expect["selected_project_ref"]:
            raise QualityGateError(f"{label} selected_project_ref mismatch")
        if "requested_facts" in expect and list(request.requested_facts) != expect["requested_facts"]:
            raise QualityGateError(f"{label} requested_facts mismatch")
    if "selected_project_ref" in expect and not (search_port and search_port.requests):
        selected = (result.state.get("selected_project") or {}).get("ref")
        if selected != expect["selected_project_ref"]:
            raise QualityGateError(f"{label} selected_project ref mismatch")
    if "selected_lot_ref" in expect:
        selected_lot = (result.state.get("selected_lot") or {}).get("ref")
        if selected_lot != expect["selected_lot_ref"]:
            raise QualityGateError(f"{label} selected_lot ref mismatch")
    if "exact_max" in expect and len(result.state.get("visible_options") or []) > int(expect["exact_max"]):
        raise QualityGateError(f"{label} exact cap exceeded")
    if "near_max" in expect and len(result.state.get("visible_options") or []) > int(expect["near_max"]):
        raise QualityGateError(f"{label} near cap exceeded")
    if "question_count" in expect and result.response_text.count("?") != int(expect["question_count"]):
        raise QualityGateError(f"{label} question count mismatch")
    for fragment in expect.get("response_required", []):
        if fragment not in result.response_text:
            raise QualityGateError(f"{label} missing response fragment")
    folded_response = result.response_text.casefold()
    for fragment in expect.get("response_forbidden", []):
        if str(fragment).casefold() in folded_response:
            raise QualityGateError(f"{label} forbidden response fragment")
    if "trace_safe_code" in expect and result.trace.get("safe_code") != expect["trace_safe_code"]:
        raise QualityGateError(f"{label} safe_code mismatch")
    if result.runtime_version != "V1" or result.trace.get("runtime_version") != "V1":
        raise QualityGateError(f"{label} runtime attribution mismatch")
    path = result.trace.get("execution_path") or {}
    if path.get("path_id") != "v1.turn.v1":
        raise QualityGateError(f"{label} execution path is not V1")


def _assert_subset(label: str, actual: Any, expected: Any) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise QualityGateError(f"{label} state subset type mismatch")
        for key, value in expected.items():
            if key not in actual:
                raise QualityGateError(f"{label} missing state key {key}")
            _assert_subset(label, actual[key], value)
    else:
        if actual != expected:
            raise QualityGateError(f"{label} state subset mismatch")


def _clean_state() -> dict[str, Any]:
    from nmbot_v1.state import V1ConversationState

    return V1ConversationState.clean().to_dict()


def _assert_no_leak(result: Any) -> None:
    blob = json.dumps({"response": result.response_text, "state": result.state, "trace": result.trace}, ensure_ascii=False, sort_keys=True)
    if SENSITIVE_RE.search(blob):
        raise QualityGateError("unsafe sensitive/internal marker leaked")
    raw_phone_hits = [hit for hit in PHONE_RE.findall(blob) if not hit.startswith("***")]
    if raw_phone_hits:
        raise QualityGateError("raw phone leaked")


def run_all(case_id: str | None = None, path: Path = FIXTURE_PATH) -> tuple[int, dict[str, Any]]:
    try:
        fixture, digest = load_fixture(path)
    except FixtureValidationError as exc:
        return 2, _summary(path, "", 0, [], {"id": "fixture", "error": _fixture_error_code(exc)})
    records = fixture["records"]
    if case_id:
        records = [r for r in records if r["id"] == case_id]
        if not records:
            return 2, _summary(path, digest, 0, [], {"id": case_id, "error": "unknown_case"})
    passed: list[dict[str, Any]] = []
    for record in records:
        try:
            passed.append(run_case(record))
        except QualityGateError as exc:
            return 1, _summary(path, digest, len(fixture["records"]), passed, {"id": record["id"], "error": str(exc)})
    return 0, _summary(path, digest, len(fixture["records"]), passed, None)


def _fixture_error_code(exc: FixtureValidationError) -> str:
    text = str(exc)
    if text in {"fixture_read_error", "fixture_json_error"}:
        return text
    return "fixture_validation_error"


def _summary(path: Path, digest: str, total: int, passed: list[dict[str, Any]], failure: dict[str, Any] | None) -> dict[str, Any]:
    try:
        fixture_path = str(path.relative_to(ROOT))
    except ValueError:
        fixture_path = path.name
    return {
        "suite": "nmbot_v1_quality_stage_c",
        "fixture": {"path": fixture_path, "sha256": digest},
        "total_cases": total,
        "passed_cases": len(passed),
        "failed_case": failure,
        "status": "passed" if failure is None else "failed",
        "case_ids": [item["id"] for item in passed],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay deterministic offline NMBot V1 Stage C quality fixture.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--case")
    args = parser.parse_args(argv)
    code, summary = run_all(args.case)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
