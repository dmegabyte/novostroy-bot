#!/usr/bin/env python3
"""Measure the existing local nmbot check gate without adding another runner."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "scripts" / "nmbot_check.py"
SAFE_SCOPES = ("docs", "contracts", "v0", "v2", "runtime", "audit", "quality")
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def percentile_nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("at least one timing sample is required")
    ordered = sorted(float(value) for value in values)
    rank = max(1, min(len(ordered), int((percentile * len(ordered)) + 0.999999999)))
    return ordered[rank - 1]


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )


def benchmark(
    scopes: Sequence[str], *, runs: int, warmup: int,
    runner: Runner = _run_command,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[int, dict[str, Any]]:
    command = [sys.executable, str(CHECK_SCRIPT), *scopes, "--json"]
    samples_ms: list[float] = []
    for index in range(warmup + runs):
        started = clock()
        result = runner(command)
        elapsed_ms = round((clock() - started) * 1000, 3)
        if result.returncode != 0:
            return result.returncode or 1, {
                "status": "failed", "scopes": list(scopes),
                "failed_phase": "warmup" if index < warmup else "measure",
                "failed_run": index + 1, "returncode": result.returncode,
                "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:],
            }
        if index >= warmup:
            samples_ms.append(elapsed_ms)

    return 0, {
        "status": "passed", "mode": "local_read_only",
        "scopes": list(scopes), "runs": runs, "warmup_runs": warmup,
        "samples_ms": samples_ms,
        "timing_ms": {
            "min": min(samples_ms),
            "p50": percentile_nearest_rank(samples_ms, 0.50),
            "p95": percentile_nearest_rank(samples_ms, 0.95),
            "max": max(samples_ms),
        },
        "network": "forbidden_by_nmbot_check_manifest",
        "secrets": "not_required",
        "model_calls": "forbidden_by_nmbot_check_manifest",
        "side_effects": "local_test_temp_files_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the existing local read-only nmbot gate.")
    parser.add_argument("scopes", nargs="*", choices=SAFE_SCOPES, default=["docs"])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 50 or args.warmup < 0 or args.warmup > 10:
        parser.error("--runs must be 1..50 and --warmup must be 0..10")

    code, report = benchmark(args.scopes or ["docs"], runs=args.runs, warmup=args.warmup)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif code == 0:
        timing = report["timing_ms"]
        print(
            f"PASS local gate {' '.join(report['scopes'])}: "
            f"p50={timing['p50']} ms p95={timing['p95']} ms "
            f"({report['runs']} runs, {report['warmup_runs']} warmup)"
        )
    else:
        print(f"FAIL local gate: {report['failed_phase']} run {report['failed_run']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
