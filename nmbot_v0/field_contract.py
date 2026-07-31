from __future__ import annotations

V0_PRESENTATION_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "required_identity": ("name", "alias"),
    "price": ("min_price", "max_price", "novos.min_price", "novos.max_price", "ads.fullprice", "ads.price"),
    "selected_wire_roots": ("ads", "house", "apartment_types"),
    "location": ("location", "location_id", "district", "street"),
    "rooms_area": ("rooms", "ads.rooms", "apartment_types", "square_min", "square_max", "ads.area"),
    "readiness_finishing": ("finishing", "ads.renovation", "house.finishing_list", "ready", "delivered", "state", "status"),
    "property_class": ("new_building_class", "building_type"),
    "transport_developer": ("property_metro", "metro", "metro_line", "developer"),
    "family_life": ("school", "kindergarten", "park_near", "water_near", "children_ground", "sports_ground", "yard_without_cars", "security", "territory", "infrastructure"),
}
V0_PRESENTATION_FIELD_LIMIT = 44
V0_PRESENTATION_TRACE_FIELDS = frozenset(field for fields in V0_PRESENTATION_FIELD_GROUPS.values() for field in fields) | {
    "price", "price_min", "price_range", "area", "room_formats", "ready_quarter", "ecology_rating", "location_2.ecology_rating",
    "parking", "elevator", "apartment_inventory", "available_apartments", "flats_available",
}


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        marker = str(item)
        if marker not in seen:
            out.append(marker)
            seen.add(marker)
    return out


def v0_presentation_search_fields() -> list[str]:
    fields: list[str] = []
    for group in V0_PRESENTATION_FIELD_GROUPS.values():
        fields.extend(group)
    return _unique(fields)[:V0_PRESENTATION_FIELD_LIMIT]
