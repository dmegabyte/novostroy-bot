from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .contracts import OptionCard, SearchResult
from .search_contract import V2SearchRequest, normalize_and_validate_search_output


QUALITY_WEIGHTS = {
    "search_accuracy": 20,
    "data_integrity": 20,
    "presentation_quality": 15,
    "language_quality": 10,
    "scenario_fit": 10,
    "dialogue_continuity": 10,
    "reliability": 10,
    "latency": 5,
}
COMPOSER_STATUSES = {"primary", "repaired", "provider_retry", "fallback"}


TECHNICAL_RE = re.compile(
    r"\b(?:mcp|json|regex|traceback|openrouter|gateway|overmind|payload|diagnostics|search_response|facts\[|near\[|params|optioncard|pending[_-]scenario|pending[_-]followup|canonical[_-](?:error|valid|fields)|dialog[_-]action|search[_-]policy|runtime[_-]stage|error[_-]code|operator[_-]reason)\b|```|[{}\[\]]",
    re.I,
)
UNSUPPORTED_CLAIM_RE = re.compile(
    r"доходност|окупаемост|ликвидност|рост(?:а|ом)?\s+цен|цены\s+выраст|гарантирован|легко\s+сдат|простой\s+сдат|арендн(?:ая|ую)\s+ставк|\b\d+\s*%\s*(?:годовых|доход|аренд)",
    re.I,
)
ABSENCE_RE = re.compile(r"(?:ничего|вариантов|точно таких|подходящих).{0,30}(?:нет|не нашла|не вижу|не найден)", re.I)
OPERATOR_RE = re.compile(r"оператор|менеджер|специалист|номер", re.I)
LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
ANGLICISM_RE = re.compile(r"\bwhite\s*box\b|\bвайт\s*бокс\b", re.I)
DUPLICATE_PUNCTUATION_RE = re.compile(r"\.{2,}|[!?]{2,}|\.[!?]")

FAMILY_TERMS = ("школ", "сад", "дет", "парк", "двор", "безопас", "спорт", "эколог", "метро")
INVESTMENT_TERMS = ("цена", "млн", "вход", "скид", "продаж", "егрн", "ипот", "сдел")
RENTAL_TERMS = ("студи", "одноком", "евро", "отдел", "метро", "сдан", "готов", "район")
LIFE_TERMS = ("локац", "метро", "парк", "вода", "террит", "безопас", "паркинг", "отдел", "сдан")
FINANCE_TERMS = ("ипот", "ставк", "взнос", "рассроч", "скид", "плат", "цена", "млн")


@dataclass(frozen=True)
class QualityReport:
    scenario: str
    ok: bool
    score: int
    verdict: str
    layer_to_fix: str
    search_mcp: str
    card: str
    response: str
    dimensions: dict[str, int]
    hard_blockers: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    quality_profile: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def markdown_row(self) -> str:
        return " | ".join(
            [
                self.scenario,
                self.search_mcp,
                self.card,
                self.response,
                str(self.dimensions.get("facts", 0)),
                str(self.dimensions.get("completeness", 0)),
                str(self.dimensions.get("beauty", 0)),
                str(self.dimensions.get("scenario_fit", 0)),
                str(self.dimensions.get("dialogue", 0)),
                str(self.score),
                self.verdict,
                self.layer_to_fix,
            ]
        )


def evaluate_scenario(
    *,
    scenario_id: str,
    response_text: str,
    search_result: SearchResult | None = None,
    search_output: Mapping[str, Any] | None = None,
    search_request: V2SearchRequest | None = None,
    viewpoint: str | None = None,
    base_viewpoint: str | None = None,
) -> QualityReport:
    """Deterministic offline quality gate for one produced V2 answer.

    The evaluator is intentionally conservative. It does not try to parse every
    Russian sentence. Instead it validates the contract-shaped search envelope,
    checks structured/card-backed facts, catches hard UX blockers, and scores the
    answer by stable heuristics that are safe for fixture and CI usage.
    """

    text = str(response_text or "").strip()
    sr = search_result or SearchResult.from_dict(search_output or {})
    cards = sr.shortlist(3)
    exact_count = len(sr.facts)
    vp = viewpoint or (search_request.response_viewpoint if search_request else None) or _viewpoint_from_scenario(scenario_id)
    base = base_viewpoint or (search_request.base_viewpoint if search_request else None)

    issues: list[str] = []
    blockers: list[str] = []
    search_ok = True
    search_errors: list[str] = []
    if search_request is not None and search_output is not None:
        _normalized, validation = normalize_and_validate_search_output(dict(search_output), search_request)
        search_ok = bool(validation.get("ok"))
        search_errors = list(validation.get("errors") or [])
        if not search_ok:
            blockers.append("search_contract_invalid")
            issues.extend(search_errors)

    questions = text.count("?")
    if questions != 1:
        blockers.append("question_count_not_one")
    elif not text.endswith("?"):
        blockers.append("final_question_not_at_end")
    if TECHNICAL_RE.search(text):
        blockers.append("technical_or_internal_leak")
    if len(cards) > 3 or _rendered_card_count(text) > 3:
        blockers.append("too_many_cards")
    if exact_count > 0 and ABSENCE_RE.search(text):
        blockers.append("false_inventory_absence")
    if exact_count > 0 and OPERATOR_RE.search(_first_half(text)) and len(cards) >= 1:
        blockers.append("early_operator")
    if _has_duplicate_blocks(text):
        blockers.append("duplicate_answers")
    if _has_duplicate_intro_summary(text, cards):
        blockers.append("duplicate_intro_summary")
    if ANGLICISM_RE.search(text):
        blockers.append("internal_enum_or_anglicism")
    if _repeated_benefit_sentences(text):
        blockers.append("repeated_identical_benefit")
    if _semantic_sales_mismatch(text, cards):
        blockers.append("semantic_label_mismatch")
    if re.search(r"идеальн\w*\s+(?:жиль|кварт)|счастлив\w*\s+жизн|наслаждаться\s+комфорт|широк\w*\s+выбор|позвол\w*\s+(?:вам\s+)?выбрать|найти\s+именно\s+то", text, re.I):
        blockers.append("unsupported_marketing_claim")
    if len(cards) >= 2 and not _has_separate_numbered_blocks(text, len(cards)):
        blockers.append("cards_not_separate_blocks")
    if _dry_cards_without_reasons(text, cards):
        blockers.append("dry_card_without_presentation_reason")
    if LONG_NUMBER_RE.search(text):
        blockers.append("raw_number_leak")
    if DUPLICATE_PUNCTUATION_RE.search(text):
        blockers.append("duplicate_punctuation")
    if re.search(r"\b(?:цены?\s+от\s+)?0(?:[.,]0+)?\s*(?:₽|руб)", text, re.I):
        blockers.append("nonpositive_price_leak")
    if re.search(r"₽\s*руб", text, re.I):
        blockers.append("duplicate_currency")
    if re.search(r"(?m)^\s*[1-9]\.\s+.*?—.*?,\s*[1-4]\s*,", text):
        blockers.append("raw_room_or_area_token")

    no_cards = not sr.facts and not sr.near
    no_constraints = search_request is not None and not _request_has_real_constraints(search_request)
    if no_cards and no_constraints:
        if re.search(r"ослаб", text, re.I):
            blockers.append("initial_empty_search_relaxation")
        if ABSENCE_RE.search(text):
            blockers.append("initial_empty_false_absence")
    if _raw_missing_leak(text, sr.missing):
        blockers.append("raw_missing_leak")

    unsupported = _unsupported_claims(text, cards)
    if unsupported:
        blockers.append("unsupported_claim")
        issues.extend(unsupported)

    invented = _invented_card_facts(text, cards)
    if invented:
        blockers.append("invented_fact")
        issues.extend(invented)

    dimensions = {
        "facts": _score_facts(blockers, search_ok),
        "completeness": _score_completeness(text, cards, vp, base, blockers),
        "beauty": _score_beauty(text, cards, blockers),
        "scenario_fit": _score_scenario_fit(text, cards, vp, base, blockers),
        "dialogue": _score_dialogue(text, blockers),
    }
    score = sum(dimensions.values())
    ok = score >= 9 and not blockers
    quality_profile = build_quality_profile(
        dimensions=dimensions,
        hard_blockers=blockers,
        search_ok=search_ok,
        search_errors=search_errors,
        evidence="offline",
    )
    return QualityReport(
        scenario=scenario_id,
        ok=ok,
        score=score,
        verdict="PASS" if ok else "FAIL",
        layer_to_fix=_route_layer(blockers, search_ok, search_errors),
        search_mcp="OK" if search_ok else "FAIL: " + ", ".join(search_errors[:3]),
        card=_card_status(cards, sr),
        response="OK" if not blockers else "FAIL: " + ", ".join(blockers[:4]),
        dimensions=dimensions,
        hard_blockers=blockers,
        issues=issues,
        quality_profile=quality_profile,
    )


def build_quality_profile(
    *,
    dimensions: Mapping[str, int] | None = None,
    hard_blockers: list[str] | tuple[str, ...] | None = None,
    search_ok: bool = True,
    search_errors: list[str] | tuple[str, ...] | None = None,
    evidence: str = "offline",
    composer_status: str = "primary",
    latency_seconds: float | None = None,
) -> dict[str, Any]:
    """Build the machine-readable V2 quality scorecard.

    Scores are deterministic and derived from structured validation output,
    existing dimensions and hard blockers.  The function intentionally never
    inspects raw model/provider bodies.
    """

    dims = dict(dimensions or {})
    blockers = list(hard_blockers or [])
    errors = list(search_errors or [])
    status = composer_status if composer_status in COMPOSER_STATUSES else "fallback"
    latency_score = _latency_score(latency_seconds)
    scores: dict[str, int | None] = {
        "search_accuracy": _score_search_accuracy(search_ok, blockers, errors),
        "data_integrity": _dim10(dims.get("facts")),
        "presentation_quality": _dim10(dims.get("beauty")),
        "language_quality": _score_language_quality(dims, blockers),
        "scenario_fit": _dim10(dims.get("scenario_fit")),
        "dialogue_continuity": _dim10(dims.get("dialogue")),
        "reliability": _score_reliability(status, blockers),
        "latency": latency_score,
    }
    overall = _weighted_overall(scores)
    gate_pass = not blockers and "composer_degraded_fallback" not in blockers
    maturity = _maturity_label(overall) if gate_pass else "failed_gate"
    return {
        "evidence": evidence,
        "composer_status": status,
        "weights": dict(QUALITY_WEIGHTS),
        "scores": scores,
        "overall": overall,
        "maturity": maturity,
        "gate_pass": gate_pass,
        "latency_seconds": latency_seconds,
        "hard_blockers": blockers,
    }


def _dim10(value: int | None) -> int:
    return max(0, min(10, int(value or 0) * 5))


def _score_search_accuracy(search_ok: bool, blockers: list[str], errors: list[str]) -> int:
    if not search_ok or any(x in blockers for x in ("search_contract_invalid", "strict_json_invalid", "gateway_network_failed", "gateway_not_ok")):
        return 0
    if any("missing_hard_evidence" in err or "violates_hard" in err or err.startswith("params_") for err in errors):
        return 4
    if errors:
        return 6
    if any(x in blockers for x in ("facts_near_mixed", "initial_empty_search_relaxation")):
        return 6
    return 10


def _score_language_quality(dimensions: Mapping[str, int], blockers: list[str]) -> int:
    if any(x in blockers for x in ("technical_or_internal_leak", "internal_enum_or_anglicism", "duplicate_punctuation", "raw_missing_leak", "raw_number_leak", "duplicate_currency", "raw_room_or_area_token")):
        return 0
    if any(x in blockers for x in ("duplicate_answers", "duplicate_intro_summary", "repeated_identical_benefit")):
        return 5
    return _dim10(dimensions.get("beauty"))


def _score_reliability(composer_status: str, blockers: list[str]) -> int:
    if composer_status == "fallback" or "composer_degraded_fallback" in blockers:
        return 3
    if composer_status in {"repaired", "provider_retry"}:
        return 7
    return 10


def _latency_score(latency_seconds: float | None) -> int | None:
    if latency_seconds is None:
        return None
    if latency_seconds <= 20:
        return 10
    if latency_seconds <= 40:
        return 8
    if latency_seconds <= 60:
        return 6
    if latency_seconds <= 90:
        return 4
    return 2


def _weighted_overall(scores: Mapping[str, int | None]) -> float:
    total = 0.0
    for key, weight in QUALITY_WEIGHTS.items():
        # Offline latency may be unknown.  Unknown latency must not improve live
        # readiness, so its weighted contribution is treated as zero.
        value = scores.get(key)
        total += (0 if value is None else float(value)) * weight / 100
    return round(total, 2)


def _maturity_label(overall: float) -> str:
    if overall < 5:
        return "experimental"
    if overall < 7:
        return "alpha"
    if overall < 8.5:
        return "beta"
    if overall < 9.2:
        return "canary_candidate"
    return "production_candidate"


def report_table(reports: list[QualityReport]) -> str:
    header = "Scenario | Search/MCP | Card | Response | Facts | Completeness | Beauty | Scenario fit | Dialogue | Score | Verdict | Layer to fix"
    sep = "--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---"
    return "\n".join([header, sep] + [report.markdown_row() for report in reports])


def _viewpoint_from_scenario(scenario_id: str) -> str:
    if "family" in scenario_id:
        return "family"
    if "investment" in scenario_id or "scenario_field_priority" in scenario_id:
        return "investment"
    if "rental" in scenario_id or "ready_finishing" in scenario_id:
        return "rental"
    if "financing" in scenario_id or "broad_candidates" in scenario_id:
        return "financing"
    return "life"


def _first_half(text: str) -> str:
    return text[: max(1, len(text) // 2)]


def _rendered_card_count(text: str) -> int:
    return len(re.findall(r"(?m)^\s*[1-9]\.", text))


def _has_duplicate_blocks(text: str) -> bool:
    blocks = [re.sub(r"\s+", " ", x.strip().casefold()) for x in text.split("\n\n") if x.strip()]
    return len(blocks) != len(set(blocks))


def _has_duplicate_intro_summary(text: str, cards: tuple[OptionCard, ...]) -> bool:
    paragraphs = [x.strip().casefold() for x in text.split("\n\n") if x.strip()]
    if len(paragraphs) < 2 or not cards:
        return False
    first_two = " ".join(paragraphs[:2])
    return bool(re.search(r"наш\w*.{0,60}(?:вариант|жк).{0,80}наш\w*\s+\d+.{0,30}(?:вариант|жк)", first_two, re.I))


def _benefit_sentences(text: str) -> list[str]:
    reason_lines = [line.strip() for line in text.splitlines() if line.strip() and not re.match(r"^\s*\d+\.\s+", line)]
    sentences = []
    for line in reason_lines:
        sentences.extend(re.split(r"(?<=[.!])\s+", line))
    out: list[str] = []
    for sentence in sentences:
        normalized = re.sub(r"\s+", " ", sentence.strip().casefold())
        if len(normalized) >= 28 and re.search(r"удоб|польз|проще|сниж|сокращ|уменьш|помог|практич|ориентир|бюджет|эконом|количеств|предложен|витрин|показыв|будн|маршрут|подготов|предсказ|меньше", normalized):
            out.append(normalized)
    return out


def _repeated_benefit_sentences(text: str) -> bool:
    benefits = _benefit_sentences(text)
    return len(benefits) != len(set(benefits))


def _semantic_sales_mismatch(text: str, cards: tuple[OptionCard, ...]) -> bool:
    # The final question may ask which search parameter to provide (for
    # example, a metro station). That is a dialogue action, not a factual
    # claim about an OptionCard. Grounding checks inspect the declarative body;
    # quoted ЖК names are still checked above against the full response.
    declarative = text.rsplit("\n\n", 1)[0] if text.rstrip().endswith("?") and "\n\n" in text else text
    lowered = declarative.casefold()
    # Honest caveats about missing sales data are not positive sales claims.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", lowered)
    positive = " ".join(
        sentence
        for sentence in sentences
        if not (
            re.search(r"\bпродаж|сдел", sentence)
            and re.search(r"\bнет\b|не\s+хватает|без\s+(?:данн|информац|подтвержден)|не\s+подтверж", sentence)
        )
    )
    says_sales = bool(re.search(r"\bпродаж|сдел", positive))
    has_sales = any(card.sales_count is not None for card in cards)
    has_ads = any(card.ads_count is not None for card in cards)
    return says_sales and has_ads and not has_sales


def _has_separate_numbered_blocks(text: str, expected_cards: int) -> bool:
    matches = list(re.finditer(r"(?m)^\s*([1-9])\.\s+", text))
    if len(matches) < min(expected_cards, 3):
        return False
    for idx, match in enumerate(matches[: min(expected_cards, 3)]):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[match.start():end].strip()
        if "\n" not in block:
            return False
    return True


def _card_has_benefit_facts(card: OptionCard) -> bool:
    return bool(card.infrastructure or card.finishing or card.ready or card.metro or card.area or card.sales_count is not None or card.ads_count is not None or card.discount)


def _dry_cards_without_reasons(text: str, cards: tuple[OptionCard, ...]) -> bool:
    if not cards:
        return False
    matches = list(re.finditer(r"(?m)^\s*([1-9])\.\s+", text))
    if len(matches) < min(len(cards), 3):
        return bool(len(cards) >= 2)
    for idx, card in enumerate(cards[:3]):
        if not _card_has_benefit_facts(card):
            continue
        start = matches[idx].start() if idx < len(matches) else -1
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip() if start >= 0 else ""
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        reason = " ".join(lines[1:])
        if len(reason) < 45 or not re.search(r"удоб|польз|полез|проще|упрощ|сниж|сокращ|уменьш|помог|практич|ориентир|бюджет|эконом|сравн|выбор|счётчик|счетчик|количеств|предложен|витрин|показыв|будн|маршрут|подготов|предсказ|хлопот|логист|спокойн|добав|ежеднев|переезд|ожидан|ждать|строительств|срок|засел", reason, re.I):
            return True
    return False


def _unsupported_claims(text: str, cards: tuple[OptionCard, ...]) -> list[str]:
    if not UNSUPPORTED_CLAIM_RE.search(text):
        return []
    facts = _all_card_text(cards)
    allowed = any(marker in facts for marker in ("egrn", "продаж", "sales", "counter", "скид", "%", "ипот", "mortgage"))
    if allowed and not re.search(r"гарантирован|окупаемост|доходност|цены\s+выраст|рост\s+цен", text, re.I):
        return []
    return ["unsupported investment/rental/finance claim"]


def _invented_card_facts(text: str, cards: tuple[OptionCard, ...]) -> list[str]:
    issues: list[str] = []
    known_names = {_normalize_name(card.name) for card in cards}
    for quoted in re.findall(r"ЖК\s+[«\"]([^»\"]+)[»\"]", text):
        if _normalize_name(quoted) not in known_names:
            issues.append(f"unknown_complex:{quoted}")
    facts = _all_card_text(cards)
    fact_sensitive = {
        "school": ("школ",),
        "kindergarten": ("сад", "детсад"),
        "park": ("парк", "зел"),
        "metro": ("метро",),
        "developer": ("застройщик",),
        "security": ("безопас", "охрана"),
        "yard": ("двор без машин",),
    }
    has_structured = {
        "metro": any(card.metro for card in cards),
        "developer": any(card.developer for card in cards),
        "school": any("школ" in " ".join(card.infrastructure).casefold() for card in cards),
        "kindergarten": any("сад" in " ".join(card.infrastructure).casefold() for card in cards),
        "park": any("парк" in " ".join(card.infrastructure).casefold() for card in cards),
        "security": any("охран" in " ".join(card.infrastructure).casefold() or "безопас" in " ".join(card.infrastructure).casefold() for card in cards),
        "yard": any("двор без машин" in " ".join(card.infrastructure).casefold() for card in cards),
    }
    # Ground only claims made inside rendered card blocks. A separate caveat
    # such as "не хватает подтверждения по безопасности" is an absence claim,
    # not evidence that security exists. With no cards, inspect the
    # declarative body but still exclude the final clarification question.
    matches = list(re.finditer(r"(?m)^\s*[1-9]\.\s+", text))
    if matches:
        blocks: list[str] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            block = text[match.start():end]
            # Stop at the first paragraph after the card/reason block.
            blocks.append(block.split("\n\n", 1)[0])
        claim_text = "\n".join(blocks)
    else:
        claim_text = text.rsplit("\n\n", 1)[0] if text.rstrip().endswith("?") and "\n\n" in text else text
    lowered = claim_text.casefold()
    for label, needles in fact_sensitive.items():
        if has_structured.get(label):
            continue
        if any(n in lowered for n in needles) and not any(n in facts for n in needles):
            # Official names may contain harmless words like Park/Парк; require a
            # benefit-ish phrase before flagging park claims.
            if label == "park" and not re.search(r"рядом.{0,20}парк|парк.{0,30}(рядом|зел|гуля|сем)", lowered):
                continue
            issues.append(f"unsupported_{label}")
    return issues


def _all_card_text(cards: tuple[OptionCard, ...]) -> str:
    return json.dumps([_card_dict(card) for card in cards], ensure_ascii=False, default=str).casefold()


def _card_dict(card: OptionCard) -> dict[str, Any]:
    return {k: v for k, v in card.__dict__.items() if v not in (None, "", (), [])}


def _normalize_name(value: str) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"^\s*жк\s+", "", text)
    return re.sub(r"[\s«»\"'.,-]+", "", text)


def _score_facts(blockers: list[str], search_ok: bool) -> int:
    if not search_ok or any(x in blockers for x in ("invented_fact", "unsupported_claim", "technical_or_internal_leak", "false_inventory_absence", "internal_enum_or_anglicism", "semantic_label_mismatch", "initial_empty_false_absence", "raw_missing_leak", "nonpositive_price_leak", "duplicate_currency", "raw_room_or_area_token")):
        return 0
    if any(x in blockers for x in ("raw_number_leak", "facts_near_mixed", "initial_empty_search_relaxation")):
        return 1
    return 2


def _score_completeness(text: str, cards: tuple[OptionCard, ...], vp: str, base: str | None, blockers: list[str]) -> int:
    if not text or "false_inventory_absence" in blockers:
        return 0
    if not cards:
        return 2 if re.search(r"ослаб|поблиз|оператор|менеджер|Москва|МО", text, re.I) else 1
    needed = _expected_terms(vp, base)
    available = _available_terms(cards, needed)
    if available and not any(term in text.casefold() for term in available):
        return 1
    return 2 if len(cards) <= 3 else 1


def _score_beauty(text: str, cards: tuple[OptionCard, ...], blockers: list[str]) -> int:
    if any(x in blockers for x in ("technical_or_internal_leak", "question_count_not_one", "too_many_cards", "duplicate_answers", "duplicate_intro_summary", "repeated_identical_benefit", "cards_not_separate_blocks", "dry_card_without_presentation_reason", "duplicate_punctuation", "raw_missing_leak")):
        return 0
    if len(text) > 1800 or (len(cards) >= 2 and _rendered_card_count(text) < 2):
        return 1
    if len(cards) >= 2 and "\n\n" not in text:
        return 1
    return 2


def _score_scenario_fit(text: str, cards: tuple[OptionCard, ...], vp: str, base: str | None, blockers: list[str]) -> int:
    if any(x in blockers for x in ("invented_fact", "unsupported_claim", "early_operator", "dry_card_without_presentation_reason", "initial_empty_search_relaxation", "initial_empty_false_absence", "raw_missing_leak")):
        return 0
    lowered = text.casefold()
    terms = _expected_terms(vp, base)
    if cards and terms and not any(term in lowered for term in terms):
        return 1
    if vp == "financing" and base and not any(term in lowered for term in _expected_terms(base, None)):
        return 1
    return 2


def _score_dialogue(text: str, blockers: list[str]) -> int:
    if any(x in blockers for x in ("question_count_not_one", "final_question_not_at_end", "early_operator")):
        return 0
    return 2 if text.endswith("?") else 1


def _expected_terms(vp: str, base: str | None) -> tuple[str, ...]:
    terms = {
        "family": FAMILY_TERMS,
        "investment": INVESTMENT_TERMS,
        "rental": RENTAL_TERMS,
        "life": LIFE_TERMS,
        "financing": FINANCE_TERMS,
    }.get(vp, LIFE_TERMS)
    if vp == "financing" and base:
        return tuple(dict.fromkeys(terms + _expected_terms(base, None)))
    return terms


def _available_terms(cards: tuple[OptionCard, ...], terms: tuple[str, ...]) -> tuple[str, ...]:
    facts = _all_card_text(cards)
    return tuple(term for term in terms if term in facts)


def _route_layer(blockers: list[str], search_ok: bool, search_errors: list[str]) -> str:
    if not search_ok:
        if any("fact_" in err or "params" in err for err in search_errors):
            return "search prompt/contract"
        return "normalization"
    if any(x in blockers for x in ("invented_fact", "unsupported_claim", "technical_or_internal_leak", "too_many_cards", "question_count_not_one", "raw_number_leak", "internal_enum_or_anglicism", "semantic_label_mismatch", "cards_not_separate_blocks", "dry_card_without_presentation_reason", "repeated_identical_benefit", "duplicate_punctuation", "raw_missing_leak", "initial_empty_search_relaxation")):
        return "response renderer"
    if any(x in blockers for x in ("false_inventory_absence", "early_operator", "duplicate_answers", "duplicate_intro_summary", "initial_empty_false_absence")):
        return "state/planner"
    return "none"


def _card_status(cards: tuple[OptionCard, ...], sr: SearchResult) -> str:
    if len(cards) > 3:
        return f"FAIL: {len(cards)} cards"
    if sr.facts:
        return f"OK: facts={len(sr.facts)}"
    if sr.near:
        return f"OK: near={len(sr.near)}"
    return "OK: no-results"


def _request_has_real_constraints(request: V2SearchRequest) -> bool:
    def non_empty(mapping: Mapping[str, Any]) -> bool:
        return any(_value_is_set(value) for value in mapping.values())

    return bool(non_empty(request.requested_hard) or non_empty(request.effective_hard))


def _value_is_set(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _raw_missing_leak(text: str, missing: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    for value in missing:
        raw = str(value or "").strip()
        if len(raw) < 12:
            continue
        raw_low = raw.casefold()
        if re.fullmatch(r"[a-z0-9_.\-/]+", raw_low):
            continue
        if raw_low in lowered:
            return True
    return False
