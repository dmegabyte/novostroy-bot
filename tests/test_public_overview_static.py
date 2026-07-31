from __future__ import annotations

from pathlib import Path

from scripts import build_public_overview


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public_site" / build_public_overview.NMBOT_SLUG


def test_public_overview_main_static_contract() -> None:
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")

    assert "<meta name=\"description\"" in html
    assert "rel=\"canonical\"" in html
    assert "name=\"robots\" content=\"index,follow\"" in html
    assert ":focus-visible" in html
    assert "<footer>" in html
    assert "history.json" not in html
    assert "setInterval" not in html
    assert "Decision architecture" not in html
    assert "Legacy-схема" not in html


def test_public_overview_version_flows_are_not_mixed() -> None:
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")

    assert "scenario_search" in html
    assert "validated brief" in html
    assert "V0 не проходит через общий V2/V3 Semantic plan" in html
    assert "V2 semantic planner" in html
    assert "V3 IntentPlanV3" in html
    assert "canonical cards" in html
    assert "ResponsePlan + deterministic fallback" in html


def test_public_history_is_one_shot_lazy_load() -> None:
    html = (PUBLIC / "history.html").read_text(encoding="utf-8")

    assert "history.json" in html
    assert "addEventListener('click', loadHistoryOnce, {once:true})" in html
    assert "setInterval" not in html
    assert "loadHistoryOnce();" not in html


def test_public_pages_do_not_publish_secret_or_raw_material_markers() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC.glob("*.html"))
    forbidden = [
        "API_KEY",
        "SECRET_KEY",
        "TOKEN=",
        "BEGIN PRIVATE KEY",
        "logs/dialogs-",
        "NMBOT_V2_RESPONSE_COMPOSER_MODE",
        "NMBOT_V3_RESPONSE_COMPOSER_MODE",
        ".env",
        "Полный текст ТЗ",
        "setInterval(loadHistory",
    ]
    for marker in forbidden:
        assert marker not in combined
