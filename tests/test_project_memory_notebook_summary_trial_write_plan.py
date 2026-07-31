from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project_memory_notebook_summary_trial_write_plan.py"
AUTHORIZATION_V2 = ROOT / "data" / "notebooklm_summary_trial_write_authorization_safe_v2.json"
AUTHORIZATION_V3 = ROOT / "data" / "notebooklm_summary_trial_write_authorization_safe_v3.json"
AUTHORIZATION_V4 = ROOT / "data" / "notebooklm_summary_trial_write_authorization_safe_v4.json"
AUTHORIZATION_V5 = ROOT / "data" / "notebooklm_summary_trial_write_authorization_safe_v5.json"
APPROVED_SUMMARY_V2 = (
    "Историческая запись от 2026-07-17 фиксирует локальное упрощение NMBot V2: "
    "runtime был сведён к более линейной обработке. Это не подтверждает текущее "
    "состояние кода или продакшена. Изменения оставались локальными, без deploy, "
    "SSH, restart, сети и eval.\n\n"
    "---\n"
    "Provenance: source record cc-daemons/note/20a382a56022; source SHA edeec9...720dc; "
    "status: historical note, not current code or production proof."
)
APPROVED_SUMMARY_SHA256_V2 = "b0859157a9722c86dac9226ace295ac2d45a5336e41b1b4fced3453870052a6d"
SOURCE_SHA256_V2 = "edeec9c399199c99f278b99dc161129cecd9f756114c2d1e2ad3ebc3ab1720dc"
APPROVED_SUMMARY_V3 = (
    "Историческая заметка фиксирует model-only сравнение нескольких моделей на одном "
    "неизменном сценарии без изменений кода, eval или production. Результаты были "
    "неоднородными: встречались слабый либо невалидный формат, пропуск важной семейной "
    "инфраструктуры и неподтверждённые детали. Вывод — нужен более строгий "
    "детерминированный контракт входных данных и recipe.\n\n"
    "---\n"
    "Provenance: source record cc-daemons/note/41b55e418687; source SHA "
    "9a51bdf4759c60255ff0fd1d6121acf6adcea1bee38a4dc3b965d2912e8b8d32; "
    "status: historical note, not current code or production proof."
)
APPROVED_SUMMARY_SHA256_V3 = "36c4d71c1fb56059ced3539963b2d01dd324af2564f06ceb95830bb0c49f8f0b"
SOURCE_SHA256_V3 = "9a51bdf4759c60255ff0fd1d6121acf6adcea1bee38a4dc3b965d2912e8b8d32"
APPROVED_SUMMARY_V4 = (
    "Историческая запись описывает локальную проверку идеи использовать одну модель "
    "одновременно для поиска и финального ответа. Такой подход показал слабое качество "
    "поиска, поэтому предпочтительным остался разделённый вариант: отдельный поиск и "
    "отдельный слой ответа.\n\n"
    "---\n"
    "Provenance: source record cc-daemons/note/531b4257e75b; source SHA "
    "c4bd8dfc38fd5a72b0bebbbc996eb43598b9fc525daf0998d4d9fb9931b74655; "
    "status: historical note, not current code or production proof."
)
APPROVED_SUMMARY_SHA256_V4 = "bf963b40949722e65a85aa54081522be022388cc11cd469b561c8cf3bf9b5c69"
SOURCE_SHA256_V4 = "c4bd8dfc38fd5a72b0bebbbc996eb43598b9fc525daf0998d4d9fb9931b74655"
APPROVED_SUMMARY_V5 = (
    "Историческая запись описывает локальный опциональный слой для формирования "
    "сравнительной подачи вариантов без изменения базового поведения. Были предусмотрены "
    "защитные проверки и fallback; без явного флага слой оставался выключенным.\n\n"
    "---\n"
    "Provenance: source record cc-daemons/note/5d2aab92d9f2; source SHA "
    "6b6cf5b4ad62f6426835b3fa9bdc867620ef1ba1f7529334b2aa00df8db0b18a; "
    "status: historical note, not current code or production proof."
)
APPROVED_SUMMARY_SHA256_V5 = "25928cd7a60a0992e6d95a9f8c0d6514a6480d180ce3bf0d960c789332ccc076"
SOURCE_SHA256_V5 = "6b6cf5b4ad62f6426835b3fa9bdc867620ef1ba1f7529334b2aa00df8db0b18a"


def load_module():
    spec = importlib.util.spec_from_file_location("project_memory_notebook_summary_trial_write_plan_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def load_authorization(path: Path = AUTHORIZATION_V3) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(auth: dict) -> dict:
    mod = load_module()
    return mod.validate_and_build_write_request(auth, authorization_sha256="auth-sha")


def test_v3_authorization_emits_exactly_one_safe_write_request() -> None:
    payload = build(load_authorization())

    assert payload["ok"] is True
    assert payload["write_count"] == 1
    assert payload["target_notebook"] == "nmbot"
    assert payload["destination_policy"] == "canonical_only"
    assert payload["allowed_operation"] == "add_note"
    assert payload["notebook_write_performed"] is False
    request = payload["write_request"]
    assert request["target_notebook"] == "nmbot"
    assert request["operation"] == "add_note"
    assert request["title"] == "NMBot model-only comparison — historical note"
    assert request["content"] == APPROVED_SUMMARY_V3
    assert "cc-daemons/note/41b55e418687" in request["content"]
    assert f"source SHA {SOURCE_SHA256_V3}" in request["content"]
    assert "historical note, not current code or production proof" in request["content"]
    assert request["source_ref"] == {"notebook": "cc-daemons", "kind": "note", "id": "41b55e418687"}
    assert request["source_sha256"] == SOURCE_SHA256_V3
    assert request["provenance"]["authorization_schema"] == "project_memory_notebook_summary_trial_write_authorization.safe_v3"
    assert request["provenance"]["approved_summary_sha256"] == APPROVED_SUMMARY_SHA256_V3


def test_v2_authorization_remains_exactly_compatible() -> None:
    payload = build(load_authorization(AUTHORIZATION_V2))

    assert payload["ok"] is True
    request = payload["write_request"]
    assert request["title"] == "NMBot V2 local simplification — historical note"
    assert request["content"] == APPROVED_SUMMARY_V2
    assert request["source_ref"] == {"notebook": "cc-daemons", "kind": "note", "id": "20a382a56022"}
    assert request["source_sha256"] == SOURCE_SHA256_V2
    assert request["provenance"]["authorization_schema"] == "project_memory_notebook_summary_trial_write_authorization.safe_v2"
    assert request["provenance"]["approved_summary_sha256"] == APPROVED_SUMMARY_SHA256_V2


def test_v4_authorization_emits_exactly_one_safe_write_request() -> None:
    payload = build(load_authorization(AUTHORIZATION_V4))

    assert payload["ok"] is True
    assert payload["write_count"] == 1
    request = payload["write_request"]
    assert request["target_notebook"] == "nmbot"
    assert request["operation"] == "add_note"
    assert request["title"] == "One-model search and answer hypothesis — historical note"
    assert request["content"] == APPROVED_SUMMARY_V4
    assert request["source_ref"] == {"notebook": "cc-daemons", "kind": "note", "id": "531b4257e75b"}
    assert request["source_sha256"] == SOURCE_SHA256_V4
    assert request["provenance"]["authorization_schema"] == "project_memory_notebook_summary_trial_write_authorization.safe_v4"
    assert request["provenance"]["approved_summary_sha256"] == APPROVED_SUMMARY_SHA256_V4


def test_v5_authorization_emits_exactly_one_safe_write_request() -> None:
    payload = build(load_authorization(AUTHORIZATION_V5))

    assert payload["ok"] is True
    assert payload["write_count"] == 1
    request = payload["write_request"]
    assert request["target_notebook"] == "nmbot"
    assert request["operation"] == "add_note"
    assert request["title"] == "Optional reasoning layer MVP — historical note"
    assert request["content"] == APPROVED_SUMMARY_V5
    assert request["source_ref"] == {"notebook": "cc-daemons", "kind": "note", "id": "5d2aab92d9f2"}
    assert request["source_sha256"] == SOURCE_SHA256_V5
    assert request["provenance"]["authorization_schema"] == "project_memory_notebook_summary_trial_write_authorization.safe_v5"
    assert request["provenance"]["approved_summary_sha256"] == APPROVED_SUMMARY_SHA256_V5


def test_cli_outputs_approved_summary_but_no_raw_source_or_storage_markers() -> None:
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--authorization", str(AUTHORIZATION_V3), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["ok"] is True
    assert payload["write_request"]["content"] == APPROVED_SUMMARY_V3
    lowered = run.stdout.lower()
    for token in ("raw source", "source title", "storage_root", "storage root", "transcript", "secret"):
        assert token not in lowered


def test_wrong_source_sha_denied() -> None:
    auth = load_authorization()
    auth["scope"]["source_sha256"] = "0" * 64

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "source_sha_mismatch"
    assert payload["write_count"] == 0


def test_wrong_target_denied() -> None:
    auth = load_authorization()
    auth["target_notebook"] = "cc-daemons"

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "target_notebook_mismatch"


def test_wrong_summary_denied() -> None:
    auth = load_authorization()
    auth["approved_summary"] = APPROVED_SUMMARY_V3 + " Лишняя фраза."

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "summary_not_exactly_approved"


def test_wrong_source_ref_denied() -> None:
    auth = load_authorization()
    auth["scope"]["source_ref"]["id"] = "20a382a56022"

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "source_ref_mismatch"


def test_wrong_title_denied_by_schema_contract() -> None:
    auth = load_authorization()
    auth["safe_title"] = "NMBot wrong title"

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "authorization_keys_invalid"


def test_footer_mismatch_denied() -> None:
    auth = load_authorization()
    auth["approved_summary"] = auth["approved_summary"].replace(
        "historical note, not current code or production proof.",
        "historical note.",
    )

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "summary_not_exactly_approved"


def test_multiple_writes_denied() -> None:
    auth = load_authorization()
    auth["maximum_write_count"] = 2
    auth["scope"]["explicit_record_count"] = 2

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "write_count_mismatch"


def test_v4_mismatch_denied() -> None:
    auth = load_authorization(AUTHORIZATION_V4)
    auth["approved_summary"] = APPROVED_SUMMARY_V5
    auth["scope"]["approved_summary_sha256"] = APPROVED_SUMMARY_SHA256_V5

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "summary_not_exactly_approved"


def test_v5_multiple_writes_denied() -> None:
    auth = load_authorization(AUTHORIZATION_V5)
    auth["maximum_write_count"] = 2
    auth["scope"]["explicit_record_count"] = 2

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "write_count_mismatch"


def test_wrong_summary_hash_denied() -> None:
    auth = load_authorization()
    auth["scope"]["approved_summary_sha256"] = "1" * 64

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "summary_sha_mismatch"


def test_multiple_refs_denied() -> None:
    auth = load_authorization()
    auth["scope"]["source_refs"] = [auth["scope"].pop("source_ref"), {"notebook": "x", "kind": "note", "id": "y"}]

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "scope_invalid"


def test_unsafe_content_denied() -> None:
    auth = load_authorization()
    auth["approved_summary"] = "raw source transcript with secret"

    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "unsafe_summary_content"


def test_failure_flags_denied() -> None:
    auth = load_authorization()
    auth["write_performed"] = True


    payload = build(auth)

    assert payload["ok"] is False
    assert payload["denied_reason"] == "failure_flag_mismatch:write_performed"


def test_no_dangerous_imports() -> None:
    banned = [
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import socket",
        "from socket",
        "import notebooklm",
        "from notebooklm",
        "import mempalace",
        "from mempalace",
    ]
    source = SCRIPT.read_text(encoding="utf-8").lower()
    assert not any(token in source for token in banned)
