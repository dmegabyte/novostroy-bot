import subprocess
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
