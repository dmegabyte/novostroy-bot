from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from .card_normalizer import normalize_card, normalize_search_result
from .contracts import OptionCard, SearchResult, to_jsonable
from .search_contract import (
    RESPONSE_VIEWPOINTS,
    V2SearchRequest,
    available_fact_fields,
    build_request_data,
    FACT_FIELD_MAP,
    load_prompt,
    normalize_and_validate_search_output,
    normalize_search_output,
    parse_strict_json,
    validate_search_output,
    matches_hard_constraint,
    lot_matches_hard_constraints,
)
from .fact_context import ALLOWED_FACTS


GatewayCallable = Callable[[dict[str, Any]], Awaitable[tuple[Any, Mapping[str, Any] | None]]]
ENRICHABLE_HARD_FIELDS = {"rooms", "ready", "finishing"}


def normalize_option_name(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[«»\"'.,!?():;]+", " ", text)
    text = re.sub(r"\bжк\b", " ", text)
    return " ".join(text.split())


def build_option_enrichment_request(base: OptionCard, viewpoint: str | None, *, base_viewpoint: str | None = None, facts_needed: tuple[str, ...] | list[str] | None = None, lot_hard: Mapping[str, Any] | None = None) -> V2SearchRequest:
    response_viewpoint = str(viewpoint or "life").strip().lower() or "life"
    if response_viewpoint not in RESPONSE_VIEWPOINTS:
        response_viewpoint = "life"
    canonical_name = str(base.name or "").strip()
    safe_facts = tuple(dict.fromkeys(str(item).strip() for item in (facts_needed or ()) if str(item).strip() in ALLOWED_FACTS))
    requested_fields: list[str] = []
    for fact in safe_facts:
        if fact == "lot_examples":
            requested_fields.extend(FACT_FIELD_MAP[fact])
        else:
            requested_fields.append(fact)
            requested_fields.extend(FACT_FIELD_MAP.get(fact, ()))
    requested_fields = list(dict.fromkeys(requested_fields))
    fact_suffix = f" Нужны только структурированные поля: {', '.join(requested_fields)}." if requested_fields else ""
    if "lot_examples" in safe_facts:
        fact_suffix += " Для примеров квартир верни до двух полных записей именно в массиве ads; не создавай отдельное поле lot_examples и не заменяй ads списком комнатностей."
    safe_lot_hard = _supported_lot_hard(lot_hard)
    if safe_lot_hard:
        rendered_lot_hard = ", ".join(f"{key}={value}" for key, value in safe_lot_hard.items())
        fact_suffix += f" Для ads применяй отдельные lot_hard условия: {rendered_lot_hard}; возвращай только активные/in-sale ads с id и status."
    natural = (
        "Найди полную структурированную карточку ровно для канонического ЖК "
        f"«{canonical_name}». Нужен exact-name lookup: верни только этот ЖК, без похожих вариантов и без расширения условий.{fact_suffix}"
    )
    explicit_terms = ["full_card", "exact_name", canonical_name, *requested_fields]
    extra_fields: list[str] = []
    for fact in safe_facts:
        if fact != "lot_examples":
            extra_fields.append(fact)
        extra_fields.extend(FACT_FIELD_MAP.get(fact, (fact,)))
    fields = available_fact_fields(response_viewpoint, base_viewpoint, extra=extra_fields)
    return V2SearchRequest(
        search_goal={"entity_type": "new_building_flat", "query_summary": natural, "explicit_terms": explicit_terms},
        requested_hard={},
        effective_hard={},
        lot_hard=safe_lot_hard,
        preferences={"format": "full_card"},
        relaxation_audit=[],
        response_viewpoint=response_viewpoint,
        base_viewpoint=base_viewpoint,
        available_fact_fields=fields,
        count=1,
        ignored_preferences=[],
        search_mode="named_object",
        facts_needed=safe_facts,
    )


async def fetch_enriched_option_v2(
    base: OptionCard,
    viewpoint: str | None,
    gateway: GatewayCallable,
    *,
    base_viewpoint: str | None = None,
    timeout: float | None = None,
    model: str | None = None,
    facts_needed: tuple[str, ...] | list[str] | None = None,
    lot_hard: Mapping[str, Any] | None = None,
) -> tuple[OptionCard, dict[str, Any]]:
    request = build_option_enrichment_request(base, viewpoint, base_viewpoint=base_viewpoint, facts_needed=facts_needed, lot_hard=lot_hard)
    enriched, meta = await _fetch_enriched_option_v2_once(
        base,
        request,
        gateway,
        timeout=timeout,
        model=model,
    )
    if meta.get("applied") is True:
        return enriched, meta
    first_skipped = str(meta.get("skipped") or "")
    if first_skipped not in _SELECTED_CORRECTABLE_FAILURES:
        return enriched, meta
    repair_request = build_option_enrichment_repair_request(request, first_skipped)
    repaired, repair_meta = await _fetch_enriched_option_v2_once(
        base,
        repair_request,
        gateway,
        timeout=timeout,
        model=model,
    )
    recovery = {
        "attempted": True,
        "count": 1,
        "classes": [first_skipped],
        "final": str("applied" if repair_meta.get("applied") is True else repair_meta.get("skipped") or "unknown"),
    }
    if repair_meta.get("applied") is True:
        return repaired, {**repair_meta, "recovery": recovery}
    return base, {**repair_meta, "source": "base", "recovery": recovery, "initial_skipped": first_skipped}


_SELECTED_CORRECTABLE_FAILURES = {"parse", "contract"}


def build_option_enrichment_repair_request(request: V2SearchRequest, failure_class: str) -> V2SearchRequest:
    """Return a second exact selected lookup without relaxing object or constraints."""

    goal = dict(request.search_goal or {})
    failure = str(failure_class or "contract").strip().lower()
    if failure not in _SELECTED_CORRECTABLE_FAILURES:
        failure = "contract"
    goal["query_summary"] = (
        str(goal.get("query_summary") or "").strip()
        + " Повтори exact-name lookup с тем же ЖК и теми же условиями: исправь только формат JSON/контракт ответа, не расширяй объект, не добавляй похожие ЖК и не ослабляй lot_hard/effective_hard."
    )[:900]
    goal["explicit_terms"] = _unique_terms(["selected_exact_repair", f"repair:{failure}", *(goal.get("explicit_terms") or ())])
    audit = list(request.relaxation_audit or [])
    audit.append({"type": "selected_exact_repair", "failure_class": failure, "constraints_preserved": True})
    return replace(request, search_goal=goal, relaxation_audit=audit)


async def _fetch_enriched_option_v2_once(
    base: OptionCard,
    request: V2SearchRequest,
    gateway: GatewayCallable,
    *,
    timeout: float | None = None,
    model: str | None = None,
) -> tuple[OptionCard, dict[str, Any]]:
    request_data = build_request_data(request, prompt=load_prompt(), model=model or "google/gemini-3.1-flash-lite-preview")
    try:
        if timeout is not None:
            raw, meta = await asyncio.wait_for(gateway(request_data), timeout=max(0.001, float(timeout)))
        else:
            raw, meta = await gateway(request_data)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return base, {"applied": False, "source": "base", "skipped": "timeout"}
    except Exception as exc:  # safe metadata only
        return base, {"applied": False, "source": "base", "skipped": exc.__class__.__name__}
    if isinstance(meta, Mapping) and (meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False):
        return base, {"applied": False, "source": "base", "skipped": "provider"}
    parsed, parse_errors = parse_strict_json(str(raw or ""))
    if parsed is None:
        return base, {"applied": False, "source": "base", "skipped": "parse", "errors": _safe_errors(parse_errors)}
    normalized, validation = normalize_and_validate_search_output(parsed, request)
    if not validation.get("ok"):
        if _only_lot_hard_violations(validation.get("errors")):
            return base, {"applied": False, "source": "base", "skipped": "empty_result", "empty_reason": "lot_hard_no_match"}
        return base, {"applied": False, "source": "base", "skipped": "contract", "errors": _safe_errors(validation.get("errors"))}
    result = normalize_search_result(normalized)
    candidates = result.shortlist(1)
    if not candidates:
        return base, {"applied": False, "source": "base", "skipped": "empty_result"}
    candidate = candidates[0]
    if normalize_option_name(candidate.name) != normalize_option_name(base.name):
        return base, {"applied": False, "source": "base", "skipped": "identity_mismatch"}
    candidate = _filter_option_lot_examples(candidate, request.lot_hard)
    enriched = merge_option_cards(base, candidate)
    if enriched == base:
        return base, {"applied": False, "source": "base", "skipped": "empty_enrichment"}
    return enriched, {"applied": True, "source": "v2_search_enrichment"}


def _only_lot_hard_violations(errors: Any) -> bool:
    if not isinstance(errors, list) or not errors:
        return False
    return all(re.fullmatch(r"fact_\d+_violates_lot_hard:[a-z_]+", str(error or "")) for error in errors)


async def enrich_search_result_top_options(
    result: SearchResult,
    viewpoint: str | None,
    gateway: GatewayCallable,
    *,
    base_viewpoint: str | None = None,
    max_options: int = 3,
    timeout: float | None = None,
    facts_needed: tuple[str, ...] | list[str] | None = None,
    lot_hard: Mapping[str, Any] | None = None,
) -> tuple[SearchResult, dict[str, Any]]:
    # Enrich visible cards from both source containers without promoting
    # ``near`` alternatives into exact ``facts``.
    fact_count = min(len(result.facts), max_options)
    near_count = max(0, max_options - fact_count)
    cards = tuple(result.facts[:fact_count]) + tuple(result.near[:near_count])
    if not cards:
        return result, {"enabled": False, "applied": False, "count": 0, "items": []}
    tasks = [
        asyncio.create_task(
            fetch_enriched_option_v2(
                card,
                viewpoint,
                gateway,
                base_viewpoint=base_viewpoint,
                timeout=timeout,
                facts_needed=facts_needed,
                lot_hard=lot_hard,
            )
        )
        for card in cards
    ]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    enriched_cards: list[OptionCard] = list(cards)
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(gathered):
        if isinstance(item, Exception):
            items.append({"idx": idx + 1, "applied": False, "skipped": item.__class__.__name__})
            continue
        enriched, meta = item
        enriched_cards[idx] = enriched
        items.append({"idx": idx + 1, **_public_meta(meta)})
    applied = sum(1 for item in items if item.get("applied"))
    enriched_facts = tuple(enriched_cards[:fact_count])
    enriched_near = tuple(enriched_cards[fact_count:])
    final_facts = enriched_facts + tuple(result.facts[fact_count:])
    final_near = enriched_near + tuple(result.near[near_count:])
    visible_cards = final_facts[:fact_count] + final_near[:near_count]
    enriched_result = SearchResult(
        facts=final_facts,
        near=final_near,
        missing=_reconcile_missing(result.missing, visible_cards),
        params=result.params,
        summary=result.summary,
    )
    return enriched_result, {"enabled": True, "applied": bool(applied), "count": len(cards), "applied_count": applied, "items": items}


async def validate_with_bounded_enrichment(
    normalized_output: Mapping[str, Any],
    request: V2SearchRequest,
    gateway: GatewayCallable,
    *,
    max_options: int = 3,
    timeout: float | None = None,
) -> tuple[SearchResult | None, dict[str, Any], dict[str, Any]]:
    """Offline quality helper: проверить и восстановить пробелы в evidence.

    Боевой first-list path эту функцию не вызывает; в production остаётся только
    точечное enrichment выбранной пользователем карточки.
    """
    initial = validate_search_output(dict(normalized_output), request)
    initial_errors = list(initial.get("errors") or [])
    recoverable = bool(initial_errors) and all(_enrichable_hard_error(error) for error in initial_errors)
    if initial_errors and not recoverable:
        return None, initial, {"enabled": False, "applied": False, "reason": "non_enrichable_contract_error"}

    if recoverable:
        return await _recover_broad_candidates_with_structured_evidence(
            normalized_output,
            request,
            gateway,
            initial,
            max_options=max_options,
            timeout=timeout,
        )

    provisional = normalize_search_result(normalized_output)
    enriched, meta = await enrich_search_result_top_options(
        provisional,
        request.response_viewpoint,
        gateway,
        base_viewpoint=request.base_viewpoint,
        max_options=max_options,
        timeout=timeout,
    )
    enriched = _preserve_validated_hard_evidence(provisional, enriched, request)
    final = _validate_canonical_hard_constraints(enriched, request)
    meta = {**meta, "trigger": "hard_evidence_gap" if recoverable else "card_quality"}
    if not final.get("ok"):
        meta["post_validation_evidence"] = [
            {
                "idx": idx + 1,
                "rooms": card.rooms,
                "room_formats": list(card.room_formats),
                "ready": card.ready,
                "finishing": card.finishing,
            }
            for idx, card in enumerate(enriched.facts[:3])
        ]
    return (enriched if final.get("ok") else None), final, meta


def _preserve_validated_hard_evidence(base: SearchResult, enriched: SearchResult, request: V2SearchRequest) -> SearchResult:
    """Keep evidence fields that already passed the original hard contract.

    Exact-card enrichment may add detail, but it must not invalidate a field
    already proven by the broad result. Recovery candidates do not use this
    helper before original-hard validation, so missing evidence can still be
    filled there.
    """
    hard = set(request.effective_hard or {})
    field_map = {
        "rooms": ("rooms", "room_formats"),
        "ready": ("ready",),
        "finishing": ("finishing",),
        "district": ("district",),
        "location": ("location",),
        "max_price": ("price", "price_min"),
        "min_price": ("price", "price_min"),
    }
    protected = tuple(dict.fromkeys(field for key in hard for field in field_map.get(key, (key,))))
    if not protected:
        return enriched
    cards: list[OptionCard] = []
    for idx, card in enumerate(enriched.facts):
        if idx >= len(base.facts):
            cards.append(card)
            continue
        original = to_jsonable(base.facts[idx])
        merged = to_jsonable(card)
        for field in protected:
            value = original.get(field)
            if not _value_missing(value):
                merged[field] = value
        cards.append(OptionCard.from_dict(merged))
    return SearchResult(
        facts=tuple(cards),
        near=enriched.near,
        missing=enriched.missing,
        params=enriched.params,
        summary=enriched.summary,
    )


async def _recover_broad_candidates_with_structured_evidence(
    original_output: Mapping[str, Any],
    request: V2SearchRequest,
    gateway: GatewayCallable,
    initial_validation: Mapping[str, Any],
    *,
    max_options: int,
    timeout: float | None,
) -> tuple[SearchResult | None, dict[str, Any], dict[str, Any]]:
    failed_fields = _recoverable_failed_fields(initial_validation.get("errors"))
    if not failed_fields:
        return None, dict(initial_validation), {"enabled": False, "applied": False, "reason": "no_recoverable_fields"}
    recovery_limit = 5
    recovery_request = build_recovery_search_request(request, failed_fields, count=recovery_limit)
    request_data = build_request_data(recovery_request, prompt=load_prompt())
    try:
        if timeout is not None:
            raw, meta = await asyncio.wait_for(gateway(request_data), timeout=max(0.001, float(timeout)))
        else:
            raw, meta = await gateway(request_data)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        validation = {"ok": False, "errors": ["recovery_gateway_timeout"], "counts": dict(initial_validation.get("counts") or {})}
        return None, validation, {"enabled": True, "applied": False, "trigger": "hard_evidence_gap", "skipped": "timeout", "fields": sorted(failed_fields)}
    except Exception as exc:  # safe metadata only
        validation = {"ok": False, "errors": ["recovery_gateway_failed:" + exc.__class__.__name__], "counts": dict(initial_validation.get("counts") or {})}
        return None, validation, {"enabled": True, "applied": False, "trigger": "hard_evidence_gap", "skipped": exc.__class__.__name__, "fields": sorted(failed_fields)}
    if isinstance(meta, Mapping) and (meta.get("_safe_fallback") or meta.get("_upstream_error") or meta.get("ok") is False):
        validation = {"ok": False, "errors": ["recovery_gateway_not_ok"], "counts": dict(initial_validation.get("counts") or {})}
        return None, validation, {"enabled": True, "applied": False, "trigger": "hard_evidence_gap", "skipped": "provider", "fields": sorted(failed_fields)}
    parsed, parse_errors = parse_strict_json(str(raw or ""))
    if parsed is None:
        validation = {"ok": False, "errors": _safe_errors(parse_errors), "counts": dict(initial_validation.get("counts") or {})}
        return None, validation, {"enabled": True, "applied": False, "trigger": "hard_evidence_gap", "skipped": "parse", "fields": sorted(failed_fields)}
    normalized, recovery_validation = normalize_and_validate_search_output(parsed, recovery_request)
    if not recovery_validation.get("ok"):
        return None, recovery_validation, {"enabled": True, "applied": False, "trigger": "hard_evidence_gap", "skipped": "contract", "fields": sorted(failed_fields)}

    broad = _normalize_search_result_limit(normalized, limit=recovery_limit)
    enriched, enrich_meta = await enrich_search_result_top_options(
        broad,
        request.response_viewpoint,
        gateway,
        base_viewpoint=request.base_viewpoint,
        max_options=recovery_limit,
        timeout=timeout,
    )
    confirmed = tuple(card for card in enriched.facts if _card_matches_all_original_hard(card, request))
    original_missing = list(normalize_search_result(original_output).missing)
    for field in sorted(failed_fields):
        if field not in original_missing:
            original_missing.append(field)
    final_result = SearchResult(
        facts=confirmed,
        near=(),
        missing=tuple(original_missing),
        params=_original_params(original_output, request),
        summary=enriched.summary,
    )
    final = _validate_canonical_hard_constraints(final_result, request)
    final["ok"] = True
    meta_out = {
        **enrich_meta,
        "enabled": True,
        "trigger": "hard_evidence_gap",
        "recovery": True,
        "fields": sorted(failed_fields),
        "confirmed_count": len(confirmed),
    }
    return final_result, final, meta_out


def build_recovery_search_request(request: V2SearchRequest, failed_fields: set[str], *, count: int = 5) -> V2SearchRequest:
    removed = set(failed_fields) & ENRICHABLE_HARD_FIELDS
    effective_hard = {key: value for key, value in dict(request.effective_hard or {}).items() if key not in removed}
    # requested_hard is an audit field in intent, but the current contract also
    # uses it for hard evidence requirements. Keeping a failed recoverable field
    # there would make broad candidate collection impossible, so remove only the
    # same recoverable fields when needed and record the original in audit.
    requested_hard = {key: value for key, value in dict(request.requested_hard or {}).items() if key not in removed}
    retained_terms = _recovery_search_terms(effective_hard)
    viewpoint = str(request.response_viewpoint or "life")
    goal = {
        "entity_type": (request.search_goal or {}).get("entity_type") or "new_building_flat",
        "query_summary": (
            "Broad candidate search for new building flats. "
            f"Viewpoint: {viewpoint}. Apply only retained hard constraints: "
            f"{', '.join(retained_terms) if retained_terms else 'none'}. "
            "Collect structured evidence for later local validation. "
            "This is internal evidence recovery, not client relaxation."
        )[:300],
        "explicit_terms": _unique_terms(["broad_candidate_collection", viewpoint, *retained_terms]),
    }
    audit = list(request.relaxation_audit or [])
    audit.append({"type": "internal_recovery", "removed_effective_hard": sorted(removed), "original_requested_hard": dict(request.requested_hard or {})})
    return V2SearchRequest(
        search_goal=goal,
        requested_hard=requested_hard,
        effective_hard=effective_hard,
        preferences=dict(request.preferences or {}),
        relaxation_audit=audit,
        response_viewpoint=request.response_viewpoint,
        base_viewpoint=request.base_viewpoint,
        available_fact_fields=list(request.available_fact_fields),
        count=min(5, max(1, int(count or 5))),
        ignored_preferences=list(request.ignored_preferences or []),
    )


def _recovery_search_terms(hard: Mapping[str, Any]) -> list[str]:
    """Render only constraints that remain active in the broad recovery query."""
    terms: list[str] = []
    for key in sorted(hard):
        value = hard.get(key)
        if value in (None, "", [], {}, ()):
            continue
        if isinstance(value, (list, tuple, set)):
            rendered = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            rendered = str(value).strip()
        if rendered:
            terms.append(f"{key}={rendered}")
    return terms


def merge_option_cards(base: OptionCard, enriched: OptionCard) -> OptionCard:
    merged = dict(to_jsonable(base))
    enriched_data = to_jsonable(enriched)
    base_has_price = not _value_missing(merged.get("price")) or not _value_missing(merged.get("price_min"))
    for key in OptionCard.__dataclass_fields__:
        if key in {"name", "is_near"}:
            continue
        if key in {"district", "location"} and not _value_missing(merged.get(key)):
            continue
        if key in {"price", "price_min"} and base_has_price:
            continue
        value = enriched_data.get(key)
        if _value_missing(value):
            continue
        merged[key] = value
    merged["name"] = base.name
    merged["is_near"] = base.is_near
    return OptionCard.from_dict(merged)


def _supported_lot_hard(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    supported = {"rooms", "max_price", "min_price", "area_min_m2", "area_max_m2", "ready", "finishing"}
    return {
        key: item
        for key, item in value.items()
        if key in supported and item not in (None, "", [], {}, ())
    }


def _filter_option_lot_examples(card: OptionCard, lot_hard: Mapping[str, Any] | None) -> OptionCard:
    safe_lot_hard = _supported_lot_hard(lot_hard)
    if not safe_lot_hard:
        return card
    lots = [
        lot for lot in card.lot_examples
        if lot_matches_hard_constraints({"ads": [to_jsonable(lot)]}, safe_lot_hard)
    ]
    data = dict(to_jsonable(card))
    data["lot_examples"] = [to_jsonable(lot) for lot in lots[:2]]
    return OptionCard.from_dict(data)


def _reconcile_missing(missing: tuple[str, ...], cards: tuple[OptionCard, ...]) -> tuple[str, ...]:
    """Remove a missing category only when every displayed card proves it."""
    if not cards:
        return tuple(missing)
    evidence = {
        "ads": lambda card: card.ads_count is not None,
        "sales": lambda card: card.sales_count is not None,
        "finance": lambda card: bool(card.discount),
        "readiness": lambda card: bool(card.ready),
        "finishing": lambda card: bool(card.finishing),
        "rooms": lambda card: bool(card.room_formats or card.rooms is not None),
        "location": lambda card: bool(card.location or card.district),
        "budget": lambda card: bool(card.price or card.price_min is not None),
        "family_infrastructure": lambda card: any(any(token in value.casefold() for token in ("школ", "сад", "дет")) for value in card.infrastructure),
        "walk_infrastructure": lambda card: any(any(token in value.casefold() for token in ("парк", "вод", "спорт", "прогул")) for value in card.infrastructure),
        "safety_infrastructure": lambda card: any(any(token in value.casefold() for token in ("охран", "безопас", "двор без машин")) for value in card.infrastructure),
    }
    remaining: list[str] = []
    for category in missing:
        check = evidence.get(str(category))
        if check is not None and all(check(card) for card in cards):
            continue
        remaining.append(str(category))
    return tuple(dict.fromkeys(remaining))


def search_result_to_output(result: SearchResult) -> dict[str, Any]:
    return {"facts": [to_jsonable(card) for card in result.facts], "near": [to_jsonable(card) for card in result.near], "missing": list(result.missing), "params": dict(result.params)}


def _normalize_search_result_limit(output: Mapping[str, Any], *, limit: int) -> SearchResult:
    source = output if isinstance(output, Mapping) else {}
    facts = source.get("facts") if isinstance(source.get("facts"), list) else []
    near = source.get("near") if isinstance(source.get("near"), list) else []
    return SearchResult(
        facts=tuple(normalize_card(item) for item in facts[:limit] if isinstance(item, Mapping)),
        near=tuple(normalize_card(item, is_near=True) for item in near[:limit] if isinstance(item, Mapping)),
        missing=normalize_search_result(source).missing,
        params=dict(source.get("params") if isinstance(source.get("params"), Mapping) else {}),
        summary=str(source.get("summary")) if source.get("summary") else None,
    )


def _value_missing(value: Any) -> bool:
    if value in (None, "", (), [], {}):
        return True
    text = str(value).strip().casefold()
    return text in {"нет", "none", "null", "не указан", "не указано", "информация отсутствует", "уточняется"} or "не указан" in text or "отсутств" in text or "уточн" in text


def _safe_errors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [re.sub(r"(?i)(token|secret|password|raw|payload|task_id)[^,;\s]*", "[redacted]", str(item))[:120] for item in value[:3]]


def _enrichable_hard_error(error: Any) -> bool:
    match = re.fullmatch(r"fact_\d+_(?:violates_hard|missing_hard_evidence):([a-z_]+)", str(error or ""))
    return bool(match and match.group(1) in ENRICHABLE_HARD_FIELDS)


def _recoverable_failed_fields(errors: Any) -> set[str]:
    fields: set[str] = set()
    for error in errors if isinstance(errors, list) else []:
        match = re.fullmatch(r"fact_\d+_(?:violates_hard|missing_hard_evidence):([a-z_]+)", str(error or ""))
        if match and match.group(1) in ENRICHABLE_HARD_FIELDS:
            fields.add(match.group(1))
    return fields


def _card_matches_all_original_hard(card: OptionCard, request: V2SearchRequest) -> bool:
    item = dict(to_jsonable(card))
    return all(matches_hard_constraint(item, str(field), expected) for field, expected in dict(request.effective_hard or {}).items())


def _original_params(output: Mapping[str, Any], request: V2SearchRequest) -> dict[str, Any]:
    params = output.get("params") if isinstance(output, Mapping) and isinstance(output.get("params"), Mapping) else {}
    hard_keys = set(request.effective_hard or {})
    return {key: value for key, value in dict(params).items() if key in hard_keys} or dict(request.effective_hard or {})


def _unique_terms(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text[:80])
            seen.add(text)
    return out[:12]


def _validate_canonical_hard_constraints(result: SearchResult, request: V2SearchRequest) -> dict[str, Any]:
    errors: list[str] = []
    hard = dict(request.effective_hard or {})
    for idx, card in enumerate(result.facts):
        item = dict(to_jsonable(card))
        for field, expected in hard.items():
            if not matches_hard_constraint(item, str(field), expected):
                errors.append(f"fact_{idx}_violates_hard:{field}")
    return {
        "ok": not errors,
        "errors": errors,
        "counts": {"facts": len(result.facts), "near": len(result.near), "missing": len(result.missing)},
    }


def _public_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dict(meta).items() if k in {"applied", "source", "skipped"}}
