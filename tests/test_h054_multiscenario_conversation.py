from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nmbot_v2.contracts import OptionCard, SemanticPlan
from nmbot_v2.conversation import build_native_conversation_answer
from nmbot_v2.state import ConversationState


def test_h054_native_current_options_acknowledges_combined_needs_with_one_question() -> None:
    state = ConversationState(visible_options=(
        OptionCard(name="Семейный берег", location="Москва", price_min=12_000_000, ready="2027", infrastructure=("школа",)),
        OptionCard(name="Арендный парк", location="Москва", price_min=11_000_000, finishing="с отделкой", metro="Сокол"),
        OptionCard(name="Финансовый квартал", location="Москва", price_min=13_000_000, discount="есть акция"),
    ))
    plan = SemanticPlan(operation="current_options", intent="family", facets=["family", "rental", "financing"])

    answer = build_native_conversation_answer(plan, state, "для семьи, под аренду и ипотеку")

    assert "для семьи, аренды и оплаты" in answer
    assert "Семейный берег" in answer
    assert "Арендный парк" in answer
    assert "Финансовый квартал" in answer
    assert "школ" in answer.lower() or "инфраструктур" in answer.lower()
    assert "отделк" in answer.lower() or "метро" in answer.lower()
    assert "скид" in answer.lower() or "цен" in answer.lower()
    assert answer.count("?") == 1
    assert answer.rstrip().endswith("?")
