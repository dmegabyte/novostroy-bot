# MCP topic coverage results — 2026-07-13

Generated: `2026-07-13T10:35:41+00:00`

Source query map: `reports/mcp_topic_coverage_queries_20260713.md`

Cases run: **23** / 23. Parsed OK: **23**.

## Summary table

| # | Theme | OK | facts | near | expected fields found | raw |
|---:|---|---:|---:|---:|---|---|
| 1 | Подбор квартиры / ЖК | yes | 3 | 0 | name, price, price_range, area, metro, ready, location | `01_Подбор_квартиры_ЖК.stdout.txt` |
| 2 | Бюджет / цена | yes | 5 | 0 | name, price, price_range, area, location | `02_Бюджет_цена.stdout.txt` |
| 3 | Метро / локация / район | yes | 5 | 0 | metro, location, price_range, area | `03_Метро_локация_район.stdout.txt` |
| 4 | Срок сдачи / готовность | yes | 5 | 0 | ready, price_range | `04_Срок_сдачи_готовность.stdout.txt` |
| 5 | Отделка / ремонт | yes | 5 | 0 | finishing, finish, price_range, area, name | `05_Отделка_ремонт.stdout.txt` |
| 6 | Семья / дети / инфраструктура | yes | 1 | 3 | school, kindergarten, family_infrastructure, infrastructure, area, price_range | `06_Семья_дети_инфраструктура.stdout.txt` |
| 7 | Выбор конкретного ЖК | yes | 1 | 0 | Мичуринский, price_range, area, metro, schools, kindergartens, parks, shops, house | `07_Выбор_конкретного_ЖК.stdout.txt` |
| 8 | Сравнение / рекомендация | yes | 0 | 0 | — | `08_Сравнение_рекомендация.stdout.txt` |
| 9 | Оператор / менеджер | yes | 0 | 0 | — | `09_Оператор_менеджер.stdout.txt` |
| 10 | Телефон / заявка | yes | 0 | 0 | — | `10_Телефон_заявка.stdout.txt` |
| 11 | Ипотека / финансирование | yes | 1 | 0 | mortgage, mortgage_calc, price_range, area | `11_Ипотека_финансирование.stdout.txt` |
| 12 | Семейная ипотека | yes | 1 | 0 | mortgage, mortgage_calc, price_range, area, Мичуринский | `12_Семейная_ипотека.stdout.txt` |
| 13 | Первоначальный взнос | yes | 1 | 0 | mortgage, mortgage_calc, price_range, area | `13_Первоначальный_взнос.stdout.txt` |
| 14 | Рассрочка / условия оплаты | yes | 1 | 0 | installment, discount, price_range, area | `14_Рассрочка_условия_оплаты.stdout.txt` |
| 15 | Инвестиции | yes | 4 | 1 | price_range, area, property_metro, mortgage | `15_Инвестиции.stdout.txt` |
| 16 | Аренда | yes | 5 | 0 | price_range, area, property_metro, finishing | `16_Аренда.stdout.txt` |
| 17 | Студия | yes | 5 | 0 | rooms, price_range, area | `17_Студия.stdout.txt` |
| 18 | Однокомнатная | yes | 1 | 0 | rooms, 1, price_range, area | `18_Однокомнатная.stdout.txt` |
| 19 | Двухкомнатная | yes | 1 | 2 | rooms, 2, price_range, area | `19_Двухкомнатная.stdout.txt` |
| 20 | Трехкомнатная+ | yes | 0 | 0 | rooms, 3 | `20_Трехкомнатная.stdout.txt` |
| 21 | Отказ / изменение условий | yes | 5 | 0 | price_range, area, ready_quarter | `21_Отказ_изменение_условий.stdout.txt` |
| 22 | Ошибка / fallback | yes | 0 | 0 | facts, near, missing | `22_Ошибка_fallback.stdout.txt` |
| 23 | Прочее / нераспознано | yes | 0 | 0 | — | `23_Прочее_нераспознано.stdout.txt` |

## Details

### 1. Подбор квартиры / ЖК

Query: `Подбери несколько новостроек в Москве до 18 млн, чтобы были понятны цены, площади, метро и готовность.`

Params:

```json
{
  "purpose": "search",
  "need": [
    "prices",
    "area",
    "property_metro",
    "stage",
    "ready_quarter",
    "house"
  ],
  "count": 3
}
```

Return code: `0`, parse_ok: `True`, facts: `3`, near: `0`, missing: `0`.

Expected fields: name, price, price_range, area, property_metro, metro, ready, stage, ready_quarter, house, location

Found expected fields: name, price, price_range, area, metro, ready, location

Observed keys sample: area, facts, facts.area, facts.finishing, facts.link, facts.location, facts.name, facts.price_range, facts.ready, finishing, link, location, missing, name, near, params, params.max_price, price_range, ready

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/01_Подбор_квартиры_ЖК.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/01_Подбор_квартиры_ЖК.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126466: статус=processing (3с)
[OK] Задача создана: id=2126475
[WAIT] Задача 2126475: статус=queued (0с)
[WAIT] Задача 2126475: статус=processing (3с)
[OK] Задача создана: id=2126478
[WAIT] Задача 2126478: статус=queued (0с)
[WAIT] Задача 2126478: статус=processing (3с)
📊 tokens_used=29905 (cost: or_cost.py)
```

### 2. Бюджет / цена

Query: `Покажи варианты до 15 млн, где видно цену и площадь.`

Params:

```json
{
  "purpose": "search",
  "need": [
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `0`.

Expected fields: name, price, price_range, min_price, area, location

Found expected fields: name, price, price_range, area, location

Observed keys sample: area, facts, facts.area, facts.finishing, facts.link, facts.location, facts.name, facts.price_range, facts.ready, finishing, link, location, max_price, missing, name, near, params, params.max_price, price_range, ready

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/02_Бюджет_цена.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/02_Бюджет_цена.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126466: статус=processing (3с)
[OK] Задача создана: id=2126475
[WAIT] Задача 2126475: статус=queued (0с)
[WAIT] Задача 2126475: статус=processing (3с)
[OK] Задача создана: id=2126478
[WAIT] Задача 2126478: статус=queued (0с)
[WAIT] Задача 2126478: статус=processing (3с)
📊 tokens_used=29905 (cost: or_cost.py)
```

### 3. Метро / локация / район

Query: `Найди варианты рядом с метро, чтобы были понятны метро, время пешком, цены и площади.`

Params:

```json
{
  "purpose": "search",
  "facets": [
    "metro"
  ],
  "need": [
    "property_metro",
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `2`.

Expected fields: property_metro, metro, walk_minutes, transport_minutes, location, price_range, area

Found expected fields: metro, location, price_range, area

Observed keys sample: area, count, facts, facts.area, facts.id, facts.location, facts.metro, facts.name, facts.on_foot, facts.price_range, id, location, metro, missing, name, near, on_foot, params, params.count, price_range

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/03_Метро_локация_район.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/03_Метро_локация_район.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126481: статус=queued (0с)
[WAIT] Задача 2126481: статус=processing (3с)
[OK] Задача создана: id=2126484
[WAIT] Задача 2126484: статус=queued (0с)
[OK] Задача создана: id=2126485
[WAIT] Задача 2126485: статус=queued (0с)
[WAIT] Задача 2126485: статус=processing (3с)
📊 tokens_used=25650 (cost: or_cost.py)
```

### 4. Срок сдачи / готовность

Query: `Покажи готовые или скоро сдающиеся варианты с ключами, сроками и ценами.`

Params:

```json
{
  "purpose": "search",
  "facets": [
    "readiness"
  ],
  "need": [
    "delivered_houses",
    "under_construction_houses",
    "keys_handover",
    "settlement_info",
    "stage",
    "ready_quarter",
    "project_ready_secondary",
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `2`.

Expected fields: ready, stage, ready_quarter, keys, keys_handover, settlement_info, delivered, price_range, area

Found expected fields: ready, price_range

Observed keys sample: developer, facets, facts, facts.developer, facts.finishing, facts.link, facts.location, facts.name, facts.price_range, facts.ready, finishing, link, location, missing, name, near, params, params.facets, params.purpose, price_range, purpose, ready

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/04_Срок_сдачи_готовность.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/04_Срок_сдачи_готовность.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126490: статус=processing (3с)
[OK] Задача создана: id=2126494
[WAIT] Задача 2126494: статус=queued (0с)
[WAIT] Задача 2126494: статус=processing (3с)
[OK] Задача создана: id=2126495
[WAIT] Задача 2126495: статус=queued (0с)
[WAIT] Задача 2126495: статус=processing (3с)
📊 tokens_used=26162 (cost: or_cost.py)
```

### 5. Отделка / ремонт

Query: `Найди квартиры с отделкой, чтобы были цены и площади.`

Params:

```json
{
  "purpose": "search",
  "facets": [
    "finishing"
  ],
  "need": [
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `0`.

Expected fields: finishing, finish, price_range, area, name

Found expected fields: finishing, finish, price_range, area, name

Observed keys sample: area, developer, facets, facts, facts.area, facts.developer, facts.finishing, facts.location, facts.name, facts.price_range, facts.ready, finishing, location, missing, name, near, params, params.facets, price_range, ready

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/05_Отделка_ремонт.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/05_Отделка_ремонт.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126500: статус=queued (0с)
[WAIT] Задача 2126500: статус=processing (3с)
[OK] Задача создана: id=2126504
[WAIT] Задача 2126504: статус=queued (0с)
[WAIT] Задача 2126504: статус=processing (3с)
[OK] Задача создана: id=2126505
[WAIT] Задача 2126505: статус=queued (0с)
📊 tokens_used=25974 (cost: or_cost.py)
```

### 6. Семья / дети / инфраструктура

Query: `Подбери варианты для семьи с детьми: школы, детские сады, парки, магазины, метро, цены и площади.`

Params:

```json
{
  "purpose": "family",
  "facets": [
    "infrastructure"
  ],
  "need": [
    "schools",
    "kindergartens",
    "parks",
    "shops",
    "family_infrastructure",
    "property_metro",
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `3`, missing: `1`.

Expected fields: schools, school, kindergartens, kindergarten, parks, shops, family_infrastructure, infrastructure, property_metro, area, price_range

Found expected fields: school, kindergarten, family_infrastructure, infrastructure, area, price_range

Observed keys sample: area, children_ground, developer, facets, facts, facts.area, facts.developer, facts.family_infrastructure, facts.family_infrastructure.children_ground, facts.family_infrastructure.kindergarten, facts.family_infrastructure.park_near, facts.family_infrastructure.school, facts.family_infrastructure.sports_ground, facts.family_infrastructure.yard_without_cars, facts.finishing, facts.link, facts.location, facts.metro, facts.name, facts.price_range, facts.ready, facts.why_family, family_infrastructure, finishing, kindergarten, link, location, metro, missing, name, near, near.finishing, near.location, near.name, near.price_range, near.why_close, params, params.facets, params.purpose, park_near, price_range, purpose, ready, school, sports_ground, why_close, why_family, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/06_Семья_дети_инфраструктура.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/06_Семья_дети_инфраструктура.stderr.txt`

stderr tail:

```text
[OK] Задача создана: id=2126509
[WAIT] Задача 2126509: статус=queued (0с)
[WAIT] Задача 2126509: статус=processing (3с)
[OK] Задача создана: id=2126514
[WAIT] Задача 2126514: статус=queued (0с)
[OK] Задача создана: id=2126516
[WAIT] Задача 2126516: статус=queued (0с)
📊 tokens_used=66198 (cost: or_cost.py)
```

### 7. Выбор конкретного ЖК

Query: `Проверь подробности по ЖК Мичуринский парк: цены, площади, метро, инфраструктура, готовность и корпуса.`

Params:

```json
{
  "purpose": "fact_check",
  "selected_option_name": "Мичуринский парк",
  "fact_to_check": "details",
  "need": [
    "prices",
    "area",
    "property_metro",
    "schools",
    "kindergartens",
    "parks",
    "shops",
    "stage",
    "ready_quarter",
    "house"
  ]
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `0`.

Expected fields: Мичуринский, price_range, area, property_metro, metro, schools, kindergartens, parks, shops, stage, ready_quarter, house

Found expected fields: Мичуринский, price_range, area, metro, schools, kindergartens, parks, shops, house

Observed keys sample: area_range, children_ground, developer, facts, facts.area_range, facts.developer, facts.finishing, facts.houses_info, facts.infrastructure, facts.infrastructure.children_ground, facts.infrastructure.kindergartens, facts.infrastructure.parks, facts.infrastructure.schools, facts.infrastructure.security, facts.infrastructure.shops, facts.infrastructure.sports_ground, facts.infrastructure.yard_without_cars, facts.link, facts.location, facts.metro, facts.name, facts.price_range, facts.ready, finishing, houses_info, infrastructure, kindergartens, link, location, metro, missing, name, near, params, params.purpose, parks, price_range, purpose, ready, schools, security, shops, sports_ground, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/07_Выбор_конкретного_ЖК.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/07_Выбор_конкретного_ЖК.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126519: статус=processing (3с)
[OK] Задача создана: id=2126522
[WAIT] Задача 2126522: статус=queued (0с)
[WAIT] Задача 2126522: статус=processing (3с)
[OK] Задача создана: id=2126525
[WAIT] Задача 2126525: статус=queued (0с)
[WAIT] Задача 2126525: статус=processing (3с)
📊 tokens_used=58613 (cost: or_cost.py)
```

### 8. Сравнение / рекомендация

Query: `Сравни текущие варианты и порекомендуй лучший.`

Params:

```json
null
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `1`.

Expected fields: —

Found expected fields: —

Observed keys sample: count, facts, missing, near, params, params.count, params.purpose, purpose

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/08_Сравнение_рекомендация.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/08_Сравнение_рекомендация.stderr.txt`

stderr tail:

```text
MCP-запрос:   {"purpose": "search", "count": 3}
[OK] Задача создана: id=2126528
[WAIT] Задача 2126528: статус=processing (0с)
[OK] Задача создана: id=2126531
[WAIT] Задача 2126531: статус=queued (0с)
[OK] Задача создана: id=2126534
[WAIT] Задача 2126534: статус=queued (0с)
📊 tokens_used=14924 (cost: or_cost.py)
```

### 9. Оператор / менеджер

Query: `Позови менеджера по выбранному ЖК.`

Params:

```json
null
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `1`.

Expected fields: —

Found expected fields: —

Observed keys sample: facts, missing, near, params, params.purpose, purpose

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/09_Оператор_менеджер.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/09_Оператор_менеджер.stderr.txt`

stderr tail:

```text
MCP-запрос:   {"purpose": "operator"}
[OK] Задача создана: id=2126537
[WAIT] Задача 2126537: статус=queued (0с)
[OK] Задача создана: id=2126538
[WAIT] Задача 2126538: статус=queued (0с)
[OK] Задача создана: id=2126541
[WAIT] Задача 2126541: статус=queued (0с)
📊 tokens_used=14929 (cost: or_cost.py)
```

### 10. Телефон / заявка

Query: `Да, хочу оставить номер.`

Params:

```json
null
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `2`.

Expected fields: —

Found expected fields: —

Observed keys sample: facts, missing, near, params, params.purpose, purpose

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/10_Телефон_заявка.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/10_Телефон_заявка.stderr.txt`

stderr tail:

```text
MCP-запрос:   {"purpose": "operator"}
[OK] Задача создана: id=2126544
[WAIT] Задача 2126544: статус=queued (0с)
[OK] Задача создана: id=2126547
[WAIT] Задача 2126547: статус=queued (0с)
[OK] Задача создана: id=2126549
[WAIT] Задача 2126549: статус=queued (0с)
📊 tokens_used=15053 (cost: or_cost.py)
```

### 11. Ипотека / финансирование

Query: `Какие варианты можно рассмотреть под ипотеку? Нужны ипотечные данные, цены и площади.`

Params:

```json
{
  "purpose": "search",
  "facets": [
    "mortgage"
  ],
  "need": [
    "mortgage",
    "mortgage_calc",
    "prices",
    "area"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `1`.

Expected fields: mortgage, mortgage_calc, price_range, area

Found expected fields: mortgage, mortgage_calc, price_range, area

Observed keys sample: area, bank_name, credit_month, developer, discount, facets, facts, facts.area, facts.developer, facts.discount, facts.finishing, facts.link, facts.location, facts.metro, facts.mortgage_calc, facts.mortgage_calc.bank_name, facts.mortgage_calc.credit_month, facts.mortgage_calc.min_fee, facts.mortgage_calc.min_percent, facts.mortgage_calc.name, facts.name, facts.payment_by_installments, facts.price_range, facts.ready, facts.why_mortgage, finishing, link, location, metro, min_fee, min_percent, missing, mortgage_calc, mortgage_type, name, near, params, params.facets, params.mortgage_type, payment_by_installments, price_range, ready, why_mortgage

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/11_Ипотека_финансирование.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/11_Ипотека_финансирование.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126550: статус=processing (3с)
[OK] Задача создана: id=2126553
[WAIT] Задача 2126553: статус=queued (0с)
[WAIT] Задача 2126553: статус=processing (3с)
[OK] Задача создана: id=2126557
[WAIT] Задача 2126557: статус=queued (0с)
[WAIT] Задача 2126557: статус=processing (3с)
📊 tokens_used=66065 (cost: or_cost.py)
```

### 12. Семейная ипотека

Query: `Проверь ЖК Мичуринский парк под семейную ипотеку: ипотечные условия, цены и площади.`

Params:

```json
{
  "purpose": "fact_check",
  "selected_option_name": "Мичуринский парк",
  "facets": [
    "mortgage"
  ],
  "need": [
    "mortgage",
    "mortgage_calc",
    "prices",
    "area",
    "house"
  ]
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `0`.

Expected fields: mortgage, mortgage_calc, price_range, area, house, Мичуринский

Found expected fields: mortgage, mortgage_calc, price_range, area, Мичуринский

Observed keys sample: area, bank_name, children_ground, count, credit_month, developer, discount, district, facets, facts, facts.area, facts.developer, facts.discount, facts.family_infrastructure, facts.family_infrastructure.children_ground, facts.family_infrastructure.kindergarten, facts.family_infrastructure.park_near, facts.family_infrastructure.school, facts.family_infrastructure.sports_ground, facts.family_infrastructure.yard_without_cars, facts.finishing, facts.location, facts.metro, facts.mortgage_calc, facts.mortgage_calc.bank_name, facts.mortgage_calc.credit_month, facts.mortgage_calc.min_fee, facts.mortgage_calc.min_percent, facts.mortgage_calc.name, facts.mortgage_calc.price_range, facts.name, facts.payment_by_installments, facts.price_range, facts.ready, facts.why_family, family_infrastructure, finishing, floor, has_renovation, kindergarten, location, max_price, metro, min_fee, min_percent, min_price, missing, mortgage_calc, mortgage_type, name, near, params, params.count, params.district, params.facets, params.floor, params.has_renovation, params.max_price, params.min_price, params.mortgage_type, params.purpose, params.rooms, park_near, payment_by_installments, price_range, purpose, ready, rooms, school, sports_ground, why_family, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/12_Семейная_ипотека.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/12_Семейная_ипотека.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126558: статус=processing (3с)
[OK] Задача создана: id=2126563
[WAIT] Задача 2126563: статус=queued (0с)
[WAIT] Задача 2126563: статус=processing (3с)
[OK] Задача создана: id=2126565
[WAIT] Задача 2126565: статус=queued (0с)
[WAIT] Задача 2126565: статус=processing (3с)
📊 tokens_used=59174 (cost: or_cost.py)
```

### 13. Первоначальный взнос

Query: `Проверь по ЖК Мичуринский парк данные для первоначального взноса и ипотеки: цены, площади, ипотечный расчёт.`

Params:

```json
{
  "purpose": "fact_check",
  "selected_option_name": "Мичуринский парк",
  "facets": [
    "mortgage"
  ],
  "need": [
    "mortgage",
    "mortgage_calc",
    "prices",
    "area"
  ]
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `0`.

Expected fields: mortgage, mortgage_calc, price_range, area

Found expected fields: mortgage, mortgage_calc, price_range, area

Observed keys sample: area, children_ground, credit_month, developer, discount, example, facts, facts.area, facts.developer, facts.discount, facts.finishing, facts.infrastructure, facts.infrastructure.children_ground, facts.infrastructure.kindergarten, facts.infrastructure.park_near, facts.infrastructure.school, facts.infrastructure.sports_ground, facts.infrastructure.water_near, facts.infrastructure.yard_without_cars, facts.link, facts.location, facts.metro, facts.mortgage_calc, facts.mortgage_calc.credit_month, facts.mortgage_calc.example, facts.mortgage_calc.min_fee, facts.mortgage_calc.min_percent, facts.mortgage_calc.name, facts.name, facts.payment_by_installments, facts.price_range, facts.ready, finishing, infrastructure, kindergarten, link, location, metro, min_fee, min_percent, missing, mortgage_calc, mortgage_type, name, near, params, params.mortgage_type, park_near, payment_by_installments, price_range, ready, school, sports_ground, water_near, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/13_Первоначальный_взнос.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/13_Первоначальный_взнос.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126569: статус=processing (3с)
[OK] Задача создана: id=2126574
[WAIT] Задача 2126574: статус=queued (0с)
[WAIT] Задача 2126574: статус=processing (3с)
[OK] Задача создана: id=2126577
[WAIT] Задача 2126577: статус=queued (0с)
[WAIT] Задача 2126577: статус=processing (3с)
📊 tokens_used=58652 (cost: or_cost.py)
```

### 14. Рассрочка / условия оплаты

Query: `Проверь по ЖК Мичуринский парк рассрочку, скидки, условия оплаты, цены и площади.`

Params:

```json
{
  "purpose": "fact_check",
  "selected_option_name": "Мичуринский парк",
  "facets": [
    "installment"
  ],
  "need": [
    "payment_by_installments",
    "discount",
    "prices",
    "area"
  ]
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `0`.

Expected fields: payment_by_installments, installment, discount, price_range, area

Found expected fields: installment, discount, price_range, area

Observed keys sample: 1-room, 2-room, 3-room, area, children_ground, developer, discounts, facets, facts, facts.area, facts.developer, facts.discounts, facts.finishing, facts.infrastructure, facts.infrastructure.children_ground, facts.infrastructure.kindergarten, facts.infrastructure.park_near, facts.infrastructure.school, facts.infrastructure.sports_ground, facts.infrastructure.water_near, facts.infrastructure.yard_without_cars, facts.installment, facts.location, facts.metro, facts.mortgage_calc, facts.name, facts.price_range, facts.prices_by_rooms, facts.prices_by_rooms.1-room, facts.prices_by_rooms.2-room, facts.prices_by_rooms.3-room, facts.prices_by_rooms.studio, facts.ready, finishing, infrastructure, installment, kindergarten, location, metro, missing, mortgage_calc, name, near, params, params.facets, params.purpose, park_near, price_range, prices_by_rooms, purpose, ready, school, sports_ground, studio, water_near, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/14_Рассрочка_условия_оплаты.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/14_Рассрочка_условия_оплаты.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126582: статус=queued (0с)
[WAIT] Задача 2126582: статус=processing (3с)
[OK] Задача создана: id=2126584
[WAIT] Задача 2126584: статус=queued (0с)
[OK] Задача создана: id=2126586
[WAIT] Задача 2126586: статус=queued (0с)
[WAIT] Задача 2126586: статус=processing (3с)
📊 tokens_used=58510 (cost: or_cost.py)
```

### 15. Инвестиции

Query: `Подбери варианты для инвестиций: цена, площадь, метро, готовность, скидки и рекламные условия.`

Params:

```json
{
  "purpose": "investment",
  "need": [
    "prices",
    "area",
    "ads",
    "property_metro",
    "stage",
    "ready_quarter",
    "mortgage",
    "mortgage_calc",
    "discount"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `4`, near: `1`, missing: `2`.

Expected fields: price_range, area, ads, property_metro, stage, ready_quarter, discount, mortgage

Found expected fields: price_range, area, property_metro, mortgage

Observed keys sample: area, facets, facts, facts.area, facts.finishing, facts.id, facts.location, facts.name, facts.price_range, facts.ready, facts.why_investment, finishing, id, location, missing, name, near, near.area, near.finishing, near.id, near.location, near.name, near.price_range, near.ready, near.why_close, near.why_investment, params, params.facets, params.purpose, price_range, purpose, ready, why_close, why_investment

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/15_Инвестиции.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/15_Инвестиции.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126589: статус=processing (6с)
[OK] Задача создана: id=2126593
[WAIT] Задача 2126593: статус=queued (0с)
[WAIT] Задача 2126593: статус=processing (3с)
[OK] Задача создана: id=2126595
[WAIT] Задача 2126595: статус=queued (0с)
[WAIT] Задача 2126595: статус=processing (3с)
📊 tokens_used=26725 (cost: or_cost.py)
```

### 16. Аренда

Query: `Подбери варианты под аренду: цена, площадь, метро, готовность, отделка.`

Params:

```json
{
  "purpose": "rental",
  "facets": [
    "finishing",
    "metro"
  ],
  "need": [
    "prices",
    "area",
    "ads",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `2`.

Expected fields: price_range, area, property_metro, finishing, stage, ready_quarter

Found expected fields: price_range, area, property_metro, finishing

Observed keys sample: area, count, district, facets, facts, facts.area, facts.finishing, facts.location, facts.name, facts.price_range, facts.ready, facts.why_rental, finishing, floor, has_renovation, location, max_price, min_price, missing, mortgage_type, name, near, params, params.count, params.district, params.facets, params.floor, params.has_renovation, params.max_price, params.min_price, params.mortgage_type, params.purpose, params.rooms, price_range, purpose, ready, rooms, why_rental

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/16_Аренда.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/16_Аренда.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126598: статус=processing (3с)
[OK] Задача создана: id=2126599
[WAIT] Задача 2126599: статус=queued (0с)
[WAIT] Задача 2126599: статус=processing (3с)
[OK] Задача создана: id=2126602
[WAIT] Задача 2126602: статус=queued (0с)
[WAIT] Задача 2126602: статус=processing (3с)
📊 tokens_used=26639 (cost: or_cost.py)
```

### 17. Студия

Query: `Найди студии, где видны цены, площади, метро и готовность.`

Params:

```json
{
  "purpose": "search",
  "rooms": "s",
  "need": [
    "prices",
    "area",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `1`.

Expected fields: rooms, studio, price_range, area, property_metro, stage

Found expected fields: rooms, price_range, area

Observed keys sample: area, facts, facts.area, facts.finishing, facts.link, facts.location, facts.name, facts.price_range, facts.ready, finishing, link, location, missing, name, near, params, params.rooms, price_range, ready, rooms

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/17_Студия.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/17_Студия.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126605: статус=processing (3с)
[OK] Задача создана: id=2126607
[WAIT] Задача 2126607: статус=queued (0с)
[WAIT] Задача 2126607: статус=processing (3с)
[OK] Задача создана: id=2126610
[WAIT] Задача 2126610: статус=queued (0с)
[WAIT] Задача 2126610: статус=processing (3с)
📊 tokens_used=29888 (cost: or_cost.py)
```

### 18. Однокомнатная

Query: `Найди однокомнатные квартиры, где видны цены, площади, метро и готовность.`

Params:

```json
{
  "purpose": "search",
  "rooms": "1",
  "need": [
    "prices",
    "area",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `0`, missing: `0`.

Expected fields: rooms, 1, price_range, area, property_metro, stage

Found expected fields: rooms, 1, price_range, area

Observed keys sample: area, children_ground, developer, facts, facts.area, facts.developer, facts.finishing, facts.infrastructure, facts.infrastructure.children_ground, facts.infrastructure.kindergarten, facts.infrastructure.park_near, facts.infrastructure.school, facts.infrastructure.sports_ground, facts.infrastructure.water_near, facts.infrastructure.yard_without_cars, facts.link, facts.location, facts.metro, facts.name, facts.price_range, facts.ready, finishing, infrastructure, kindergarten, link, location, metro, missing, name, near, params, params.rooms, park_near, price_range, ready, rooms, school, sports_ground, water_near, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/18_Однокомнатная.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/18_Однокомнатная.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126612: статус=processing (3с)
[WAIT] Задача 2126612: статус=processing (6с)
[OK] Задача создана: id=2126615
[WAIT] Задача 2126615: статус=queued (0с)
[OK] Задача создана: id=2126621
[WAIT] Задача 2126621: статус=queued (0с)
[WAIT] Задача 2126621: статус=processing (3с)
📊 tokens_used=72732 (cost: or_cost.py)
```

### 19. Двухкомнатная

Query: `Найди двухкомнатные квартиры для семьи: цены, площади, школы, сады, парки, магазины, метро и готовность.`

Params:

```json
{
  "purpose": "family",
  "rooms": "2",
  "facets": [
    "infrastructure"
  ],
  "need": [
    "prices",
    "area",
    "schools",
    "kindergartens",
    "parks",
    "shops",
    "family_infrastructure",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `1`, near: `2`, missing: `0`.

Expected fields: rooms, 2, price_range, area, schools, kindergartens, parks, shops, property_metro, stage

Found expected fields: rooms, 2, price_range, area

Observed keys sample: area, children_ground, developer, facts, facts.area, facts.developer, facts.family_infrastructure, facts.family_infrastructure.children_ground, facts.family_infrastructure.kindergarten, facts.family_infrastructure.park_near, facts.family_infrastructure.school, facts.family_infrastructure.sports_ground, facts.family_infrastructure.yard_without_cars, facts.finishing, facts.link, facts.location, facts.metro, facts.name, facts.price_range, facts.ready, facts.why_family, family_infrastructure, finishing, kindergarten, link, location, metro, missing, name, near, near.link, near.location, near.metro, near.name, near.price_range, near.ready, near.why_close, params, params.purpose, params.rooms, park_near, price_range, purpose, ready, rooms, school, sports_ground, why_close, why_family, yard_without_cars

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/19_Двухкомнатная.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/19_Двухкомнатная.stderr.txt`

stderr tail:

```text
[OK] Задача создана: id=2126626
[WAIT] Задача 2126626: статус=queued (0с)
[WAIT] Задача 2126626: статус=processing (3с)
[OK] Задача создана: id=2126628
[WAIT] Задача 2126628: статус=queued (0с)
[OK] Задача создана: id=2126630
[WAIT] Задача 2126630: статус=queued (0с)
📊 tokens_used=73730 (cost: or_cost.py)
```

### 20. Трехкомнатная+

Query: `Найди трехкомнатные или больше для семьи: цены, площади, школы, сады, парки, магазины, метро и готовность.`

Params:

```json
{
  "purpose": "family",
  "rooms": "3",
  "facets": [
    "infrastructure"
  ],
  "need": [
    "prices",
    "area",
    "schools",
    "kindergartens",
    "parks",
    "shops",
    "family_infrastructure",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `2`.

Expected fields: rooms, 3, price_range, area, schools, kindergartens, parks, shops, property_metro, stage

Found expected fields: rooms, 3

Observed keys sample: facets, facts, missing, near, params, params.facets, params.purpose, params.rooms, purpose, rooms

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/20_Трехкомнатная.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/20_Трехкомнатная.stderr.txt`

stderr tail:

```text
MCP-запрос:   {"purpose": "family", "rooms": "3", "facets": ["infrastructure"], "need": ["prices", "area", "schools", "kindergartens", "parks", "shops", "family_infrastructure", "property_metro", "stage", "ready_quarter", "yard_without_cars"], "count": 5}
[OK] Задача создана: id=2126631
[WAIT] Задача 2126631: статус=queued (0с)
[OK] Задача создана: id=2126632
[WAIT] Задача 2126632: статус=queued (0с)
[OK] Задача создана: id=2126634
[WAIT] Задача 2126634: статус=queued (0с)
📊 tokens_used=23749 (cost: or_cost.py)
```

### 21. Отказ / изменение условий

Query: `Покажи другие варианты рядом с метро, не Мичуринский парк, с ценами, площадями и сроками.`

Params:

```json
{
  "purpose": "repeat_search",
  "facets": [
    "metro"
  ],
  "exclude": [
    "Мичуринский парк"
  ],
  "need": [
    "prices",
    "area",
    "property_metro",
    "stage",
    "ready_quarter"
  ],
  "count": 5
}
```

Return code: `0`, parse_ok: `True`, facts: `5`, near: `0`, missing: `0`.

Expected fields: price_range, area, property_metro, stage, ready_quarter

Found expected fields: price_range, area, ready_quarter

Observed keys sample: area, count, developer, facts, facts.area, facts.developer, facts.finishing, facts.location, facts.name, facts.price_range, facts.ready_quarter, finishing, location, missing, name, near, params, params.count, price_range, ready_quarter

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/21_Отказ_изменение_условий.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/21_Отказ_изменение_условий.stderr.txt`

stderr tail:

```text
[WAIT] Задача 2126637: статус=processing (3с)
[OK] Задача создана: id=2126640
[WAIT] Задача 2126640: статус=queued (0с)
[WAIT] Задача 2126640: статус=processing (3с)
[OK] Задача создана: id=2126643
[WAIT] Задача 2126643: статус=queued (0с)
[WAIT] Задача 2126643: статус=processing (3с)
📊 tokens_used=27430 (cost: or_cost.py)
```

### 22. Ошибка / fallback

Query: `Найди ЖК с единорогами на крыше и ценой 1 рубль у Кремля.`

Params:

```json
{
  "purpose": "search",
  "need": [
    "prices",
    "area"
  ],
  "count": 3
}
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `3`.

Expected fields: facts, near, missing

Found expected fields: facts, near, missing

Observed keys sample: count, facts, missing, near, params, params.count, params.purpose, purpose

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/22_Ошибка_fallback.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/22_Ошибка_fallback.stderr.txt`

stderr tail:

```text
MCP-запрос:   {"purpose": "search", "need": ["prices", "area"], "count": 3}
[OK] Задача создана: id=2126646
[WAIT] Задача 2126646: статус=queued (0с)
[OK] Задача создана: id=2126648
[WAIT] Задача 2126648: статус=queued (0с)
[OK] Задача создана: id=2126649
[WAIT] Задача 2126649: статус=queued (0с)
📊 tokens_used=14985 (cost: or_cost.py)
```

### 23. Прочее / нераспознано

Query: `Какая завтра погода?`

Params:

```json
null
```

Return code: `0`, parse_ok: `True`, facts: `0`, near: `0`, missing: `0`.

Expected fields: —

Found expected fields: —

Observed keys sample: facts, missing, near, params, params.purpose, purpose

Raw stdout: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/23_Прочее_нераспознано.stdout.txt`

Raw stderr: `/tmp/opencode-run-nmbot/project/reports/mcp_topic_coverage_raw_20260713/23_Прочее_нераспознано.stderr.txt`

stderr tail:

```text
Поиск:        google/gemini-3.1-flash-lite-preview
Общение:      google/gemini-2.5-flash
MCP поиск:    False
chat_max_tok: 2500
Таймаут:      300с
Запрос:       Какая завтра погода?

MCP-запрос:   {}
```

