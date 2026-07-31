"""Model-facing scenario mechanics for dynamic response writing.

The registry below is not customer-ready prose.  It is a bounded semantic
corridor selected before the model sees the brief: active scenario, safe
OptionCard evidence, shared facts and per-card anchors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import JsonDict, LotExample, OptionCard
from .fact_context import family_education_evidence, present_fact_names


@dataclass(frozen=True)
class FactMechanic:
    communication_goal: str
    allowed_concepts: tuple[str, ...]
    forbidden_meanings: tuple[str, ...]


PRIMARY_SCENARIOS = frozenset({"life", "family", "investment", "rental"})


SCENARIO_MECHANICS: Mapping[str, Mapping[str, FactMechanic]] = {
    "life": {
        "metro": FactMechanic("Объяснить, какой ежедневный маршрут подтверждён.", ("станция", "способ пути", "время пути", "повседневные поездки"), ("престиж района", "ликвидность", "экономия времени без подтверждённого времени пути")),
        "readiness": FactMechanic("Объяснить подтверждённый статус дома или срок ожидания.", ("готовность дома", "стадия строительства", "срок сдачи"), ("выдача ключей", "немедленное заселение", "отсутствие задержек")),
        "finishing": FactMechanic("Объяснить, какой тип отделки подтверждён и какую подготовку он предполагает.", ("наличие или отсутствие отделки", "подтверждённый тип", "объём стартовых работ"), ("переезд сразу", "ремонт не нужен", "точная экономия")),
        "location": FactMechanic("Зафиксировать географический ориентир для личного маршрута.", ("район", "город", "направление", "личная география поиска"), ("преимущества района без evidence",)),
        "parks": FactMechanic("Объяснить только подтверждённые факты о прогулках и территории.", ("парк", "вода", "двор", "площадка", "охрана — только если есть в evidence"), ("экологичность", "безопасность района", "гарантированное качество среды")),
        "safe_yard": FactMechanic("Объяснить только подтверждённые факты о прогулках и территории.", ("двор", "площадка", "охрана — только если есть в evidence"), ("экологичность", "безопасность района", "гарантированное качество среды")),
        "daily_services": FactMechanic("Зафиксировать подтверждённые бытовые сервисы без вывода о близости.", ("магазины", "сервисы", "ритейл", "аптеки — только если есть в evidence"), ("рядом", "пешком", "удобно без evidence", "полная инфраструктура")),
        "healthcare": FactMechanic("Зафиксировать подтверждённые медицинские ориентиры без вывода о расстоянии.", ("клиника", "поликлиника", "аптека", "медицинская инфраструктура"), ("рядом", "пешком", "качество медицины", "безопасность для здоровья")),
        "ecology_rating": FactMechanic("Упомянуть только сам подтверждённый экологический рейтинг.", ("значение рейтинга", "источник карточки"), ("экологически чисто", "здоровая среда", "безопасность", "лучше для здоровья")),
        "transport_access": FactMechanic("Зафиксировать буквальный транспортно-географический ориентир.", ("железная дорога", "шоссе", "расстояние от МКАД"), ("экономия времени", "престиж", "спрос", "удобно без evidence")),
        "parking": FactMechanic("Зафиксировать подтверждённый факт паркинга без обещания мест.", ("наличие паркинга",), ("доступные места", "бронь", "дефицит", "гарантия покупки")),
        "area": FactMechanic("Помочь сопоставить подтверждённый формат с бытовой задачей.", ("диапазон площади", "доступные комнатности", "набор форматов"), ("конкретная планировка", "отдельные комнаты без evidence", "просторность без evidence")),
        "room_formats": FactMechanic("Помочь сопоставить подтверждённый формат с бытовой задачей.", ("диапазон площади", "доступные комнатности", "набор форматов"), ("конкретная планировка", "отдельные комнаты без evidence", "просторность без evidence")),
        "apartment_price": FactMechanic("Зафиксировать нижнюю ценовую точку для сравнения вариантов.", ("нижняя граница проекта", "стартовая цена как ориентир"), ("попадание в бюджет", "цена нужной квартиры", "скидка", "выгода")),
        "developer": FactMechanic("Зафиксировать нейтральный подтверждённый профиль проекта.", ("имя застройщика", "подтверждённый класс проекта"), ("надёжность застройщика", "премиальный образ жизни", "лучшее качество")),
        "property_class": FactMechanic("Зафиксировать нейтральный подтверждённый профиль проекта.", ("имя застройщика", "подтверждённый класс проекта"), ("надёжность застройщика", "премиальный образ жизни", "лучшее качество")),
    },
    "family": {
        "schools": FactMechanic("Объяснить, какая семейная инфраструктура подтверждена и чем она полезна при оценке ежедневной логистики.", ("школа", "детский сад", "подтверждённые семейные ориентиры"), ("качество школы", "гарантированное место", "близость или точное время пути без evidence")),
        "safe_yard": FactMechanic("Буквально назвать подтверждённый элемент двора без оценки его безопасности.", ("двор без машин", "детская площадка", "спортивная площадка", "охрана — только буквально"), ("безопаснее", "безопасный двор", "полная безопасность", "дети могут гулять без присмотра", "закрытая территория без evidence")),
        "parks": FactMechanic("Объяснить подтверждённую возможность для прогулок.", ("парк", "лес", "набережная", "вода рядом", "прогулочный ориентир"), ("гарантированная экология", "безопасность", "точная близость без evidence")),
        "daily_services": FactMechanic("Объяснить, какие бытовые сервисы подтверждены для ежедневной семейной логистики.", ("магазины", "сервисы", "ритейл", "аптеки — только если есть в evidence"), ("близко", "пешком", "всё необходимое", "качество сервиса")),
        "healthcare": FactMechanic("Зафиксировать подтверждённые медицинские ориентиры для семейной оценки.", ("клиника", "поликлиника", "аптека", "медицинская инфраструктура"), ("близко", "пешком", "качество медицины", "безопасность для детей")),
        "ecology_rating": FactMechanic("Упомянуть только сам подтверждённый экологический рейтинг.", ("значение рейтинга", "источник карточки"), ("экологически чисто", "здоровая среда", "безопасность", "лучше для детей")),
        "parking": FactMechanic("Зафиксировать подтверждённый факт паркинга как отдельный бытовой параметр.", ("наличие паркинга",), ("доступные места", "бронь", "дефицит", "гарантия покупки")),
        "metro": FactMechanic("Объяснить подтверждённый маршрут для семейных ежедневных поездок.", ("поездки по делам", "на работу и обратно", "станция и способ пути"), ("школа рядом только из-за метро", "престиж района", "короткая дорога без времени")),
        "readiness": FactMechanic("Объяснить подтверждённый семейный горизонт ожидания.", ("статус дома", "стадия строительства", "заявленный срок сдачи"), ("ключи", "можно сразу переехать", "точная дата заселения")),
        "finishing": FactMechanic("Объяснить, какой тип отделки подтверждён и какой объём бытовой подготовки он предполагает.", ("наличие или отсутствие отделки", "подтверждённый тип", "объём стартовых работ"), ("переезд быстрее", "ремонт не нужен", "квартира готова к жизни")),
        "area": FactMechanic("Помочь оценить подтверждённый семейный формат.", ("диапазон площади", "доступные комнатности", "набор форматов"), ("конкретные спальни", "детская", "кухня или удобная планировка без lot evidence")),
        "room_formats": FactMechanic("Помочь оценить подтверждённый семейный формат.", ("диапазон площади", "доступные комнатности", "набор форматов"), ("конкретные спальни", "детская", "кухня или удобная планировка без lot evidence")),
        "apartment_price": FactMechanic("Зафиксировать ценовую точку для семейного сравнения.", ("нижняя граница проекта", "стартовая цена как ориентир"), ("точно укладывается в семейный бюджет", "цена нужной квартиры", "будущие платежи")),
        "location": FactMechanic("Зафиксировать географию семейного поиска.", ("район", "город", "направление"), ("семейная инфраструктура по одному названию района",)),
    },
    "investment": {
        "apartment_price": FactMechanic("Зафиксировать подтверждённый порог входа для сравнения с другими проектами.", ("нижняя граница проекта", "место в текущем ценовом сравнении"), ("доходность", "окупаемость", "выгодная инвестиция", "будущая цена", "попадание в бюджет")),
        "sales_count": FactMechanic("Зафиксировать подтверждённый счётчик сделок без рыночных выводов.", ("число сделок/записей ЕГРН", "дата счётчика"), ("спрос", "ликвидность", "надёжность", "прогноз продаж")),
        "ads_count": FactMechanic("Зафиксировать текущий счётчик витрины без вывода о спросе.", ("число объявлений на момент данных",), ("дефицит", "спрос", "скорость продажи", "ликвидность")),
        "readiness": FactMechanic("Объяснить стадию проекта и строительный горизонт.", ("сдан", "строится", "срок", "отсутствие ожидания стройки у готового дома"), ("безрисковость", "рост цены", "быстрая перепродажа", "отсутствие задержек")),
        "room_formats": FactMechanic("Объяснить, какие форматы подтверждённо представлены.", ("подтверждённые комнатности", "площади", "lot examples"), ("самый востребованный формат", "легко сдать или продать", "будущая доступность")),
        "lot_examples": FactMechanic("Объяснить, какие форматы подтверждённо представлены.", ("подтверждённые комнатности", "площади", "lot examples"), ("самый востребованный формат", "легко сдать или продать", "будущая доступность")),
        "finishing": FactMechanic("Объяснить, какой тип отделки подтверждён и какую подготовку он предполагает.", ("наличие или отсутствие отделки", "подтверждённый тип", "объём стартовых работ"), ("готово к аренде", "точные вложения", "срок запуска")),
        "metro": FactMechanic("Зафиксировать подтверждённый транспортно-географический профиль.", ("станция", "способ пути", "время пути", "район или направление"), ("арендный спрос", "ликвидность", "рост цены", "престиж района")),
        "location": FactMechanic("Зафиксировать подтверждённый транспортно-географический профиль.", ("станция", "способ пути", "время пути", "район или направление"), ("арендный спрос", "ликвидность", "рост цены", "престиж района")),
        "transport_access": FactMechanic("Зафиксировать буквальную географию маршрута без рыночных выводов.", ("железная дорога", "шоссе", "расстояние от МКАД"), ("спрос", "ликвидность", "рост цены", "престиж", "экономия времени без времени пути")),
        "price_per_m2": FactMechanic("Зафиксировать подтверждённую цену за квадратный метр для сравнения.", ("цена за м²", "нейтральное сравнение"), ("выгодно", "доходность", "рост цены", "окупаемость")),
        "parking": FactMechanic("Зафиксировать подтверждённый факт паркинга без вывода о ликвидности.", ("наличие паркинга",), ("спрос", "ликвидность", "доступные места", "бронь", "дефицит")),
        "developer": FactMechanic("Зафиксировать подтверждённый профиль проекта.", ("имя застройщика", "класс проекта"), ("гарантия качества", "инвестиционный статус", "надёжность застройщика")),
        "property_class": FactMechanic("Зафиксировать подтверждённый профиль проекта.", ("имя застройщика", "класс проекта"), ("гарантия качества", "инвестиционный статус", "надёжность застройщика")),
    },
    "rental": {
        "room_formats": FactMechanic("Объяснить, какие форматы объекта подтверждены для дальнейшей подготовки под сдачу.", ("подтверждённые комнатности", "площадь", "набор форматов"), ("формат востребован", "его легко сдать", "конкретный профиль арендатора")),
        "area": FactMechanic("Объяснить, какие форматы объекта подтверждены для дальнейшей подготовки под сдачу.", ("подтверждённые комнатности", "площадь", "набор форматов"), ("формат востребован", "его легко сдать", "конкретный профиль арендатора")),
        "finishing": FactMechanic("Объяснить, какой тип отделки подтверждён и какую подготовку объекта он предполагает.", ("наличие или отсутствие отделки", "подтверждённый тип", "объём стартовых работ"), ("готово к аренде", "можно сразу заселить арендатора", "ремонт не нужен")),
        "readiness": FactMechanic("Объяснить подтверждённый временной горизонт до следующего этапа работы с объектом.", ("статус дома", "стадия строительства", "заявленный срок сдачи"), ("дата запуска аренды", "ключи", "доступность квартиры")),
        "metro": FactMechanic("Зафиксировать подтверждённый транспортный профиль без вывода о спросе.", ("станция", "время и способ пути, если подтверждены"), ("высокий арендный спрос", "ликвидность", "гарантированный интерес арендаторов")),
        "transport_access": FactMechanic("Зафиксировать буквальный транспортно-географический ориентир без вывода о спросе.", ("железная дорога", "шоссе", "расстояние от МКАД"), ("спрос арендаторов", "ликвидность", "престиж", "экономия времени без evidence")),
        "daily_services": FactMechanic("Зафиксировать подтверждённые бытовые сервисы без портрета арендатора.", ("магазины", "сервисы", "ритейл", "аптеки — только если есть в evidence"), ("востребованность", "легко сдать", "конкретный арендатор", "близость без evidence")),
        "price_per_m2": FactMechanic("Зафиксировать подтверждённую цену за квадратный метр для сравнения.", ("цена за м²", "нейтральное сравнение"), ("доходность", "окупаемость", "выгодно")),
        "parking": FactMechanic("Зафиксировать подтверждённый факт паркинга без вывода об арендаторах.", ("наличие паркинга",), ("спрос арендаторов", "доступные места", "бронь", "дефицит")),
        "apartment_price": FactMechanic("Зафиксировать нижнюю границу покупки.", ("нижняя граница проекта", "стартовая цена как ориентир"), ("окупаемость", "доходность", "цена подходящего лота", "попадание в бюджет")),
        "location": FactMechanic("Зафиксировать географический профиль объекта.", ("район", "город", "направление"), ("спрос арендаторов", "престиж без evidence")),
        "ads_count": FactMechanic("Зафиксировать текущий счётчик объявлений без рыночных выводов.", ("число объявлений на момент данных",), ("спрос", "конкуренция", "скорость сдачи")),
        "apartment_inventory": FactMechanic("Зафиксировать подтверждённое наличие форматов на момент свежего запроса.", ("буквальный inventory", "доступные форматы", "примеры лотов"), ("будущая доступность", "бронь", "стабильный выбор")),
        "lot_examples": FactMechanic("Зафиксировать подтверждённое наличие форматов на момент свежего запроса.", ("буквальный inventory", "доступные форматы", "примеры лотов"), ("будущая доступность", "бронь", "стабильный выбор")),
    },
}


OVERLAY_MECHANICS: Mapping[str, Mapping[str, FactMechanic]] = {
    "financing": {
        "mortgage_terms": FactMechanic("Зафиксировать только подтверждённые условия программы.", ("ставка", "взнос", "срок", "название программы — только если есть в evidence"), ("одобрение кредита", "доступность программы сейчас", "персональная ставка")),
        "mortgage_rate": FactMechanic("Зафиксировать только подтверждённую ставку.", ("ставка",), ("одобрение кредита", "доступность программы сейчас", "персональная ставка", "ежемесячный платёж")),
        "mortgage_down_payment": FactMechanic("Зафиксировать только подтверждённый первоначальный взнос.", ("первоначальный взнос",), ("одобрение кредита", "персональные условия", "ежемесячный платёж")),
        "mortgage_term": FactMechanic("Зафиксировать только подтверждённый срок ипотеки.", ("срок",), ("одобрение кредита", "персональные условия", "ежемесячный платёж")),
        "installment_months": FactMechanic("Зафиксировать только подтверждённый срок рассрочки.", ("срок рассрочки",), ("одобрение", "доступность без проверки", "персональное право клиента на рассрочку")),
        "discount": FactMechanic("Зафиксировать только подтверждённое условие скидки.", ("размер скидки", "условие применения", "источник подтверждения"), ("гарантированное применение", "срок действия без evidence")),
        "payment_by_installments": FactMechanic("Зафиксировать подтверждённую схему рассрочки.", ("платежи", "сроки", "порядок оплаты — только если есть в evidence"), ("одобрение", "доступность без проверки", "персональное право клиента на рассрочку")),
        "apartment_price": FactMechanic("Зафиксировать основу для расчёта, но не сам расчёт.", ("нижняя граница проекта", "точная цена lot example"), ("первый взнос/платёж без расчётного evidence", "одобрение ипотеки")),
    }
}


SELECTED_DETAIL_MECHANICS: Mapping[str, FactMechanic] = {
    "parking_price": FactMechanic("Зафиксировать буквальную цену паркинга.", ("цена паркинга", "единица цены из evidence"), ("наличие места", "бронь", "дефицит", "можно купить сейчас")),
    "parking_inventory": FactMechanic("Зафиксировать буквальный свежий счётчик мест.", ("число мест", "свежий счётчик"), ("будущая доступность", "бронь", "дефицит", "гарантия места")),
    "room_specific_price": FactMechanic("Зафиксировать буквальную комнатность и цену.", ("метка комнатности", "цена этой метки"), ("есть в продаже сейчас", "подходит по бюджету", "это выбранный лот", "доступность без lot evidence")),
    "price_per_m2": FactMechanic("Зафиксировать буквальную цену за квадратный метр.", ("цена за м²", "нейтральное сравнение"), ("выгода", "доходность", "рост цены", "окупаемость")),
    "recurring_costs": FactMechanic("Зафиксировать буквальный регулярный или коммунальный платёж.", ("ставка платежа", "единица измерения"), ("полная стоимость владения в месяц", "итоговый платёж", "персональный расчёт")),
    "purchase_terms": FactMechanic("Зафиксировать буквальные флаги условий покупки.", ("trade-in", "эскроу", "214-ФЗ"), ("юридическая консультация", "гарантия сделки", "доступность программы", "персональные условия")),
    "building_profile": FactMechanic("Зафиксировать буквальный профиль здания.", ("этажность", "лифт", "тип здания", "высота потолков"), ("качество", "комфорт", "надёжность", "доступная среда без отдельного evidence")),
    "property_formats": FactMechanic("Зафиксировать буквальные подтверждённые типы форматов.", ("тип объекта", "формат"), ("текущий выбор", "наличие в продаже", "доступность")),
    "lot_examples": FactMechanic("Зафиксировать буквальные факты из примеров лотов.", ("комнатность", "площадь", "этаж", "цена", "планировочные признаки"), ("наличие сейчас", "бронь", "репрезентативность для всего проекта", "гарантия выбора")),
}


SCENARIO_PRIORITY: Mapping[str, tuple[str, ...]] = {
    "life": ("metro", "daily_services", "healthcare", "readiness", "finishing", "location", "transport_access", "parks", "safe_yard", "ecology_rating", "parking", "area", "room_formats", "apartment_price", "developer", "property_class"),
    "family": ("schools", "safe_yard", "parks", "daily_services", "healthcare", "metro", "readiness", "finishing", "ecology_rating", "parking", "area", "room_formats", "apartment_price", "location"),
    "investment": ("apartment_price", "price_per_m2", "sales_count", "ads_count", "readiness", "transport_access", "room_formats", "finishing", "metro", "location", "developer", "property_class"),
    "rental": ("room_formats", "area", "finishing", "readiness", "metro", "daily_services", "transport_access", "apartment_price", "price_per_m2", "location", "parking", "ads_count", "apartment_inventory"),
}

OVERLAY_PRIORITY: Mapping[str, tuple[str, ...]] = {"financing": ("mortgage_rate", "mortgage_down_payment", "mortgage_term", "installment_months", "mortgage_terms", "discount", "payment_by_installments", "apartment_price")}
NEUTRAL_FALLBACK_PRIORITY = ("metro", "location", "readiness", "finishing", "area", "property_class", "developer", "apartment_price")
SHARED_FACT_CANDIDATES = ("location", "readiness", "finishing")


def build_scenario_context(*, cards: tuple[OptionCard, ...] | list[OptionCard], primary_scenario: str | None, facets: tuple[str, ...] | list[str] = (), overlay: str | None = None, presentation_scope: str = "shortlist", requested_facts: tuple[str, ...] | list[str] = ()) -> JsonDict:
    active_cards = tuple(cards or ())[:3]
    scenario = _normalize_primary(primary_scenario)
    active_overlay = _normalize_overlay(overlay)
    normalized_facets = tuple(dict.fromkeys(_norm_text(item) for item in facets if _norm_text(item)))
    shared_facts = _shared_facts(active_cards)
    shared_names = {str(item.get("fact") or "") for item in shared_facts}
    primary_rules = SCENARIO_MECHANICS[scenario]
    overlay_rules = OVERLAY_MECHANICS.get(active_overlay or "", {})
    priority = _priority_for(scenario, normalized_facets)
    scope = "selected" if _norm_text(presentation_scope) in {"selected", "detail", "details"} else "shortlist"
    requested_detail_facts = _requested_detail_facts(requested_facts)
    used: set[str] = set()
    overlay_used: set[str] = set()
    out_cards: list[JsonDict] = []
    for card in active_cards:
        available = [fact for fact in priority if fact not in shared_names and _evidence_for_fact(card, fact)]
        anchor = next((fact for fact in available if fact not in used), "") or (available[0] if available else "")
        if anchor:
            used.add(anchor)
        mechanic = primary_rules.get(anchor) or _fallback_mechanic(anchor)
        overlay_available = [
            fact
            for fact in OVERLAY_PRIORITY.get(active_overlay or "", ())
            if fact not in shared_names and fact != anchor and _evidence_for_fact(card, fact)
        ]
        overlay_anchor = next((fact for fact in overlay_available if fact not in overlay_used), "") or (
            overlay_available[0] if overlay_available else ""
        )
        if overlay_anchor:
            overlay_used.add(overlay_anchor)
        overlay_mechanic = overlay_rules.get(overlay_anchor) or _fallback_mechanic(overlay_anchor)
        item = {
            "card_name": card.name,
            "base_facts": _base_facts(card, anchor=anchor, shared_names=shared_names),
            "anchor_fact": anchor,
            "evidence": _bounded_evidence(_evidence_for_fact(card, anchor)) if anchor else [],
            "communication_goal": mechanic.communication_goal if anchor else "",
            "allowed_concepts": list(mechanic.allowed_concepts) if anchor else [],
            "forbidden_meanings": list(mechanic.forbidden_meanings) if anchor else [],
            "overlay_angle": {
                "anchor_fact": overlay_anchor,
                "evidence": _bounded_evidence(_evidence_for_fact(card, overlay_anchor)) if overlay_anchor else [],
                "communication_goal": overlay_mechanic.communication_goal if overlay_anchor else "",
                "allowed_concepts": list(overlay_mechanic.allowed_concepts) if overlay_anchor else [],
                "forbidden_meanings": list(overlay_mechanic.forbidden_meanings) if overlay_anchor else [],
            } if overlay_anchor else {},
            "card_mode": "near" if card.is_near else "bounded",
        }
        if scope == "selected":
            details = _selected_detail_facts(card, anchor=anchor, shared_names=shared_names, requested_facts=requested_detail_facts)
            if details:
                item["detail_facts"] = details
        out_cards.append(item)
    return {
        "content_source": "scenario_context_only",
        "primary_scenario": scenario,
        "facets": list(normalized_facets),
        "overlay": active_overlay,
        "presentation_scope": scope,
        "shared_facts": shared_facts,
        "cards": out_cards,
    }


def _normalize_primary(value: str | None) -> str:
    text = _norm_text(value)
    return text if text in PRIMARY_SCENARIOS else "life"


def _normalize_overlay(value: str | None) -> str | None:
    text = _norm_text(value)
    return "financing" if text in {"financing", "mortgage"} else None


def _norm_text(value: Any) -> str:
    return str(value or "").strip().casefold().replace("ё", "е")


def _priority_for(scenario: str, facets: tuple[str, ...]) -> tuple[str, ...]:
    boosted: list[str] = []
    facet_text = " ".join(facets)
    facet_boosts = {
        "metro": ("metro", "transport", "route"),
        "schools": ("school", "family", "дет", "сад", "школ"),
        "parks": ("park", "green", "eco", "парк", "лес"),
        "safe_yard": ("yard", "двор", "безопас"),
        "daily_services": ("shop", "service", "retail", "магаз", "сервис", "ритейл", "аптек"),
        "healthcare": ("clinic", "medical", "health", "клиник", "поликлиник", "медицин"),
        "ecology_rating": ("eco", "ecology", "эколог", "рейтинг"),
        "parking": ("parking", "паркинг", "парков"),
        "transport_access": ("railway", "highway", "mkad", "жд", "ж/д", "шоссе", "мкад"),
        "price_per_m2": ("price_square", "м2", "м²", "метр"),
        "mortgage_terms": ("mortgage", "finance", "ипот", "кредит"),
        "discount": ("discount", "скид"),
        "readiness": ("ready", "срок", "сдач"),
        "finishing": ("finish", "отдел"),
        "apartment_price": ("price", "budget", "цен", "бюдж"),
    }
    for fact, tokens in facet_boosts.items():
        if any(token in facet_text for token in tokens):
            boosted.append(fact)
    base = list(SCENARIO_PRIORITY[scenario])
    return tuple(dict.fromkeys([*boosted, *base, *NEUTRAL_FALLBACK_PRIORITY]))


def _shared_facts(cards: tuple[OptionCard, ...]) -> list[JsonDict]:
    if len(cards) < 2:
        return []
    shared: list[JsonDict] = []
    for fact in SHARED_FACT_CANDIDATES:
        evidences = [_evidence_for_fact(card, fact) for card in cards]
        if all(evidences) and len({repr(items) for items in evidences}) == 1:
            shared.append({"fact": fact, "evidence": _bounded_evidence(evidences[0]), "scope": "all_cards"})
    return shared


def _fallback_mechanic(fact: str) -> FactMechanic:
    return FactMechanic("Зафиксировать только подтверждённый факт без дополнительного вывода.", (fact,), ("вывод без evidence",))


def _base_facts(card: OptionCard, *, anchor: str, shared_names: set[str]) -> list[JsonDict]:
    """Return the complete, bounded factual material for a shortlist headline.

    The prose model must not choose extra fields from the full OptionCard. The
    scenario anchor owns the description; this list owns only neutral identity
    context and a safe project price boundary.
    """

    out: list[JsonDict] = []
    for fact in ("location", "apartment_price"):
        if fact == anchor or fact in shared_names:
            continue
        evidence = _bounded_evidence(_evidence_for_fact(card, fact))
        if evidence:
            out.append({"fact": fact, "evidence": evidence})
    return out


def _selected_detail_facts(card: OptionCard, *, anchor: str, shared_names: set[str], requested_facts: tuple[str, ...] = ()) -> list[JsonDict]:
    detail_order = (
        "parking_price", "parking_inventory", "room_specific_price", "price_per_m2",
        "recurring_costs", "purchase_terms", "building_profile", "property_formats", "lot_examples",
    )
    ordered = tuple(dict.fromkeys([*requested_facts, *detail_order]))
    out: list[JsonDict] = []
    for fact in ordered:
        if fact == anchor or fact in shared_names:
            continue
        evidence = _bounded_evidence(_evidence_for_fact(card, fact))
        if evidence:
            mechanic = SELECTED_DETAIL_MECHANICS[fact]
            out.append({
                "fact": fact,
                "evidence": evidence,
                "communication_goal": mechanic.communication_goal,
                "allowed_concepts": list(mechanic.allowed_concepts),
                "forbidden_meanings": list(mechanic.forbidden_meanings),
            })
        if len(out) >= 5:
            break
    return out


def _requested_detail_facts(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    raw = values if isinstance(values, (list, tuple)) else ()
    allowed = set(SELECTED_DETAIL_MECHANICS)
    out: list[str] = []
    for item in raw:
        fact = _norm_text(item)
        # Conservative by design: only exact canonical detail facts are allowed.
        # Do not expand broad subjects such as "parking" into parking_price or
        # parking_inventory; that would invent detail intent not present in the
        # executable request.
        if fact in allowed and fact not in out:
            out.append(fact)
    return tuple(out)


def _evidence_for_fact(card: OptionCard, fact: str) -> list[Any]:
    if fact == "apartment_price":
        if card.price_min is not None:
            return [{"kind": "project_lower_bound", "value": card.price_min, "safe_meaning": "нижняя цена проекта, не цена нужной комнатности и не попадание в бюджет"}]
        return [{"kind": "project_price_label", "value": _safe_scalar(card.price), "safe_meaning": "ценовой ориентир проекта"}] if card.price else []
    if fact == "location":
        return [_safe_scalar(card.location)] if card.location else []
    if fact == "readiness":
        return [_safe_scalar(card.ready)] if card.ready else []
    if fact == "finishing":
        return [_safe_scalar(card.finishing)] if card.finishing else []
    if fact == "metro":
        return [_safe_scalar(card.metro)] if card.metro else []
    if fact == "transport_access":
        return [_safe_scalar(item) for item in card.transport_access[:3] if _safe_scalar(item)]
    if fact == "developer":
        return [_safe_scalar(card.developer)] if card.developer else []
    if fact == "property_class":
        return [_safe_scalar(card.property_class)] if card.property_class else []
    if fact == "area":
        return [_safe_scalar(card.area)] if card.area else []
    if fact == "room_formats":
        return [_safe_scalar(item) for item in card.room_formats[:3] if _safe_scalar(item)]
    if fact == "schools":
        return [_safe_scalar(item) for item in family_education_evidence(card)[:3]]
    if fact == "daily_services":
        return [_safe_scalar(item) for item in card.daily_services[:3] if _safe_scalar(item)]
    if fact == "healthcare":
        return [_safe_scalar(item) for item in card.healthcare[:3] if _safe_scalar(item)]
    if fact == "ecology_rating" and card.ecology_rating not in (None, ""):
        return [{"kind": "ecology_rating", "value": _safe_scalar(card.ecology_rating), "safe_meaning": "только значение рейтинга, без вывода о здоровье или безопасности"}]
    if fact == "parks":
        return [_safe_scalar(item) for item in card.infrastructure if _has_any(item, ("парк", "лес", "лесопарк", "сквер", "набереж", "вод", "река", "озер", "пруд", "park", "forest", "water"))][:3]
    if fact == "safe_yard":
        return [_safe_scalar(item) for item in card.infrastructure if _has_any(item, ("двор", "площад", "охран", "без машин", "закрыт", "yard", "playground", "security"))][:3]
    if fact == "sales_count" and card.sales_count is not None:
        out: JsonDict = {"count": card.sales_count}
        if card.sales_date:
            out["date"] = card.sales_date
        return [out]
    if fact == "ads_count" and card.ads_count is not None:
        return [{"count": card.ads_count}]
    if fact == "apartment_inventory" and card.apartment_inventory not in (None, "", False):
        return [_safe_scalar(card.apartment_inventory)]
    if fact == "mortgage_terms":
        return [_safe_scalar(card.mortgage_terms)] if card.mortgage_terms else []
    if fact == "mortgage_rate" and card.mortgage_rate not in (None, ""):
        return [{"kind": "mortgage_rate", "value": _safe_scalar(card.mortgage_rate)}]
    if fact == "mortgage_down_payment" and card.mortgage_down_payment not in (None, ""):
        return [{"kind": "mortgage_down_payment", "value": _safe_scalar(card.mortgage_down_payment)}]
    if fact == "mortgage_term" and card.mortgage_term not in (None, ""):
        return [{"kind": "mortgage_term", "value": _safe_scalar(card.mortgage_term)}]
    if fact == "installment_months" and card.installment_months not in (None, ""):
        return [{"kind": "installment_months", "value": _safe_scalar(card.installment_months)}]
    if fact == "discount":
        return [_safe_scalar(card.discount)] if card.discount else []
    if fact == "parking" and card.parking not in (None, "", False):
        return [{"kind": "parking_exists", "value": _safe_scalar(card.parking), "safe_meaning": "факт паркинга, не наличие мест"}]
    if fact == "parking_price" and card.parking_price not in (None, ""):
        return [{"kind": "parking_price", "value": _safe_scalar(card.parking_price), "safe_meaning": "цена паркинга, не доступность места"}]
    if fact == "parking_inventory" and card.parking_inventory not in (None, ""):
        return [{"kind": "parking_inventory", "value": _safe_scalar(card.parking_inventory), "safe_meaning": "счётчик мест, без обещания брони"}]
    if fact == "room_specific_price":
        return [{"kind": "room_price", **dict(item)} for item in card.room_prices[:3]]
    if fact == "price_per_m2" and card.price_square not in (None, ""):
        return [{"kind": "price_per_m2", "value": _safe_scalar(card.price_square)}]
    if fact in {"recurring_costs", "purchase_terms", "building_profile", "property_formats"}:
        value = getattr(card, fact, None)
        if isinstance(value, tuple):
            return [_safe_scalar(item) for item in value[:3] if _safe_scalar(item)]
        return [_safe_scalar(value)] if value not in (None, "") else []
    if fact == "lot_examples":
        return [_lot_to_evidence(lot) for lot in card.lot_examples[:3]]
    return [fact] if fact in present_fact_names(card) else []


def _lot_to_evidence(lot: LotExample) -> JsonDict:
    out: JsonDict = {}
    for key in ("rooms", "area_m2", "living_space", "kitchen_area", "floor", "floors_total", "full_price", "renovation", "status", "house_name", "balcony", "bathroom", "ceiling_height", "window_view"):
        value = getattr(lot, key, None)
        if value not in (None, ""):
            out[key] = _safe_scalar(value)
    if lot.layout_features:
        out["layout_features"] = ", ".join(str(item)[:60] for item in lot.layout_features[:3])
    return out


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    return str(value)[:160]


def _bounded_evidence(items: list[Any]) -> list[Any]:
    bounded: list[Any] = []
    for item in items[:3]:
        if isinstance(item, Mapping):
            bounded.append({str(key)[:64]: _safe_scalar(value) for key, value in list(item.items())[:8]})
        else:
            bounded.append(_safe_scalar(item))
    return bounded


def _has_any(value: Any, tokens: tuple[str, ...]) -> bool:
    text = _norm_text(value)
    return any(token in text for token in tokens)
