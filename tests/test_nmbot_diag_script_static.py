from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_diag_script_targets_current_jivo_production_units_not_legacy_service() -> None:
    text = (ROOT / "scripts" / "nmbot_diag.sh").read_text(encoding="utf-8")
    assert "novostroy-bot-api.service" in text
    assert "novostroy-bot-n8n-bridge.service" in text
    assert "scripts/nmbot_api_server.py" in text
    assert "scripts/nmbot_n8n_bridge_server.py" in text
    assert "127.0.0.1:8088/health" in text
    assert "127.0.0.1:8093/health" in text
    assert "logs/dialogue_journal.jsonl" in text
    assert "logs/n8n_bridge_structured.jsonl" in text
    assert "--audit-log logs/dialogue_journal.jsonl --audit-only --last 20" in text
    assert 'for file in "$ERROR_LOG" "logs/n8n_bridge_structured.jsonl" "logs/dialogue_journal.jsonl"' not in text
    assert "SERVICE=\"novostroy-bot.service\"" not in text
    assert "tail -15 $VPS_BOT_DIR/logs/bot.log" not in text


def test_vps_json_runtime_truth_comes_only_from_protected_live_endpoint() -> None:
    text = (ROOT / "scripts" / "nmbot_diag.sh").read_text(encoding="utf-8")
    vps_json = text.split("diag_vps_json()", 1)[1].split("# ── диагностика", 1)[0]
    assert "http://127.0.0.1:8088/api/runtime-version" in text
    assert '"Authorization": "Bearer " + token' in text
    assert 'timeout=2' in text
    assert 'payload.get("runtime_version")' in text
    assert 'current_runtime_version' in text
    assert 'persisted_runtime_selector' in text
    assert 'active_process_truth": False' in text
    assert '"runtime_version": {"status": "malformed_default", "effective_version": "V2"}' not in vps_json
    assert '"runtime_version": runtime' not in vps_json


def test_diag_accepts_current_v6_runtime_version() -> None:
    text = (ROOT / "scripts" / "nmbot_diag.sh").read_text(encoding="utf-8")
    assert '"V6"' in text
