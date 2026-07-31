from __future__ import annotations

from pathlib import Path

from nmbot_v2.prompt_provenance import build_prompt_provenance, identity_from_path, identity_from_text, sanitize_prompt_provenance


def test_identity_hash_and_prompt_set_are_deterministic(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Привет\n", encoding="utf-8")

    item = identity_from_path("response_writer", "prompts/v2_response_writer.txt", prompt)
    same = identity_from_text("response_writer", "prompts/v2_response_writer.txt", "Привет\n")
    assert item == same
    assert item["prompt_id"] == "p_" + item["sha256"][:12]

    first = build_prompt_provenance([item, identity_from_text("search", "prompts/v2_search_mcp.txt", "S")])
    second = build_prompt_provenance(list(reversed(first["prompts"])))
    assert first["prompt_set_id"] == second["prompt_set_id"]
    assert first["set_sha256"] == second["set_sha256"]


def test_usage_does_not_change_prompt_set_identity() -> None:
    invoked = identity_from_text("search", "prompts/v2_search_mcp.txt", "same", usage="invoked")
    configured = identity_from_text("search", "prompts/v2_search_mcp.txt", "same", usage="configured")
    a = build_prompt_provenance([invoked], coverage="complete")
    b = build_prompt_provenance([configured], coverage="configured_only")
    assert a["prompt_set_id"] == b["prompt_set_id"]
    assert a["set_sha256"] == b["set_sha256"]


def test_sanitization_rejects_prompt_body_unsafe_source_and_hash() -> None:
    good = build_prompt_provenance([identity_from_text("search", "prompts/v2_search_mcp.txt", "safe")])
    assert sanitize_prompt_provenance(good) == good

    tampered = dict(good)
    tampered["prompts"] = [dict(good["prompts"][0], prompt_body="secret prompt")]
    assert "prompt_body" not in sanitize_prompt_provenance(tampered)["prompts"][0]

    bad_source = build_prompt_provenance([dict(good["prompts"][0], source="../secret/prompt.txt")])
    assert sanitize_prompt_provenance(bad_source)["prompts"] == []

    bad_hash = dict(good)
    bad_hash["prompts"] = [dict(good["prompts"][0], sha256="abc")]
    assert sanitize_prompt_provenance(bad_hash) is None
