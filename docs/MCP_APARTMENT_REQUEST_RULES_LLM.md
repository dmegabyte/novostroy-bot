# MCP apartment contract — LLM quick start

Это короткая инструкция для LLM. Полный нормативный контракт находится в
[`MCP_APARTMENT_REQUEST_RULES.md`](MCP_APARTMENT_REQUEST_RULES.md).

## 1. Твоя роль

Ты не выдумываешь объекты и не принимаешь решение по строкам `price_range` или
`description`. Твоя задача:

1. понять цель клиента;
2. вынести явные ограничения в structured request;
3. запросить только нужные MCP-факты;
4. вернуть JSON по response contract;
5. говорить клиенту только по подтверждённым MCP-фактам.

Единственный источник фактов: `novostroym/get_flat_info`.

## 2. Быстрое дерево решений

```text
Назван конкретный ЖК или задан вопрос о нём?
  ├─ да → search_mode="current_options_fact_check" или "named_object"
  │       + selected_option_name / fact_to_check
  └─ нет → это новый подбор: search_mode="broad"

Клиент просит «другие варианты»?
  └─ да → planner purpose="repeat_search"; в executable request:
          current_option_names + excluded_names

Есть «для семьи», «под аренду», «для инвестиций»?
  └─ назначь base viewpoint: family / rental / investment

Есть ипотека?
  └─ добавь mortgage facet/metadata и нужные finance facts; не заменяй им
          основной сценарий

Есть точное условие цены, комнат, площади, региона, готовности или отделки?
  └─ положи его в effective_hard, а не в preferences
```

## 3. Request: обязательный минимум

Перед MCP собери объект со следующими полями:

```json
{
  "search_goal": {
    "entity_type": "new_building_flat",
    "query_summary": "двухкомнатная квартира для семьи в Москве",
    "explicit_terms": ["2 комнаты", "Москва", "для семьи"]
  },
  "constraints": {
    "requested_hard": {
      "district": "msk",
      "rooms": [2]
    },
    "effective_hard": {
      "district": "msk",
      "rooms": [2]
    },
    "preferences": {},
    "relaxation_audit": []
  },
  "response_viewpoint": "family",
  "base_viewpoint": "family",
  "available_fact_fields": [
    "id", "name", "location", "rooms", "min_price", "max_price",
    "property_metro", "ready", "finishing", "school", "kindergarten",
    "park_near", "yard_without_cars"
  ],
  "count": 3,
  "excluded_names": [],
  "search_mode": "broad",
  "current_option_names": [],
  "facts_needed": ["prices", "area", "schools", "kindergartens", "parks"]
}
```

### Типы

- `min_price`, `max_price`, `area_min_m2`, `area_max_m2` — числа;
- `rooms` — канонические токены, например `s`, `1`, `2`, `3`;
- `district` — только `msk`, `newmsk` или `mo`;
- `price_range` и `area` — только display-поля, не evidence;
- `requested_hard` — что попросил клиент;
- `effective_hard` — что реально разрешено применить;
- `preferences` не превращаются в hard-фильтры;
- `relaxation_audit` не заполняется выдуманным ослаблением.

## 4. Специальные режимы

### Fact-check выбранного ЖК

Ниже показаны дополнительные поля режима; они добавляются к обязательному
envelope из раздела 3, а не заменяют его.

```json
{
  "search_mode": "current_options_fact_check",
  "current_option_names": ["ЖК «Лучи»"],
  "search_goal": {
    "entity_type": "new_building_flat",
    "query_summary": "проверить двор без машин",
    "explicit_terms": ["ЖК «Лучи»", "двор без машин"],
    "current_option_names": ["ЖК «Лучи»"],
    "facts_needed": ["yard_without_cars"],
    "scope_policy": "exact_current_option_names_only"
  },
  "facts_needed": ["yard_without_cars"]
}
```

На planner-уровне это соответствует `selected_option_name`; в executable
request используй только `current_option_names` и `search_goal` из примера.
Проверяй только выбранные `current_option_names`. Отсутствие поля означает
«не подтверждено», а не «да».

### Repeat search

Передай сохранённые ограничения и исключения поверх обязательного envelope:

```json
{
  "search_mode": "broad",
  "excluded_names": ["ЖК «Лучи»", "Второй Нагатинский"],
  "current_option_names": ["ЖК «Лучи»", "Второй Нагатинский"]
}
```

Не возвращай объекты из `excluded_names` повторно.

### Ипотека

Ипотека — facet/overlay. Для семейного запроса оставь
`base_viewpoint="family"`, добавь mortgage-поля в `facts_needed`. Если planner
распознал тип, храни `mortgage_type="family_mortgage"` в planner metadata, а не
добавляй это поле в executable request: его нет в request schema. Не обещай
доступность программы без structured evidence и eligibility.

## 5. Response: единственная допустимая форма

```json
{
  "facts": [],
  "near": [],
  "missing": [],
  "params": {},
  "diagnostics": {
    "mcp_tool": "novostroym/get_flat_info",
    "response_viewpoint": "family",
    "base_viewpoint": "family",
    "requested_field_priorities": [],
    "relaxation_audit": [],
    "ignored_preferences": [],
    "notes": []
  }
}
```

### Как раскладывать результаты

- `facts` — search-agent должен класть только точные совпадения со всеми
  `effective_hard` и structured evidence; runtime не удаляет уже полученную
  identifiable карточку при нарушении, а пишет report-only diagnostics;
- `near` — только близкие, но неточные варианты; обязательно `is_near: true`,
  `why_close` и непустой `differences[]`;
- `missing` — всегда массив; указывает недоступное или неподтверждённое поле;
- `params` — только принятые hard-поля и allowlisted preferences;
- `diagnostics` — служебные поля runtime, не клиентский ответ.

Пример near:

```json
{
  "name": "ЖК Почти",
  "location": "Москва",
  "is_near": true,
  "why_close": "цена немного выше бюджета",
  "differences": ["max_price"]
}
```

## 6. Do / Don’t

### Делай

- различай `requested_hard` и `effective_hard`;
- сохраняй exact facts отдельно от near;
- нормализуй legacy `missing` string/object в `string[]`;
- переноси legacy `why_close` в `differences[]`;
- не создавай дубли по `id`, иначе по `name + location`; production validator
  сообщает о них, но не скрывает карточки;
- при отсутствии результатов объясняй невыполненное условие;
- предлагай ослабление только явно и фиксируй его в `relaxation_audit`.

### Не делай

- не считай `effective_query` заменой structured request;
- не называй строковую цену доказательством hard matching;
- не помещай near в facts ради достижения `count`;
- не превращай отсутствие данных в подтверждение;
- не меняй молча регион, бюджет, комнаты или отделку;
- не добавляй в response `response`, `visible_options`, `action`, `target` и
  другие поля вне schema;
- не отвечай по памяти модели.

## 7. Проверка перед выдачей

1. Сначала проверь response JSON Schema.
2. Затем проверь semantic contract через
   `nmbot_v2.search_contract.validate_search_output()`.
3. Передай результат presenter независимо от `valid/degraded/invalid`:
   production validator report-only и не удаляет identifiable cards.
4. Не передавай malformed/unidentifiable карточку и не обходи exact named scope.

Schema проверяет форму. Python validator наблюдает смысл: hard matching,
evidence, exact/near, deduplication и relaxation invariants. Нарушения логируются
без raw query/values; блокирующими остаются только safety/schema и named scope.
