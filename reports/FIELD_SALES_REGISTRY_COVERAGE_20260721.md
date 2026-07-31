# Field Sales Registry v1 — offline coverage audit

## Summary

- Registry fields: 35
- Adapter-reachable fields: 31
- Observed corpus fields: 31
- Reachable coverage: 100.0%
- Registry reachability: 88.6%
- Runtime/deploy/API/selector/services are not imported or called.

## Expected gaps

- `down_payment` — `structured_finance_missing`
- `house_link` — `provenance_only`
- `installment_months` — `structured_finance_missing`
- `mortgage_rate` — `structured_finance_missing`

## Domain coverage

| Domain | Registry | Reachable | Observed | Expected unreachable | Reachable coverage |
|---|---:|---:|---:|---|---:|
| `apartments` | 4 | 4 | 4 | — | 100.0% |
| `family` | 5 | 5 | 5 | — | 100.0% |
| `financing` | 4 | 1 | 1 | `down_payment`, `installment_months`, `mortgage_rate` | 100.0% |
| `investment` | 2 | 2 | 2 | — | 100.0% |
| `lots` | 7 | 6 | 6 | `house_link` | 100.0% |
| `parking` | 3 | 3 | 3 | — | 100.0% |
| `project` | 3 | 3 | 3 | — | 100.0% |
| `readiness` | 2 | 2 | 2 | — | 100.0% |
| `transport` | 1 | 1 | 1 | — | 100.0% |
| `yard_safety` | 4 | 4 | 4 | — | 100.0% |

## Case examples

### `family_yard_complete`

- Selected field IDs: `school`, `kindergarten`, `children_ground`, `park_near`, `water_near`, `ecology_rating`, `yard_without_cars`, `sports_ground`, `security`
- Safe combination IDs: `school_plus_kindergarten`, `park_plus_water`, `yard_without_cars_plus_security`
- Adapter unmapped names: —
- Lot selection: `not_requested`, house linkage diagnostic only: `no`

- Brief descriptor `school` — школа рядом: Можно отметить семейную логистику, если школа подтверждена в карточке.
- Brief descriptor `kindergarten` — детский сад рядом: Можно отметить удобство для семьи с маленьким ребёнком, если детский сад подтверждён.
- Brief descriptor `children_ground` — детская площадка: Можно отметить дворовую инфраструктуру для ребёнка, если площадка подтверждена.
- Brief descriptor `park_near` — парк рядом: только буквальный факт, без сценарной выгоды
- Brief descriptor `water_near` — водоём рядом: только буквальный факт, без сценарной выгоды
- Brief descriptor `ecology_rating` — экологический рейтинг: Можно назвать рейтинг как справочный показатель, если клиент спрашивает об окружении.
- Brief descriptor `yard_without_cars` — двор без машин: только буквальный факт, без сценарной выгоды
- Brief descriptor `sports_ground` — спортивная площадка: только буквальный факт, без сценарной выгоды
- Brief descriptor `security` — охрана: только буквальный факт, без сценарной выгоды
- Safe phrasing `school_plus_kindergarten`: Для семейного сценария в карточке указаны и школа, и детский сад; расстояния и места лучше уточнить отдельно.
- Safe phrasing `park_plus_water`: Для прогулок можно отметить, что в карточке указаны парк и вода рядом.
- Safe phrasing `yard_without_cars_plus_security`: По карточке есть двор без машин и охрана — это можно отметить как заявленные элементы дворового сценария.

### `finance_unstructured`

- Selected field IDs: `discount`
- Safe combination IDs: —
- Adapter unmapped names: `down_payment`, `installment_months`, `mortgage_rate`
- Lot selection: `not_requested`, house linkage diagnostic only: `no`

- Brief descriptor `discount` — скидка: Можно упомянуть скидку только как указанное условие и предложить проверить детали.

### `investment_counters_zero`

- Selected field IDs: `sales_count`, `ads_count`
- Safe combination IDs: —
- Adapter unmapped names: —
- Lot selection: `not_requested`, house linkage diagnostic only: `no`

- Brief descriptor `sales_count` — сделки ЕГРН: Можно назвать только сам счётчик сделок ЕГРН как справочный факт.
- Brief descriptor `ads_count` — объявления на витрине: Можно назвать только количество объявлений как справочный факт по витрине.

### `lot_first_with_house_diagnostic`

- Selected field IDs: `lot_full_price`, `lot_area`, `lot_floor`, `lot_rooms`, `lot_renovation`, `lot_status`
- Safe combination IDs: `exact_lot_price_area_floor`
- Adapter unmapped names: —
- Lot selection: `selected`, house linkage diagnostic only: `yes`

- Brief descriptor `lot_full_price` — полная цена лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_area` — площадь лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_floor` — этаж лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_rooms` — комнатность лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_renovation` — отделка лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_status` — статус лота: Можно использовать как служебную подсказку для уточнения доступности, если статус безопасно нормализован.
- Safe phrasing `exact_lot_price_area_floor`: По конкретному лоту известны полная цена, площадь и этаж — его можно сравнить предметно, а не по общему диапазону ЖК.

### `lot_second_without_house_diagnostic`

- Selected field IDs: `lot_full_price`, `lot_area`, `lot_floor`, `lot_rooms`, `lot_renovation`, `lot_status`
- Safe combination IDs: `exact_lot_price_area_floor`
- Adapter unmapped names: —
- Lot selection: `selected`, house linkage diagnostic only: `no`

- Brief descriptor `lot_full_price` — полная цена лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_area` — площадь лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_floor` — этаж лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_rooms` — комнатность лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_renovation` — отделка лота: только буквальный факт, без сценарной выгоды
- Brief descriptor `lot_status` — статус лота: Можно использовать как служебную подсказку для уточнения доступности, если статус безопасно нормализован.
- Safe phrasing `exact_lot_price_area_floor`: По конкретному лоту известны полная цена, площадь и этаж — его можно сравнить предметно, а не по общему диапазону ЖК.

### `parking_inventory_numeric_string`

- Selected field IDs: `parking`, `parking_price`, `parking_inventory`
- Safe combination IDs: —
- Adapter unmapped names: —
- Lot selection: `not_requested`, house linkage diagnostic only: `no`

- Brief descriptor `parking` — паркинг: Можно сказать, что паркинг заявлен, если признак подтверждён.
- Brief descriptor `parking_price` — цена паркинга: Можно назвать ориентир по цене машиноместа, если он свежий.
- Brief descriptor `parking_inventory` — наличие машиномест: Можно обсуждать паркинг предметно, если свежее наличие машиномест подтверждено.

### `project_general_transport`

- Selected field IDs: `developer`, `property_class`, `location`, `metro`, `readiness`, `finishing`, `apartment_price`, `room_formats`, `area`, `apartment_inventory`
- Safe combination IDs: `ready_plus_finishing`
- Adapter unmapped names: —
- Lot selection: `not_requested`, house linkage diagnostic only: `no`

- Brief descriptor `developer` — застройщик: Можно назвать, кто указан застройщиком, если клиент спрашивает об объекте или хочет сверить карточку.
- Brief descriptor `property_class` — класс проекта: только буквальный факт, без сценарной выгоды
- Brief descriptor `location` — локация: Помогает быстро понять, где находится ЖК, без подмены района внутренним кодом.
- Brief descriptor `metro` — метро: только буквальный факт, без сценарной выгоды
- Brief descriptor `readiness` — готовность дома: только буквальный факт, без сценарной выгоды
- Brief descriptor `finishing` — отделка: только буквальный факт, без сценарной выгоды
- Brief descriptor `apartment_price` — цена квартиры: только буквальный факт, без сценарной выгоды
- Brief descriptor `room_formats` — форматы квартир: только буквальный факт, без сценарной выгоды
- Brief descriptor `area` — площадь: только буквальный факт, без сценарной выгоды
- Brief descriptor `apartment_inventory` — наличие квартир: только буквальный факт, без сценарной выгоды
- Safe phrasing `ready_plus_finishing`: Дом указан как сданный, а отделка подтверждена в карточке — это может быть удобно, если не хочется ждать стройку.

## Boundaries

- Corpus is synthetic, PII-free and excludes denied outreach/runtime envelope categories.
- Finance text is used only to prove unmapped structured names; reports keep names/reasons only.
- `house_link` remains provenance-only and appears only as a boolean diagnostic about linkage availability.
- Reports are deterministic: sorted IDs, sorted domains and stable case ordering.

## Source refs

- `field_sales_registry/v1/coverage_corpus.json`
- `field_sales_registry/v1/coverage_audit.py`
- `field_sales_registry/v1/option_card_adapter.py`
- `field_sales_registry/v1/brief_builder.py`
- `field_sales_registry/v1/{project,apartments,readiness,transport,family,yard_safety,parking,financing,investment,lots}.json`
