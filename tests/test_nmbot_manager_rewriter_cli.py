import subprocess
import sys
from pathlib import Path

from nmbot_v2 import manager_rewriter


ROOT = Path(__file__).resolve().parents[1]


def test_manager_rewriter_cli_status_and_set(tmp_path):
    env_path = tmp_path / ".env"
    script = ROOT / "scripts" / "nmbot_manager_rewriter.py"

    status = subprocess.run([sys.executable, str(script), "status", "--env", str(env_path)], check=True, capture_output=True, text=True)
    assert status.stdout.splitlines() == ["V2 NMBOT_V2_MANAGER_REWRITER_MODE=off", "V3 NMBOT_V3_MANAGER_REWRITER_MODE=off"]

    updated = subprocess.run([sys.executable, str(script), "shadow", "--runtime", "V3", "--env", str(env_path)], check=True, capture_output=True, text=True)
    assert "NMBOT_V3_MANAGER_REWRITER_MODE=shadow" in updated.stdout
    assert env_path.read_text(encoding="utf-8").strip() == "NMBOT_V3_MANAGER_REWRITER_MODE=shadow"

    status = subprocess.run([sys.executable, str(script), "status", "--env", str(env_path)], check=True, capture_output=True, text=True)
    assert status.stdout.splitlines() == ["V2 NMBOT_V2_MANAGER_REWRITER_MODE=off", "V3 NMBOT_V3_MANAGER_REWRITER_MODE=shadow"]

    subprocess.run([sys.executable, str(script), "publish", "--runtime", "V2", "--env", str(env_path)], check=True, capture_output=True, text=True)
    status = subprocess.run([sys.executable, str(script), "status", "--env", str(env_path)], check=True, capture_output=True, text=True)
    assert status.stdout.splitlines() == ["V2 NMBOT_V2_MANAGER_REWRITER_MODE=publish", "V3 NMBOT_V3_MANAGER_REWRITER_MODE=shadow"]

    subprocess.run([sys.executable, str(script), "off", "--runtime", "all", "--env", str(env_path)], check=True, capture_output=True, text=True)
    status = subprocess.run([sys.executable, str(script), "status", "--env", str(env_path)], check=True, capture_output=True, text=True)
    assert status.stdout.splitlines() == ["V2 NMBOT_V2_MANAGER_REWRITER_MODE=off", "V3 NMBOT_V3_MANAGER_REWRITER_MODE=off"]


def test_manager_rewriter_cli_requires_runtime_for_mutation(tmp_path):
    script = ROOT / "scripts" / "nmbot_manager_rewriter.py"
    proc = subprocess.run([sys.executable, str(script), "publish", "--env", str(tmp_path / ".env")], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "--runtime is required" in proc.stderr


def test_manager_rewriter_request_payload_stage_source_symbol_is_callable():
    assert callable(manager_rewriter.manager_rewriter_request_payload)
