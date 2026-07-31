from __future__ import annotations

import json
import builtins
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_jivo_audit.sh"
DELIVERY_TRACE = "/home/neiro/novostroy-bot/logs/jivo_delivery_trace.jsonl"


def test_delivery_trace_option_uses_safe_canonical_trace_route(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$SSH_LOG\"\n"
        "if [[ \"$*\" == *\"nmbot-jivo-extract\"* ]]; then\n"
        "  printf '%s\\n' '{\"schema\":\"nmbot.jivo.delivery_trace.v1\",\"trace_ref\":\"trace_test\",\"stage\":\"terminal_delivery\",\"outcome\":\"not_sent\"}'\n"
        "  printf '%s\\n' 'nmbot_jivo_audit_extract records=1 bytes=112 requested=1000 truncated=false' >&2\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$PYTHON_LOG\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "python3"):
        command.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SSH_LOG": str(ssh_log),
        "PYTHON_LOG": str(tmp_path / "python.log"),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--delivery-trace"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert DELIVERY_TRACE in ssh_log.read_text(encoding="utf-8")
    assert "nmbot_jivo_trace_analyze.py" in (tmp_path / "python.log").read_text(encoding="utf-8")


def test_audit_strict_requires_delivery_trace_before_ssh(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    (fake_bin / "ssh").write_text("#!/usr/bin/env bash\nprintf 'called\\n' >> \"$SSH_LOG\"\n", encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--strict"], cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "SSH_LOG": str(ssh_log)},
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert "--strict requires --delivery-trace" in result.stderr
    assert not ssh_log.exists()


def test_audit_rejects_unsafe_remote_log_path() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--remote-log", "/tmp/a'; echo injected"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Unsafe remote log path" in result.stderr


@pytest.mark.parametrize(
    ("option", "value", "error"),
    [
        ("--host", "", "Unsafe SSH host"),
        ("--host", "-oProxyCommand=evil", "Unsafe SSH host"),
        ("--host", "vps;touch /tmp/pwned", "Unsafe SSH host"),
        ("--host", "vps host", "Unsafe SSH host"),
        ("--user", "", "Unsafe SSH user"),
        ("--user", "-oProxyCommand=evil", "Unsafe SSH user"),
        ("--user", "neiro;touch /tmp/pwned", "Unsafe SSH user"),
        ("--user", "neiro user", "Unsafe SSH user"),
        ("--port", "", "Unsafe SSH port"),
        ("--port", "-1", "Unsafe SSH port"),
        ("--port", "22;touch /tmp/pwned", "Unsafe SSH port"),
        ("--port", "65536", "Unsafe SSH port"),
    ],
)
def test_audit_rejects_unsafe_ssh_connection_values_before_ssh(
    tmp_path, option: str, value: str, error: str
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "printf 'called\\n' >> \"$SSH_LOG\"\n"
        "exit 99\n",
        encoding="utf-8",
    )
    (fake_bin / "ssh").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), option, value],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "SSH_LOG": str(ssh_log)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert error in result.stderr
    assert not ssh_log.exists()


@pytest.mark.parametrize(
    ("host", "user", "port"),
    [
        ("193.107.155.236", "neiro", "1905"),
        ("audit.example.test", "audit_user-1", "22"),
        ("10.0.0.12", "deploy", "65535"),
    ],
)
def test_audit_accepts_safe_ssh_connection_values(tmp_path, host: str, user: str, port: str) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$SSH_LOG\"\n"
        "if [[ \"$*\" == *\"nmbot-jivo-extract\"* ]]; then\n"
        "  printf '%s\\n' '{\"schema\":\"nmbot.jivo.delivery_trace.v1\",\"trace_ref\":\"trace_test\",\"stage\":\"terminal_delivery\",\"outcome\":\"not_sent\"}'\n"
        "  printf '%s\\n' 'nmbot_jivo_audit_extract records=1 bytes=112 requested=1000 truncated=false' >&2\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    for command in (fake_bin / "ssh", fake_bin / "python3"):
        command.chmod(0o755)

    result = subprocess.run(
        [
            "bash", str(SCRIPT), "--host", host, "--user", user, "--port", port,
            "--delivery-trace",
        ],
        cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "SSH_LOG": str(ssh_log)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    ssh_calls = ssh_log.read_text(encoding="utf-8")
    assert f"-- {user}@{host}" in ssh_calls
    assert f"-p {port}" in ssh_calls


@pytest.mark.parametrize("value", ["0", "1001", "-1", "one", "1;touch /tmp/pwned"])
def test_audit_rejects_unbounded_or_unsafe_last_before_ssh(tmp_path, value: str) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    (fake_bin / "ssh").write_text("#!/usr/bin/env bash\nprintf 'called\\n' >> \"$SSH_LOG\"\n", encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--last", value], cwd=ROOT,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "SSH_LOG": str(ssh_log)},
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 2
    assert "--last must be an integer" in result.stderr
    assert not ssh_log.exists()


def test_audit_extracts_complete_newest_jsonl_records_within_byte_cap(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh_log = tmp_path / "ssh.log"
    source_log = tmp_path / "structured.jsonl"
    source_log.write_text(
        '{"trace_id":"older","stage":"final_answer"}\n'
        + '{"trace_id":"oversized","payload":"' + ("x" * (1024 * 1024 + 1)) + '"}\n'
        + '{"trace_id":"newest","stage":"final_answer"}\n',
        encoding="utf-8",
    )
    extracted = tmp_path / "extracted.jsonl"
    actual_python = os.sys.executable
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$SSH_LOG\"\n"
        "command=\"${!#}\"\n"
        "if [[ \"$command\" == test\\ -r* ]]; then exit 0; fi\n"
        "PATH=\"${PATH#*:}\" bash -c \"$command\"\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text(
        "#!/usr/bin/env bash\n"
        "input=\"$2\"\n"
        "cp -- \"$input\" \"$EXTRACTED_LOG\"\n"
        "exec \"$ACTUAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "python3"):
        command.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--last", "3", "--remote-log", str(source_log)], cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "SSH_LOG": str(ssh_log),
            "EXTRACTED_LOG": str(extracted),
            "ACTUAL_PYTHON": actual_python,
        },
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    extracted_lines = extracted.read_text(encoding="utf-8").splitlines()
    assert extracted_lines == [
        '{"trace_id":"older","stage":"final_answer"}',
        '{"trace_id":"newest","stage":"final_answer"}',
    ]
    assert [json.loads(line) for line in extracted_lines] == [
        {"trace_id": "older", "stage": "final_answer"},
        {"trace_id": "newest", "stage": "final_answer"}
    ]
    assert result.stdout.count("malformed_json_line") == 0
    assert "records=2" in result.stdout
    assert "truncated=true" in result.stdout
    assert "oversized" not in result.stdout
    ssh_calls = ssh_log.read_text(encoding="utf-8")
    assert "nmbot-jivo-extract" in ssh_calls
    assert "head -c" not in ssh_calls


def test_audit_extractor_does_not_read_oversized_record_before_cap(tmp_path, monkeypatch) -> None:
    source = tmp_path / "structured.jsonl"
    huge = b'{"trace_id":"oversized","payload":"' + (b"x" * (1024 * 1024 + 1)) + b'"}\n'
    source.write_bytes(b'{"trace_id":"older"}\n' + huge + b'{"trace_id":"newest"}\n')
    script = SCRIPT.read_text(encoding="utf-8")
    heredoc = script.split("REMOTE_EXTRACTOR <<'PY'", 1)[1]
    extractor = heredoc.split("\n", 1)[1].split("\nPY", 1)[0]

    original_open = builtins.open
    oversized_reads: list[int] = []

    class SpyFile:
        def __init__(self, handle):
            self.handle = handle

        def read(self, size=-1):
            if size == len(huge):
                oversized_reads.append(size)
                raise AssertionError("oversized record was materialized")
            return self.handle.read(size)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

    def spy_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return SpyFile(handle) if os.fspath(path) == str(source) else handle

    stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(sys, "argv", ["extractor", str(source), "3", str(1024 * 1024)])
    monkeypatch.setattr(sys, "stdout", stdout)
    exec(extractor, {"__name__": "__main__"})

    stdout.flush()
    assert oversized_reads == []
    assert stdout.buffer.getvalue() == b'{"trace_id":"older"}\n{"trace_id":"newest"}\n'


def test_audit_preserves_normal_last_window_without_truncation(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    source_log = tmp_path / "structured.jsonl"
    source_log.write_text(
        '{"trace_id":"first","stage":"bridge_request"}\n'
        '{"trace_id":"last","stage":"final_answer"}\n',
        encoding="utf-8",
    )
    extracted = tmp_path / "extracted.jsonl"
    actual_python = os.sys.executable
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "command=\"${!#}\"\n"
        "if [[ \"$command\" == test\\ -r* ]]; then exit 0; fi\n"
        "PATH=\"${PATH#*:}\" bash -c \"$command\"\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text(
        "#!/usr/bin/env bash\n"
        "cp -- \"$2\" \"$EXTRACTED_LOG\"\n"
        "exec \"$ACTUAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "python3"):
        command.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--last", "2", "--remote-log", str(source_log)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "EXTRACTED_LOG": str(extracted),
            "ACTUAL_PYTHON": actual_python,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert extracted.read_text(encoding="utf-8") == source_log.read_text(encoding="utf-8")
    assert "records=2" in result.stdout
    assert "truncated=false" in result.stdout


def test_audit_skips_trailing_empty_jsonl_records_without_false_truncation(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    source_log = tmp_path / "structured.jsonl"
    source_log.write_text(
        '{"trace_id":"older","stage":"bridge_request"}\n'
        '{"trace_id":"newest","stage":"final_answer"}\n\n',
        encoding="utf-8",
    )
    extracted = tmp_path / "extracted.jsonl"
    actual_python = os.sys.executable
    (fake_bin / "ssh").write_text(
        "#!/usr/bin/env bash\n"
        "command=\"${!#}\"\n"
        "if [[ \"$command\" == test\\ -r* ]]; then exit 0; fi\n"
        "PATH=\"${PATH#*:}\" bash -c \"$command\"\n",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text(
        "#!/usr/bin/env bash\n"
        "cp -- \"$2\" \"$EXTRACTED_LOG\"\n"
        "exec \"$ACTUAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    for command in (fake_bin / "ssh", fake_bin / "python3"):
        command.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "--last", "1", "--remote-log", str(source_log)],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "EXTRACTED_LOG": str(extracted),
            "ACTUAL_PYTHON": actual_python,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    extracted_lines = extracted.read_text(encoding="utf-8").splitlines()
    assert extracted_lines == ['{"trace_id":"newest","stage":"final_answer"}']
    assert [json.loads(line) for line in extracted_lines] == [
        {"trace_id": "newest", "stage": "final_answer"}
    ]
    assert "records=1" in result.stdout
    assert "truncated=false" in result.stdout
