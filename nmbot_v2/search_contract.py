from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .contracts import ExecutableTurn, IntentGoal, OptionCard, SafeTurnContext, SearchResult, SemanticPlan, TurnPlan
from .fact_context import normalize_facts
from .state import ConversationState


MCP_ALIAS = "novostroym"
MCP_TOOL = f"{MCP_ALIAS}/get_flat_info"
SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"

OUTPUT_TOP_LEVEL_KEYS = {"facts", "near", "missing", "params", "diagnostics"}
FORBIDDEN_TOP_LEVEL_KEYS = {
    "action", "target", "scope", "search_policy", "clarification_question", "response",
    "current_options", "visible_options", "client_text", "routing_decision",
}
DIAGNOSTIC_KEYS = {
    "mcp_tool", "response_viewpoint", "base_viewpoint", "requested_field_priorities",
    "relaxation_audit", "ignored_preferences", "notes",
}
SEARCH_MODES = {"broad", "named_object", "current_options_fact_check"}
REGION_CODES = {"msk", "mo", "newmsk"}
CAO_DISTRICTS = (
    "Арбат", "Басманный", "Замоскворечье", "Красносельский", "Мещанский",
    "Пресненский", "Таганский", "Тверской", "Хамовники", "Якиманка",
)
CAO_ALIASES = {"центр", "центр москвы", "цао", "центральный административный округ", "center"}
CAO_CENTER_WORD_FORMS = {"центр", "центре", "центра", "центру", "центром"}
BASE_VIEWPOINTS = {"investment", "rental", "family", "life"}
RESPONSE_VIEWPOINTS = BASE_VIEWPOINTS | {"financing"}
SCENARIO_NEEDS_ORDER = ("family", "rental", "investment", "life", "financing")
ALLOWED_PREFERENCES = {
    "format", "rooms_preference", "budget_preference", "location_preference",
    "infrastructure_preference", "transport_preference", "finance_preference", "sort_hint",
}
HARD_KEYS = {"district", "location", "rooms", "max_price", "min_price", "ready", "finishing", "area_min_m2", "area_max_m2"}
LOT_HARD_KEYS = {"rooms"}
SENSITIVE_RE = re.compile(r"phone|телефон|email|mail|token|secret|password|client|chat_id|raw|payload", re.I)
HARD_EVIDENCE_MAP: dict[str, list[str]] = {
    "rooms": ["rooms", "apartment_types.rooms", "ads.rooms"],
    "max_price": ["min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "price_square", "ads.fullprice", "ads.price", "novos.min_price", "novos.max_price"],
    "min_price": ["min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "ads.fullprice", "ads.price", "novos.min_price", "novos.max_price"],
    "area_min_m2": ["square_min", "square_max", "ads.area", "apartment_types.area"],
    "area_max_m2": ["square_min", "square_max", "ads.area", "apartment_types.area"],
    "district": ["district"],
    "location": ["location", "location_id", "district"],
    "ready": ["ready", "delivered", "state", "status"],
    "finishing": ["finishing", "ads.renovation", "house.finishing_list"],
}

SCENARIO_FIELD_PRIORITIES: dict[str, list[str]] = {
    "family": ["school", "kindergarten", "park_near", "water_near", "yard_without_cars", "children_ground", "sports_ground", "security", "property_metro", "location_2.ecology_rating", "infrastructure", "shops", "services", "retail", "clinic", "clinics", "pharmacy", "pharmacies"],
    "financing": ["mortgage_calc", "mortgage", "discount", "payment_by_installments", "ads.fullprice", "novos.min_price", "novos.max_price"],
    "investment": ["min_price", "price1", "price_s", "price_n", "ads.fullprice", "rooms", "apartment_types", "apartment_inventory", "ready", "finishing", "property_metro", "egrn_top_novos", "counter_novos", "ads", "ads_add.stat_price"],
    "rental": ["rooms", "apartment_types", "apartment_inventory", "ready", "finishing", "property_metro", "property_railway", "highway_name", "distance_from_mkad", "location", "ads", "counter_novos", "egrn_top_novos", "infrastructure", "shops", "services", "retail"],
    "life": ["location", "district", "property_metro", "property_railway", "highway_name", "distance_from_mkad", "ready", "finishing", "apartment_inventory", "territory", "park_near", "water_near", "security", "parking", "elevator", "infrastructure", "shops", "services", "retail", "clinic", "clinics", "pharmacy", "pharmacies"],
}
COMMON_FACT_FIELDS = {
    "id", "name", "alias", "type_object", "district", "location_id", "location", "street",
    "new_building_class", "building_type", "rooms", "min_price", "max_price", "price1", "price2",
    "price3", "price4", "price_s", "price_n", "price_square", "square_min", "square_max",
    "floors_total", "delivered", "built_year", "ready_quarter", "ready", "status", "lat", "long",
    "distance_from_mkad", "rating", "count_ads", "object_site", "developer", "developer_description",
    "state", "link", "ipoteka", "fz214", "parking", "elevator", "concierge", "garage", "balcony",
    "loggia", "territory", "security", "yard_without_cars", "children_ground", "sports_ground",
    "heating_type", "conditioning_type", "finishing", "apartments", "taunhouse", "site_url",
    "utility_fee", "park_near", "water_near", "trade_in", "is_investment", "school", "kindergarten",
    "ddu_escrow", "ads_type_list", "total_area", "property_metro", "metro", "metro_line",
    "property_railway", "highway_name", "location_2.ecology_rating", "ecology_rating", "house", "ads", "ads.fullprice",
    "ads.price", "ads.area", "ads.rooms", "ads.floor", "ads.floors_total", "ads.renovation", "ads.status", "ads.apart", "ads.house_id",
    "ads_add.stat_price", "apartment_types", "mortgage_calc", "mortgage", "discount",
    "payment_by_installments", "apartment_inventory", "available_apartments", "flats_available", "egrn_top_novos", "egrn_contracts", "counter_novos",
    "novos.min_price", "novos.max_price", "infrastructure", "shops", "services", "retail", "clinic", "clinics", "pharmacy", "pharmacies", "house.finishing_list", "parking_price", "parking_inventory", "parking_count", "garage_price", "garage_count", "ceiling_height",
}
FACT_FIELD_MAP: dict[str, tuple[str, ...]] = {
    "parks": ("park_near", "water_near", "territory", "infrastructure", "location_2.ecology_rating", "ecology_rating"),
    "parking": ("parking", "garage", "territory", "infrastructure"),
    "parking_price": ("parking", "garage", "parking_price", "garage_price"),
    "parking_inventory": ("parking", "garage", "parking_inventory", "parking_count", "garage_count"),
    "schools": ("school", "kindergarten", "infrastructure"),
    "daily_services": ("infrastructure", "shops", "services", "retail", "pharmacy", "pharmacies"),
    "healthcare": ("infrastructure", "clinic", "clinics", "pharmacy", "pharmacies"),
    "ecology_rating": ("location_2.ecology_rating", "ecology_rating"),
    "metro": ("property_metro", "metro", "metro_line"),
    "transport_access": ("property_railway", "highway_name", "distance_from_mkad"),
    "location": ("location", "location_id", "district", "street"),
    "readiness": ("ready", "delivered", "state", "status", "built_year", "ready_quarter"),
    "finishing": ("finishing", "ads.renovation", "house.finishing_list"),
    "apartment_price": ("min_price", "max_price", "price1", "price2", "price3", "price4", "price_s", "price_n", "price_square", "ads.fullprice", "ads.price", "novos.min_price", "novos.max_price"),
    "apartment_inventory": ("apartment_inventory", "available_apartments", "flats_available", "rooms", "apartment_types"),
    "mortgage_terms": ("mortgage_calc", "mortgage", "discount", "payment_by_installments", "ipoteka"),
    "mortgage_rate": ("mortgage_calc", "mortgage"),
    "mortgage_down_payment": ("mortgage_calc", "mortgage"),
    "mortgage_term": ("mortgage_calc", "mortgage"),
    "installment_months": ("payment_by_installments",),
    "room_specific_price": ("price1", "price2", "price3", "price4", "price_s", "price_n"),
    "price_per_m2": ("price_square", "ads_add.stat_price"),
    "recurring_costs": ("utility_fee",),
    "purchase_terms": ("trade_in", "ddu_escrow", "fz214"),
    "building_profile": ("floors_total", "house", "elevator", "ceiling_height", "building_type"),
    "property_formats": ("apartments", "taunhouse", "ads_type_list"),
    "lot_examples": ("ads", "ads.fullprice", "ads.area", "ads.rooms", "ads.floor", "ads.floors_total", "ads.renovation", "ads.status", "ads.apart", "ads.house_id", "house", "house.finishing_list", "apartment_types"),
}

MISSING_REASON_CODES = {
    "requested_but_unavailable", "requested_but_unconfirmed", "malformed_evidence",
    "hard_evidence_missing", "enrichment_timeout", "provider_unavailable",
}
PRESENTATION_MISSING_CATEGORIES = {
    "finance", "family_infrastructure", "walk_infrastructure", "safety_infrastructure",
    "sales", "ads", "location", "budget", "rooms", "readiness", "finishing", "details",
}
ALLOWED_MISSING_VALUES = set(COMMON_FACT_FIELDS) | MISSING_REASON_CODES | PRESENTATION_MISSING_CATEGORIES


@dataclass(frozen=True)
class V2SearchRequest:
    search_goal: dict[str, Any]
    requested_hard: dict[str, Any] = field(default_factory=dict)
    effective_hard: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    relaxation_audit: list[dict[str, Any]] = field(default_factory=list)
    response_viewpoint: str = "life"
    base_viewpoint: str | None = None
    scenario_needs: tuple[str, ...] = ()
    available_fact_fields: list[str] = field(default_factory=list)
    count: int = 3
    ignored_preferences: list[str] = field(default_factory=list)
    excluded_names: tuple[str, ...] = ()
    search_mode: str = "broad"
    current_option_names: tuple[str, ...] = ()
    facts_needed: tuple[str, ...] = ()
    lot_hard: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A typed request is the executable MCP boundary regardless of whether
        # it came from the dialogue runtime, a replay fixture or a live probe.
        # Keep requested_hard untouched for audit, but always canonicalize the
        # executable geography before building/validating a request.
        object.__setattr__(self, "effective_hard", _normalize_broad_geo_for_mcp(dict(self.effective_hard)))
        object.__setattr__(self, "excluded_names", tuple(dict.fromkeys(
            str(name).strip()[:120] for name in self.excluded_names if str(name).strip()
        ))[:6])
        object.__setattr__(self, "scenario_needs", _scenario_needs_from_facets(self.scenario_needs))
        mode = str(self.search_mode or "broad").strip() or "broad"
        object.__setattr__(self, "search_mode", mode if mode in SEARCH_MODES else "broad")
        object.__setattr__(self, "current_option_names", tuple(dict.fromkeys(
            str(name).strip()[:120] for name in self.current_option_names if str(name).strip()
        ))[:3])
        object.__setattr__(self, "facts_needed", normalize_facts(self.facts_needed))
        object.__setattr__(self, "lot_hard", _safe_lot_hard(self.lot_hard))

    @property
    def constraints(self) -> dict[str, Any]:
        return {
            "requested_hard": dict(self.requested_hard),
            "effective_hard": dict(self.effective_hard),
            "preferences": dict(self.preferences),
            "relaxation_audit": list(self.relaxation_audit),
            "lot_hard": dict(self.lot_hard),
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "search_goal": dict(self.search_goal),
            "constraints": self.constraints,
            "response_viewpoint": self.response_viewpoint,
            "base_viewpoint": self.base_viewpoint,
            "available_fact_fields": list(self.available_fact_fields),
            "count": int(self.count),
            "excluded_names": list(self.excluded_names),
            "search_mode": self.search_mode,
            "current_option_names": list(self.current_option_names),
            "facts_needed": list(self.facts_needed),
            "lot_hard": dict(self.lot_hard),
        }


def build_candidate_retrieval_request(request: V2SearchRequest) -> V2SearchRequest:
    """Return an internal broad retrieval request for specific-location search.

    The returned request is only for candidate collection.  The caller must still
    normalize and validate the raw response against the original strict request.
    """

    if not _candidate_first_location_enabled(request):
        return request
    requested_hard = {key: value for key, value in request.requested_hard.items() if key != "location"}
    effective_hard = {key: value for key, value in request.effective_hard.items() if key != "location"}
    goal = dict(request.search_goal)
    goal["query_summary"] = _candidate_retrieval_query(request)
    terms = [str(item) for item in goal.get("explicit_terms", []) if str(item).strip()]
    goal["explicit_terms"] = _unique([*terms, "internal_candidate_retrieval", "candidate_first_location"])[:12]
    goal["internal_candidate_retrieval"] = {"enabled": True, "field": "location", "client_relaxation": False}
    return replace(
        request,
        search_goal=goal,
        requested_hard=requested_hard,
        effective_hard=effective_hard,
        relaxation_audit=[
            *list(request.relaxation_audit),
            {"field": "location", "mode": "internal_candidate_retrieval", "client_relaxation": False},
        ],
    )


def is_candidate_retrieval_request(request: V2SearchRequest) -> bool:
    marker = request.search_goal.get("internal_candidate_retrieval")
    return isinstance(marker, Mapping) and marker.get("enabled") is True


def _candidate_first_location_enabled(request: V2SearchRequest) -> bool:
    if request.search_mode != "broad" or request.current_option_names or request.search_goal.get("entity_reference"):
        return False
    if "location" not in request.effective_hard:
        return False
    values = request.effective_hard.get("location")
    items = values if isinstance(values, list) else [values]
    return any(str(value or "").strip() and not _broad_geo_district(value) for value in items)


def _candidate_retrieval_query(request: V2SearchRequest) -> str:
    original = _clean_text(str(request.search_goal.get("query_summary") or ""))
    if _is_cao_only_effective_location(request):
        return "Новостройки в ЦАО: какие варианты есть?"
    generic_expands = {"ищи лучше", "покажи еще", "покажи ещё", "найди еще", "найди ещё", "другие варианты", "еще", "ещё"}
    if original.casefold() not in generic_expands:
        return original or "base catalogue search for new building flats"
    values = request.effective_hard.get("location")
    items = values if isinstance(values, list) else [values]
    safe_location = next((str(value).strip() for value in items if str(value or "").strip()), "")
    if safe_location:
        return f"Новостройки в локации «{safe_location}»: какие варианты есть?"
    return original or "base catalogue search for new building flats"


def _is_cao_only_effective_location(request: V2SearchRequest) -> bool:
    values = request.effective_hard.get("location")
    if not isinstance(values, list) or len(values) != len(CAO_DISTRICTS):
        return False
    return list(values) == list(CAO_DISTRICTS)


def build_search_request(plan: TurnPlan, state: ConversationState, context: SafeTurnContext) -> V2SearchRequest:
    merged_hard = _hard_from_params(state.params)
    requested_from_delta, effective_from_delta, preferences, relaxation, ignored = _constraints_from_delta(plan.constraints_delta)
    named_lookup = _is_lookup_object(plan) and bool(str(plan.reference or "").strip())
    # Адресный lookup обязан получить сам объект, даже если он не проходит
    # накопленный бюджет. Пригодность сравнивается уже по подтверждённым фактам.
    requested_hard = {} if named_lookup else {**merged_hard, **requested_from_delta}
    effective_hard = {} if named_lookup else _normalize_broad_geo_for_mcp({**merged_hard, **effective_from_delta})
    viewpoint, base_viewpoint = _derive_viewpoints(plan, state, preferences)
    scenario_needs = _scenario_needs_from_facets(plan.facets)
    fields = available_fact_fields(viewpoint, base_viewpoint, scenario_needs=scenario_needs)
    excluded_names = ()
    if plan.fresh_search:
        excluded_names = tuple(dict.fromkeys(
            card.name for card in (*state.visible_options, *state.previous_options) if str(card.name).strip()
        ))[:6]
    return V2SearchRequest(
        search_goal=_search_goal(plan, context, requested_hard, preferences, viewpoint),
        requested_hard=_safe_mapping(requested_hard),
        effective_hard=_safe_mapping(effective_hard),
        preferences=_safe_mapping(preferences),
        relaxation_audit=relaxation,
        response_viewpoint=viewpoint,
        base_viewpoint=base_viewpoint,
        scenario_needs=scenario_needs,
        available_fact_fields=fields,
        count=1 if named_lookup else 5 if viewpoint == "financing" and not effective_hard else 3,
        ignored_preferences=ignored,
        excluded_names=excluded_names,
    )


def available_fact_fields(response_viewpoint: str, base_viewpoint: str | None = None, extra: list[str] | None = None, scenario_needs: tuple[str, ...] | list[str] = ()) -> list[str]:
    fields: list[str] = sorted(COMMON_FACT_FIELDS | set(SCENARIO_FIELD_PRIORITIES.get(response_viewpoint, [])))
    if base_viewpoint:
        fields.extend(SCENARIO_FIELD_PRIORITIES.get(base_viewpoint, []))
    for scenario in _scenario_needs_from_facets(scenario_needs):
        fields.extend(SCENARIO_FIELD_PRIORITIES.get(scenario, []))
    fields.extend(extra or [])
    return _unique([str(item) for item in fields if item])


def build_current_options_fact_check_request(
    cards: tuple[OptionCard, ...] | list[OptionCard],
    facts_needed: tuple[str, ...] | list[str] | set[str],
    viewpoint: str,
    base_viewpoint: str | None = None,
) -> V2SearchRequest:
    scoped_cards = tuple(card for card in (cards or ()) if str(card.name or "").strip())[:3]
    names = tuple(card.name.strip() for card in scoped_cards)
    safe_facts = normalize_facts(facts_needed)
    extra_fields: list[str] = ["name", "alias", "id"]
    for fact in safe_facts:
        extra_fields.extend(FACT_FIELD_MAP.get(fact, ()))
    fields = available_fact_fields(viewpoint, base_viewpoint, extra=extra_fields)
    return V2SearchRequest(
        search_goal={
            "entity_type": "current_new_building_options",
            "query_summary": "Проверь только запрошенные факты по текущим ЖК: " + ", ".join(names),
            "explicit_terms": ["current_options_fact_check", *safe_facts],
            "current_option_names": list(names),
            "facts_needed": list(safe_facts),
            "scope_policy": "exact_current_option_names_only",
        },
        requested_hard={},
        effective_hard={},
        preferences={},
        relaxation_audit=[],
        response_viewpoint=viewpoint,
        base_viewpoint=base_viewpoint,
        available_fact_fields=fields,
        count=max(0, min(len(scoped_cards), 3)),
        ignored_preferences=[],
        excluded_names=(),
        search_mode="current_options_fact_check",
        current_option_names=names,
        facts_needed=safe_facts,
    )


def build_query(request: V2SearchRequest, *, output_keys: list[str] | None = None, forbidden_keys: list[str] | None = None) -> str:
    envelope = {
        "contract": "v2_search_mcp_contract",
        "search_mode": request.search_mode,
        "mcp_alias": MCP_ALIAS,
        "mcp_tool": MCP_TOOL,
        "output_top_level_keys": output_keys or sorted(OUTPUT_TOP_LEVEL_KEYS),
        "forbidden_top_level_keys": forbidden_keys or sorted(FORBIDDEN_TOP_LEVEL_KEYS),
        "response_viewpoint": request.response_viewpoint,
        "base_viewpoint": request.base_viewpoint,
        "available_fact_fields": list(request.available_fact_fields),
        "count": int(request.count),
        "current_option_names": list(request.current_option_names),
        "facts_needed": list(request.facts_needed),
        "hard_evidence_requirements": hard_evidence_requirements(request),
        "lot_hard": dict(request.lot_hard),
        "lot_hard_evidence_requirements": lot_hard_evidence_requirements(request),
    }
    current_params = {
        "search_goal": dict(request.search_goal),
        "requested_hard": dict(request.requested_hard),
        "effective_hard": dict(request.effective_hard),
        "preferences": dict(request.preferences),
        "relaxation_audit": list(request.relaxation_audit),
        "excluded_names": list(request.excluded_names),
        "search_mode": request.search_mode,
        "current_option_names": list(request.current_option_names),
        "facts_needed": list(request.facts_needed),
        "lot_hard": dict(request.lot_hard),
    }
    client_query = _clean_text(str(request.search_goal.get("query_summary") or "")) or "base catalogue search for new building flats"
    return (
        "SEARCH_CONTRACT_ENVELOPE=" + json.dumps(envelope, ensure_ascii=False, sort_keys=True) +
        "\nПолитика exact/near: facts — только объекты, соответствующие всем effective_hard; near — близкие альтернативы, не подмена facts."
        "\nПеред добавлением объекта в facts проверь hard evidence по активным hard-полям из envelope; если evidence нет — перенеси в near/missing."
        + ("\nРежим current_options_fact_check: верни facts/near только для exact current_option_names из envelope; посторонние ЖК запрещены." if request.search_mode == "current_options_fact_check" else "") +
        "\nТекущие параметры: " + json.dumps(current_params, ensure_ascii=False, sort_keys=True) +
        ("\nНе возвращай объекты из excluded_names ни в facts, ни в near." if request.excluded_names else "") +
        "\nКлиент: " + client_query +
        "\nВерни только строгий JSON по контракту из system_prompt."
    )


def hard_evidence_requirements(request: V2SearchRequest) -> dict[str, list[str]]:
    active = set(request.requested_hard) | set(request.effective_hard)
    return {field: list(HARD_EVIDENCE_MAP[field]) for field in sorted(active) if field in HARD_EVIDENCE_MAP}


def lot_hard_evidence_requirements(request: V2SearchRequest) -> dict[str, list[str]]:
    return {field: ["ads.rooms"] for field in sorted(request.lot_hard) if field in LOT_HARD_KEYS}


def build_request_data(request: V2SearchRequest, *, prompt: str, model: str = SEARCH_MODEL) -> dict[str, Any]:
    data = {
        "_payload_stage": "main_search",
        "query": build_query(request),
        "service": "openrouter",
        "model": model,
        "system_prompt": prompt,
        "parameters": {"temperature": 0.1, "max_tokens": int(os.getenv("NMBOT_SEARCH_MAX_TOKENS", "5000"))},
        "mcp_servers": [MCP_ALIAS],
    }
    api_key = os.getenv("OPENROUTER_API_KEY") or ""
    if api_key:
        data["external_api_key"] = api_key
    return data


def parse_strict_json(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        data = json.loads(str(text or "").strip())
    except json.JSONDecodeError as exc:
        return None, [f"invalid_strict_json:{exc.msg}"]
    if not isinstance(data, dict):
        return None, ["json_root_must_be_object"]
    return data, []


def normalize_search_output(output: dict[str, Any], request: V2SearchRequest) -> dict[str, Any]:
    """Return the runtime-owned V2 search envelope before strict validation.

    The model owns semantic search content (facts/near/missing/params). Runtime
    owns diagnostics because they are derived from the already-built request.
    This function therefore strips forbidden/extra top-level keys, preserves the
    model semantic containers as-is, and deterministically rebuilds diagnostics
    from ``request``.
    """

    source = output if isinstance(output, dict) else {}
    diagnostics = source.get("diagnostics") if isinstance(source.get("diagnostics"), dict) else {}
    source_params = source.get("params") if isinstance(source.get("params"), dict) else {}
    expected_params = {**dict(request.effective_hard), **dict(request.preferences)}
    ignored_param_keys = sorted(str(key) for key in set(source_params) - set(expected_params))
    notes = _safe_notes(diagnostics.get("notes"))
    notes.extend(f"ignored_param:{key}" for key in ignored_param_keys[:8])
    facts, near, sanitize_notes = _sanitize_option_containers(source, request)
    notes.extend(sanitize_notes)
    missing, missing_notes = _normalize_missing_items(source.get("missing"))
    notes.extend(missing_notes)
    return {
        "facts": facts,
        "near": near,
        "missing": missing,
        # Params are runtime-owned state echo, not model-owned inference. This
        # prevents an inferred district/location from silently mutating dialogue
        # state while still keeping the extra key visible as safe diagnostics.
        "params": expected_params,
        "diagnostics": {
            "mcp_tool": MCP_TOOL,
            "response_viewpoint": request.response_viewpoint,
            "base_viewpoint": request.base_viewpoint,
            "requested_field_priorities": _requested_field_priorities(request, diagnostics),
            "relaxation_audit": list(request.relaxation_audit),
            "ignored_preferences": list(request.ignored_preferences),
            "notes": notes,
        },
    }


def normalize_and_validate_search_output(output: dict[str, Any], request: V2SearchRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = normalize_search_output(output, request)
    validation = validate_search_output(normalized, request)
    return normalized, validation


def validate_search_output(output: dict[str, Any], request: V2SearchRequest) -> dict[str, Any]:
    errors: list[str] = []
    keys = set(output)
    if keys != OUTPUT_TOP_LEVEL_KEYS:
        errors.append("top_level_keys_mismatch")
    forbidden = sorted(keys & FORBIDDEN_TOP_LEVEL_KEYS)
    if forbidden:
        errors.append("forbidden_top_level_keys:" + ",".join(forbidden))
    facts = output.get("facts") if isinstance(output.get("facts"), list) else []
    near = output.get("near") if isinstance(output.get("near"), list) else []
    params = output.get("params") if isinstance(output.get("params"), dict) else {}
    diagnostics = output.get("diagnostics") if isinstance(output.get("diagnostics"), dict) else {}
    if not isinstance(output.get("facts"), list): errors.append("facts_must_be_list")
    if not isinstance(output.get("near"), list): errors.append("near_must_be_list")
    if not isinstance(output.get("missing"), list): errors.append("missing_must_be_list")
    if not isinstance(output.get("params"), dict): errors.append("params_must_be_object")
    if not isinstance(output.get("diagnostics"), dict): errors.append("diagnostics_must_be_object")
    extra_diag = set(diagnostics) - DIAGNOSTIC_KEYS
    if extra_diag:
        errors.append("diagnostics_extra_keys:" + ",".join(sorted(extra_diag)))
    missing_diag = DIAGNOSTIC_KEYS - set(diagnostics)
    if missing_diag:
        errors.append("diagnostics_missing_keys:" + ",".join(sorted(missing_diag)))
    if diagnostics.get("mcp_tool") != MCP_TOOL:
        errors.append("diagnostics_mcp_tool_mismatch")
    if diagnostics.get("response_viewpoint") != request.response_viewpoint:
        errors.append("diagnostics_response_viewpoint_mismatch")
    if diagnostics.get("base_viewpoint") != request.base_viewpoint:
        errors.append("diagnostics_base_viewpoint_mismatch")
    if diagnostics.get("relaxation_audit") != request.relaxation_audit:
        errors.append("relaxation_audit_mismatch")
    allowed_fact_fields = set(request.available_fact_fields)
    for idx, item in enumerate(facts):
        if isinstance(item, dict):
            extra = set(item) - allowed_fact_fields
            if extra:
                errors.append(f"fact_{idx}_has_non_whitelisted_fields:{','.join(sorted(extra))}")
            for field in set(request.requested_hard) | set(request.effective_hard):
                if not hard_evidence_present(item, field):
                    errors.append(f"fact_{idx}_missing_hard_evidence:{field}")
            for field, expected in request.effective_hard.items():
                if not _matches_hard(item, field, expected):
                    errors.append(f"fact_{idx}_violates_hard:{field}")
            for field, expected in request.lot_hard.items():
                if not _has_matching_active_lot(item, field, expected):
                    errors.append(f"fact_{idx}_violates_lot_hard:{field}")
    allowed_params = set(request.effective_hard) | set(request.preferences)
    extra_params = set(params) - allowed_params
    if extra_params:
        errors.append("params_extra_keys:" + ",".join(sorted(extra_params)))
    for key, expected in request.effective_hard.items():
        if key in params and params.get(key) != expected:
            errors.append(f"params_not_effective_hard:{key}")
    ignored = set(diagnostics.get("ignored_preferences") or []) if isinstance(diagnostics.get("ignored_preferences"), list) else set()
    if set(request.ignored_preferences) - ignored:
        errors.append("unknown_preferences_not_reported:" + ",".join(sorted(set(request.ignored_preferences) - ignored)))
    fact_ids = {_item_id(item) for item in facts}
    near_ids = {_item_id(item) for item in near}
    if {item for item in fact_ids & near_ids if item}:
        errors.append("near_duplicates_facts")
    for idx, item in enumerate(near):
        if not isinstance(item, Mapping):
            continue
        if item.get("is_near") is not True:
            errors.append(f"near_{idx}_missing_is_near")
        if not isinstance(item.get("why_close"), str) or not item.get("why_close", "").strip():
            errors.append(f"near_{idx}_missing_why_close")
        if not isinstance(item.get("differences"), list) or not [x for x in item.get("differences", []) if isinstance(x, str) and x.strip()]:
            errors.append(f"near_{idx}_missing_differences")
    rendered_missing = json.dumps({"missing": output.get("missing"), "notes": diagnostics.get("notes")}, ensure_ascii=False).lower()
    if any(marker in rendered_missing for marker in ("inventory_absent", "no_inventory", "absence_claim")):
        errors.append("absence_claim_without_hard_evidence")
    if isinstance(output.get("missing"), list):
        for idx, item in enumerate(output.get("missing") or []):
            if not _is_normalized_missing_item(item):
                errors.append(f"missing_{idx}_unknown_value")
    warnings = _validation_warnings(diagnostics)
    note_blockers = _validation_blockers(diagnostics)
    errors.extend(note_blockers)
    status = "invalid" if errors else "degraded" if warnings else "valid"
    return {
        "ok": status != "invalid",
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "facts": len(facts),
            "near": len(near),
            "missing": len(output.get("missing") or []) if isinstance(output.get("missing"), list) else 0,
            "warnings": len(warnings),
        },
    }


def validate_current_options_fact_check_result(result: Any, request: V2SearchRequest) -> dict[str, Any]:
    allowed = {_normalized_option_name(name) for name in request.current_option_names if _normalized_option_name(name)}
    errors: list[str] = []
    if request.search_mode != "current_options_fact_check":
        errors.append("request_mode_mismatch")
    if not allowed:
        errors.append("current_option_names_empty")
    if len(allowed) > 3:
        errors.append("too_many_current_option_names")

    if isinstance(result, SearchResult):
        facts = tuple(result.facts)
        near = tuple(result.near)
    elif isinstance(result, Mapping):
        facts = tuple(OptionCard.from_dict(item) for item in result.get("facts", []) if isinstance(item, Mapping)) if isinstance(result.get("facts"), list) else ()
        near = tuple(OptionCard.from_dict({**item, "is_near": True}) for item in result.get("near", []) if isinstance(item, Mapping)) if isinstance(result.get("near"), list) else ()
    else:
        facts = ()
        near = ()
        errors.append("result_shape_invalid")

    for container, cards in (("fact", facts), ("near", near)):
        for idx, card in enumerate(cards):
            if _normalized_option_name(card.name) not in allowed:
                errors.append(f"{container}_{idx}_foreign_object")
    fact_names = {_normalized_option_name(card.name) for card in facts}
    near_names = {_normalized_option_name(card.name) for card in near}
    if {name for name in fact_names & near_names if name}:
        errors.append("near_duplicates_facts")
    return {
        "ok": not errors,
        "errors": tuple(errors),
        "counts": {"facts": len(facts), "near": len(near), "current_option_names": len(allowed)},
    }


def _sanitize_option_containers(source: Mapping[str, Any], request: V2SearchRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    facts_src = source.get("facts")
    near_src = source.get("near")
    missing_src = source.get("missing")
    if not isinstance(facts_src, list):
        notes.append("contract_blocker:facts_must_be_list")
        facts_src = []
    if not isinstance(near_src, list):
        notes.append("contract_blocker:near_must_be_list")
        near_src = []
    if not isinstance(missing_src, list):
        notes.append("contract_blocker:missing_must_be_list")

    facts: list[dict[str, Any]] = []
    near: list[dict[str, Any]] = []
    near_ids: set[str] = set()
    named_reference = str(request.search_goal.get("entity_reference") or "").strip()

    def is_excluded(item: Mapping[str, Any]) -> bool:
        return any(_same_named_object(item, name) for name in request.excluded_names)

    def add_near(item: dict[str, Any]) -> None:
        marker = _item_id(item)
        if marker and marker in near_ids:
            notes.append("contract_warning:repeated_near_item_reported")
        near_item = dict(item)
        near_item["is_near"] = True
        differences, labels = _near_differences(near_item, request)
        if not differences:
            notes.append("contract_warning:near_without_structured_difference_reported")
            differences = ["неполное подтверждение условий"]
            labels = ["неполное подтверждение условий"]
        # Near explanations are client-visible and therefore runtime-owned.
        # Never preserve free model prose here: it may contain internal terms
        # (for example, "MCP-карточка") that make the final response unsafe.
        near_item["why_close"] = "; ".join(labels or differences)
        near_item["differences"] = differences
        near.append(near_item)
        if marker:
            near_ids.add(marker)

    for raw in near_src:
        cleaned, item_notes = _sanitize_card(raw, request, container="near")
        notes.extend(item_notes)
        if cleaned is not None:
            if is_excluded(cleaned):
                notes.append("contract_warning:previous_option_reported")
            if named_reference and not _same_named_object(cleaned, named_reference):
                notes.append("contract_warning:named_object_mismatch_reported")
            add_near(cleaned)

    for raw in facts_src:
        cleaned, item_notes = _sanitize_card(raw, request, container="facts")
        notes.extend(item_notes)
        if cleaned is None:
            continue
        if is_excluded(cleaned):
            notes.append("contract_warning:previous_option_reported")
        if named_reference and not _same_named_object(cleaned, named_reference):
            notes.append("contract_warning:named_object_mismatch_reported")
        missing_hard = [field for field in sorted(set(request.requested_hard) | set(request.effective_hard)) if not hard_evidence_present(cleaned, field)]
        if missing_hard:
            notes.append("contract_warning:fact_missing_hard_evidence_reported")
        violates_hard = [field for field, expected in request.effective_hard.items() if not _matches_hard(cleaned, field, expected)]
        if violates_hard:
            notes.append("contract_warning:fact_violates_hard_reported")
        violates_lot_hard = [field for field, expected in request.lot_hard.items() if not _has_matching_active_lot(cleaned, field, expected)]
        if violates_lot_hard:
            notes.append("contract_warning:fact_violates_lot_hard_reported")
        facts.append(cleaned)
    return facts, near, notes


def _normalize_missing_items(value: Any) -> tuple[list[Any], list[str]]:
    notes: list[str] = []
    if not isinstance(value, list):
        return [], notes
    normalized: list[Any] = []
    for raw in value[:20]:
        item = _normalize_missing_item(raw)
        if item is None:
            notes.append("contract_warning:missing_value_normalized")
            item = "requested_but_unconfirmed"
        normalized.append(item)
    return _unique(normalized), notes


def _normalize_missing_item(raw: Any) -> Any | None:
    if isinstance(raw, Mapping):
        field = _safe_field_name(raw.get("field")) or _missing_string_to_value(raw.get("field"))
        reason = str(raw.get("reason_code") or raw.get("reason") or "").strip()
        reason_code = reason if reason in MISSING_REASON_CODES else "requested_but_unconfirmed"
        if isinstance(field, str) and field in COMMON_FACT_FIELDS:
            out: dict[str, Any] = {"field": field, "reason_code": reason_code}
            details = _safe_value(raw.get("details"))
            if isinstance(details, str) and details:
                out["details"] = details
            return out
        return reason_code
    if isinstance(raw, str):
        return _missing_string_to_value(raw)
    return None


def _missing_string_to_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or SENSITIVE_RE.search(text):
        return None
    if text in ALLOWED_MISSING_VALUES:
        return text
    lowered = text.casefold().replace("ё", "е")
    phrase_map = (
        (("timeout", "таймаут", "не успел"), "enrichment_timeout"),
        (("provider", "источник не ответ", "недоступ"), "provider_unavailable"),
        (("malformed", "unsupported", "бит", "неподдерж"), "malformed_evidence"),
        (("hard", "evidence", "подтвержд"), "hard_evidence_missing"),
        (("unavailable", "не вернул", "недоступно"), "requested_but_unavailable"),
    )
    for needles, category in phrase_map:
        if any(needle in lowered for needle in needles):
            return category
    return "requested_but_unconfirmed"


def _is_normalized_missing_item(item: Any) -> bool:
    if isinstance(item, str):
        return item in ALLOWED_MISSING_VALUES
    if isinstance(item, Mapping):
        field = item.get("field")
        reason = item.get("reason_code")
        details = item.get("details")
        return (
            isinstance(field, str) and field in COMMON_FACT_FIELDS
            and isinstance(reason, str) and reason in MISSING_REASON_CODES
            and (details is None or isinstance(details, str))
        )
    return False


_NEAR_FIELD_LABELS = {
    "max_price": ("цена не подтверждена", "цена выше бюджета"),
    "min_price": ("цена не подтверждена", "цена ниже заданного диапазона"),
    "area_min_m2": ("площадь не подтверждена", "площадь меньше нужной"),
    "area_max_m2": ("площадь не подтверждена", "площадь больше нужной"),
    "rooms": ("комнатность не подтверждена", "другая комнатность"),
    "location": ("локация не подтверждена", "другая локация"),
    "district": ("район не подтверждён", "другой район"),
    "ready": ("готовность дома не подтверждена", "другой срок готовности"),
    "finishing": ("отделка не подтверждена", "другой вариант отделки"),
}


def _near_differences(item: Mapping[str, Any], request: V2SearchRequest) -> tuple[list[str], list[str]]:
    """Объясняет near только из structured evidence, без model prose."""

    differences: list[str] = []
    labels: list[str] = []
    for field in ("max_price", "min_price", "area_min_m2", "area_max_m2", "rooms", "location", "district", "ready", "finishing"):
        if field not in request.effective_hard:
            continue
        missing_label, mismatch_label = _NEAR_FIELD_LABELS[field]
        if not hard_evidence_present(item, field):
            differences.append(field)
            labels.append(missing_label)
        elif not _matches_hard(dict(item), field, request.effective_hard[field]):
            differences.append(field)
            labels.append(mismatch_label)
        if len(differences) == 2:
            break
    if not differences and request.facts_needed:
        missing_needed = [field for field in request.facts_needed if field in COMMON_FACT_FIELDS and not _has_nested(item, field)]
        if missing_needed:
            return ["неполное подтверждение условий"], ["неполное подтверждение условий"]
    return differences, labels


def _near_difference(item: Mapping[str, Any], request: V2SearchRequest) -> str:
    """Backward-compatible string form for older focused tests/callers."""

    _differences, labels = _near_differences(item, request)
    return "; ".join(labels)


def _same_named_object(card: Mapping[str, Any], reference: str) -> bool:
    """Сравнивает только канонические имена, не разрешая похожую подмену."""

    target = _normalized_option_name(reference)
    names = (card.get("name"), card.get("alias"), card.get("title"), card.get("label"))
    return bool(target and any(_normalized_option_name(name) == target for name in names if name))


def _normalized_option_name(value: Any) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"\b(?:жк|жилой комплекс)\b", " ", text)
    return re.sub(r"[^a-zа-я0-9]+", "", text)


def _sanitize_card(raw: Any, request: V2SearchRequest, *, container: str) -> tuple[dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    if not isinstance(raw, Mapping):
        notes.append("contract_warning:non_dict_card_dropped")
        return None, notes
    allowed = set(request.available_fact_fields)
    cleaned = {str(key): _safe_value(value) for key, value in raw.items() if str(key) in allowed and _safe_value(value) not in (None, "", [], {})}
    if set(raw) - allowed:
        notes.append("contract_warning:unknown_fact_fields_removed")
    if not _item_id(cleaned):
        notes.append("contract_warning:unidentifiable_card_dropped")
        return None, notes
    if container == "near":
        if raw.get("is_near") is True:
            cleaned["is_near"] = True
    return cleaned, notes


def _validation_warnings(diagnostics: Mapping[str, Any]) -> list[str]:
    notes = diagnostics.get("notes") if isinstance(diagnostics, Mapping) else []
    warnings: list[str] = []
    if isinstance(notes, list):
        for item in notes:
            text = str(item or "")
            if text.startswith("contract_warning:"):
                code = text.split(":", 1)[1]
                if code and code not in warnings:
                    warnings.append(code)
    return warnings


def _validation_blockers(diagnostics: Mapping[str, Any]) -> list[str]:
    notes = diagnostics.get("notes") if isinstance(diagnostics, Mapping) else []
    blockers: list[str] = []
    if isinstance(notes, list):
        for item in notes:
            text = str(item or "")
            if text.startswith("contract_blocker:"):
                code = text.split(":", 1)[1]
                if code and code not in blockers:
                    blockers.append(code)
    return blockers


def validate_fixture_case(request: V2SearchRequest) -> dict[str, Any]:
    errors: list[str] = []
    goal = request.search_goal
    if not goal.get("entity_type") or not goal.get("query_summary") or not isinstance(goal.get("explicit_terms"), list):
        errors.append("search_goal_shape_invalid")
    if request.response_viewpoint not in RESPONSE_VIEWPOINTS:
        errors.append("unknown_response_viewpoint")
    if request.base_viewpoint is not None and request.base_viewpoint not in BASE_VIEWPOINTS:
        errors.append("unknown_base_viewpoint")
    if request.count <= 0:
        errors.append("count_must_be_positive")
    if not request.available_fact_fields:
        errors.append("available_fact_fields_empty")
    return {"ok": not errors, "errors": errors, "network": False}


def load_prompt(path: Path | None = None) -> str:
    root = Path(__file__).resolve().parents[1]
    return (path or (root / "prompts" / "v2_search_mcp.txt")).read_text(encoding="utf-8")


def _constraints_from_delta(delta: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], list[str]]:
    delta = delta if isinstance(delta, Mapping) else {}
    requested_src = delta.get("requested_hard") if isinstance(delta.get("requested_hard"), Mapping) else delta.get("hard") if isinstance(delta.get("hard"), Mapping) else delta
    effective_src = delta.get("effective_hard") if isinstance(delta.get("effective_hard"), Mapping) else requested_src
    preferences_src = delta.get("preferences") if isinstance(delta.get("preferences"), Mapping) else {}
    requested = _hard_from_params(requested_src)
    effective = _hard_from_params(effective_src)
    preferences: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in preferences_src.items():
        key = _alias(str(key))
        if key in ALLOWED_PREFERENCES and not SENSITIVE_RE.search(key):
            preferences[key] = _safe_value(value)
        else:
            ignored.append(key)
    relaxation_raw = delta.get("relaxation_audit") if isinstance(delta.get("relaxation_audit"), list) else []
    relaxation = [_safe_mapping(x) for x in relaxation_raw[:3] if isinstance(x, Mapping)]
    return requested, effective, preferences, relaxation, ignored


def _hard_from_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(params, Mapping):
        return out
    for raw_key, value in params.items():
        key = _alias(str(raw_key))
        if key in HARD_KEYS and not SENSITIVE_RE.search(key):
            cleaned = _safe_value(value)
            if cleaned not in (None, "", [], {}):
                out[key] = cleaned
    return out


def _normalize_broad_geo_for_mcp(hard: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(hard)
    raw_locations = out.get("location")
    if raw_locations in (None, "", [], {}):
        return out
    values = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    specific: list[Any] = []
    mapped_district: str | None = None
    for value in values:
        district = _broad_geo_district(value)
        if district:
            mapped_district = district
        elif _is_cao_alias(value):
            specific.extend(CAO_DISTRICTS)
        else:
            specific.append(value)
    if "district" not in out and mapped_district:
        out["district"] = mapped_district
    specific = _unique(specific)
    if specific:
        out["location"] = specific if isinstance(raw_locations, list) or _is_cao_alias(raw_locations) else specific[0]
    elif mapped_district:
        out.pop("location", None)
    return out


def _broad_geo_district(value: Any) -> str | None:
    text = _normalized_geo_text(value)
    if text == "москва":
        return "msk"
    if text == "новая москва":
        return "newmsk"
    if text in {"московская область", "мо", "м.о.", "подмосковье"}:
        return "mo"
    return None


def _is_cao_alias(value: Any) -> bool:
    text = _normalized_geo_text(value)
    if text in CAO_ALIASES:
        return True
    if text in CAO_CENTER_WORD_FORMS:
        return True
    if not re.fullmatch(r"[а-я]+", text) or not 5 <= len(text) <= 7:
        return False
    if any(text.startswith(form) and text != form for form in CAO_CENTER_WORD_FORMS):
        return False
    return any(_one_edit_or_transposition(text, form) for form in CAO_CENTER_WORD_FORMS)


def _one_edit_or_transposition(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        diffs = [idx for idx, (a, b) in enumerate(zip(left, right)) if a != b]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2:
            first, second = diffs
            return second == first + 1 and left[first] == right[second] and left[second] == right[first]
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    i = j = edits = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


def _normalized_geo_text(value: Any) -> str:
    return " ".join(str(value or "").replace("ё", "е").casefold().split())


def _derive_viewpoints(plan: SemanticPlan, state: ConversationState, preferences: Mapping[str, Any]) -> tuple[str, str | None]:
    raw = str(plan.intent or "").strip().lower()
    facets = {str(x).strip().lower() for x in (plan.facets or [])}
    scenario_needs = _scenario_needs_from_facets(plan.facets)
    finance_requested = bool(facets & {"mortgage", "financing", "installment"} or preferences.get("finance_preference"))
    if raw in RESPONSE_VIEWPOINTS - {"financing"} and not (finance_requested and len(scenario_needs) <= 1):
        return raw, None
    if raw in {"mortgage", "financing"} or finance_requested:
        base = _base_viewpoint(state, fallback="life")
        return "financing", base
    base = _base_viewpoint(state, fallback=None)
    return (base or "life"), None


def _scenario_needs_from_facets(facets: Any) -> tuple[str, ...]:
    raw_items = facets if isinstance(facets, (list, tuple, set)) else ([] if facets in (None, "", {}, ()) else [facets])
    aliases = {"mortgage": "financing", "finance": "financing"}
    seen: set[str] = set()
    values: set[str] = set()
    for item in raw_items:
        text = aliases.get(str(item or "").strip().lower(), str(item or "").strip().lower())
        if text in SCENARIO_NEEDS_ORDER:
            values.add(text)
    ordered: list[str] = []
    for scenario in SCENARIO_NEEDS_ORDER:
        if scenario in values and scenario not in seen:
            ordered.append(scenario)
            seen.add(scenario)
    return tuple(ordered)


def _base_viewpoint(state: ConversationState, *, fallback: str | None) -> str | None:
    for value in (state.active_topic, state.params.get("purpose"), state.params.get("primary_intent")):
        text = str(value or "").strip().lower()
        if text in BASE_VIEWPOINTS:
            return text
        if text == "mortgage":
            continue
    return fallback


def _search_goal(plan: TurnPlan, context: SafeTurnContext, hard: Mapping[str, Any], preferences: Mapping[str, Any], viewpoint: str) -> dict[str, Any]:
    text = _clean_text(context.user_text or plan.query_text or "")
    if not text:
        text = "base catalogue search for new building flats"
    terms = _explicit_terms(text)
    terms.extend(str(k) for k in hard.keys())
    terms.extend(str(k) for k in preferences.keys())
    if viewpoint not in terms and viewpoint != "life":
        terms.append(viewpoint)
    goal = {"entity_type": "new_building_flat", "query_summary": text[:180], "explicit_terms": _unique(terms)[:12]}
    if _is_lookup_object(plan) and str(plan.reference or "").strip():
        goal["entity_reference"] = _clean_text(str(plan.reference))[:100]
        goal["lookup_mode"] = "exact_named_object"
    return goal


def _is_lookup_object(plan: TurnPlan) -> bool:
    return (isinstance(plan, ExecutableTurn) and plan.goal == IntentGoal.LOOKUP_OBJECT) or (isinstance(plan, SemanticPlan) and plan.operation == "lookup_object")


def _clean_text(text: str) -> str:
    text = re.sub(r"\+?\d[\d\s().-]{7,}\d", "[redacted-contact]", str(text or ""))
    text = re.sub(r"[\w.+-]+@[\w.-]+", "[redacted-email]", text)
    text = re.sub(r"(?i)(token|secret|password)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return " ".join(text.split())


def _explicit_terms(text: str) -> list[str]:
    markers = {
        "family": ("сем", "школ", "сад"), "investment": ("инвест",), "rental": ("аренд", "сдать"),
        "financing": ("ипот", "расср", "платеж", "взнос"), "ready": ("сдан", "готов"), "finishing": ("отдел"),
        "rooms": ("комн", "двуш", "треш", "однуш"), "budget": ("млн", "бюдж", "цен"), "location": ("район", "метро", "моск", "сокол"),
    }
    lowered = text.casefold()
    return [term for term, needles in markers.items() if any(needle in lowered for needle in needles)]


def _safe_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): _safe_value(v) for k, v in data.items() if not SENSITIVE_RE.search(str(k)) and _safe_value(v) not in (None, "", [], {})}


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(x) for x in list(value)[:10] if _safe_value(x) not in (None, "", [], {})]
    if isinstance(value, str):
        return _clean_text(value)[:160]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean_text(str(value))[:160]


def _safe_field_name(value: Any) -> str | None:
    field = str(value or "").strip()
    if not field or SENSITIVE_RE.search(field):
        return None
    known = set(COMMON_FACT_FIELDS)
    for fields in SCENARIO_FIELD_PRIORITIES.values():
        known.update(fields)
    return field if field in known else None


def _requested_field_priorities(request: V2SearchRequest, diagnostics: Mapping[str, Any]) -> list[str]:
    available: set[str] = set()
    for item in request.available_fact_fields:
        field = _safe_field_name(item)
        if field:
            available.add(field)
    ordered: list[str] = []
    for viewpoint in (request.response_viewpoint, request.base_viewpoint, *request.scenario_needs):
        for field in SCENARIO_FIELD_PRIORITIES.get(str(viewpoint or ""), []):
            if field in available:
                ordered.append(field)
    model_priorities = diagnostics.get("requested_field_priorities")
    if isinstance(model_priorities, list):
        for item in model_priorities:
            field = _safe_field_name(item)
            if field and field in available:
                ordered.append(field)
    for field in request.available_fact_fields:
        safe = _safe_field_name(field)
        if safe and safe in available:
            ordered.append(safe)
    return [str(item) for item in _unique(ordered)]


def _safe_notes(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    notes: list[Any] = []
    for item in value[:5]:
        if isinstance(item, Mapping):
            cleaned = _safe_mapping(item)
            if cleaned:
                notes.append(cleaned)
        elif isinstance(item, (str, int, float, bool)):
            cleaned = _safe_value(item)
            if cleaned not in (None, "", [], {}):
                notes.append(cleaned)
    return notes


def _alias(key: str) -> str:
    return {"budget_max": "max_price", "price_max": "max_price", "max_budget": "max_price", "budget": "max_price", "room_count": "rooms", "rooms_count": "rooms"}.get(key, key)


def _unique(items: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in items:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if marker not in seen:
            out.append(item)
            seen.add(marker)
    return out


def _item_id(item: Any) -> str | None:
    return str(item.get("id") or item.get("alias") or item.get("name")) if isinstance(item, dict) and (item.get("id") or item.get("alias") or item.get("name")) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    if isinstance(value, (int, float)): return float(value)
    return None


def _numbers_for_paths(item: Mapping[str, Any], paths: list[str] | tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for path in paths:
        values.extend(_numbers_at_path(item, path))
    return values


def _numbers_at_path(value: Any, path: str) -> list[float]:
    if isinstance(value, Mapping) and path in value:
        number = _number(value.get(path))
        return [number] if number is not None else []
    parts = path.split(".") if path else []
    current: list[Any] = [value]
    for part in parts:
        next_values: list[Any] = []
        for node in current:
            if isinstance(node, Mapping):
                if part in node:
                    next_values.append(node[part])
            elif isinstance(node, (list, tuple)):
                for child in node:
                    if isinstance(child, Mapping) and part in child:
                        next_values.append(child[part])
        current = next_values
        if not current:
            return []
    numbers: list[float] = []
    for node in current:
        if isinstance(node, (list, tuple)):
            for child in node:
                number = _number(child)
                if number is not None:
                    numbers.append(number)
        else:
            number = _number(node)
            if number is not None:
                numbers.append(number)
    return numbers


def _price_values(item: Mapping[str, Any]) -> list[float]:
    return _numbers_for_paths(item, HARD_EVIDENCE_MAP["max_price"])


def _area_values(item: Mapping[str, Any]) -> list[float]:
    return _numbers_for_paths(item, HARD_EVIDENCE_MAP["area_min_m2"])


def hard_evidence_present(item: Mapping[str, Any], field: str) -> bool:
    if field in {"max_price", "min_price"}:
        return bool(_numbers_for_paths(item, HARD_EVIDENCE_MAP[field]))
    if field in {"area_min_m2", "area_max_m2"}:
        return bool(_area_values(item))
    if field == "rooms":
        return "rooms" in item or any(isinstance(ad, Mapping) and "rooms" in ad for ad in (item.get("ads") or []) if isinstance(item.get("ads"), list))
    if field == "ready":
        return any(key in item for key in HARD_EVIDENCE_MAP["ready"])
    if field == "finishing":
        return bool(_finishing_tokens(item))
    if field == "location":
        return any(_has_nested(item, key) for key in HARD_EVIDENCE_MAP["location"])
    if field == "district":
        return "district" in item
    return field in item


def _safe_lot_hard(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, Any] = {}
    for field, expected in value.items():
        key = str(field or "").strip()
        if key not in LOT_HARD_KEYS or expected in (None, "", [], {}, ()):
            continue
        out[key] = expected
    return out


def _has_matching_active_lot(item: Mapping[str, Any], field: str, expected: Any) -> bool:
    if field not in LOT_HARD_KEYS:
        return False
    ads = item.get("ads")
    rows = [ads] if isinstance(ads, Mapping) else [ad for ad in ads if isinstance(ad, Mapping)] if isinstance(ads, list) else []
    for ad in rows:
        if not _selected_lot_has_valid_id(ad.get("id")):
            continue
        if not _selected_lot_status_active(ad.get("status")):
            continue
        if _matches_hard({"rooms": ad.get("rooms")}, field, expected):
            return True
    return False


def _selected_lot_has_valid_id(lot_id: Any) -> bool:
    if isinstance(lot_id, bool) or lot_id in (None, ""):
        return False
    if isinstance(lot_id, (int, float)):
        return int(lot_id) > 0
    return bool(str(lot_id).strip()) and str(lot_id).strip() != "0"


def _selected_lot_status_active(status: Any) -> bool:
    if isinstance(status, bool) or status in (None, ""):
        return False
    if isinstance(status, (int, float)):
        return int(status) == 2
    normalized = re.sub(r"[^a-zа-я0-9]+", "_", str(status).strip().casefold().replace("ё", "е")).strip("_")
    return normalized in {"2", "active", "available", "sale", "in_sale", "on_sale", "for_sale", "в_продаже", "продается"}


def _get_nested(item: Mapping[str, Any], key: str) -> Any:
    if key in item:
        return item.get(key)
    current: Any = item
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _has_nested(item: Mapping[str, Any], key: str) -> bool:
    if key in item:
        return True
    current: Any = item
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


RoomToken = int | str


def _room_tokens(value: Any) -> set[RoomToken]:
    """Normalize structured room values to exact comparable tokens.

    The V2 search validator must only trust structured room evidence from the
    MCP result's ``rooms`` field.  MCP can return that field as a scalar,
    comma-separated string, list, set, tuple, or nested dict/list shape.  We do
    not inspect prose fields such as description/why_close, and numeric tokens
    are extracted with word-ish boundaries so ``1`` never matches ``10``.
    """

    tokens: set[RoomToken] = set()
    if value is None or isinstance(value, bool):
        return tokens
    if isinstance(value, int):
        if value >= 0:
            tokens.add(value)
        return tokens
    if isinstance(value, float):
        if value >= 0 and value.is_integer():
            tokens.add(int(value))
        return tokens
    if isinstance(value, str):
        text = value.strip().casefold().replace("ё", "е")
        if not text:
            return tokens
        if text in {"s", "st", "studio", "studios", "ст", "студия", "студии", "студию"} or re.search(r"\bстуди[яиюе]\b", text):
            tokens.add("studio")
        for match in re.finditer(r"(?<!\d)(\d{1,2})(?!\d)", text):
            tokens.add(int(match.group(1)))
        return tokens
    if isinstance(value, Mapping):
        for nested in value.values():
            tokens.update(_room_tokens(nested))
        return tokens
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            tokens.update(_room_tokens(nested))
        return tokens
    return tokens


DELIVERED_READY_EXPECTED = {"delivered", "ready", "сдан", "сдано", "готов", "готово", "готовый"}
DELIVERED_READY_RE = re.compile(r"(^|\b|[^\wа-яё])(delivered|ready|sdan|сдан[аоы]?|дом\s+сдан|готов(?:о|ый|ые|а)?|дом\s+готов)(\b|[^\wа-яё])", re.I)
NOT_DELIVERED_READY_RE = re.compile(
    r"строит|строится|строящ|construction|under\s+construction|planned|планир|проект|очеред|будет|сдач[аеиу]|срок\s+сдачи|"
    r"\b20(?:2[7-9]|[3-9]\d)\b|\b[1-4]\s*(?:кв\.?|квартал)",
    re.I,
)


def _ready_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().replace("ё", "е").split())


def _is_expected_delivered(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        text = _ready_text(value)
        return text in DELIVERED_READY_EXPECTED
    return False


def _is_structured_delivered_value(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if not isinstance(value, str):
        return False
    text = _ready_text(value)
    if not text:
        return False
    # Evidence-safe: a future year, quarter-only deadline, planned or construction
    # state is not a delivered house, even if phrased as a future delivery date.
    if NOT_DELIVERED_READY_RE.search(text):
        return False
    return bool(DELIVERED_READY_RE.search(f" {text} "))


def _item_matches_delivered_ready(item: Mapping[str, Any]) -> bool:
    # Only structured/allowlisted readiness fields are accepted. Do not inspect
    # description, why_close, notes or other prose containers as hard evidence.
    return any(_is_structured_delivered_value(item.get(field)) for field in ("ready", "delivered", "state", "status"))


def _finishing_tokens(item: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()

    def collect(value: Any) -> None:
        if value is True:
            tokens.add("finished")
            return
        if value is False or value is None or isinstance(value, (int, float)):
            return
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key) in {"finishing", "renovation", "finishing_list", "name", "title", "value"}:
                    collect(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                collect(nested)
            return
        text = " ".join(str(value).strip().casefold().replace("ё", "е").replace("_", " ").split())
        if not text or "без отдел" in text or text in {"none", "null", "no finishing"}:
            return
        if any(marker in text for marker in ("с отдел", "есть отдел", "отделка", "white box", "вайт бокс", "предчист", "чистов")):
            tokens.add("finished")

    collect(item.get("finishing"))
    collect(item.get("ads"))
    collect(item.get("house"))
    return tokens


def _matches_hard(item: dict[str, Any], field: str, expected: Any) -> bool:
    if field == "max_price":
        expected_number = _number(expected)
        values = _price_values(item)
        return expected_number is not None and bool(values) and min(values) <= expected_number
    if field == "min_price":
        expected_number = _number(expected)
        values = _price_values(item)
        return expected_number is not None and bool(values) and max(values) >= expected_number
    if field == "area_min_m2":
        expected_number = _number(expected)
        values = _area_values(item)
        return expected_number is not None and bool(values) and max(values) >= expected_number
    if field == "area_max_m2":
        expected_number = _number(expected)
        values = _area_values(item)
        return expected_number is not None and bool(values) and min(values) <= expected_number
    if field == "rooms":
        expected_values = _room_tokens(expected)
        actual_values = _room_tokens(item.get("rooms")) if "rooms" in item else set()
        # Canonical OptionCard stores normalized structured formats here.
        if "room_formats" in item:
            actual_values |= _room_tokens(item.get("room_formats"))
        return bool(expected_values and actual_values and (actual_values & expected_values))
    if field == "location":
        actual_values = [
            str(item.get(key) or "").lower()
            for key in ("location", "location_id", "district")
            if item.get(key)
        ]
        expected_values = [str(value).lower() for value in (expected if isinstance(expected, list) else [expected])]
        return any(
            value in actual or actual in value
            for actual in actual_values
            for value in expected_values
        )
    if field == "ready":
        if _is_expected_delivered(expected):
            return _item_matches_delivered_ready(item)
        return item.get("ready") == expected
    if field == "finishing":
        expected_positive = expected is True or (isinstance(expected, str) and any(marker in expected.casefold().replace("ё", "е") for marker in ("с отдел", "есть отдел", "white box", "предчист", "чистов")))
        return bool(_finishing_tokens(item)) if expected_positive else item.get("finishing") == expected
    return field in item and item.get(field) == expected


def matches_hard_constraint(item: Mapping[str, Any], field: str, expected: Any) -> bool:
    """Public evidence-safe hard constraint matcher for canonical runtime cards.

    Keep callers on the one-way wire → canonical boundary without importing the
    private validator implementation directly. The matcher intentionally uses
    the same structured evidence rules as the V2 search contract.
    """

    canonical = dict(item)
    if "price_min" in canonical and "min_price" not in canonical:
        canonical["min_price"] = canonical.get("price_min")
    return _matches_hard(canonical, str(field), expected)
