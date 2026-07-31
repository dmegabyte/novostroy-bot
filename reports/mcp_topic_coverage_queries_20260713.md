# MCP topic coverage audit — проверочные запросы по тематикам MINION

Дата: 2026-07-13

Источник тематик: `reports/dialog_topic_network_20260713.md` — 138 диалогов, 23 темы, 253 возможные пары, 218 реальных пересечений.

Цель файла: проверить, какие данные по каждой теме реально можно получить через MCP/search `novostroym`, а какие темы должны оставаться сценарной логикой, честной границей данных или переводом к менеджеру.

## 1. Контракт проверки

По текущему runtime `mcp_request_patch` может просить:

```json
{
  "purpose": "search|family|investment|rental|repeat_search|fact_check",
  "selected_option_name": "точное имя из selected_option/visible_options/last_options или null",
  "fact_to_check": "коротко что проверить или null",
  "facets": ["mortgage", "discount", "installment", "finishing", "metro", "infrastructure", "readiness"],
  "need": [
    "delivered_houses", "under_construction_houses", "keys_handover", "settlement_info",
    "stage", "ready_quarter", "project_ready_secondary", "mortgage_calc", "mortgage",
    "discount", "payment_by_installments", "property_metro", "schools", "kindergartens",
    "parks", "shops", "family_infrastructure", "ads", "house", "prices", "area"
  ],
  "exclude": [],
  "count": null
}
```

Проверять надо не только факт ответа MCP, но и путь поля:

- пришло ли поле в raw/search JSON;
- попало ли в `facts[]` или `near[]`;
- не потерялось ли при normalizer/presenter;
- можно ли безопасно озвучивать клиенту;
- нужна ли граница данных или менеджер.

## 2. Базовые тестовые ЖК / сценарии

Для покрытия лучше использовать две группы:

1. **Точечные ЖК из реальных диалогов** — например `Мичуринский парк`, `Лучи`, `Скандинавия`, `Переделкино Ближнее`, если они есть в текущем `visible_options/last_options` или точно находятся поиском.
2. **Широкие запросы** — чтобы проверить, какие поля приходят в первом shortlist, а не только при fact-check выбранного ЖК.

## 3. Запросы по 23 тематикам

### 1. Подбор квартиры / ЖК

**Пользовательский запрос:**

```text
Подбери 3 варианта квартир в Москве до 18 млн
```

**mcp_request_patch:**

```json
{"purpose":"search","need":["prices","area","property_metro","stage","ready_quarter","house"],"count":3}
```

**Проверить поля:** `name`, `location`, `price_range/prices`, `area`, `metro/property_metro`, `ready/stage/ready_quarter`, `house`, `link`, `developer`.

**Можно говорить уверенно:** только найденные ЖК, цены, площади, метро, сроки, отделку, если они есть в JSON.

---

### 2. Бюджет / цена

**Пользовательский запрос:**

```text
Покажи варианты до 15 млн, желательно дешевле
```

**mcp_request_patch:**

```json
{"purpose":"search","need":["prices","area"],"count":5}
```

**Проверить поля:** `price_range`, `min_price`, `prices`, `area`, `why_close` для near.

**Граница:** нельзя обещать финальную цену сделки, скидку или бронь без live-проверки.

---

### 3. Метро / локация / район

**Пользовательский запрос:**

```text
Нужны варианты рядом с метро, чтобы было удобно пешком
```

**mcp_request_patch:**

```json
{"purpose":"search","facets":["metro"],"need":["property_metro","prices","area"],"count":5}
```

**Проверить поля:** `property_metro`, `metro`, `walk_minutes`, `transport`, `location`.

**Можно говорить уверенно:** только конкретное метро/минуты, если они пришли.

---

### 4. Срок сдачи / готовность

**Пользовательский запрос:**

```text
Нужен готовый или почти готовый дом, чтобы быстрее заехать
```

**mcp_request_patch:**

```json
{"purpose":"search","facets":["readiness"],"need":["delivered_houses","under_construction_houses","keys_handover","settlement_info","stage","ready_quarter","project_ready_secondary","prices","area"],"count":5}
```

**Проверить поля:** `ready`, `stage`, `ready_quarter`, `delivered_houses`, `under_construction_houses`, `keys_handover`, `settlement_info`, `project_ready_secondary`.

**Граница:** готовность всего ЖК не равна готовности конкретного корпуса; по корпусу лучше делать `fact_check`.

---

### 5. Отделка / ремонт

**Пользовательский запрос:**

```text
Покажи варианты с отделкой, чтобы не делать ремонт
```

**mcp_request_patch:**

```json
{"purpose":"search","facets":["finishing"],"need":["prices","area"],"count":5}
```

**Проверить поля:** `finishing`, `has_renovation`, `price_range`, `area`.

**Можно говорить:** «с отделкой / без отделки» только если поле есть.

---

### 6. Семья / дети / инфраструктура

**Пользовательский запрос:**

```text
Нужна квартира для семьи с ребёнком, важны школы, садики и парки рядом
```

**mcp_request_patch:**

```json
{"purpose":"family","facets":["infrastructure"],"need":["schools","kindergartens","parks","shops","family_infrastructure","property_metro","prices","area"],"count":5}
```

**Проверить поля:** `schools`, `kindergartens`, `parks`, `shops`, `family_infrastructure`, `infrastructure_family`, `yard_without_cars`, `children_ground`, `property_metro`.

**Граница:** нельзя писать «идеально для семьи» без конкретных фактов.

---

### 7. Выбор конкретного ЖК

**Пользовательский запрос:**

```text
Расскажи подробнее про Мичуринский парк
```

**mcp_request_patch:**

```json
{"purpose":"fact_check","selected_option_name":"Мичуринский парк","fact_to_check":"подробные факты по выбранному ЖК","need":["prices","area","property_metro","schools","kindergartens","parks","shops","stage","ready_quarter","house"]}
```

**Проверить поля:** все выбранные факты по конкретному ЖК, плюс что `selected_option_name` точно совпадает с memory.

**Граница:** если ЖК не в памяти, patch должен быть отброшен.

---

### 8. Сравнение / рекомендация

**Пользовательский запрос:**

```text
Сравни эти варианты и скажи, какой лучше
```

**mcp_request_patch:**

```json
null
```

**Проверить:** сравнение должно идти по `visible_options/last_options/enriched_options`, без нового MCP, если клиент не просит свежие/другие варианты.

**Граница:** «лучше» — это рекомендация по критериям, а не MCP-факт.

---

### 9. Оператор / менеджер

**Пользовательский запрос:**

```text
Позови менеджера / хочу связаться с оператором
```

**mcp_request_patch:**

```json
null
```

**Проверить:** `operator_live_check` не должен запускать MCP. Контекст берётся из state: `selected_option`, `visible_options`, `params`, `active_conversation_topic`.

**Граница:** менеджер — действие, не источник факта в MCP.

---

### 10. Телефон / заявка

**Пользовательский запрос:**

```text
Да, передай менеджеру
```

**mcp_request_patch:**

```json
null
```

**Проверить:** после явного согласия на менеджера бот сразу спрашивает номер телефона, без повторного промежуточного вопроса.

**Граница:** номер телефона не имеет отношения к MCP.

---

### 11. Ипотека / финансирование

**Пользовательский запрос:**

```text
А по этим вариантам есть ипотека? Можно посчитать платёж?
```

**mcp_request_patch:**

```json
{"purpose":"search","facets":["mortgage"],"need":["mortgage","mortgage_calc","prices","area"],"count":5}
```

**Для выбранного ЖК:**

```json
{"purpose":"fact_check","selected_option_name":"Мичуринский парк","fact_to_check":"ипотека и расчёт платежа по выбранному ЖК","facets":["mortgage"],"need":["mortgage","mortgage_calc","prices","area"]}
```

**Проверить поля:** `mortgage`, `mortgage_calc`, `finance`, `why_mortgage`, `prices`, `area`.

**Граница:** нельзя гарантировать ставку, одобрение и точный платёж, если нет расчёта; лучше предлагать менеджера.

---

### 12. Семейная ипотека

**Пользовательский запрос:**

```text
Подойдёт ли Мичуринский парк под семейную ипотеку?
```

**mcp_request_patch:**

```json
{"purpose":"fact_check","selected_option_name":"Мичуринский парк","fact_to_check":"семейная ипотека по выбранному ЖК","facets":["mortgage"],"need":["mortgage","mortgage_calc","prices","area","house"]}
```

**Проверить поля:** `mortgage`, `mortgage_calc`, признаки программы, если MCP реально отдаёт.

**Граница:** даже если есть mortgage-блок, не обещать, что семейная ипотека точно одобряется; точные условия — менеджер/банк.

---

### 13. Первоначальный взнос

**Пользовательский запрос:**

```text
А какой первоначальный взнос нужен?
```

**mcp_request_patch:**

```json
{"purpose":"fact_check","selected_option_name":"Мичуринский парк","fact_to_check":"первоначальный взнос и ипотечные условия","facets":["mortgage"],"need":["mortgage","mortgage_calc","prices","area"]}
```

**Проверить поля:** down payment в `mortgage_calc` или `mortgage`, если есть.

**Граница:** ПВ зависит от банка, программы, квартиры и клиента; если поля нет — только менеджер.

---

### 14. Рассрочка / условия оплаты

**Пользовательский запрос:**

```text
Есть рассрочка или другие условия оплаты?
```

**mcp_request_patch:**

```json
{"purpose":"fact_check","selected_option_name":"Мичуринский парк","fact_to_check":"рассрочка и условия оплаты","facets":["installment"],"need":["payment_by_installments","discount","prices","area"]}
```

**Проверить поля:** `payment_by_installments`, `discount`, `prices`.

**Граница:** акции и рассрочка быстро меняются; при пустом MCP — менеджер.

---

### 15. Инвестиции

**Пользовательский запрос:**

```text
Подбери вариант под инвестиции, чтобы потом было проще продать или сдать
```

**mcp_request_patch:**

```json
{"purpose":"investment","need":["prices","area","ads","property_metro","stage","ready_quarter","mortgage","mortgage_calc","discount"],"count":5}
```

**Проверить поля:** `prices`, `area`, `ads`, `property_metro`, `stage`, `ready_quarter`, `mortgage`, `discount`, `why_investment`.

**Граница:** нельзя обещать доходность, окупаемость или рост цены.

---

### 16. Аренда

**Пользовательский запрос:**

```text
Нужна квартира под сдачу в аренду
```

**mcp_request_patch:**

```json
{"purpose":"rental","need":["prices","area","ads","property_metro","stage","ready_quarter"],"facets":["finishing","metro"],"count":5}
```

**Проверить поля:** компактный формат, `area`, `prices`, `finishing`, `property_metro`, `ready/stage`, `ads`, `why_rental`.

**Граница:** нельзя писать арендную ставку и гарантированный спрос без прямых данных.

---

### 17. Студия

**Пользовательский запрос:**

```text
Покажи студии до 12 млн
```

**mcp_request_patch:**

```json
{"purpose":"search","need":["prices","area","property_metro","stage","ready_quarter"],"count":5}
```

**Проверить поля:** `rooms`, studio marker, `area`, `prices`, `location`, `property_metro`.

**Граница:** если MCP не подтверждает студию — не показывать как точный факт.

---

### 18. Однокомнатная

**Пользовательский запрос:**

```text
Нужна однокомнатная квартира до 15 млн
```

**mcp_request_patch:**

```json
{"purpose":"search","need":["prices","area","property_metro","stage","ready_quarter"],"count":5}
```

**Проверить поля:** `rooms`, `area`, `prices`, `location`, `property_metro`.

---

### 19. Двухкомнатная

**Пользовательский запрос:**

```text
Нужна двушка для семьи
```

**mcp_request_patch:**

```json
{"purpose":"family","facets":["infrastructure"],"need":["prices","area","schools","kindergartens","parks","shops","family_infrastructure","property_metro","stage","ready_quarter"],"count":5}
```

**Проверить поля:** `rooms`, `area`, `prices`, family infrastructure, `property_metro`, `stage`.

---

### 20. Трёхкомнатная+

**Пользовательский запрос:**

```text
Нужна трёшка или больше для семьи
```

**mcp_request_patch:**

```json
{"purpose":"family","facets":["infrastructure"],"need":["prices","area","schools","kindergartens","parks","shops","family_infrastructure","property_metro","stage","ready_quarter"],"count":5}
```

**Проверить поля:** `rooms`, `area`, `prices`, family infrastructure, `ready/stage`.

---

### 21. Отказ / изменение условий

**Пользовательский запрос:**

```text
Не подходит, хочу дешевле и ближе к метро
```

**mcp_request_patch:**

```json
{"purpose":"search","facets":["metro"],"need":["prices","area","property_metro","stage","ready_quarter"],"exclude":["<имя отвергнутого ЖК из памяти>"],"count":5}
```

**Проверить:** старый ЖК попал в `exclude`, выбранный ЖК очищен, новый поиск не повторяет rejected option.

---

### 22. Ошибка / fallback

**Пользовательский запрос:**

```text
Покажи что-нибудь непонятное / запрос вне базы
```

**mcp_request_patch:**

```json
{"purpose":"search","need":["prices","area"],"count":3}
```

**Проверить:** если `facts=[]` и `near=[]`, бот честно сообщает, что не нашёл, не придумывает ЖК и не показывает объекты без MCP.

---

### 23. Прочее / нераспознано

**Пользовательский запрос:**

```text
Расскажи что-нибудь не по недвижимости
```

**mcp_request_patch:**

```json
null
```

**Проверить:** бот не запускает лишний поиск, ставит границу тематики и возвращает к новостройкам.

## 4. Минимальный формат результата аудита

После реальных прогонов сохранить таблицу:

| Тема | request | mcp_request_patch | facts_count | near_count | найденные поля | пустые поля | можно говорить | нужна граница/менеджер |
|---|---|---|---:|---:|---|---|---|---|

## 5. Критерий готовности

Для каждой темы должно быть одно из четырёх решений:

1. **MCP-факт подтверждён** — можно использовать в ответе.
2. **MCP даёт частично** — использовать только найденные поля, остальное честно ограничивать.
3. **MCP не даёт** — не говорить как факт, оставить как сценарную подсказку.
4. **Менеджерская зона** — ипотека, ПВ, рассрочка, бронь, наличие, ставка, точный платёж, корпус/ключи при неполных данных.

## 6. Source refs

- `reports/dialog_topic_network_20260713.md` — темы и пересечения.
- `followup_intent_classifier.py`, блок `MCP request patch` — разрешённая форма patch, facets и need.
- `prompts/search_v1.txt` — поля, которые search prompt обязан пробрасывать из MCP.
- `docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md` — сценарные поля для family / investment / self-use / budget / metro / fast move.
