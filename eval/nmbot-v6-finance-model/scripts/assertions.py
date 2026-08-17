import json
import re


SPECIALIST_CTA = "Подключить специалиста, чтобы он проверил актуальные варианты по вашему запросу?"
ASYNC_PROMISE = re.compile(
    r"\b(?:я\s+уточню|уточню|проверю|сообщу|вернусь|позже|уведомлю|"
    r"перезвоню|свяжусь)\b",
    re.IGNORECASE,
)
UNSUPPORTED_FINANCE = re.compile(
    r"(?:\bвам\s+(?:точно\s+)?одобрят\b|"
    r"\b(?:ипотека|программа|рассрочка|скидка)\s+(?:вам\s+)?(?:доступн\w*|предусмотрен\w*|действу\w*)\b|"
    r"\b(?:доступн\w*|предусмотрен\w*|действу\w*)\s+(?:ипотека|программа|рассрочка|скидка)\b|"
    r"\bвы\s+(?:точно\s+)?(?:подходите|имеете\s+право|можете\s+оформить)\b|"
    r"\bгарантир\w*\b|\bприменяется\s+к\s+вам\b)",
    re.IGNORECASE,
)


def _fail(reason):
    return {"pass": False, "score": 0.0, "reason": reason}


def _pass():
    return {"pass": True, "score": 1.0, "reason": "pass"}


def _json_from_output(output):
    text = output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    return json.loads(text)


def _variables(context):
    return context.get("vars", context) if isinstance(context, dict) else {}


def get_assert(output, context):
    try:
        result = _json_from_output(output)
    except (json.JSONDecodeError, AttributeError) as exc:
        return _fail(f"Output is not a JSON object: {exc}")

    if set(result) != {"action", "response", "final_question"}:
        return _fail("V6 JSON must have exactly action, response, final_question")
    if result["action"] != "reply" or not isinstance(result["response"], str) or not result["response"].strip():
        return _fail("action must be reply with a non-empty response")
    if "?" in result["response"]:
        return _fail("response must not contain a question")

    question = result["final_question"]
    if not isinstance(question, str) or (question and question.count("?") != 1):
        return _fail("final_question must be empty or exactly one question")

    variables = _variables(context)
    response = result["response"]
    published = f"{response}\n{question}"
    if ASYNC_PROMISE.search(published):
        return _fail("must not promise an asynchronous check or later reply")
    if UNSUPPORTED_FINANCE.search(response):
        return _fail("must not claim unsupported approval, eligibility, applicability, or guarantee")

    if variables.get("expect_absent") and not re.search(
        r"(?:нет\s+(?:подтвержд[её]нн(?:ой|ых)\s+информац|данн|информац)|"
        r"подтвержд[её]нн(?:ой|ых)\s+информац[^.]{0,80}(?:нет|не\s+указан|отсутств)|"
        r"(?:информац|данн)[^.]{0,80}отсутств|"
        r"(?:услови\w*|факт\w*|данн\w*)[^.]{0,80}не\s+подтвержд)",
        response,
        re.IGNORECASE,
    ):
        return _fail("missing finance fact needs an honest not-confirmed boundary")

    expected_values = [item for item in variables.get("expected_values", "").split("|") if item]
    for expected in expected_values:
        if expected.lower() not in response.lower():
            return _fail(f"missing confirmed value: {expected}")

    if variables.get("require_source_framing") and not re.search(
        r"(?:(?:материал|карточк|страниц|сайт)[^.]{0,120}(?:указан|содержит|расч[её]т|ориентир)|"
        r"(?:указан|содержит|расч[её]т|ориентир)[^.]{0,120}(?:материал|карточк|страниц|сайт))",
        response,
        re.IGNORECASE,
    ):
        return _fail("confirmed finance values must be attributed to the supplied material or page")

    if variables.get("require_page_calculation") and not re.search(
        r"(?:(?:расч[её]т|ориентир)[^.]{0,100}(?:страниц|сайт|переданн\w*\s+материал)|"
        r"(?:страниц|сайт|переданн\w*\s+материал)[^.]{0,100}(?:расч[её]т|ориентир))", response, re.IGNORECASE
    ):
        return _fail("monthly payment must be framed as a page calculation or orientation")

    if variables.get("require_approval_boundary") and not re.search(
        r"(?:(?:одобрен|одобрени|применим|доступн)[^.]{0,80}(?:не\s+подтвержд|нельзя\s+подтвердить)|"
        r"(?:не\s+подтвержд|нельзя\s+подтвердить)[^.]{0,80}(?:одобрен|одобрени|применим|доступн))",
        response,
        re.IGNORECASE,
    ):
        return _fail("family mortgage case needs an explicit approval/applicability boundary")

    if variables.get("expect_specialist_cta"):
        if question != SPECIALIST_CTA:
            return _fail("specialist CTA must be exact and the only question")
    elif question == SPECIALIST_CTA or "специалист" in published.lower():
        return _fail("specialist CTA is not allowed for this case")

    return _pass()
