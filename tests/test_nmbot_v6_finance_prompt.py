from pathlib import Path


PROMPT = (
    Path(__file__).resolve().parents[1] / "prompts" / "v6_simple_answer_writer.txt"
).read_text(encoding="utf-8")


def test_finance_facts_have_a_strict_proof_boundary():
    assert "ФИНАНСОВЫЕ ВОПРОСЫ" in PROMPT
    assert "Скидку, первоначальный взнос, ипотеку, рассрочку и условия платежа" in PROMPT
    assert "совпадающим непустым facts или полям url_card.card" in PROMPT
    assert "current_message, dialogue_history, params и missing" in PROMPT
    assert "не доказывают финансовые факты" in PROMPT


def test_finance_absence_and_mortgage_calculations_are_bounded():
    assert "подтверждённой информации об этом условии нет" in PROMPT
    assert "не повторяй финансовый вопрос клиента" in PROMPT
    assert "mortgage_from_rub_per_month" in PROMPT
    assert "только как расчёт/ориентир со страницы" in PROMPT
    assert "не подтверждают одобрение банка" in PROMPT
    assert "Подтверждённое финансовое поле передавай только как содержание источника" in PROMPT
    assert "без конструкции «указано, что условие действует/доступно/предусмотрено»" in PROMPT
    assert "Не используй «доступно», «предусмотрено», «действует» и их формы" in PROMPT
    assert "Переданный материал не подтверждает применимость этой программы или условия к клиенту и одобрение банка" in PROMPT


def test_finance_keeps_existing_v6_speech_act_and_specialist_ownership():
    assert "закрытых речевых актов" in PROMPT
    assert "Не создавай новые маршруты или вопросы" in PROMPT
    assert "только при dialogue_policy.offer_specialist_now=true" in PROMPT
    assert "Для ANSWER_ONLY final_question может быть пустым" in PROMPT
    assert "ровно один вопрос, соответствующий выбранному речевому акту" in PROMPT
    assert '"action":"reply"' in PROMPT
    assert "Значение request_phone запрещено" in PROMPT
