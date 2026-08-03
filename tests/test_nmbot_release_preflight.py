from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_release_preflight.py"


def load_preflight_module():
    spec = importlib.util.spec_from_file_location("nmbot_release_preflight_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def run_preflight(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True, check=False)


def test_default_has_no_subprocess_and_stays_incomplete(monkeypatch) -> None:
    mod = load_preflight_module()

    def fail_run(*_args, **_kwargs):
        raise AssertionError("default preflight must not invoke subprocess")

    monkeypatch.setattr(mod.subprocess, "run", fail_run)
    report = mod.build_evidence(scopes=["docs", "contracts"], files=["scripts/nmbot_check.py"], manifest_path=mod.DEFAULT_MANIFEST, run_checks=False)

    assert report["checks"]["status"] == "not_run"
    assert report["evidence"]["vps"]["status"] == "not_checked"
    assert report["evidence"]["direct_api"]["status"] == "not_checked"
    assert report["evidence"]["jivo"]["status"] == "incomplete"
    assert report["overall"] == {
        "status": "incomplete",
        "green": False,
        "reason": "Step 8 requires separated local/fixture/VPS/direct API/Jivo evidence; this local-only tool never supplies external evidence.",
    }


def test_run_checks_invokes_only_local_nmbot_check_direct_argv(monkeypatch) -> None:
    mod = load_preflight_module()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"status":"passed"}\n', stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    report = mod.build_evidence(scopes=["docs"], files=["scripts/nmbot_check.py"], manifest_path=mod.DEFAULT_MANIFEST, run_checks=True)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [sys.executable, "scripts/nmbot_check.py", "docs", "--json"]
    assert kwargs["cwd"] == ROOT
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False
    assert not any(token in argv for token in ("ssh", "scp", "curl", "deploy", "scripts/nmbot_release.py"))
    assert report["checks"]["status"] == "passed"
    assert report["overall"]["status"] == "incomplete"


def test_release_cli_plans_owner_scopes_and_delegates_release_directly(monkeypatch, capsys) -> None:
    mod = load_preflight_module()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='{"status":"passed"}\n', stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main(["--scope", "release", "--run-checks"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["manifest"]["selected_scopes"] == ["release"]
    assert report["manifest"]["planned_owner_scopes"] == list(mod.RELEASE_OWNER_SCOPES)
    assert set(report["manifest"]["plan"]) == set(mod.RELEASE_OWNER_SCOPES)
    assert calls == [
        ([sys.executable, "scripts/nmbot_check.py", "release", "--json"], {"cwd": ROOT, "text": True, "capture_output": True, "check": False})
    ]
    assert not any(token in calls[0][0] for token in ("ssh", "scp", "curl", "deploy", "scripts/nmbot_release.py"))


def test_release_alias_fails_closed_when_an_expanded_manifest_scope_is_missing(tmp_path) -> None:
    mod = load_preflight_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"scopes": {scope: {"commands": []} for scope in mod.RELEASE_OWNER_SCOPES if scope != "isolation"}}), encoding="utf-8")

    try:
        mod.load_manifest_plan(manifest, ["release"])
    except mod.PreflightError as exc:
        assert str(exc) == "scope missing from manifest: isolation"
    else:
        raise AssertionError("release planning must fail when an expanded owner scope is absent")


def test_non_strict_architecture_finding_is_not_reported_as_clean_pass(monkeypatch) -> None:
    mod = load_preflight_module()

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout='{\n  "overall": "FAIL"\n}\n', stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    report = mod.build_evidence(scopes=["docs"], files=["scripts/nmbot_check.py"], manifest_path=mod.DEFAULT_MANIFEST, run_checks=True)

    assert report["checks"]["status"] == "passed_with_findings"
    assert report["checks"]["findings"] == ["architecture_preflight overall=FAIL (non-strict local report)"]
    assert report["evidence"]["local"]["status"] == "passed_with_findings"


def test_unknown_scope_rejected_by_cli() -> None:
    proc = run_preflight("--scope", "prod")
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr


def test_hash_output_and_missing_file_are_honest() -> None:
    proc = run_preflight("--target-file", "scripts/nmbot_check.py", "--target-file", "missing/local-only-file.txt")
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    by_path = {item["path"]: item for item in data["target_files"]}

    assert by_path["scripts/nmbot_check.py"]["status"] == "present"
    assert len(by_path["scripts/nmbot_check.py"]["sha256"]) == 64
    assert by_path["missing/local-only-file.txt"] == {"path": "missing/local-only-file.txt", "status": "missing", "sha256": None}
    assert data["overall"]["green"] is False
