from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_release.py"


def load_release_module():
    spec = importlib.util.spec_from_file_location("nmbot_release_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_default_release_manifest_is_v2_jivo_and_excludes_v1_telegram() -> None:
    mod = load_release_module()
    files = set(mod.DEFAULT_FILES)

    assert "scripts/chat_tester_bot.py" not in files
    assert not any("telegram" in path.casefold() for path in files)
    assert "scripts/nmbot_api_server.py" in files
    assert "scripts/nmbot_n8n_bridge_server.py" in files
    assert "scripts/nmbot_jivo_trace_analyze.py" in files
    assert "scripts/nmbot_jivo_dialogue_diagnose.py" in files
    assert "scripts/nmbot_diag.sh" in files
    assert "scripts/nmbot_release.py" in files
    assert "scripts/nmbot_release_identity.py" in files
    assert "scripts/nmbot_runtime_adapter.py" in files
    assert "scripts/nmbot_dialogue_report.py" in files
    assert "data/nmbot_release_identity.json" in files
    assert "scripts/nmbot_gateway_client.py" in files
    assert "scripts/nmbot_planner_context.py" in files
    assert "scripts/nmbot_crm_outbox.py" in files
    assert "scripts/dialogue_journal.py" in files
    assert "followup_intent_classifier.py" in files
    assert "search_profiles.py" in files
    assert "prompts/v2_search_mcp.txt" in files
    assert {str(path.relative_to(ROOT)) for path in (ROOT / "nmbot_v2").glob("*.py")} <= files
    assert mod.SERVICES == ("novostroy-bot-api.service", "novostroy-bot-n8n-bridge.service")
    assert set(mod.HEALTH_URLS) == {"api", "bridge"}


def test_backup_command_preserves_relative_paths_and_remote_mkdir_targets_parent() -> None:
    mod = load_release_module()
    command = mod.backup_command(("scripts/nmbot_api_server.py", "nmbot_v2/runtime.py"))

    assert "scripts/nmbot_api_server.py" in command
    assert "nmbot_v2/runtime.py" in command
    assert '"$dir"/scripts' in command
    assert '"$dir"/nmbot_v2' in command
    assert '"$dir/scripts/nmbot_api_server.py"' not in command

    mkdir = mod.remote_mkdir_command("scripts/nmbot_api_server.py")
    assert mkdir.endswith("/scripts")


def test_deploy_requires_release_id_and_default_includes_identity(monkeypatch) -> None:
    mod = load_release_module()

    called = False
    captured_files: tuple[str, ...] = ()

    def fake_deploy(files, **_kwargs):
        nonlocal called
        nonlocal captured_files
        called = True
        captured_files = files

    monkeypatch.setattr(mod, "deploy", fake_deploy)

    try:
        mod.main(["deploy", "scripts/nmbot_api_server.py"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("deploy without --release-id must fail in parser")
    assert called is False
    assert mod.main(["deploy", "--release-id", "rel-test", "scripts/nmbot_api_server.py"]) == 0
    assert called is True
    assert "scripts/nmbot_api_server.py" in captured_files
    assert "scripts/nmbot_release_identity.py" in captured_files
    assert "data/nmbot_release_identity.json" in captured_files
