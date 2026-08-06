#!/usr/bin/env python3
"""Fail-closed, TEST-only core for future V3/V5 route comparisons."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Sequence


SCHEMA_VERSION = "nmbot-v3-v5-compare-v1"
EXPECTED_SCENARIOS = (
    "explicit_pair_first_third",
    "generic_compare_all",
    "partial_enrichment_compare_cycle",
)
EXPECTED_USER_TURNS = (
    (
        "Нужна двушка для семьи",
        "Сравни первый и третий",
        "Выбираю Бусиновский парк",
        "Позови оператора",
        "{{TEST_PHONE}}",
    ),
    (
        "Нужна двушка для семьи",
        "Сравни их",
        "Выбираю первый вариант",
        "Позови оператора",
        "{{TEST_PHONE}}",
    ),
    (
        "Нужна двушка для семьи",
        "Сравни первый и второй",
        "Теперь сравни второй и третий",
        "Сравни первый и третий",
        "Выбираю Бусиновский парк",
        "Позови оператора",
        "{{TEST_PHONE}}",
    ),
)


class ManifestValidationError(ValueError):
    """Raised without embedding manifest content in a public receipt."""


class Runtime(str, Enum):
    V3 = "v3"
    V5 = "v5"


class RouteMarker(str, Enum):
    V3_TEST = "v3_test"
    V5_TEST = "v5_test"


class TurnEvent(str, Enum):
    BOT_MESSAGE = "BOT_MESSAGE"
    INVITE_AGENT = "INVITE_AGENT"


class ObservationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FALLBACK = "fallback"


class ResultStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class FailureStage(str, Enum):
    PREFLIGHT = "preflight"
    TURN = "turn"
    BRIDGE = "bridge"
    TERMINAL = "terminal"
    SUITE = "suite"


class FailureCode(str, Enum):
    V5_ROUTE_UNAVAILABLE = "v5_route_unavailable"
    ROUTE_UNAVAILABLE = "route_unavailable"
    UNKNOWN_RUNTIME = "unknown_runtime"
    MARKER_MISMATCH = "marker_mismatch"
    ROUTE_CALL_FAILED = "route_call_failed"
    TURN_FAILED = "turn_failed"
    FALLBACK_OBSERVED = "fallback_observed"
    NON_TERMINAL_OBSERVATION = "non_terminal_observation"
    EARLY_AGENT_INVITE = "early_agent_invite"
    BRIDGE_EVIDENCE_MISSING = "bridge_evidence_missing"
    TERMINAL_OUTCOME_INVALID = "terminal_outcome_invalid"
    PRIOR_SCENARIO_NOT_GREEN = "prior_scenario_not_green"
    INVALID_SCENARIO = "invalid_scenario"


class TerminalOutcome(str, Enum):
    BOT_MESSAGE = "BOT_MESSAGE"
    INVITE_AGENT = "INVITE_AGENT"


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    ordinal: int
    user_turns: tuple[str, ...]
    expected_stages: tuple[str, ...]
    terminal_policy: str


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    fixture_identity: str
    source: Mapping[str, str]
    scenarios: tuple[Scenario, ...]
    manifest_digest: str


@dataclass(frozen=True)
class RouteProbe:
    available: bool
    marker: Optional[RouteMarker]


@dataclass(frozen=True)
class TurnObservation:
    marker: RouteMarker
    status: ObservationStatus
    event: Optional[TurnEvent]


@dataclass(frozen=True)
class BridgeReceipt:
    marker: RouteMarker
    evidence: bool
    terminal_outcome: Optional[TerminalOutcome]


class RouteAdapter(Protocol):
    """Opaque TEST adapter boundary; implementations live outside this harness."""

    def probe(self) -> RouteProbe:
        ...

    def send(self, user_turn: str) -> TurnObservation:
        ...

    def bridge_receipt(self) -> BridgeReceipt:
        ...


@dataclass(frozen=True)
class ScenarioResult:
    schema_version: str
    manifest_digest: str
    scenario_ordinal: int
    requested_runtime: str
    status: ResultStatus
    failure_stage: Optional[FailureStage]
    failure_code: Optional[FailureCode]
    attempted: bool
    comparable: bool
    completed_turn_count: int
    marker_valid: bool
    bridge_evidence: bool
    terminal_outcome: Optional[TerminalOutcome]

    def to_safe_dict(self) -> dict[str, Any]:
        """Return the complete and intentionally bounded public receipt."""
        return {
            "schema_version": self.schema_version,
            "manifest_digest": self.manifest_digest,
            "scenario_ordinal": self.scenario_ordinal,
            "requested_runtime": self.requested_runtime,
            "status": self.status.value,
            "failure_stage": self.failure_stage.value if self.failure_stage else None,
            "failure_code": self.failure_code.value if self.failure_code else None,
            "attempted": self.attempted,
            "comparable": self.comparable,
            "completed_turn_count": self.completed_turn_count,
            "marker_valid": self.marker_valid,
            "bridge_evidence": self.bridge_evidence,
            "terminal_outcome": (
                self.terminal_outcome.value if self.terminal_outcome else None
            ),
        }


def _canonical_digest(document: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ManifestValidationError("manifest shape is not canonical")


def validate_manifest(document: Any) -> Manifest:
    if not isinstance(document, dict):
        raise ManifestValidationError("manifest must be an object")
    _require_exact_keys(
        document, {"schema_version", "fixture_identity", "source", "scenarios"}
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise ManifestValidationError("unsupported manifest schema")
    if not isinstance(document["fixture_identity"], str) or not document[
        "fixture_identity"
    ].strip():
        raise ManifestValidationError("fixture identity is required")
    source = document["source"]
    if not isinstance(source, dict):
        raise ManifestValidationError("source must be an object")
    _require_exact_keys(source, {"report_path"})
    if not isinstance(source["report_path"], str) or not source[
        "report_path"
    ].strip():
        raise ManifestValidationError("source report path is required")

    raw_scenarios = document["scenarios"]
    if not isinstance(raw_scenarios, list) or len(raw_scenarios) != 3:
        raise ManifestValidationError("exactly three scenarios are required")
    scenarios: list[Scenario] = []
    for index, raw in enumerate(raw_scenarios):
        if not isinstance(raw, dict):
            raise ManifestValidationError("scenario must be an object")
        _require_exact_keys(
            raw,
            {"id", "ordinal", "user_turns", "expected_stages", "terminal_policy"},
        )
        ordinal = index + 1
        if raw["id"] != EXPECTED_SCENARIOS[index] or raw["ordinal"] != ordinal:
            raise ManifestValidationError("scenario identity or order is not canonical")
        turns = raw["user_turns"]
        if (
            not isinstance(turns, list)
            or not turns
            or any(not isinstance(turn, str) or not turn.strip() for turn in turns)
            or tuple(turns) != EXPECTED_USER_TURNS[index]
        ):
            raise ManifestValidationError("scenario turns are not canonical")
        stages = raw["expected_stages"]
        if (
            not isinstance(stages, list)
            or len(stages) != len(turns)
            or any(not isinstance(stage, str) or not stage.strip() for stage in stages)
        ):
            raise ManifestValidationError("expected stages must align with turns")
        if raw["terminal_policy"] != "bridge_terminal_event_required":
            raise ManifestValidationError("terminal policy is not canonical")
        scenarios.append(
            Scenario(
                scenario_id=raw["id"],
                ordinal=ordinal,
                user_turns=tuple(turns),
                expected_stages=tuple(stages),
                terminal_policy=raw["terminal_policy"],
            )
        )
    return Manifest(
        schema_version=document["schema_version"],
        fixture_identity=document["fixture_identity"],
        source=dict(source),
        scenarios=tuple(scenarios),
        manifest_digest=_canonical_digest(document),
    )


def load_manifest(path: str | Path) -> Manifest:
    with Path(path).open("r", encoding="utf-8") as handle:
        return validate_manifest(json.load(handle))


def _result(
    manifest: Manifest,
    ordinal: int,
    runtime: str,
    status: ResultStatus,
    *,
    stage: Optional[FailureStage] = None,
    code: Optional[FailureCode] = None,
    attempted: bool = False,
    comparable: bool = False,
    completed: int = 0,
    marker_valid: bool = False,
    bridge_evidence: bool = False,
    terminal: Optional[TerminalOutcome] = None,
) -> ScenarioResult:
    return ScenarioResult(
        schema_version=SCHEMA_VERSION,
        manifest_digest=manifest.manifest_digest,
        scenario_ordinal=ordinal,
        requested_runtime=runtime,
        status=status,
        failure_stage=stage,
        failure_code=code,
        attempted=attempted,
        comparable=comparable,
        completed_turn_count=completed,
        marker_valid=marker_valid,
        bridge_evidence=bridge_evidence,
        terminal_outcome=terminal,
    )


def run_scenario(
    manifest: Manifest,
    scenario_ordinal: int,
    requested_runtime: str | Runtime,
    route: Optional[RouteAdapter],
) -> ScenarioResult:
    runtime_text = (
        requested_runtime.value
        if isinstance(requested_runtime, Runtime)
        else str(requested_runtime)
    )
    try:
        runtime = Runtime(runtime_text)
    except ValueError:
        return _result(
            manifest,
            scenario_ordinal,
            runtime_text,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=FailureCode.UNKNOWN_RUNTIME,
        )
    if not 1 <= scenario_ordinal <= len(manifest.scenarios):
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=FailureCode.INVALID_SCENARIO,
        )
    if route is None:
        code = (
            FailureCode.V5_ROUTE_UNAVAILABLE
            if runtime is Runtime.V5
            else FailureCode.ROUTE_UNAVAILABLE
        )
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=code,
        )

    expected_marker = (
        RouteMarker.V3_TEST if runtime is Runtime.V3 else RouteMarker.V5_TEST
    )
    try:
        probe = route.probe()
    except Exception:
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=FailureCode.ROUTE_CALL_FAILED,
        )
    if not probe.available:
        code = (
            FailureCode.V5_ROUTE_UNAVAILABLE
            if runtime is Runtime.V5
            else FailureCode.ROUTE_UNAVAILABLE
        )
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=code,
        )
    if probe.marker is not expected_marker:
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.BLOCKED,
            stage=FailureStage.PREFLIGHT,
            code=FailureCode.MARKER_MISMATCH,
        )

    scenario = manifest.scenarios[scenario_ordinal - 1]
    completed = 0
    attempted = False
    for turn_index, user_turn in enumerate(scenario.user_turns):
        attempted = True
        try:
            observation = route.send(user_turn)
        except Exception:
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TURN,
                code=FailureCode.ROUTE_CALL_FAILED,
                attempted=True,
                completed=completed,
                marker_valid=True,
            )
        if observation.marker is not expected_marker:
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TURN,
                code=FailureCode.MARKER_MISMATCH,
                attempted=True,
                completed=completed,
            )
        if observation.status is ObservationStatus.FALLBACK:
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TURN,
                code=FailureCode.FALLBACK_OBSERVED,
                attempted=True,
                completed=completed,
                marker_valid=True,
            )
        if observation.status is not ObservationStatus.SUCCEEDED:
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TURN,
                code=FailureCode.TURN_FAILED,
                attempted=True,
                completed=completed,
                marker_valid=True,
            )
        if observation.event not in (TurnEvent.BOT_MESSAGE, TurnEvent.INVITE_AGENT):
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TURN,
                code=FailureCode.NON_TERMINAL_OBSERVATION,
                attempted=True,
                completed=completed,
                marker_valid=True,
            )
        if (
            observation.event is TurnEvent.INVITE_AGENT
            and turn_index != len(scenario.user_turns) - 1
        ):
            return _result(
                manifest,
                scenario_ordinal,
                runtime.value,
                ResultStatus.FAILED,
                stage=FailureStage.TERMINAL,
                code=FailureCode.EARLY_AGENT_INVITE,
                attempted=True,
                completed=completed + 1,
                marker_valid=True,
                terminal=TerminalOutcome.INVITE_AGENT,
            )
        completed += 1

    try:
        bridge = route.bridge_receipt()
    except Exception:
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.FAILED,
            stage=FailureStage.BRIDGE,
            code=FailureCode.ROUTE_CALL_FAILED,
            attempted=attempted,
            completed=completed,
            marker_valid=True,
        )
    if bridge.marker is not expected_marker:
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.FAILED,
            stage=FailureStage.BRIDGE,
            code=FailureCode.MARKER_MISMATCH,
            attempted=attempted,
            completed=completed,
        )
    if not bridge.evidence:
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.FAILED,
            stage=FailureStage.BRIDGE,
            code=FailureCode.BRIDGE_EVIDENCE_MISSING,
            attempted=attempted,
            completed=completed,
            marker_valid=True,
        )
    if bridge.terminal_outcome not in (
        TerminalOutcome.BOT_MESSAGE,
        TerminalOutcome.INVITE_AGENT,
    ):
        return _result(
            manifest,
            scenario_ordinal,
            runtime.value,
            ResultStatus.FAILED,
            stage=FailureStage.TERMINAL,
            code=FailureCode.TERMINAL_OUTCOME_INVALID,
            attempted=attempted,
            completed=completed,
            marker_valid=True,
            bridge_evidence=True,
        )
    return _result(
        manifest,
        scenario_ordinal,
        runtime.value,
        ResultStatus.PASSED,
        attempted=attempted,
        comparable=True,
        completed=completed,
        marker_valid=True,
        bridge_evidence=True,
        terminal=bridge.terminal_outcome,
    )


def run_suite(
    manifest: Manifest,
    requested_runtime: str | Runtime,
    route: Optional[RouteAdapter] | Sequence[Optional[RouteAdapter]],
) -> tuple[ScenarioResult, ...]:
    """Run scenario one as the gate, then scenarios two and three."""
    runtime_text = (
        requested_runtime.value
        if isinstance(requested_runtime, Runtime)
        else str(requested_runtime)
    )
    if isinstance(route, Sequence):
        if len(route) != 3:
            raise ValueError("run_suite requires exactly three route adapters")
        routes = tuple(route)
    else:
        routes = (route, route, route)
    first = run_scenario(manifest, 1, requested_runtime, routes[0])
    results = [first]
    if first.status is not ResultStatus.PASSED:
        for ordinal in (2, 3):
            results.append(
                _result(
                    manifest,
                    ordinal,
                    runtime_text,
                    ResultStatus.BLOCKED,
                    stage=FailureStage.SUITE,
                    code=FailureCode.PRIOR_SCENARIO_NOT_GREEN,
                )
            )
        return tuple(results)
    results.extend(
        run_scenario(manifest, ordinal, requested_runtime, routes[ordinal - 1])
        for ordinal in (2, 3)
    )
    return tuple(results)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--runtime", choices=[runtime.value for runtime in Runtime])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--validate-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight and not args.runtime:
        parser.error("--runtime is required with --preflight")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    except (OSError, json.JSONDecodeError, ManifestValidationError):
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "status": "invalid", "failure_code": "manifest_invalid"},
                sort_keys=True,
            )
        )
        return 2
    if args.validate_manifest:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "manifest_digest": manifest.manifest_digest,
                    "scenario_count": len(manifest.scenarios),
                    "status": "valid",
                },
                sort_keys=True,
            )
        )
        return 0
    result = run_scenario(manifest, 1, args.runtime, None)
    print(json.dumps(result.to_safe_dict(), sort_keys=True))
    return 0 if result.status is ResultStatus.PASSED else 3


if __name__ == "__main__":
    raise SystemExit(main())
