from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nmbot-test-atomic-release.yml"
LOCK = ROOT / "requirements-deploy.lock"


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), "TEST release workflow must be versioned"
    return WORKFLOW.read_text(encoding="utf-8")


def test_test_release_is_manual_test_only_and_serialized() -> None:
    text = _workflow_text()

    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden_trigger in ("push:", "pull_request:", "schedule:", "workflow_run:"):
        assert forbidden_trigger not in text
    assert re.search(r"(?m)^\s+environment:\s*test\s*$", text)
    assert "environment: production" not in text.casefold()
    assert re.search(r"(?m)^  group:\s*nmbot-test-atomic-release\s*$", text)
    assert re.search(r"(?m)^  cancel-in-progress:\s*false\s*$", text)


def test_test_release_has_least_privilege_and_pinned_actions() -> None:
    text = _workflow_text()

    assert re.search(r"(?m)^permissions:\s*\n  contents:\s*read\s*$", text)
    assert "write" not in text.casefold()
    uses_lines = re.findall(r"(?m)^\s*uses:\s*(actions/[^@\s]+)@([^\s]+)\s*$", text)
    assert uses_lines == [
        ("actions/checkout", "11bd71901bbe5b1630ceea73d27597364c9af683"),
        ("actions/setup-python", "a26af69be951a213d495a4c3e4e4022e16d87065"),
    ]
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for _, ref in uses_lines)
    assert re.search(r'python-version:\s*["\']3\.12\.3["\']', text)


def test_dependencies_are_hash_locked_and_verified_before_ssh() -> None:
    text = _workflow_text()
    install = "python -m pip install --require-hashes --only-binary :all: -r requirements-deploy.lock"

    assert LOCK.is_file(), "privileged workflow dependency lock must be versioned"
    assert install in text
    assert "python -m pip check" in text
    assert "pip install --upgrade pip" not in text
    assert "pip install -r requirements.txt" not in text
    assert text.index(install) < text.index("NMBOT_TEST_SSH_PRIVATE_KEY")

    lock_text = LOCK.read_text(encoding="utf-8")
    requirement_lines = [
        line for line in lock_text.splitlines() if line and not line.startswith(("#", " ", "-"))
    ]
    assert requirement_lines
    assert all(re.match(r"^[A-Za-z0-9_.-]+==[^\s\\]+", line) for line in requirement_lines)
    assert "--hash=sha256:" in lock_text
    assert not re.search(r"(?im)^\s*(?:https?://|git\+|file:)", lock_text)


def test_ssh_secrets_feed_only_the_guarded_test_release_path() -> None:
    text = _workflow_text()

    assert text.count("secrets.NMBOT_TEST_SSH_PRIVATE_KEY") == 1
    assert text.count("secrets.NMBOT_TEST_SSH_KNOWN_HOSTS") == 1
    command = (
        'python3 scripts/nmbot_atomic_release.py test-release --release-id "$release_id" '
        "--auto-overlays --confirm"
    )
    assert text.count(command) == 1
    assert "scripts/nmbot_release.py" not in text
    assert "git pull" not in text
    assert "systemctl" not in text
    assert "manual Jivo smoke remains required" in text
