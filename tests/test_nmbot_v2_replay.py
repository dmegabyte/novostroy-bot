import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.replay import load_dialogues, run_corpus


FIXTURE = Path(__file__).parent / "fixtures" / "nmbot_v2_dialogue_replay.jsonl"


def test_replay_fixture_has_required_15_dialogues_and_no_raw_contact_shapes():
    records = list(load_dialogues(FIXTURE))

    assert len(records) == 15
    dialogue_ids = [record["id"] for record in records]
    assert len(set(dialogue_ids)) == len(dialogue_ids)
    raw = FIXTURE.read_text(encoding="utf-8")
    assert "+7" not in raw
    assert "token" not in raw.lower()
    assert "phone" not in raw.lower()


def test_replay_all_corpus_rows_pass_deterministically():
    first = run_corpus(FIXTURE)
    second = run_corpus(FIXTURE)

    assert [r.dialogue_id for r in first] == [r.dialogue_id for r in second]
    assert [[t.response_text for t in r.turns] for r in first] == [[t.response_text for t in r.turns] for r in second]
    assert len(first) == 15
