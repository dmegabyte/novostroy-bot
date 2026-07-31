from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nmbot-local-fast-gate.yml"


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), "CI workflow must live under .github/workflows"
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_path_and_exact_local_gate_command() -> None:
    text = _workflow_text()

    assert WORKFLOW.relative_to(ROOT).as_posix() == ".github/workflows/nmbot-local-fast-gate.yml"
    assert "python scripts/nmbot_check.py docs contracts" in text
    assert "python -m pip install --upgrade pip" in text
    assert "python -m pip install -r requirements.txt" in text
    assert re.search(r'python-version:\s*["\']3\.12["\']', text)


def test_workflow_triggers_are_only_local_ci_triggers() -> None:
    text = _workflow_text()

    # Text inspection avoids YAML 1.1 ambiguity where an unquoted `on` key can be
    # parsed as boolean True by older parsers.
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  push:\s*$", text)
    assert re.search(r"(?m)^  pull_request:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    assert "schedule:" not in text


def test_workflow_has_least_privilege_permissions() -> None:
    text = _workflow_text()

    assert re.search(r"(?m)^permissions:\s*\n  contents:\s*read\s*$", text)
    assert not re.search(r"(?m)^  (actions|checks|deployments|id-token|issues|packages|pull-requests|statuses):", text)
    assert "write" not in text.casefold()


def test_workflow_is_non_secret_and_non_runtime() -> None:
    text = _workflow_text()
    folded = text.casefold()

    for forbidden in (
        "secrets.",
        "secrets:",
        "ssh",
        "deploy",
        "release",
        "systemctl",
        "curl",
        "wget",
        "nmbot_release",
    ):
        assert forbidden not in folded

    assert "env:" not in folded
    assert "artifact" not in folded



def test_nmbot_check_scopes_are_exactly_docs_contracts() -> None:
    text = _workflow_text()

    commands = re.findall(r"python\s+scripts/nmbot_check\.py\s+([^\n]+)", text)
    assert commands == ["docs contracts"]
    scopes = commands[0].split()
    assert scopes == ["docs", "contracts"]


def test_actions_are_pinned_to_full_commit_shas() -> None:
    text = _workflow_text()

    uses_lines = re.findall(r"(?m)^\s*uses:\s*(actions/[^@\s]+)@([^\s]+)\s*$", text)
    assert uses_lines == [
        ("actions/checkout", "11bd71901bbe5b1630ceea73d27597364c9af683"),
        ("actions/setup-python", "a26af69be951a213d495a4c3e4e4022e16d87065"),
    ]
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for _, ref in uses_lines)
