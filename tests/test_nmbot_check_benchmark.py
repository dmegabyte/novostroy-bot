import subprocess
import json
from pathlib import Path

import pytest

from scripts import nmbot_check_benchmark as benchmark


def test_percentile_uses_nearest_rank() -> None:
    values = [10, 20, 30, 40, 50]
    assert benchmark.percentile_nearest_rank(values, 0.50) == 30
    assert benchmark.percentile_nearest_rank(values, 0.95) == 50


def test_percentile_rejects_empty_samples() -> None:
    with pytest.raises(ValueError):
        benchmark.percentile_nearest_rank([], 0.50)


def test_benchmark_runs_existing_dispatcher_and_reports_p50_p95() -> None:
    calls: list[list[str]] = []
    clock_values = iter([0.0, 0.010, 1.0, 1.020, 2.0, 2.030])

    def runner(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='{"status":"passed"}', stderr="")

    code, report = benchmark.benchmark(
        ["docs"], runs=2, warmup=1, runner=runner, clock=lambda: next(clock_values)
    )

    assert code == 0
    assert len(calls) == 3
    assert all(Path(call[1]).name == "nmbot_check.py" for call in calls)
    assert report["samples_ms"] == [20.0, 30.0]
    assert report["timing_ms"] == {"min": 20.0, "p50": 20.0, "p95": 30.0, "max": 30.0}
    assert report["network"] == "forbidden_by_nmbot_check_manifest"
    assert report["model_calls"] == "forbidden_by_nmbot_check_manifest"


def test_benchmark_stops_on_first_failure() -> None:
    calls = 0
    clock_values = iter([0.0, 0.1])

    def runner(argv):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(argv, 7, stdout="bad", stderr="failed")

    code, report = benchmark.benchmark(
        ["runtime"], runs=5, warmup=0, runner=runner, clock=lambda: next(clock_values)
    )

    assert code == 7
    assert calls == 1
    assert report["status"] == "failed"
    assert report["failed_phase"] == "measure"
    assert report["failed_run"] == 1


def test_context_benchmark_requires_separate_frozen_artifacts_with_24_unique_cases() -> None:
    cases = benchmark.load_context_benchmark_cases()

    assert len(cases) >= 24
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["query"] for case in cases}) == len(cases)
    assert all(case["label"].get("expect_abstain") or case["label"].get("expected_owner_path") for case in cases)


@pytest.mark.parametrize(
    ("artifact", "tampered_id"),
    [
        ("queries", '"q001-tampered"'),
        ("labels", '"q001-tampered"'),
    ],
)
def test_context_benchmark_fails_closed_when_pinned_artifact_is_tampered(
    tmp_path: Path, artifact: str, tampered_id: str,
) -> None:
    queries_path = tmp_path / "queries.json"
    labels_path = tmp_path / "labels.json"
    queries_path.write_bytes((benchmark.ROOT / benchmark.CONTEXT_QUERIES).read_bytes())
    labels_path.write_bytes((benchmark.ROOT / benchmark.CONTEXT_LABELS).read_bytes())
    path = queries_path if artifact == "queries" else labels_path
    path.write_text(path.read_text(encoding="utf-8").replace('"q001"', tampered_id, 1), encoding="utf-8")

    with pytest.raises(ValueError, match=rf"{artifact} artifact SHA-256 mismatch"):
        benchmark.load_context_benchmark_cases(queries_path, labels_path)


def test_context_benchmark_locks_verbose_output_before_scoring(monkeypatch, tmp_path: Path) -> None:
    case = {"id": "one", "query": "one", "label": {"target_kind": "stage", "target": "v2.search", "expected_owner_path": "scripts/search.py"}}
    outputs = {
        "navigation": {"abstain": False, "results": [{"path": "scripts/search.py", "start_line": 2, "end_line": 3}]},
        "retrieval": {"abstain": True, "cards": []},
        "strict_gate": {"abstain": False, "stop_reason": "owner_contract_and_test", "context": [{"path": "scripts/search.py"}], "trace": {"selected_source_count": 1, "lines_loaded": 2, "characters_loaded": 20}},
    }
    monkeypatch.setattr(benchmark, "_route_outputs", lambda *_args, **_kwargs: outputs)

    code, report = benchmark.context_benchmark([case], output_dir=tmp_path / "locked")

    assert code == 0
    assert report["locked_output_count"] == 1
    locked_case = tmp_path / "locked" / "01-one.json"
    assert locked_case.exists()
    assert json.loads(locked_case.read_text(encoding="utf-8"))["outputs"] == outputs
    assert report["comparison"]["strict_stop_2_context_gate"]["owner_hit_rate"] == 1.0


def test_strict_target_is_selected_from_navigation_not_label() -> None:
    selected: dict[str, object] = {}

    class Navigation:
        def navigate(self, _query, *, root):
            spec = {"target_kind": "symbol", "target": "right_symbol", "target_owner": "scripts/right.py", "owner_path": "scripts/right.py"}
            return {"abstain": False, "fallback": False, "selection_required": False, "selected_target_spec": spec, "results": [{"path": "scripts/right.py", "target_spec": spec}]}

    class Retrieval:
        def search_cards(self, _query, *, root):
            return {"abstain": True, "cards": []}

    class Gate:
        def run_gate(self, _query, **kwargs):
            selected.update(kwargs)
            return {"abstain": False, "stop_reason": "definition_of_done", "context": [{"path": "scripts/right.py"}], "trace": {"selected_source_count": 1, "lines_loaded": 1, "characters_loaded": 1}}

    outputs = benchmark._route_outputs(
        {"id": "wrong-label", "query": "query", "label": {"expected_owner_path": "scripts/wrong.py", "target": "wrong", "target_kind": "stage"}},
        root=Path("."), routes={"navigation": Navigation(), "retrieval": Retrieval(), "gate": Gate()},
    )

    assert selected["target"] == "right_symbol"
    assert selected["target_kind"] == "symbol"
    assert outputs["strict_selection"]["target_owner"] == "scripts/right.py"


def test_ambiguous_navigation_never_auto_selects_top_candidate() -> None:
    report = {
        "abstain": False,
        "fallback": False,
        "selection_required": True,
        "results": [
            {"candidate_id": "c1", "target_spec": {"target_kind": "symbol", "target": "same", "target_owner": "scripts/a.py", "owner_path": "scripts/a.py"}},
            {"candidate_id": "c2", "target_spec": {"target_kind": "symbol", "target": "same", "target_owner": "scripts/b.py", "owner_path": "scripts/b.py"}},
        ],
    }

    assert benchmark._selected_target_spec(report) is None


def test_successful_context_budget_stop_is_not_harmful() -> None:
    payload = {
        "id": "budget", "label": {"expected_owner_path": "scripts/right.py"},
        "outputs": {
            "navigation": {"abstain": False, "results": [{"path": "scripts/right.py", "start_line": 1, "end_line": 1}]},
            "retrieval": {"abstain": True, "cards": []},
            "strict_gate": {"abstain": False, "stop_reason": "context_budget_reached", "context": [{"path": "scripts/right.py"}], "trace": {"selected_source_count": 1, "lines_loaded": 1, "characters_loaded": 1}},
            "strict_selection": {"target_kind": "symbol", "target": "right", "target_owner": "scripts/right.py"},
        },
    }

    score = benchmark._score_locked_case(payload)

    assert score["strict_stop_2"]["owner_hit"] is True
    assert score["strict_stop_2"]["harmful_early_stop"] is False
