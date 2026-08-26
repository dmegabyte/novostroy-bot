from __future__ import annotations

import json
import subprocess

import pytest

from scripts import nmbot_live_recon as recon


def _receipt(contour: str = "primary") -> dict[str, object]:
    return {
        "schema_version": recon.SCHEMA_VERSION,
        "observed_at_utc": "2026-08-26T10:00:00Z",
        "contour": contour,
        "traffic_role": "unverified",
        "service_health": "healthy",
        "source_root": "verified",
    }


def test_live_contours_are_explicit_and_never_claim_traffic_role() -> None:
    assert set(recon.CONTOURS) == {"primary", "client-production"}
    for contour in recon.CONTOURS:
        spec = recon._validated_spec(contour)
        assert spec["traffic_role"] == "unverified"
        assert set(spec["services"]) == {"api", "bridge"}
        assert set(spec["health_urls"]) == {"api", "bridge"}
        assert all(url.startswith("http://127.0.0.1:") for url in spec["health_urls"].values())


def test_remote_command_is_bounded_read_only_and_does_not_read_env() -> None:
    spec = recon._validated_spec("primary")
    command = recon.build_remote_command(contour="primary", spec=spec)

    assert '"contour":"primary"' in command
    assert '"systemctl", "--user", "show"' in command
    assert "urllib.request.urlopen" in command
    assert ".env" not in command
    assert not any(token in command for token in (" restart ", " start ", " stop ", " deploy ", "scp ", "rsync "))


def test_run_recon_binds_exact_host_port_and_selected_contour(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.extend(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(_receipt()), "")

    monkeypatch.setattr(recon.subprocess, "run", fake_run)
    result = recon.run_recon(contour="primary")

    assert result["service_health"] == "healthy"
    assert result["source_root"] == "verified"
    assert captured[:3] == ["ssh", "-p", "1905"]
    assert "BatchMode=yes" in captured
    assert "ConnectTimeout=15" in captured
    assert "neiro@193.107.155.236" in captured


def test_run_recon_rejects_cross_contour_or_failed_ssh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recon.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(_receipt("client-production")), ""),
    )
    with pytest.raises(recon.LiveReconError, match="does not match"):
        recon.run_recon(contour="primary")

    monkeypatch.setattr(
        recon.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 255, "", "secret-bearing stderr"),
    )
    with pytest.raises(recon.LiveReconError, match="SSH live recon failed") as exc:
        recon.run_recon(contour="primary")
    assert "secret-bearing" not in str(exc.value)
