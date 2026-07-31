from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Awaitable, Callable, Mapping

from .contracts import V0Answer


ALLOWED_MODES = {"off", "shadow", "publish"}
MAX_CANDIDATE_CHARS = 1800
MAX_ASSIGNMENT_TEXT_CHARS = 2000
SEARCH_MISS_RE = re.compile(r"(не\s+(наш[её]л|нашла|вижу|подобрал[аи]?|удалось\s+найти)|ничего\s+не\s+наш)", re.IGNORECASE)
CARD_NAME_RE = re.compile(r"\b(?:ЖК|МФК|Апарт-отель)\s+[«\"']?([^—:\n,.!?]+)", re.IGNORECASE)


@dataclass(frozen=True)
class FixedOutput:
    intro: str
    card_lines: tuple[str, ...]
    recommendation: str
    missing_note: str
    final_question: str
    deterministic_text: str
    option_line_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class WriterValidation:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WriterCandidate:
    text: str
    validation: WriterValidation


WriterPort = Callable[[dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_card_ref(value: Any) -> str:
    text = _compact_text(value).casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'`.,:;!?()\[\]{}]", " ", text)
    text = re.sub(r"\b(жк|мфк|апарт\s*отель)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _card_refs_from_line(line: str) -> tuple[str, ...]:
    refs: list[str] = []
    compact = _compact_text(line)
    for match in CARD_NAME_RE.finditer(compact):
        ref = _normalize_card_ref(match.group(1))
        if ref:
            refs.append(ref)
    before_dash = re.split(r"\s+[—-]\s+|:", compact, maxsplit=1)[0]
    before_dash = re.sub(r"^\s*\d+[.)]?\s*", "", before_dash)
    ref = _normalize_card_ref(before_dash)
    if ref:
        refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _card_identity_refs_from_line(line: str) -> tuple[str, ...]:
    refs: list[str] = []
    compact = _compact_text(line)
    for match in CARD_NAME_RE.finditer(compact):
        ref = _normalize_card_ref(match.group(1))
        if ref:
            refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _line_matches_selected(line: str, selected_option_name: str) -> bool:
    selected = _normalize_card_ref(selected_option_name)
    if not selected:
        return False
    line_norm = _normalize_card_ref(line)
    if selected and selected in line_norm:
        return True
    return selected in _card_refs_from_line(line)


def _candidate_mentions_ref(text: str, ref: str) -> bool:
    if not ref:
        return False
    return ref in _normalize_card_ref(text)


def _card_lines_in_order(text: str, lines: tuple[str, ...]) -> tuple[bool, bool]:
    """Return (all_present, in_order) for exact material card line retention."""
    position = 0
    all_present = True
    in_order = True
    for line in lines:
        anywhere = text.find(line)
        if anywhere < 0:
            all_present = False
            in_order = False
            continue
        ordered = text.find(line, position)
        if ordered < 0:
            in_order = False
            continue
        position = ordered + len(line)
    return all_present, in_order


def normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in ALLOWED_MODES else "off"


def fixed_output_from_answer(answer: V0Answer, deterministic_text: str | None = None) -> FixedOutput:
    card_lines: list[str] = []
    option_line_groups: list[tuple[str, ...]] = []
    for option in answer.options:
        lines = option.get("lines", ()) if isinstance(option, Mapping) else ()
        option_lines: list[str] = []
        for line in lines:
            text = str(line).rstrip()
            if text.strip():
                card_lines.append(text)
                option_lines.append(text)
        if option_lines:
            option_line_groups.append(tuple(option_lines))
    return FixedOutput(
        intro=str(answer.intro or "").strip(),
        card_lines=tuple(card_lines),
        recommendation=str(answer.recommendation or "").strip(),
        missing_note=str(answer.missing_note or "").strip(),
        final_question=str(answer.final_question or "").strip(),
        deterministic_text=str(deterministic_text or answer.text()),
        option_line_groups=tuple(option_line_groups),
    )


def normalize_fixed_output_for_response_job(
    fixed_output: FixedOutput,
    response_job: Mapping[str, Any],
    *,
    selected_option_name: str | None = None,
) -> tuple[FixedOutput, tuple[str, ...]]:
    """Apply code-owned V0 Answer Writer material scope before provider input."""
    scope = str(response_job.get("scope") or "").strip()
    answer_kind = str(response_job.get("answer_kind") or "").strip()
    lines = tuple(str(line).rstrip() for line in fixed_output.card_lines if str(line).strip())
    option_line_groups = tuple(
        tuple(str(line).rstrip() for line in group if str(line).strip())
        for group in fixed_output.option_line_groups
    )
    option_line_groups = tuple(group for group in option_line_groups if group)
    if not option_line_groups:
        option_line_groups = tuple((line,) for line in lines)
    errors: list[str] = []
    filtered = lines

    if scope == "no_cards":
        filtered = ()
    elif scope == "one_card":
        selected = str(selected_option_name or response_job.get("selected_option_name") or response_job.get("selected_object") or "").strip()
        matching_groups = tuple(group for group in option_line_groups if any(_line_matches_selected(line, selected) for line in group))
        if selected and len(matching_groups) == 1:
            filtered = tuple(line for line in matching_groups[0] if line in lines)
        else:
            filtered = ()
            errors.append("one_card_selection_failed_closed")
    elif scope == "shortlist" and answer_kind == "search_many":
        filtered = lines[:3]

    return (
        FixedOutput(
            intro=fixed_output.intro,
            card_lines=filtered,
            recommendation=fixed_output.recommendation,
            missing_note=fixed_output.missing_note,
            final_question=fixed_output.final_question,
            deterministic_text=fixed_output.deterministic_text,
            option_line_groups=(filtered,) if filtered else (),
        ),
        tuple(errors),
    )


def build_assignment(
    *,
    client_message: str,
    previous_assistant_message: str | None,
    response_job: Mapping[str, Any],
    fixed_output: FixedOutput,
    canonical_names: tuple[str, ...] = (),
    operator_allowed: bool = False,
) -> dict[str, Any]:
    del canonical_names, operator_allowed
    return {
        "client_message": str(client_message or "")[:MAX_ASSIGNMENT_TEXT_CHARS],
        "previous_assistant_message": str(previous_assistant_message or "")[:MAX_ASSIGNMENT_TEXT_CHARS],
        "response_job": dict(response_job),
        "material": {
            "intro": fixed_output.intro,
            "card_lines": list(fixed_output.card_lines),
            "recommendation": fixed_output.recommendation,
            "missing_note": fixed_output.missing_note,
            "final_question": fixed_output.final_question,
        },
    }


def validate_candidate_against_assignment(
    candidate_text: str,
    assignment: Mapping[str, Any],
    *,
    original_card_lines: tuple[str, ...] = (),
) -> WriterValidation:
    errors: list[str] = []
    text = str(candidate_text or "").strip()
    if not text:
        return WriterValidation(False, ("empty_candidate",))
    response_job = assignment.get("response_job") if isinstance(assignment.get("response_job"), Mapping) else {}
    material = assignment.get("material") if isinstance(assignment.get("material"), Mapping) else {}
    scope = str(response_job.get("scope") or "").strip()
    answer_kind = str(response_job.get("answer_kind") or "").strip()
    allowed_lines = tuple(str(line) for line in material.get("card_lines", []) if str(line).strip()) if isinstance(material.get("card_lines", []), list) else ()
    allowed_refs = {ref for line in allowed_lines for ref in _card_refs_from_line(line)}
    allowed_card_identities = {ref for line in allowed_lines for ref in _card_identity_refs_from_line(line)}
    original_refs = {ref for line in original_card_lines for ref in _card_refs_from_line(str(line))}
    forbidden_refs = original_refs - allowed_refs
    if scope == "no_cards":
        forbidden_refs |= allowed_refs
    for ref in sorted(forbidden_refs):
        if _candidate_mentions_ref(text, ref):
            errors.append("candidate_mentions_disallowed_card")
            break
    if (scope == "one_card" or (scope == "shortlist" and answer_kind == "search_many")) and allowed_lines:
        all_card_lines_present, card_lines_in_order = _card_lines_in_order(text, allowed_lines)
        if not all_card_lines_present:
            errors.append("candidate_omits_required_card_line")
        elif not card_lines_in_order:
            errors.append("candidate_reorders_required_card_lines")
    if scope == "one_card" and len(allowed_card_identities) > 1:
        errors.append("one_card_material_has_multiple_cards")
    if scope == "shortlist" and answer_kind == "search_many" and len(allowed_lines) > 3:
        errors.append("shortlist_material_too_many_cards")
    question_count = text.count("?")
    if question_count > 1:
        errors.append("candidate_too_many_questions")
    final_question = str(material.get("final_question") or "").strip()
    if final_question:
        if text.count(final_question) > 1:
            errors.append("candidate_repeats_cta")
        if question_count == 1 and not text.endswith(final_question):
            errors.append("candidate_cta_mismatch")
    elif question_count:
        errors.append("candidate_adds_cta")
    if answer_kind == "off_topic" and scope == "no_cards" and SEARCH_MISS_RE.search(text):
        errors.append("unsearched_no_cards_search_claim")
    return WriterValidation(not errors, tuple(dict.fromkeys(errors)))


def candidate_from_raw(raw: Any) -> WriterCandidate:
    text = str(raw or "").strip()
    if not text:
        return WriterCandidate(text="", validation=WriterValidation(False, ("empty_candidate",)))
    if len(text) > MAX_CANDIDATE_CHARS:
        return WriterCandidate(text=text, validation=WriterValidation(False, ("candidate_too_long",)))
    return WriterCandidate(text=text, validation=WriterValidation(True, ()))


def safe_candidate_text(text: str) -> str:
    value = str(text or "")[:MAX_CANDIDATE_CHARS]
    return value if value else ""
