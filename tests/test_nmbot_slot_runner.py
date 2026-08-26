from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from scripts.nmbot_slot_runner import SLOT_SCHEMA, SlotRunnerError, build_environment, load_descriptor, run_descriptor


def _slot_fixture(tmp_path: Path, *, profile: str = "TEST", release_id: str = "v6-r41") -> Path:
    release_root = tmp_path / "releases" / release_id
    (release_root / "scripts").mkdir(parents=True)
    (release_root / "scripts" / "nmbot_api_server.py").write_text("# entrypoint\n", encoding="utf-8")
    identity = release_root / "release_identity" / "nmbot_release_identity.json"
    identity.parent.mkdir()
    identity.write_text(json.dumps({"schema": "nmbot.release_identity.v1", "release_id": release_id}), encoding="utf-8")
    file_rows = []
    for path in (release_root / "scripts" / "nmbot_api_server.py", identity):
        file_rows.append({"path": path.relative_to(release_root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = tmp_path / f"{release_id}.manifest.json"
    manifest.write_text(json.dumps({"schema_version": "nmbot.atomic_release.v1", "release_id": release_id, "files": file_rows}), encoding="utf-8")
    env_file = tmp_path / f"{profile.lower()}.env"
    env_file.write_text("NMBOT_API_TOKEN=token-for-test\nSHARED_SETTING='shared value'\n", encoding="utf-8")
    descriptor = tmp_path / f"{profile.lower()}-a.json"
    descriptor.write_text(json.dumps({
        "schema": SLOT_SCHEMA,
        "profile": profile,
        "slot": "A",
        "release_id": release_id,
        "release_root": str(release_root),
        "manifest_path": str(manifest),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "env_file": str(env_file),
        "data_root": str(tmp_path / "profiles" / profile.lower() / "data"),
        "port": 18088,
    }), encoding="utf-8")
    return descriptor


def test_descriptor_and_environment_pin_exact_release_and_profile_data(tmp_path: Path) -> None:
    path = _slot_fixture(tmp_path)
    descriptor = load_descriptor(path)
    environment = build_environment(descriptor, base_env={"PATH": os.environ.get("PATH", "")})

    assert descriptor["release_id"] == "v6-r41"
    assert environment["NMBOT_CONTOUR_PROFILE"] == "TEST"
    assert environment["NMBOT_API_PORT"] == "18088"
    assert environment["NMBOT_API_STATE_FILE"].endswith("profiles/test/data/nmbot_api_state.json")
    assert environment["NMBOT_CALLBACK_OUTBOX_DIR"].endswith("profiles/test/data/crm_callback_outbox")
    assert environment["NMBOT_DIALOGUE_JOURNAL"].endswith("profiles/test/data/dialogue/dialogue.jsonl")
    assert environment["NMBOT_LOGS_DIR"].endswith("profiles/test/data/logs")
    assert environment["NMBOT_RELEASE_IDENTITY_FILE"].endswith("releases/v6-r41/release_identity/nmbot_release_identity.json")
    assert environment["NMBOT_API_TOKEN"] == "token-for-test"
    assert environment["SHARED_SETTING"] == "shared value"


def test_test_and_prod_use_separate_data_roots(tmp_path: Path) -> None:
    test = load_descriptor(_slot_fixture(tmp_path / "test", profile="TEST"))
    prod = load_descriptor(_slot_fixture(tmp_path / "prod", profile="PROD"))

    assert build_environment(test)["NMBOT_API_STATE_FILE"] != build_environment(prod)["NMBOT_API_STATE_FILE"]
    assert build_environment(test)["NMBOT_CALLBACK_OUTBOX_DIR"] != build_environment(prod)["NMBOT_CALLBACK_OUTBOX_DIR"]
    assert build_environment(test)["NMBOT_DIALOGUE_JOURNAL"] != build_environment(prod)["NMBOT_DIALOGUE_JOURNAL"]


def test_descriptor_rejects_identity_mismatch_and_extra_fields(tmp_path: Path) -> None:
    path = _slot_fixture(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["release_id"] = "v6-r42"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SlotRunnerError, match="release"):
        load_descriptor(path)

    payload["release_id"] = "v6-r41"
    payload["command"] = "unsafe"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SlotRunnerError, match="schema mismatch"):
        load_descriptor(path)


def test_run_descriptor_execs_only_pinned_entrypoint(tmp_path: Path, monkeypatch) -> None:
    path = _slot_fixture(tmp_path)
    called: dict = {}

    def fake_exec(executable: str, argv: list[str], environment: dict[str, str]) -> None:
        called.update(executable=executable, argv=argv, environment=environment, cwd=os.getcwd())
        raise RuntimeError("exec captured")

    with pytest.raises(RuntimeError, match="exec captured"):
        run_descriptor(path, exec_fn=fake_exec)

    assert called["argv"][1].endswith("releases/v6-r41/scripts/nmbot_api_server.py")
    assert called["argv"][-1] == "18088"
    assert called["environment"]["NMBOT_CONTOUR_PROFILE"] == "TEST"
    assert called["cwd"].endswith("releases/v6-r41")


def test_runner_rejects_tampering_after_slot_prepare(tmp_path: Path) -> None:
    path = _slot_fixture(tmp_path)
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    entrypoint = Path(descriptor["release_root"]) / "scripts" / "nmbot_api_server.py"
    entrypoint.write_text("# tampered\n", encoding="utf-8")
    with pytest.raises(SlotRunnerError, match="hash mismatch"):
        run_descriptor(path, exec_fn=lambda *_args: None)
