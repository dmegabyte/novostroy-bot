from nmbot_v6.gateway import PROMPT2_PATH, build_question_policy
from nmbot_v6.prompt1_contract import parse_prompt1


def _plan(*, search_mode=None, count=1, facts=1, near=0):
    params = {"count": count}
    if search_mode is not None:
        params["search_mode"] = search_mode
    fact_cards = [
        {"name": f"ЖК {index}", "location": "Москва", "district": "msk"}
        for index in range(facts)
    ]
    near_cards = [
        {
            "name": f"Рядом {index}",
            "location": "Москва",
            "district": "msk",
            "price_range": "не указано",
            "finishing": "не указано",
            "why_close": "отличие: другой проект",
        }
        for index in range(near)
    ]
    return parse_prompt1({
        "action": "search",
        "target": "new_search",
        "search_policy": "required",
        "clarification_question": "",
        "response": "",
        "facts": fact_cards,
        "near": near_cards,
        "missing": [],
        "params": params,
    })


def test_named_object_uses_expanded_answer_mode_on_first_turn():
    policy = build_question_policy(
        "Расскажи подробно про ЖК Люблинский парк",
        {"revision": 0},
        _plan(search_mode="named_object"),
    )

    assert policy == {
        "question_goal": "offer_layouts_or_viewing",
        "answer_mode": "expanded_detail",
        "cards_displayed": 1,
        "dialogue_step": 1,
    }


def test_single_broad_card_keeps_existing_first_turn_goal():
    policy = build_question_policy(
        "двушка в Люблино",
        {"revision": 0},
        _plan(search_mode="broad", near=1),
    )

    assert policy["answer_mode"] == "standard"
    assert policy["question_goal"] == "choose_complex"


def test_answer_prompt_has_grounded_expanded_contract():
    prompt = PROMPT2_PATH.read_text(encoding="utf-8")

    assert 'answer_mode="expanded_detail"' in prompt
    assert "price_range" in prompt and "area" in prompt and "ready" in prompt
    assert "не спрашивай «Хотите узнать подробнее?»" in prompt
