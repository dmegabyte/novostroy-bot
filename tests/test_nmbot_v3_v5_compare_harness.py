import json
from pathlib import Path

from scripts.nmbot_v3_v5_compare_harness import (
    BridgeReceipt,
    FailureCode,
    ObservationStatus,
    ResultStatus,
    RouteMarker,
    RouteProbe,
    TerminalOutcome,
    TurnEvent,
    TurnObservation,
    load_manifest,
    run_scenario,
    run_suite,
)


MANIFEST_PATH = Path(__file__).parent / "fixtures" / "nmbot_v3_v5_compare_manifest.json"


class FakeRoute:
    def __init__(
        self,
        marker=RouteMarker.V3_TEST,
        observations=None,
        bridge=None,
        available=True,
    ):
        self.marker = marker
        self.observations = list(observations or [])
        self.bridge = bridge or BridgeReceipt(
            marker=marker,
            evidence=True,
            terminal_outcome=TerminalOutcome.BOT_MESSAGE,
        )
        self.available = available
        self.send_count = 0

    def probe(self):
        return RouteProbe(available=self.available, marker=self.marker)

    def send(self, user_turn):
        self.send_count += 1
        if self.observations:
            return self.observations.pop(0)
        return TurnObservation(
            marker=self.marker,
            status=ObservationStatus.SUCCEEDED,
            event=TurnEvent.BOT_MESSAGE,
        )

    def bridge_receipt(self):
        return self.bridge


def test_manifest_exact_count_order_and_stable_digest():
    first = load_manifest(MANIFEST_PATH)
    second = load_manifest(MANIFEST_PATH)
    assert [scenario.scenario_id for scenario in first.scenarios] == [
        "explicit_pair_first_third",
        "generic_compare_all",
        "partial_enrichment_compare_cycle",
    ]
    assert [scenario.ordinal for scenario in first.scenarios] == [1, 2, 3]
    assert len(first.manifest_digest) == 64
    assert first.manifest_digest == second.manifest_digest
    assert first.manifest_digest == (
        "3dced1f644dded1baa867313fe99013a10cb4e0f4b25ea8424c1ed2a364320d0"
    )


def test_same_manifest_digest_is_used_for_v3_and_v5():
    manifest = load_manifest(MANIFEST_PATH)
    v3 = run_scenario(manifest, 1, "v3", None)
    v5 = run_scenario(manifest, 1, "v5", None)
    assert v3.manifest_digest == v5.manifest_digest == manifest.manifest_digest


def test_missing_v5_route_is_blocked_without_send():
    manifest = load_manifest(MANIFEST_PATH)
    result = run_scenario(manifest, 1, "v5", None)
    assert result.status is ResultStatus.BLOCKED
    assert result.failure_code is FailureCode.V5_ROUTE_UNAVAILABLE
    assert result.attempted is False
    assert result.comparable is False
    assert result.completed_turn_count == 0


def test_marker_mismatch_stops_before_send():
    manifest = load_manifest(MANIFEST_PATH)
    route = FakeRoute(marker=RouteMarker.V5_TEST)
    result = run_scenario(manifest, 1, "v3", route)
    assert result.failure_code is FailureCode.MARKER_MISMATCH
    assert result.attempted is False
    assert route.send_count == 0


def test_first_failed_turn_stops_remaining_turns():
    manifest = load_manifest(MANIFEST_PATH)
    route = FakeRoute(
        observations=[
            TurnObservation(
                RouteMarker.V3_TEST,
                ObservationStatus.SUCCEEDED,
                TurnEvent.BOT_MESSAGE,
            ),
            TurnObservation(
                RouteMarker.V3_TEST, ObservationStatus.FAILED, None
            ),
        ]
    )
    result = run_scenario(manifest, 1, "v3", route)
    assert result.failure_code is FailureCode.TURN_FAILED
    assert result.completed_turn_count == 1
    assert route.send_count == 2


def test_fallback_cannot_pass():
    manifest = load_manifest(MANIFEST_PATH)
    route = FakeRoute(
        observations=[
            TurnObservation(
                RouteMarker.V3_TEST,
                ObservationStatus.FALLBACK,
                TurnEvent.BOT_MESSAGE,
            )
        ]
    )
    result = run_scenario(manifest, 1, "v3", route)
    assert result.status is ResultStatus.FAILED
    assert result.failure_code is FailureCode.FALLBACK_OBSERVED
    assert result.comparable is False


def test_missing_bridge_evidence_and_non_terminal_outcome_cannot_pass():
    manifest = load_manifest(MANIFEST_PATH)
    no_evidence = FakeRoute(
        bridge=BridgeReceipt(RouteMarker.V3_TEST, False, TerminalOutcome.BOT_MESSAGE)
    )
    invalid_terminal = FakeRoute(
        bridge=BridgeReceipt(RouteMarker.V3_TEST, True, None)
    )
    evidence_result = run_scenario(manifest, 1, "v3", no_evidence)
    terminal_result = run_scenario(manifest, 1, "v3", invalid_terminal)
    assert evidence_result.failure_code is FailureCode.BRIDGE_EVIDENCE_MISSING
    assert terminal_result.failure_code is FailureCode.TERMINAL_OUTCOME_INVALID
    assert evidence_result.status is terminal_result.status is ResultStatus.FAILED


def test_scenarios_two_and_three_wait_for_green_first_scenario():
    manifest = load_manifest(MANIFEST_PATH)
    first = FakeRoute(
        observations=[
            TurnObservation(RouteMarker.V3_TEST, ObservationStatus.FAILED, None)
        ]
    )
    second = FakeRoute()
    third = FakeRoute()
    results = run_suite(manifest, "v3", [first, second, third])
    assert [result.failure_code for result in results[1:]] == [
        FailureCode.PRIOR_SCENARIO_NOT_GREEN,
        FailureCode.PRIOR_SCENARIO_NOT_GREEN,
    ]
    assert all(not result.attempted for result in results[1:])
    assert second.send_count == third.send_count == 0


def test_safe_receipt_serialization_has_only_allowlisted_fields_and_no_content():
    manifest = load_manifest(MANIFEST_PATH)
    result = run_scenario(manifest, 1, "v3", FakeRoute())
    receipt = result.to_safe_dict()
    assert set(receipt) == {
        "schema_version",
        "manifest_digest",
        "scenario_ordinal",
        "requested_runtime",
        "status",
        "failure_stage",
        "failure_code",
        "attempted",
        "comparable",
        "completed_turn_count",
        "marker_valid",
        "bridge_evidence",
        "terminal_outcome",
    }
    serialized = json.dumps(receipt, ensure_ascii=False).lower()
    for forbidden in (
        "нужна двушка",
        "{{test_phone}}",
        "bot text",
        "endpoint",
        "token",
        "sheet",
        "exception",
        "trace_id",
        "session_id",
        "task_id",
    ):
        assert forbidden not in serialized


def test_fully_green_fake_route_passes_without_external_calls():
    manifest = load_manifest(MANIFEST_PATH)
    route = FakeRoute()
    result = run_scenario(manifest, 1, "v3", route)
    assert result.status is ResultStatus.PASSED
    assert result.failure_code is None
    assert result.completed_turn_count == len(manifest.scenarios[0].user_turns)
    assert result.marker_valid is True
    assert result.bridge_evidence is True
    assert result.terminal_outcome is TerminalOutcome.BOT_MESSAGE
    assert route.send_count == len(manifest.scenarios[0].user_turns)
