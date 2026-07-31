from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ExecutableTurn, IntentGoal, OptionCard
from .fact_context import ALLOWED_FACTS
from .search_enrichment import GatewayCallable, fetch_enriched_option_v2, merge_option_cards
from .state import ConversationState, EnrichedCardCacheEntry, enriched_cache_entry_is_fresh, enriched_card_identity


JsonDict = dict[str, Any]


@dataclass(frozen=True)
class PairComparisonExecution:
    """Isolated pair-comparison executor output.

    This is intentionally not part of ``ExecutionResult`` yet.  Runtime,
    response-plan and presenter projection are later phases.
    """

    ordered_cards: tuple[OptionCard, ...]
    cache_additions: tuple[EnrichedCardCacheEntry, ...]
    metadata: JsonDict
    attempts: tuple[JsonDict, ...]
    error_status: str | None = None


async def execute_pair_comparison(
    turn: ExecutableTurn,
    state: ConversationState,
    gateway: GatewayCallable,
    *,
    viewpoint: str | None = None,
    facts_needed: tuple[str, ...] | list[str] | None = None,
    timeout: float | None = None,
    model: str | None = None,
    clock: Callable[[], datetime] | None = None,
    cache_ttl_seconds: int = 900,
    base_viewpoint: str | None = None,
) -> PairComparisonExecution:
    """Resolve and enrich exactly the declared visible pair, fail-closed.

    The function performs no fuzzy matching, no broad search, no state mutation,
    no lot-level auto-expansion and no presenter/operator behavior.
    """

    requested_facts = _bounded_facts(facts_needed if facts_needed is not None else (*turn.facts_needed, *turn.requested_facts))
    scenario = _scenario(viewpoint if viewpoint is not None else turn.viewpoint)

    valid, cards_or_empty, error = _resolve_declared_visible_pair(turn, state)
    if not valid:
        return _safe_closed(error or "invalid_pair", requested_facts=requested_facts, scenario=scenario)

    base_cards = cards_or_empty
    now = _now(clock)
    ordered_cards: list[OptionCard] = list(base_cards)
    attempts: list[JsonDict] = []
    misses: list[tuple[int, OptionCard]] = []

    for idx, base in enumerate(base_cards):
        cached = _fresh_sufficient_cache_entry(
            base,
            state.enriched_card_cache,
            scenario=scenario,
            requested_facts=requested_facts,
            now=now,
            ttl_seconds=cache_ttl_seconds,
        )
        if cached is None:
            misses.append((idx, base))
            attempts.append({"idx": idx + 1, "source": "fetch", "status": "pending", "applied": False})
            continue
        ordered_cards[idx] = merge_option_cards(base, cached.card)
        attempts.append({"idx": idx + 1, "source": "state_cache", "status": "hit", "applied": True})

    additions: list[EnrichedCardCacheEntry] = []
    if misses:
        tasks = [
            asyncio.create_task(
                fetch_enriched_option_v2(
                    base,
                    scenario,
                    gateway,
                    base_viewpoint=base_viewpoint,
                    timeout=timeout,
                    model=model,
                    facts_needed=requested_facts,
                    lot_hard={},
                )
            )
            for _, base in misses
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for (idx, base), item in zip(misses, gathered):
            if isinstance(item, BaseException):
                status = _exception_status(item)
                attempts[idx] = {"idx": idx + 1, "source": "base", "status": status, "applied": False}
                ordered_cards[idx] = base
                continue
            enriched, raw_meta = item
            safe = _safe_fetch_meta(raw_meta)
            applied = safe.get("status") == "applied"
            ordered_cards[idx] = enriched if applied else base
            attempts[idx] = {"idx": idx + 1, "source": "fetch" if applied else "base", **safe}
            if applied:
                additions.append(
                    EnrichedCardCacheEntry(
                        identity=enriched_card_identity(base),
                        name=base.name,
                        card=enriched,
                        scenario=scenario,
                        loaded_facts=requested_facts,
                        fetched_at=now.isoformat(),
                    )
                )

    applied_count = sum(1 for attempt in attempts if attempt.get("applied") is True)
    failure_count = sum(1 for attempt in attempts if attempt.get("applied") is not True)
    error_status = None
    if failure_count == 1:
        error_status = "partial_enrichment_failed"
    elif failure_count == 2:
        error_status = "all_enrichment_failed"
    metadata = {
        "status": "ok" if error_status is None else error_status,
        "requested_count": 2,
        "resolved_count": len(ordered_cards),
        "cache_hit_count": sum(1 for attempt in attempts if attempt.get("source") == "state_cache"),
        "fetch_count": len(misses),
        "applied_count": applied_count,
        "failure_count": failure_count,
        "requested_fact_count": len(requested_facts),
    }
    return PairComparisonExecution(
        ordered_cards=tuple(ordered_cards),
        cache_additions=tuple(additions),
        metadata=metadata,
        attempts=tuple(attempts),
        error_status=error_status,
    )


def _resolve_declared_visible_pair(turn: ExecutableTurn, state: ConversationState) -> tuple[bool, tuple[OptionCard, ...], str | None]:
    if turn.goal != IntentGoal.COMPARE_CURRENT:
        return False, (), "wrong_goal"
    names = tuple(str(name).strip() for name in (turn.comparison_option_names or ()) if str(name).strip())
    if len(names) != 2:
        return False, (), "pair_missing"
    if names[0] == names[1]:
        return False, (), "duplicate_pair_names"
    visible = tuple(state.visible_options or (state.last_search.shortlist(3) if state.last_search else ()))
    if not visible:
        return False, (), "no_visible_options"
    cards: list[OptionCard] = []
    for name in names:
        exact_matches = [card for card in visible if card.name == name]
        if len(exact_matches) != 1:
            return False, (), "name_not_exact_visible" if not exact_matches else "duplicate_visible_name"
        cards.append(exact_matches[0])
    return True, tuple(cards), None


def _fresh_sufficient_cache_entry(
    base: OptionCard,
    entries: tuple[EnrichedCardCacheEntry, ...],
    *,
    scenario: str,
    requested_facts: tuple[str, ...],
    now: datetime,
    ttl_seconds: int,
) -> EnrichedCardCacheEntry | None:
    identity = enriched_card_identity(base)
    needed = set(requested_facts)
    for entry in entries:
        if entry.identity != identity:
            continue
        if str(entry.scenario or "").strip().lower() != scenario:
            continue
        if needed and not needed.issubset({str(item).strip().lower() for item in entry.loaded_facts}):
            continue
        if not enriched_cache_entry_is_fresh(entry, now=now, ttl_seconds=ttl_seconds):
            continue
        return entry
    return None


def _safe_closed(error_status: str, *, requested_facts: tuple[str, ...], scenario: str) -> PairComparisonExecution:
    return PairComparisonExecution(
        ordered_cards=(),
        cache_additions=(),
        metadata={
            "status": "failed_closed",
            "requested_count": 0,
            "resolved_count": 0,
            "cache_hit_count": 0,
            "fetch_count": 0,
            "applied_count": 0,
            "failure_count": 0,
            "requested_fact_count": len(requested_facts),
            "scenario_class": "known" if scenario else "default",
        },
        attempts=(),
        error_status=error_status,
    )


def _bounded_facts(value: Any) -> tuple[str, ...]:
    raw = value if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else [value])
    out: list[str] = []
    for item in raw:
        fact = str(item or "").strip().lower()
        if fact in ALLOWED_FACTS and fact not in out:
            out.append(fact)
    return tuple(out)


def _scenario(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text or text == "unchanged":
        return "life"
    return text


def _now(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _safe_fetch_meta(meta: Mapping[str, Any] | None) -> JsonDict:
    source = meta if isinstance(meta, Mapping) else {}
    if source.get("applied") is True:
        return {"status": "applied", "applied": True}
    skipped = str(source.get("skipped") or "unknown").strip().lower()
    if skipped not in {"timeout", "provider", "parse", "contract", "empty_result", "identity_mismatch", "empty_enrichment", "unknown"}:
        skipped = "technical_error"
    return {"status": skipped, "applied": False}


def _exception_status(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "technical_error"
