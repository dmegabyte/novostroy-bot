from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_project_audit.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("nmbot_project_audit_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root / "scripts" / "a.py", "print('same')\n")
    write(root / "scripts" / "b.py", "print('same')\n")
    write(root / "scripts" / "c.py", "print('unique')\n")
    write(root / "prompts" / "chat.txt", "hello\n")
    write(root / "docs" / "map.md", "scripts/a.py and chat.txt are documented\n")
    write(root / "tests" / "test_a.py", "# a.py is covered by basename\n")
    write(root / ".env", "SECRET_VALUE_SHOULD_NOT_APPEAR=super-secret\n")
    write(root / "scripts" / "__pycache__" / "ghost.py", "print('excluded')\n")
    write(root / ".venv" / "scripts" / "hidden.py", "print('excluded')\n")
    write(root / "reports" / "nmbot_project_audit.json", "super-secret\n")
    return root


def test_report_is_deterministic_for_static_fixture(tmp_path: Path) -> None:
    mod = load_audit_module()
    root = make_fixture(tmp_path)

    first = mod.build_report(root)
    second = mod.build_report(root)

    assert first == second
    assert first["schema_version"] == "nmbot.project_audit.v1"
    assert [item["path"] for item in first["inventory"]["records"]] == sorted(item["path"] for item in first["inventory"]["records"])


def test_duplicate_detection_uses_content_sha_not_filename(tmp_path: Path) -> None:
    mod = load_audit_module()
    report = mod.build_report(make_fixture(tmp_path))

    groups = [set(group["paths"]) for group in report["duplicate_groups"]]
    assert {"scripts/a.py", "scripts/b.py"} in groups


def test_candidates_are_labeled_needs_review_never_unused(tmp_path: Path) -> None:
    mod = load_audit_module()
    report = mod.build_report(make_fixture(tmp_path))

    candidate = next(item for item in report["coverage_candidates"] if item["path"] == "scripts/c.py")
    assert candidate["label"] == "unreferenced_candidate"
    assert candidate["review_status"] == "needs_review"
    assert "unused" not in str(report).casefold()


def test_env_values_and_forbidden_directories_are_excluded(tmp_path: Path) -> None:
    mod = load_audit_module()
    report = mod.build_report(make_fixture(tmp_path))
    text = str(report)

    assert "super-secret" not in text
    assert "SECRET_VALUE_SHOULD_NOT_APPEAR" not in text
    paths = {item["path"] for item in report["inventory"]["records"]}
    assert "scripts/__pycache__/ghost.py" not in paths
    assert ".venv/scripts/hidden.py" not in paths
    assert "reports/nmbot_project_audit.json" not in paths


def test_prompt_referenced_by_allowlisted_check_harness_has_test_coverage(tmp_path: Path) -> None:
    mod = load_audit_module()
    root = tmp_path / "repo"
    write(root / "prompts" / "chat_v1.txt", "hello\n")
    write(root / "scripts" / "nmbot_test_agent.py", "PROMPT = 'prompts/chat_v1.txt'\n")

    report = mod.build_report(root)

    prompt = next(item for item in report["inventory"]["records"] if item["path"] == "prompts/chat_v1.txt")
    candidate = next(item for item in report["coverage_candidates"] if item["path"] == "prompts/chat_v1.txt")
    assert prompt["references"]["check_references"] == ["scripts/nmbot_test_agent.py"]
    assert "no_test_reference" not in candidate["reasons"]
    assert "no_doc_reference" in candidate["reasons"]


def test_prompt_referenced_by_arbitrary_runtime_script_still_lacks_test_coverage(tmp_path: Path) -> None:
    mod = load_audit_module()
    root = tmp_path / "repo"
    write(root / "prompts" / "chat_v1.txt", "hello\n")
    write(root / "scripts" / "runtime.py", "PROMPT = 'prompts/chat_v1.txt'\n")

    report = mod.build_report(root)

    prompt = next(item for item in report["inventory"]["records"] if item["path"] == "prompts/chat_v1.txt")
    candidate = next(item for item in report["coverage_candidates"] if item["path"] == "prompts/chat_v1.txt")
    assert prompt["references"]["check_references"] == []
    assert "no_test_reference" in candidate["reasons"]
