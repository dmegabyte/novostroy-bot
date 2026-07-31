from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_v2_version_compare.py"
spec = importlib.util.spec_from_file_location("nmbot_v2_version_compare_test", SCRIPT)
assert spec and spec.loader
compare_cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compare_cli
spec.loader.exec_module(compare_cli)


def test_compare_passes_only_when_sources_and_runtime_match() -> None:
    test = {"ok": True, "runtime_version": "V2", "files": {"nmbot_v2/runtime.py": "a"}}
    production = {"ok": True, "runtime_version": "V2", "files": {"nmbot_v2/runtime.py": "a"}}

    assert compare_cli.compare(test, production) == {
        "match": True,
        "source_match": True,
        "runtime_match": True,
        "test_runtime_version": "V2",
        "client_production_runtime_version": "V2",
        "different_files": [],
    }


def test_compare_reports_source_and_runtime_differences() -> None:
    test = {"ok": True, "runtime_version": "V2", "files": {"nmbot_v2/runtime.py": "a"}}
    production = {"ok": True, "runtime_version": "V3", "files": {"nmbot_v2/runtime.py": "b", "prompts/v2_response_writer.txt": "c"}}

    result = compare_cli.compare(test, production)

    assert result["match"] is False
    assert result["source_match"] is False
    assert result["runtime_match"] is False
    assert result["different_files"] == ["nmbot_v2/runtime.py", "prompts/v2_response_writer.txt"]


def test_compare_reports_failed_contour() -> None:
    result = compare_cli.compare({"ok": False, "error": "timeout"}, {"ok": True, "runtime_version": "V2", "files": {}})

    assert result == {"match": False, "reason": "contour_check_failed", "different_files": []}
