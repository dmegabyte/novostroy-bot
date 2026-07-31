from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_architecture_preflight.py"


def load_architecture_preflight_module():
    spec = importlib.util.spec_from_file_location("nmbot_architecture_preflight_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["nmbot_architecture_preflight_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def check_by_name(result: dict, name: str) -> dict:
    return next(item for item in result["checks"] if item["name"] == name)


def test_real_repo_bridge_async_acknowledgement_with_final_delivery_is_pass() -> None:
    mod = load_architecture_preflight_module()

    result = mod.check(ROOT)
    check = check_by_name(result, "accepted_async_production_path")

    assert check["status"] == "PASS"
    assert "final delivery" in check["explain"]
    assert any(ref["file"] == "scripts/nmbot_n8n_bridge_server.py" and "accepted_async" in ref["snippet"] for ref in check["refs"])
    assert any(ref["file"] == "scripts/nmbot_n8n_bridge_server.py" and "_post_event_to_jivo" in ref["snippet"] for ref in check["refs"])
    assert result["strict_fail"] is False


def test_accepted_async_without_final_delivery_markers_is_strict_fail(tmp_path: Path) -> None:
    mod = load_architecture_preflight_module()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "nmbot_n8n_bridge_server.py").write_text(
        """
async def handle_proxy(request):
    print({"result": "accepted_async"})
    return {"ok": True, "accepted": True}
""".strip(),
        encoding="utf-8",
    )

    result = mod.check(tmp_path)
    check = check_by_name(result, "accepted_async_production_path")

    assert check["status"] == "FAIL"
    assert result["strict_fail"] is True
    assert "final delivery" in check["explain"]
    assert check["refs"] == [{"file": "scripts/nmbot_n8n_bridge_server.py", "line": 2, "snippet": 'print({"result": "accepted_async"})'}]
