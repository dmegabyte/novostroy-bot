from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import cc_daemons_shared_core_dependency_card as card_validator  # noqa: E402


def make_cc_daemons_root(root: Path) -> None:
    (root / "core").mkdir(parents=True)
    (root / "core" / "daemon_engine.py").write_text(
        "class DaemonEngine:\n"
        "    def __init__(self, config=None, config_path=None):\n"
        "        raise ValueError('Provide either config dict or config_path')\n",
        encoding="utf-8",
    )
    (root / "core" / "logger.py").write_text(
        "LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARN': 30, 'ERROR': 40}\n"
        "class Logger:\n"
        "    def info(self, message):\n"
        "        return message\n",
        encoding="utf-8",
    )
    for name in ("crm.py", "sheets.py", "vault.py", "provider_router.py", "config.py", "constants.py"):
        (root / "core" / name).write_text("x = 1\n", encoding="utf-8")


def valid_card(root: Path) -> dict:
    return {
        "schema": "project_dependency_card.v1",
        "scope_id": "cc-daemons.shared-core.v1",
        "owner_project": "cc-daemons",
        "consumer_projects": ["cc2", "mpn", "qapairs"],
        "canonical_notebook": "cc-daemons",
        "contract_ref": "docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md:163-189; /home/ser/projects/cc-daemons/PROJECT_MAP.md:44-59",
        "reason": "one-hop shared dependency contour",
        "allowed_query_types": ["contract", "interface"],
        "max_depth": 1,
        "max_records": 2,
        "transitive_traversal": False,
        "source_body_access": False,
        "records": [
            {
                "id": "daemon",
                "kind": "interface",
                "owner_root": str(root),
                "paths": [{"path": "core/daemon_engine.py", "symbols": ["DaemonEngine"], "literals": ["Provide either config dict or config_path"]}],
                "excluded_operations": ["run loop", "Google Sheets read/write", "subprocess pipeline execution"],
            },
            {
                "id": "logger",
                "kind": "interface",
                "owner_root": str(root),
                "paths": [{"path": "core/logger.py", "symbols": ["Logger"], "literals": ["DEBUG", "INFO", "WARN", "ERROR"]}],
                "excluded_operations": ["log file body retrieval"],
            },
        ],
        "excluded_paths": ["core/crm.py", "core/sheets.py", "core/vault.py", "core/provider_router.py", "core/config.py", "core/constants.py", "configs/", "logs/", ".env"],
        "excluded_interfaces": ["CRMSender", "ProviderRouter", "ConfigLoader", "VaultToken"],
        "excluded_operations": ["CRM", "Sheets", "Vault", "provider routing", "config secrets", "network", "runtime", "VPS", "apply", "write"],
        "no_transitive": True,
    }


def test_valid_card_is_bounded_to_two_shared_core_interfaces(tmp_path: Path) -> None:
    root = tmp_path / "cc-daemons"
    make_cc_daemons_root(root)
    summary = card_validator.validate_card(valid_card(root), root)
    assert "consumer_projects=cc2,mpn,qapairs" in summary
    assert "records=2" in summary
    assert "interface_paths=core/daemon_engine.py,core/logger.py" in summary


def test_runtime_sensitive_core_paths_and_symbols_are_denied(tmp_path: Path) -> None:
    root = tmp_path / "cc-daemons"
    make_cc_daemons_root(root)
    bad = deepcopy(valid_card(root))
    bad["records"][0]["paths"][0]["path"] = "core/sheets.py"
    with pytest.raises(card_validator.CardError, match="denied runtime/integration-sensitive path"):
        card_validator.validate_card(bad, root)

    bad_symbol = deepcopy(valid_card(root))
    bad_symbol["records"][0]["paths"][0]["symbols"] = ["ProviderRouter"]
    with pytest.raises(card_validator.CardError, match="sensitive symbols are denied"):
        card_validator.validate_card(bad_symbol, root)


def test_depth_record_source_body_and_transitive_controls_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "cc-daemons"
    make_cc_daemons_root(root)
    too_deep = deepcopy(valid_card(root))
    too_deep["max_depth"] = 2
    with pytest.raises(card_validator.CardError, match="max_depth=1"):
        card_validator.validate_card(too_deep, root)

    body_access = deepcopy(valid_card(root))
    body_access["source_body_access"] = True
    with pytest.raises(card_validator.CardError, match="source_body_access"):
        card_validator.validate_card(body_access, root)

    transitive = deepcopy(valid_card(root))
    transitive["records"][0]["dependencies"] = ["other"]
    with pytest.raises(card_validator.CardError, match="transitive dependency fields are denied"):
        card_validator.validate_card(transitive, root)

    too_many = deepcopy(valid_card(root))
    too_many["records"].append(deepcopy(too_many["records"][0]))
    with pytest.raises(card_validator.CardError, match="exceeds max_records=2"):
        card_validator.validate_card(too_many, root)


def test_missing_required_exclusions_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "cc-daemons"
    make_cc_daemons_root(root)
    bad = deepcopy(valid_card(root))
    bad["excluded_paths"].remove("core/vault.py")
    with pytest.raises(card_validator.CardError, match="excluded_paths"):
        card_validator.validate_card(bad, root)


def test_real_checked_in_card_validates_and_prints_no_source_body() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "cc_daemons_shared_core_dependency_card.py")],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cc-daemons shared-core dependency card OK" in result.stdout
    assert "Provide either config dict or config_path" not in result.stdout
    assert "class DaemonEngine" not in result.stdout


def test_cli_rejects_override_options() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "cc_daemons_shared_core_dependency_card.py"), "--card", "x.json"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_card_path_override_is_rejected(tmp_path: Path) -> None:
    other = tmp_path / "card.json"
    other.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(card_validator.CardError, match="checked-in shared-core dependency card"):
        card_validator.validate(other)
