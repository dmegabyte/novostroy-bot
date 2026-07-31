from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_check.py"
MANIFEST = ROOT / "tests" / "nmbot_check_manifest.yaml"


def load_check_module():
    spec = importlib.util.spec_from_file_location("nmbot_check_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def run_check(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True, check=False)


def write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def minimal_manifest(command: list[str] | None = None) -> dict:
    command = command or ["{python}", "-m", "py_compile", "scripts/nmbot_check.py"]
    scopes = {}
    for scope in ("docs", "contracts", "v0", "v2", "runtime", "audit", "quality"):
        scopes[scope] = {"commands": [{"name": f"{scope}_ok", "argv": command}]}
    return {"version": 1, "scopes": scopes}


ROUTES = """# NMBot documentation registry

Intro.

## 1. Start and understand

- [Current](CURRENT_ARCHITECTURE.md)

## 2. Build and verify

- [Contract](NMBOT_EXTERNAL_CONTRACTS.md#section?ignored=true)

## 3. Operate and release

- [Runbook](NMBOT_RUNBOOK.md)

## 4. Decisions and history

- [Archive](ARCHIVE_INDEX.md)
- [External](https://example.test/docs)
- [Anchor](#local)
"""


def write_docs_fixture(tmp_path: Path, *, readme: str = ROUTES, owner_target: str = "docs/CURRENT_ARCHITECTURE.md") -> Path:
    root = tmp_path / "repo"
    docs = root / "docs"
    config = root / "config"
    docs.mkdir(parents=True)
    config.mkdir()
    for name in (
        "README.md",
        "CURRENT_ARCHITECTURE.md",
        "NMBOT_EXTERNAL_CONTRACTS.md",
        "NMBOT_RUNBOOK.md",
        "ARCHIVE_INDEX.md",
    ):
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    (docs / "README.md").write_text(readme, encoding="utf-8")
    (config / "project_documentation_owners.json").write_text(
        json.dumps({"projects": {"nmbot": {"architecture": owner_target}}}),
        encoding="utf-8",
    )
    return root


def test_manifest_loads_supported_scopes_and_dry_run_is_offline() -> None:
    mod = load_check_module()
    manifest = mod.load_and_validate_manifest(MANIFEST)
    assert set(manifest) == {"docs", "contracts", "v0", "v2", "runtime", "audit", "quality"}

    proc = run_check("docs", "--dry-run")
    assert proc.returncode == 0
    assert "SKIPPED docs:" in proc.stdout
    assert "dry-run" in proc.stdout


def test_unknown_scope_is_rejected() -> None:
    proc = run_check("prod", "--dry-run", "--json")
    assert proc.returncode == 2
    assert "unknown scope" in proc.stdout


def test_successful_tiny_manifest_runs_allowlisted_command(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, minimal_manifest())
    proc = run_check("docs", "--manifest", str(manifest), "--json")
    assert proc.returncode == 0
    assert "PASSED docs:docs_ok" in proc.stdout
    assert '"status": "passed"' in proc.stdout


def test_malformed_manifest_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.yaml"
    manifest.write_text("not: yaml: for: this: loader", encoding="utf-8")
    proc = run_check("docs", "--manifest", str(manifest))
    assert proc.returncode == 2
    assert "manifest must be JSON-compatible YAML" in proc.stderr


def test_unsafe_remote_or_deploy_commands_are_rejected_and_not_executed(tmp_path: Path) -> None:
    for unsafe in (
        ["ssh", "host", "true"],
        ["{python}", "scripts/nmbot_release.py", "status"],
        ["{python}", "scripts/nmbot_release.py", "deploy"],
        ["{python}", "-m", "pytest"],
    ):
        manifest = write_manifest(tmp_path, minimal_manifest(unsafe))
        proc = run_check("docs", "--manifest", str(manifest), "--dry-run")
        assert proc.returncode == 2
        assert "unsafe" in proc.stderr or "allowlisted" in proc.stderr or "explicit test files" in proc.stderr


def test_quality_script_is_allowlisted_only_for_offline_all_fixture_replay(tmp_path: Path) -> None:
    safe = minimal_manifest(["{python}", "scripts/nmbot_v2_quality_gate.py", "--all"])
    assert run_check("quality", "--manifest", str(write_manifest(tmp_path, safe)), "--dry-run").returncode == 0

    unsafe = minimal_manifest(["{python}", "scripts/nmbot_v2_quality_gate.py", "--all", "--live"])
    proc = run_check("quality", "--manifest", str(write_manifest(tmp_path, unsafe)), "--dry-run")
    assert proc.returncode == 2
    assert "offline fixture replay" in proc.stderr


def test_documentation_structure_accepts_valid_registry_and_owner_fixture(tmp_path: Path) -> None:
    mod = load_check_module()
    root = write_docs_fixture(tmp_path)

    assert mod.validate_documentation_structure(root) == []


def test_documentation_structure_rejects_missing_registry_link(tmp_path: Path) -> None:
    mod = load_check_module()
    root = write_docs_fixture(tmp_path, readme=ROUTES.replace("ARCHIVE_INDEX.md", "MISSING.md"))

    failures = mod.validate_documentation_structure(root)

    assert any("docs/README.md missing local link target: MISSING.md" in item for item in failures)


def test_documentation_structure_rejects_unsafe_or_unknown_link_schemes(tmp_path: Path) -> None:
    mod = load_check_module()
    for index, target in enumerate(("file:///tmp/outside.md", "custom:payload")):
        readme = ROUTES.replace("https://example.test/docs", target)
        root = write_docs_fixture(tmp_path / str(index), readme=readme)

        failures = mod.validate_documentation_structure(root)

        scheme = target.split(":", 1)[0]
        assert any(f"unsupported link scheme '{scheme}'" in item for item in failures)


def test_documentation_structure_rejects_missing_or_escaping_owner_target(tmp_path: Path) -> None:
    mod = load_check_module()
    missing_root = write_docs_fixture(tmp_path / "missing", owner_target="docs/MISSING.md")
    escaping_root = write_docs_fixture(tmp_path / "escaping", owner_target="../outside.md")

    missing = mod.validate_documentation_structure(missing_root)
    escaping = mod.validate_documentation_structure(escaping_root)

    assert any("missing owner target: docs/MISSING.md" in item for item in missing)
    assert any("rejects absolute or escaping path: ../outside.md" in item for item in escaping)


def test_documentation_structure_rejects_malformed_or_duplicate_route_layout(tmp_path: Path) -> None:
    mod = load_check_module()
    malformed_root = write_docs_fixture(tmp_path / "malformed", readme=ROUTES.replace("## 4. Decisions and history", "## 5. Extra"))
    duplicate_root = write_docs_fixture(tmp_path / "duplicate", readme=f"{ROUTES}\n## 1. Start and understand\n")

    malformed = mod.validate_documentation_structure(malformed_root)
    duplicate = mod.validate_documentation_structure(duplicate_root)

    assert any("unexpected numbered route heading '## 5. Extra'" in item for item in malformed)
    assert any("route heading '## 4. Decisions and history' count=0" in item for item in malformed)
    assert any("route heading '## 1. Start and understand' count=2" in item for item in duplicate)
