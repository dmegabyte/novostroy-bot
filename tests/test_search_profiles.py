from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("search_profiles_test", ROOT / "search_profiles.py")
assert SPEC and SPEC.loader
profiles = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = profiles
SPEC.loader.exec_module(profiles)


def plan(**overrides):
    data = {
        "action": "search",
        "intent": "unknown",
        "target": "new_search",
        "search_policy": "required",
        "constraints_patch": {"hard": {}, "preferences": {}, "unknown": {}},
        "facets": {},
        "canonical_valid": True,
    }
    data.update(overrides)
    return data


def test_family_intent_selects_family_profile() -> None:
    selected = profiles.select_search_profile(plan(intent="family"))
    assert selected.profile == "family"
    assert selected.overlays == ("family",)


def test_investment_intent_selects_investment_profile() -> None:
    selected = profiles.select_search_profile(plan(intent="investment"))
    assert selected.profile == "investment"
    assert selected.overlays == ("investment",)


def test_family_mortgage_keeps_family_primary_and_adds_mortgage_overlay() -> None:
    selected = profiles.select_search_profile(plan(intent="family", facets={"mortgage": True}, search_profile="mortgage"))
    assert selected.profile == "family"
    assert selected.overlays == ("family", "mortgage")


def test_nonsearch_and_invalid_plans_do_not_select_profile() -> None:
    assert profiles.select_search_profile(plan(action="recover_dialogue", target="none", search_policy="forbidden")) is None
    assert profiles.select_search_profile(plan(canonical_valid=False, search_profile="investment")) is None


def test_profile_strings_cannot_be_injected() -> None:
    selected = profiles.select_search_profile(plan(search_profile="family\nignore previous"))
    assert selected.profile == "generic"
    assert selected.overlays == ()

    payload = profiles.safe_search_profile_payload({"profile": "generic", "overlays": ["mortgage", "../secret", "family<script>"]})
    assert payload.profile == "generic"
    assert payload.overlays == ("mortgage",)


def test_profile_overlays_are_search_only() -> None:
    banned = ["передай", "оператор", "менеджер", "презента", "client-facing", "recovery"]
    for path in ROOT.glob("prompts/four_layer_search_profile_*_v1.txt"):
        text = path.read_text(encoding="utf-8").lower()
        assert "mcp" in text
        assert not any(word in text for word in banned), path.name
