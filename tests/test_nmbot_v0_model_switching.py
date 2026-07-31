from __future__ import annotations

from scripts import nmbot_runtime_adapter as adapter


def _minimal_request(stage: str) -> dict[str, object]:
    return adapter._build_v0_gateway_request(
        stage=stage,
        query="test",
        mcp=False,
        max_tokens_env="NMBOT_TEST_MAX_TOKENS",
        max_tokens_default=123,
    )


def test_v0_gateway_uses_independent_search_and_answer_models(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_V0_MODEL", raising=False)
    monkeypatch.setenv("NMBOT_V0_SEARCH_MODEL", "search/model")
    monkeypatch.setenv("NMBOT_V0_ANSWER_MODEL", "answer/model")

    assert _minimal_request("nmbot_v0_scenario_search")["model"] == "search/model"
    assert _minimal_request("nmbot_v0_answer")["model"] == "answer/model"


def test_v0_gateway_falls_back_to_legacy_common_model(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_V0_SEARCH_MODEL", raising=False)
    monkeypatch.delenv("NMBOT_V0_ANSWER_MODEL", raising=False)
    monkeypatch.setenv("NMBOT_V0_MODEL", "legacy/model")

    assert _minimal_request("nmbot_v0_scenario_search")["model"] == "legacy/model"
    assert _minimal_request("nmbot_v0_answer")["model"] == "legacy/model"


def test_v0_gateway_falls_back_to_existing_default(monkeypatch) -> None:
    monkeypatch.delenv("NMBOT_V0_MODEL", raising=False)
    monkeypatch.delenv("NMBOT_V0_SEARCH_MODEL", raising=False)
    monkeypatch.delenv("NMBOT_V0_ANSWER_MODEL", raising=False)

    assert _minimal_request("nmbot_v0_scenario_search")["model"] == adapter.SEARCH_MODEL
    assert _minimal_request("nmbot_v0_answer")["model"] == adapter.SEARCH_MODEL


def test_v0_gateway_rejects_unknown_stage() -> None:
    try:
        adapter._v0_model_for_stage("nmbot_v0_other")
    except ValueError as exc:
        assert "unknown_v0_stage" in str(exc)
    else:
        raise AssertionError("unknown V0 stage must be rejected")
