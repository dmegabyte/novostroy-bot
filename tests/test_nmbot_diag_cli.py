import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_diag.sh"


def run_diag(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env or {})
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_local_json_does_not_invoke_ssh_and_reports_shape(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh-forbidden"
    ssh.write_text("#!/usr/bin/env bash\necho ssh must not run >&2\nexit 99\n", encoding="utf-8")
    ssh.chmod(0o755)
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"version":"V3"}', encoding="utf-8")

    result = run_diag(
        "--local",
        "--json",
        env={
            "NMBOT_DIAG_SSH": str(ssh),
            "NMBOT_RUNTIME_VERSION_FILE": str(runtime),
            "JIVO_PROVIDER_TOKEN": "token-present",
            "NMBOT_API_TOKEN": "api-present",
            "NMBOT_CALLBACK_OUTBOX_DIR": str(tmp_path / "outbox"),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "local"
    assert payload["production"] == {"status": "not_checked"}
    assert payload["runtime_version"]["status"] == "version"
    assert payload["runtime_version"]["version"] == "V3"
    assert payload["config_shape"] == {
        "JIVO_PROVIDER_TOKEN": True,
        "NMBOT_API_TOKEN": True,
        "NMBOT_CALLBACK_OUTBOX_DIR": True,
    }
    assert "token-present" not in result.stdout
    assert "api-present" not in result.stdout


def test_local_text_mode_does_not_invoke_ssh(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh-forbidden"
    ssh.write_text("#!/usr/bin/env bash\necho ssh must not run >&2\nexit 99\n", encoding="utf-8")
    ssh.chmod(0o755)

    result = run_diag("--local", env={"NMBOT_DIAG_SSH": str(ssh)})

    assert result.returncode == 0, result.stderr
    assert "Dev API/bridge" in result.stdout
    assert "ssh must not run" not in result.stderr


def test_vps_json_unavailable_is_unverified_not_healthy(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh-unavailable"
    ssh.write_text("#!/usr/bin/env bash\nexit 255\n", encoding="utf-8")
    ssh.chmod(0o755)

    result = run_diag("--vps", "--json", env={"NMBOT_DIAG_SSH": str(ssh)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["production"]["status"] == "unavailable"
    assert payload["production"]["health"] == "unverified"
    assert "healthy" not in result.stdout.lower()


def test_local_json_malformed_runtime_version_defaults_with_label(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{not json", encoding="utf-8")

    result = run_diag("--local", "--json", env={"NMBOT_RUNTIME_VERSION_FILE": str(runtime)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime_version"]["status"] == "malformed_default"
    assert payload["runtime_version"]["effective_version"] == "V2"


def test_vps_json_malformed_output_labels_runtime_default(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh-malformed"
    ssh.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${*: -1}\" = \"true\" ]; then exit 0; fi\n"
        "printf 'not-json-runtime-version-output'\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    result = run_diag("--vps", "--json", env={"NMBOT_DIAG_SSH": str(ssh)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "malformed_vps_output"
    assert payload["production"]["status"] == "unverified"
    assert payload["current_runtime_version"] == {
        "source": "live_endpoint",
        "status": "unknown",
        "verified": False,
        "reason": "malformed_vps_output",
    }
    assert payload["runtime_version"] == payload["current_runtime_version"]
    assert payload["persisted_runtime_selector"] == {
        "source": "persisted_selector_file",
        "status": "unknown",
        "active_process_truth": False,
    }
    assert "effective_version" not in payload["runtime_version"]


def test_vps_json_missing_error_log_labels_freshness_missing(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh-vps-json"
    ssh.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${*: -1}\" = \"true\" ]; then exit 0; fi\n"
        "printf '%s' '{\"ok\":true,\"mode\":\"vps\",\"production\":{\"status\":\"verified_read_only\",\"health\":\"checked\"},\"error_event_log\":{\"exists\":false,\"freshness\":\"missing\"}}'\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)

    result = run_diag("--vps", "--json", env={"NMBOT_DIAG_SSH": str(ssh)})

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["error_event_log"]["freshness"] == "missing"
