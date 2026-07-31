from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nmbot_bridge_smoke.py"
spec = importlib.util.spec_from_file_location("nmbot_bridge_smoke", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeResponse:
    status = 200

    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def test_live_posts_once_with_bridge_header_and_redacted_output(capsys) -> None:
    calls = []
    provider_token = "provider-secret"
    bridge_token = "bridge-secret"

    def opener(req, *, timeout):
        calls.append((req, timeout))
        return FakeResponse({"trace_ref": "trace_abcdef123456"})

    assert mod.main(["--live"], environ={mod.PROVIDER_TOKEN_ENV: provider_token, mod.BRIDGE_TOKEN_ENV: bridge_token}, opener=opener) == 0
    output = capsys.readouterr().out

    assert len(calls) == 1
    req, timeout = calls[0]
    assert timeout == mod.DEFAULT_TIMEOUT_SEC
    assert req.get_header("X-nmbot-bridge-token") == bridge_token
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode("utf-8"))["event"] == "CLIENT_MESSAGE"
    assert provider_token not in output
    assert bridge_token not in output
    assert "nmbot bridge smoke" not in output
    assert json.loads(output) == {"accepted_async": True, "http_status": 200, "trace_ref": "trace_abcdef123456"}


def test_without_live_never_calls_network(capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called without --live")

    assert mod.main([], environ={}, opener=forbidden) == 2
    assert json.loads(capsys.readouterr().out) == {"accepted_async": False, "http_status": None, "trace_ref": None}


@pytest.mark.parametrize("argv", [["--host", "example.test"], ["--host", "127.0.0.1:8093"], ["--port", "0"], ["--port", "65536"]])
def test_rejects_unsafe_endpoint_before_post(argv) -> None:
    with pytest.raises(SystemExit) as exc:
        mod.parse_args(argv)
    assert exc.value.code == 2


def test_output_ignores_untrusted_response_fields(capsys) -> None:
    def opener(req, *, timeout):
        return FakeResponse({"trace_ref": "raw-client-id", "text": "private response", "token": "leak"})

    assert mod.main(["--live"], environ={mod.PROVIDER_TOKEN_ENV: "provider", mod.BRIDGE_TOKEN_ENV: "bridge"}, opener=opener) == 1
    assert json.loads(capsys.readouterr().out) == {"accepted_async": False, "http_status": 200, "trace_ref": None}


def test_delivery_trace_mode_is_read_only_command(capsys) -> None:
    assert mod.main(["--delivery-trace"], environ={}) == 0
    assert capsys.readouterr().out == "bash scripts/nmbot_jivo_audit.sh --delivery-trace\n"
