# Единый контракт MCP для запросов квартир

Статус: рабочий контракт nmbot для поиска квартир и новостроек через MCP
`novostroym`.

Для LLM и prompt-authoring используйте короткую инструкцию
[`MCP_APARTMENT_REQUEST_RULES_LLM.md`](MCP_APARTMENT_REQUEST_RULES_LLM.md).
Она является navigation/quick-start слоем; нормативными владельцами схемы и
semantic rules остаются этот документ, JSON Schema и
`nmbot_v2/search_contract.py`.

Канонический справочник базы `novostroym` находится рядом с этим контрактом:
[`NOVOSTROYM_MCP_SCHEMA.md`](NOVOSTROYM_MCP_SCHEMA.md). В нём собраны таблицы,
поля, связи, статусы и безопасные SQL-шаблоны. При расхождении между кратким
описанием в prompt и справочником сначала проверяйте этот документ и фактический
adapter/fixture.

Важно различать два уровня:

- `NOVOSTROYM_MCP_SCHEMA.md` описывает базу данных `novostroym`: таблицы,
  поля, связи и значения статусов;
- этот контракт описывает фактический путь `typed request → Gateway → MCP →
  normalized result → canonical card`.

Поле, существующее в базе, нельзя автоматически требовать от
`get_flat_info`: его доступность должна быть подтверждена wire-ответом,
adapter или fixture.

### FAQ: что считается доказательством

| Наблюдение | Это доказательство? | Почему |
|---|---|---|
| Поле есть в `NOVOSTROYM_MCP_SCHEMA.md` | Нет | Это описание базы, не гарантия текущего wire-поля |
| Gateway task завершён | Нет | Он не доказывает конкретный MCP tool call или результат |
| Модель назвала число квартир | Нет | Свободный текст не проходит provenance/normalization |
| Exact enrichment дал normalized `LotExample` с ID и `status=2` | Да, для каталожного лота | Есть typed evidence активного лота в продаже |
| `availability_evidence=confirmed` | Да, для момента enrichment | Это не гарантия брони или сделки |
| Отсутствует typed lot evidence | Нет | Правильный публичный статус — `not_confirmed`, а не «квартир нет» |

## 1. Назначение и маршрут

MCP `novostroym` — единственный источник фактов о новостройках, корпусах,
квартирах, ипотеке и ЕГРН. Запрос проходит по цепочке:

```text
клиентский текст
  → orchestrator нормализует цель и ограничения
  → search-agent вызывает novostroym/get_flat_info
  → runtime валидирует и нормализует facts/near/missing
  → chat/presenter формирует человеческий ответ
```

Все LLM/MCP-вызовы идут через gateway-agent Overmind. Search-agent не является
router, presenter или чат-ответом клиенту.

Обязательное правило: не отвечать по памяти модели. В клиентский ответ можно
попадать только фактам из MCP/search и безопасным выводам непосредственно из
этих фактов.

## 2. Канонический инструмент и envelope

Канонический вызов:

```text
novostroym/get_flat_info
```

На один ход передаётся короткий envelope, а не полный повтор system prompt:

```json
{
  "contract": "v2_search_mcp_contract",
  "search_mode": "broad",
  "mcp_alias": "novostroym",
  "mcp_tool": "novostroym/get_flat_info",
  "output_top_level_keys": ["diagnostics", "facts", "missing", "near", "params"],
  "forbidden_top_level_keys": [],
  "response_viewpoint": "investment|rental|family|life|financing",
  "base_viewpoint": "investment|rental|family|life|null",
  "available_fact_fields": [],
  "hard_evidence_requirements": {},
  "count": 3,
  "current_option_names": [],
  "facts_needed": []
}
```

После envelope передаются:

```text
Текущие параметры: {normalized search parameters}
Клиент: <естественный запрос без лишнего технического контекста>
```

Не передавать одновременно два конкурирующих формата контракта и не помещать
длинный system contract в per-turn query.

## 3. Нормализация запроса до MCP

### 3.1. Цель поиска

`search_goal` обязателен и описывает, что ищем, но сам по себе не создаёт
новых hard-фильтров. Для точного названного ЖК используется режим
`exact_named_object` с `entity_reference`; возвращается только этот ЖК, без
похожих объектов в `near`.

### 3.2. Жёсткие ограничения

- `requested_hard` — исходные ограничения клиента для аудита.
- `effective_hard` — единственные фактически применяемые hard-фильтры.
- Только `effective_hard` управляет точным поиском и составом `facts`.
- Search-agent не расширяет, не ослабляет и не заменяет их сценарием или
  предпочтениями.
- `relaxation_audit` содержит только ослабления, которые уже сделал runtime.
  Search-agent не выбирает и не предлагает ослабление.

Основные hard-поля executable validator: `district`, `location`, `rooms`,
`max_price`, `min_price`, `ready`, `finishing`, `area_min_m2`, `area_max_m2`.
Для каждого hard-поля требуется structured MCP-evidence; свободный prose,
`description`, `why_close` или текст модели не подтверждают exact-совпадение.

### 3.3. География

| Текст клиента | Нормализованный параметр |
|---|---|
| Москва | `district="msk"` |
| Новая Москва | `district="newmsk"` |
| Московская область | `district="mo"` |
| Сокол, Коммунарка | `location=["..."]` |

`district` — только код региона `msk|mo|newmsk`. Район, город или локация
хранятся отдельно в `location`/`location_id`; нельзя подменять одно другим.

### 3.4. Мягкие предпочтения

Разрешённый allowlist: `format`, `rooms_preference`, `budget_preference`,
`location_preference`, `infrastructure_preference`, `transport_preference`,
`finance_preference`, `sort_hint`.

Предпочтения влияют на сортировку, `near` и набор полей, но не превращают
карточку в точное `facts`-совпадение. Неизвестные ключи игнорируются; в
`diagnostics.ignored_preferences` записывается только имя ключа, без значения.

### 3.5. Количество

Для широкого подбора runtime держит `count >= 3`. Если точных результатов
меньше, нельзя добивать `facts` альтернативами. Для выбранного конкретного ЖК
допустим bounded enrichment с `count=1`.

## 4. Сценарии и приоритеты полей

Сценарий задаёт приоритет запрашиваемых полей, но не становится hard-фильтром.
Ипотека — facet/overlay поверх основного сценария.

| Сценарий | Приоритетные факты |
|---|---|
| `family` | `school`, `kindergarten`, `park_near`, `water_near`, `yard_without_cars`, `children_ground`, `sports_ground`, `security`, `property_metro`, ecology |
| `financing` | `mortgage_calc`, `mortgage`, `discount`, `payment_by_installments`, `ads.fullprice`, цены `novos` |
| `investment` | входная цена, `ads.fullprice`, `rooms`, `apartment_types`, `ready`, `finishing`, метро, `egrn_top_novos`, `counter_novos`, `ads`, `ads_add.stat_price` |
| `rental` | `rooms`, компактные `apartment_types`, `ready`, `finishing`, метро, `location`, `ads`, подтверждённые счётчики |
| `life` | `location`, `district`, метро, `ready`, `finishing`, `territory`, парк/вода, безопасность, parking, elevator, инфраструктура |

Для `family + mortgage` основной сценарий остаётся `family`, а запрос
дополняется `facets:["mortgage"]`, при понятном типе — `mortgage_type`, и
finance-полями. Финансовый overlay не заменяет семейные факты.

Минимум сценарных опор:

- family: школы, сады, парки, двор без машин;
- investment: цена входа, компактные форматы/лоты, подтверждённые объявления
  или ЕГРН-сигналы;
- rental: компактность, отделка, метро, готовность, локация и подтверждённый
  MCP-сигнал спроса;
- fact-check: конкретный выбранный ЖК и конкретное проверяемое поле.

## 5. Evidence и допустимые поля

Search-agent должен помещать в `facts` объекты, совпадающие со всеми
`effective_hard` и имеющие MCP-evidence по каждому hard-полю. Production runtime
проверяет это правило в report-only режиме: пригодная идентифицируемая карточка
не удаляется и не переносится между `facts`/`near` из-за business mismatch, а
нарушение попадает в bounded diagnostics и operational log.

| Hard-поле | Допустимое evidence |
|---|---|
| `rooms` | `rooms`, `apartment_types.rooms`, `ads.rooms` |
| `max_price` | numeric `min_price`, `max_price`, комнатные цены, `price_s`, `price_n`, `price_square`, `ads.fullprice`, `ads.price`, цены `novos` |
| `min_price` | numeric `min_price`, `max_price`, `price1`..`price4`, `price_s`, `price_n`, `ads.fullprice`, `ads.price`, `novos.min_price`, `novos.max_price` |
| `area_min_m2` | numeric structured `square_min`, `square_max`, `ads.area`, `apartment_types.area` |
| `area_max_m2` | numeric structured `square_min`, `square_max`, `ads.area`, `apartment_types.area` |
| `district` | `district` |
| `location` | `location`, `location_id`, `street`, `property_metro`, `metro` |
| `ready` | `ready`, `delivered`, `state`, `status`, `built_year`, `ready_quarter` |
| `finishing` | `finishing`, `ads.renovation`, `house.finishing_list` |

Matcher-семантика для диапазонов консервативная:

- `max_price`: у кандидата должна быть подтверждённая нижняя/точная цена не выше
  ожидаемой;
- `min_price`: у кандидата должна быть подтверждённая верхняя граница диапазона
  или точная numeric price не ниже ожидаемой;
- `area_min_m2`: у кандидата должна быть подтверждённая максимальная площадь не
  меньше ожидаемой;
- `area_max_m2`: у кандидата должна быть подтверждённая минимальная площадь не
  больше ожидаемой.

Если нужной numeric boundary нет, exact matching не угадывает по тексту и
validator сообщает missing evidence. Production runtime не скрывает из-за этого
идентифицируемую карточку, уже полученную от MCP/search.

Ключевые группы MCP-полей:

- ЖК: `id`, `name`, `district`, `location`, цены, `rooms`, `ready`, `finishing`;
- локация/транспорт: `property_metro`, `metro`, `metro_line`, `railway`;
- лоты: `ads`, `ads.fullprice`, `ads.area`, `ads.rooms`, `ads.renovation`;
- планировки: `apartment_types`;
- finance: `mortgage_calc`, `mortgage`, `discount`,
  `payment_by_installments`;
- спрос/сделки: `egrn_top_novos`, агрегаты `egrn_contracts`, `counter_novos`;
- family: `school`, `kindergarten`, `park_near`, `water_near`,
  `yard_without_cars`, `children_ground`, `sports_ground`, `security`.

## 6. Реальные wire-формы и нормализация ответа

- `rooms` может быть числом, строкой (`"2-комнатные"`,
  `"1, 2, 3, студии"`) или списком. Нормализовать отдельными токенами;
  поиск по подстроке запрещён: `1` не совпадает с `10`. `description` не
  является evidence комнатности.
- `delivered` может быть `true/false` или `1/0`; `1` означает сдан.
  `ready/state/status` могут содержать `сдан`, `дом сдан`, `готов`,
  `строится`. Будущий год/квартал сам по себе не означает, что дом сдан.
- `location` может быть строкой или списком строк. Нельзя применять к списку
  операции, требующие hashable-значение.
- Runtime владеет нормализацией diagnostics: `mcp_tool`, viewpoints,
  priorities, relaxation audit и ignored preferences. Модель не придумывает
  эти поля.

## 7. Строгий ответ search-agent

Ответ — только JSON, без markdown и текста вокруг:

```json
{
  "facts": [],
  "near": [],
  "missing": [],
  "params": {},
  "diagnostics": {
    "mcp_tool": "novostroym/get_flat_info",
    "response_viewpoint": "life",
    "base_viewpoint": null,
    "requested_field_priorities": [],
    "relaxation_audit": [],
    "ignored_preferences": [],
    "notes": []
  }
}
```

Запрещены верхнеуровневые поля `action`, `target`, `scope`, `search_policy`,
`clarification_question`, `response`, `current_options`, `visible_options`,
`client_text`, `routing_decision` и любые другие поля вне контракта.

### `facts`

Search-agent обязан возвращать здесь точные совпадения со всеми hard-условиями и
evidence. Runtime сохраняет все реально пришедшие поля из allowlist и не
выбрасывает цены, комнаты, локацию, готовность, отделку, метро и сценарные поля.
Если search-agent положил сюда карточку без evidence или с hard mismatch,
production runtime сохраняет её в `facts` и создаёт report-only warning/error;
он не исправляет business-классификацию удалением или demotion.

### `near`

Search-agent использует `near` для близких, но неточных альтернатив. Runtime
сохраняет идентифицируемую карточку в `near`, выставляет `is_near=true` и заменяет
model-owned объяснение на structured hard mismatch/missing evidence. Если
structured отличие не подтверждено, runtime использует безопасное
`differences=["неполное подтверждение условий"]` и такое же `why_close`, а
нарушение логирует. Карточка не удаляется только из-за отсутствия отличия.
`near` не меняет `effective_hard` и не является скрытым relaxation.

### `missing`

Только нужные, но недоступные или неподтверждённые поля: например,
`ads.fullprice`, `school`, `mortgage_calc.min_percent`, `egrn_top_novos`.
Отсутствие evidence не означает, что квартир нет.
Runtime владеет нормализацией `missing`: известные MCP fact-поля сохраняются как
имена полей, известные причины приводятся к canonical categories, а неизвестные
reason_code в object-форме безопасно становятся `requested_but_unconfirmed`.
Неизвестные сырые строки после runtime-normalization тоже не проходят наружу как
новый enum.

### `params` и `diagnostics`

`params` содержит только принятые `effective_hard` и разрешённые preferences.
Сценарий не записывать как hard-фильтр. `diagnostics.relaxation_audit` отражает
runtime-аудит дословно.

## 8. Двухэтапный поиск и выбранный ЖК

Общие карточки ЖК не подтверждают lot-level hard-фильтры комнат, цены,
наличия или финансирования:

1. broad search получает кандидатов;
2. bounded enrichment запрашивает `ads`, `apartment_types`, `house` или finance;
3. runtime снова применяет hard-validator;
4. только подтверждённые объекты становятся `facts`.

При запросе «подробнее про ЖК» не запускать широкий новый поиск. Использовать
сохранённую карточку и, если не хватает свежего поля, запросить только
канонический выбранный ЖК (`count=1`, bounded `facts_needed`).

Для уточнений сохранять контекст (`params`, `last_search_response`,
`last_options`, `visible_options`, `selected_option`, текущую тему). Запросы
«ещё», «похожие», «другие варианты» запускают свежий поиск с исключением уже
показанных ЖК, а не повторяют список.

## 9. Антигаллюцинации и семантика данных

Запрещено придумывать или обещать:

- доходность, окупаемость, будущий рост цены, арендную ставку или спрос;
- ипотечную ставку, взнос, срок, скидку или рассрочку без соответствующего
  MCP-поля;
- школы, сады, парки, двор, безопасность, метро, отделку, площадь или
  наличие без evidence.

`egrn_top_novos.sales` и `egrn_contracts` aggregates можно описывать как
сделки/продажи. `counter_novos.count_ads` и `count_ads` — это объявления на
витрине, а не продажи, спрос или ликвидность.

Если MCP не вернул поле, честно отметить, что оно не подтверждено. Не писать
«вариантов нет» только из-за отсутствующего evidence.

## 10. Клиентская презентация после MCP

Search-agent не пишет клиентский текст, не задаёт вопрос и не решает routing.
Следующий слой должен:

- показать не более трёх карточек;
- использовать только подтверждённые факты;
- объяснить пользу через связку «факт → отличие → польза»;
- задать ровно один следующий вопрос;
- не показывать слова `MCP`, `JSON`, `diagnostics`, `payload`, внутренние enum;
- не предлагать оператора вместо полезного ответа, если карточки уже есть.

## 11. Проверка и release gate

Проверять последовательно:

1. shape: строгий JSON и только разрешённые top-level keys;
2. request: правильные `purpose/need/facets/exclude` и normalized hard;
3. evidence: `facts` соответствуют всем hard, `near` отделён и объяснён;
4. card: нормализатор не теряет поля и не превращает вложенные блоки в строку;
5. answer: нет выдумок, максимум 3 карточки и 1 вопрос;
6. state: уточнение, выбранный ЖК и repeat-search не теряют контекст;
7. live Jivo/VPS smoke для изменений, влияющих на MCP/search parsing или ответы.

При первом fail batch остановить. Сначала определить слой: Search/MCP,
normalizer/Card, response или state; после исправления повторить только
упавший кейс, затем полный набор.

Не считать PASS только потому, что в ответе есть названия ЖК: contract error,
потерянное evidence или выдуманный факт — это fail.

## 12. Состав стабильного MCP-контракта

Этот раздел описывает **форму и границы контракта**, а не текущие значения в
каталоге. Значения цен, количества квартир, сроков, скидок и другие записи
MCP динамичны и не являются частью контракта.

### 12.1. Стабильные поля executable request

Runtime формирует typed request со следующими частями:

```json
{
  "search_goal": {},
  "constraints": {
    "requested_hard": {},
    "effective_hard": {},
    "preferences": {},
    "relaxation_audit": []
  },
  "response_viewpoint": "life",
  "base_viewpoint": null,
  "available_fact_fields": [],
  "count": 3,
  "excluded_names": [],
  "search_mode": "broad",
  "current_option_names": [],
  "facts_needed": []
}
```

Стабильные request-поля:

- `search_goal` — нормализованная цель и entity reference;
- `requested_hard` — ограничения клиента для аудита;
- `effective_hard` — реально применяемые ограничения;
- `preferences` — только allowlisted soft preferences;
- `relaxation_audit` — уже выполненные runtime ослабления;
- `response_viewpoint` / `base_viewpoint` — состав и приоритет полей;
- `available_fact_fields` — allowlist полей, которые разрешено принять;
- `count` — число exact-кандидатов;
- `excluded_names` — исключения для repeat search;
- `search_mode` — `broad`, `named_object` или `current_options_fact_check`;
- `current_option_names` — ограничение проверки уже показанными ЖК;
- `facts_needed` — точечный список фактов для enrichment/fact-check.

Допустимые hard-ключи контракта: `district`, `location`, `rooms`, `max_price`,
`min_price`, `ready`, `finishing`, `area_min_m2`, `area_max_m2`. Новое hard-поле
нельзя добавлять только в prompt: его нужно добавить в typed contract,
evidence map, matcher и тесты.

### 12.2. Стабильные режимы поиска

- `broad` — общий подбор;
- `named_object` — поиск ровно одного названного ЖК;
- `current_options_fact_check` — проверка фактов только у уже показанных
  карточек; посторонние ЖК запрещены.

Для `current_options_fact_check` контракт дополнительно фиксирует:

```json
{
  "current_option_names": ["..."],
  "facts_needed": ["metro", "parking_inventory"],
  "scope_policy": "exact_current_option_names_only"
}
```

### 12.3. Стабильный allowlist принимаемых полей

Allowlist делится на группы, но не содержит самих текущих значений:

- identity: `id`, `name`, `alias`, `type_object`, `link`;
- geography: `district`, `location`, `location_id`, `street`, координаты;
- prices/areas/rooms: цены, площади, `rooms`, `apartment_types`;
- readiness: `ready`, `delivered`, `built_year`, `ready_quarter`, `state`;
- finishing: `finishing`, `ads.renovation`, `house.finishing_list`;
- transport: `property_metro`, `metro`, `metro_line`, railway, highway;
- infrastructure: park/water, school/kindergarten, security, territory,
  parking, elevator, yard and playground fields;
- inventory/lots: `ads`, `ads.fullprice`, `ads.area`, `ads.rooms`,
  `ads.status`, `ads.apart`, `apartment_inventory`, `available_apartments`,
  `flats_available`;
- finance: `mortgage_calc`, `mortgage`, `discount`,
  `payment_by_installments`;
- market aggregates: `egrn_top_novos`, `egrn_contracts`, `counter_novos`;
- additional: `developer`, `house`, `ads_add.stat_price`, `infrastructure`.

Эти имена — контрактные ключи. Наличие конкретного ключа в конкретном ответе
не гарантируется и проверяется через `missing`/evidence.

### 12.4. Стабильные правила нормализации

Runtime обязан нормализовать wire-варианты, не меняя смысл:

- комнаты — число/строка/список → структурные room-токены;
- готовность — boolean/numeric/string → каноническое значение;
- локация — строка/список → безопасное сравнение без подмены `district`;
- вложенные блоки (`ads`, `house`, `mortgage_calc`, `apartment_types`,
  `egrn_top_novos`) → структурированные поля карточки;
- неизвестные поля не становятся автоматически клиентскими facts;
- `diagnostics` собирает runtime, а не модель.

### 12.5. Стабильная граница между контрактом и данными

Контрактом являются:

1. имена и типы request/response-полей;
2. допустимые режимы поиска;
3. allowlist и evidence map;
4. правила exact/near/missing;
5. правила нормализации и ownership полей;
6. правила ошибок, fallback и валидации.

Не являются контрактом:

- конкретные ЖК и их названия;
- цены, скидки, сроки и остатки квартир;
- количество объявлений и сделок;
- текущие ипотечные программы;
- конкретные школы, парки, станции метро и застройщики.

Источник стабильного списка полей и режимов — `nmbot_v2/search_contract.py`;
источник бизнес-смысла сущностей — `docs/NOVOSTROYM_MCP_SCHEMA.md`.

## 13. Дополнительные обязательные слои контракта

### 13.1. Разделение уровней

В системе есть три разных слоя, их нельзя смешивать:

1. **Planner/query profile** — определяет `purpose`, `facets`, `need`,
   `exclude`, intent и сценарий.
2. **Executable MCP request** — typed request из раздела 12: `search_goal`,
   `constraints`, viewpoints, allowlist, mode и `facts_needed`.
3. **Transport payload** — доставка search-запроса через gateway:

```json
{
  "_payload_stage": "main_search",
  "query": "SEARCH_CONTRACT_ENVELOPE=...",
  "service": "openrouter",
  "model": "<search model>",
  "system_prompt": "<v2 search prompt>",
  "parameters": {"temperature": 0.1, "max_tokens": 5000},
  "mcp_servers": ["novostroym"]
}
```

Transport-поля не являются MCP-фактами и не должны попадать в клиентский
ответ. Секреты (`external_api_key`) не являются частью документационного
примера и никогда не логируются.

### 13.2. Обязательная форма `search_goal`

```json
{
  "entity_type": "new_building_flat",
  "query_summary": "normalized search intent",
  "explicit_terms": []
}
```

Для exact named lookup добавляются:

```json
{
  "entity_reference": "Каноническое название ЖК",
  "lookup_mode": "exact_named_object"
}
```

`entity_type`, непустой `query_summary` и массив `explicit_terms` обязательны.
Из `query_summary` нельзя выводить новые hard-фильтры: строгие ограничения
берутся только из `effective_hard`.

### 13.3. Строгая форма response

Корень ответа обязан быть JSON-object с точным набором ключей:

```text
facts: list
near: list
missing: list
params: object
diagnostics: object
```

`diagnostics` обязан содержать ровно:

```text
mcp_tool
response_viewpoint
base_viewpoint
requested_field_priorities
relaxation_audit
ignored_preferences
notes
```

Любой другой top-level key или diagnostic key — contract error. Модель владеет
только semantic-контейнерами `facts`, `near`, `missing`, `params`; runtime
пересобирает `params` и `diagnostics` из typed request.

### 13.4. Статусы валидации

Validator возвращает:

```text
valid    — ошибок и contract warnings нет;
degraded — контракт соблюдён, но есть безопасные предупреждения;
invalid  — есть contract error/blocker.
```

Диагностический результат также содержит `errors`, `warnings` и `counts`.
Статус остаётся правдивой диагностикой, но в production V0/V2/V3 не является
business gate: `invalid` и `degraded` не скрывают пригодные идентифицируемые
карточки. Они создают bounded `search_validation_report`. Offline quality gate
может считать `invalid` провалом теста. Malformed root/container и safety/schema
boundary по-прежнему нельзя превращать в клиентскую карточку.

### 13.5. Sanitation и безопасность

До формирования MCP query runtime удаляет или редактирует:

- телефон, email, token, secret, password;
- чувствительные поля `client`, `chat_id`, `raw`, `payload`;
- неизвестные hard/preferences keys;
- значения, которые превышают допустимый размер.

Для карточек runtime:

- удаляет поля вне `available_fact_fields`;
- отбрасывает не-object карточки;
- отбрасывает карточки без `id`, `alias` или `name`;
- не переносит неизвестные поля в `facts`.

### 13.6. Лимиты

Контракт фиксирует следующие пределы:

- до 3 `current_option_names`;
- до 6 `excluded_names`;
- до 3 записей `relaxation_audit`;
- до 5 diagnostic `notes`;
- до 10 элементов в безопасных списках;
- строки и безопасные значения ограничиваются runtime-санитизацией.

### 13.7. Exact/near report-only normalization

Перед validation runtime обязан:

1. сохранить identifiable объекты из `excluded_names` и зафиксировать violation;
2. сохранить named mismatch в diagnostics; exact named/current-option runtime
   отдельно применяет scope guard и не показывает чужой объект как искомый;
3. сообщить о дублях, не удаляя их business-validator'ом;
4. для `near` выставить `is_near=true`;
5. добавить runtime-owned `why_close` из structured difference или безопасное
   `неполное подтверждение условий`;
6. не показывать карточку без `id`, `alias` или `name`.

Для `current_options_fact_check` любой посторонний ЖК — ошибка `foreign_object`;
scope guard не даёт показать его как выбранный объект, даже если его предложила
модель. Это safety/scope boundary, а не общий business-validator поиска.

### 13.8. Обязательная contract-test matrix

Минимальный набор тестов должен покрывать:

- base search;
- family;
- financing overlay;
- investment;
- rental;
- rooms + budget + location;
- ready + finishing;
- district + human location;
- exact facts vs near;
- missing evidence;
- runtime relaxation;
- unknown preference;
- broad candidates with later enrichment;
- current-options fact-check.

Каждый кейс проверяет одновременно request shape, response shape, hard
matching, allowlist, separation `facts/near`, `missing` и сохранение
контрактных полей.

### 13.9. Завершённость hard-field contract

`min_price`, `area_min_m2` и `area_max_m2` закрыты в executable contract:
они имеют entries в `HARD_EVIDENCE_MAP`, structured evidence-проверку,
boundary-matcher и focused regression tests. Для них действует то же правило,
что и для остальных hard fields: нет structured numeric evidence — нет exact
`facts`.

Любое добавление hard-поля считается завершённым только после обновления:

```text
HARD_KEYS
→ HARD_EVIDENCE_MAP
→ _matches_hard / hard_evidence_present
→ normalizer
→ fixture и contract tests
```

## 14. Ошибки, fallback, provenance и trace

### 14.1. Коды contract validation

Минимальные коды ошибок валидатора:

```text
top_level_keys_mismatch
forbidden_top_level_keys:<keys>
facts_must_be_list
near_must_be_list
missing_must_be_list
params_must_be_object
diagnostics_must_be_object
diagnostics_extra_keys:<keys>
diagnostics_missing_keys:<keys>
diagnostics_mcp_tool_mismatch
diagnostics_response_viewpoint_mismatch
diagnostics_base_viewpoint_mismatch
relaxation_audit_mismatch
fact_<n>_has_non_whitelisted_fields:<keys>
fact_<n>_missing_hard_evidence:<field>
fact_<n>_violates_hard:<field>
params_extra_keys:<keys>
params_not_effective_hard:<field>
unknown_preferences_not_reported:<keys>
near_duplicates_facts
absence_claim_without_hard_evidence
```

Ошибки означают `invalid`, а не «частично пригодный ответ».

### 14.2. Ошибки парсинга

До contract validation выполняется strict JSON parsing:

- невалидный JSON → `invalid_strict_json:<reason>`;
- корень не object → `json_root_must_be_object`;
- отсутствие ответа после отправленного request →
  `mcp_response_missing_after_request`;
- ненулевой код CLI/transport → `cli_exit_nonzero:<code>`.

`mcp_request` без `mcp_response` не является пустым результатом поиска. Это
ошибка transport/parser и не может оцениваться как корректный MCP-ответ.

### 14.3. Fallback-порядок

Fallback не меняет search-контракт и не имеет права придумывать карточки.

1. primary search provider;
2. provider retry/fallback при upstream/safe error;
3. fallback provider, если результат пустой или broad-shortlist underfilled;
4. безопасный ответ без новых объектов, если пригодного результата нет.

Пригодный fallback обязан пройти тот же strict JSON parsing, normalization,
allowlist и hard validation. Быстрый, но invalid/empty fallback не побеждает
валидный результат.

Timeout, parse error, contract error и identity mismatch не должны стирать
уже сохраненную canonical card. Для selected enrichment сохраняется base card и
фиксируется честный missing/timeout outcome.

### 14.4. Provenance результата

Каждый результат должен иметь внутренний источник:

```text
primary
provider_retry
repaired
fallback
cached_base
selected_enrichment
```

Отдельно сохраняются слои:

```text
mcp_request       — что отправили;
mcp_response      — сырой structured response;
normalized_result — результат после sanitation/validation;
canonical_card    — карточка для presenter.
```

Нельзя считать факт подтверждённым, если он появился только в `mcp_request`,
свободном тексте модели или fallback-тексте.

### 14.5. Timeout и retry boundary

Нужно различать:

- MCP/provider timeout;
- gateway timeout;
- parser/validation timeout;
- внешний Jivo webhook timeout.

Повтор provider не считается новым semantic search для state и счётчиков. Если
все попытки завершились ошибкой, наружу уходит только safe fallback, а trace
сохраняет причину и число попыток.

### 14.6. Минимальный trace

Внутренний trace должен связывать попытку с результатом через безопасные поля:

```json
{
  "trace_id": "...",
  "event_id_ref": "...",
  "payload_stage": "main_search",
  "search_mode": "broad",
  "provider_attempt": 1,
  "status": "valid|degraded|invalid|timeout|fallback",
  "parse_status": "ok|invalid_json|missing",
  "validation_errors": [],
  "validation_warnings": [],
  "fallback_used": false,
  "duration_ms": 0
}
```

Trace не должен содержать raw payload, prompt с персональными данными, токены,
телефоны, email, Authorization или клиентский текст.

## 15. Границы прямого MCP-протокола

В текущем проекте нет отдельного локального контракта прямого вызова
`tools/list`/`tools/call`. Фактически реализован следующий путь:

```text
typed V2SearchRequest
  → SEARCH_CONTRACT_ENVELOPE
  → gateway request_data
  → OpenRouter model + mcp_servers=["novostroym"]
  → structured search response
```

Поэтому `novostroym/get_flat_info` является каноническим **tool identity**, а
не самостоятельным локальным HTTP API с документированной схемой аргументов.
Нельзя добавлять в контракт выдуманный `tools/call` payload до появления
реального adapter/fixture для него.

## 16. Версия и совместимость

В контракте нужно различать версии:

```text
contract = v2_search_mcp_contract
IntentPlanV3 schema_version = 3 (это planner/runtime intent contract, не search response)
```

Правила совместимости:

- неизвестная версия контракта блокирует обработку;
- изменение top-level keys, hard semantics, allowlist или types — breaking
  change;
- изменение динамических значений каталога — не изменение контракта;
- fixture, prompt и validator обновляются одной версией;
- старую fixture нельзя считать доказательством нового контракта без миграции.

## 17. Схема одного элемента `facts` / `near`

Каждый элемент обязан быть object и иметь идентификатор через одно из полей:
`id`, `alias` или `name`. Остальные поля — только из
`available_fact_fields`.

Для `facts`:

```text
is_near отсутствует или false
все effective_hard подтверждены
hard matcher проходит
```

Для `near`:

```text
is_near = true
why_close — краткое подтверждённое объяснение
differences — явные отличия от hard-запроса
```

Карточка без идентификатора, не-object карточка и карточка с неизвестным
полем отбрасываются runtime, а не передаются дальше как частичный факт.

## 18. Канонизация входных параметров

До построения `effective_hard` runtime применяет только известные aliases:

```text
budget_max  → max_price
price_max   → max_price
max_budget  → max_price
budget      → max_price
room_count  → rooms
rooms_count → rooms
```

География нормализуется отдельно:

```text
Москва              → district=msk
Новая Москва        → district=newmsk
МО / Подмосковье    → district=mo
конкретный район    → location
```

Сценарий, естественный текст и preference не могут незаметно стать новым
hard-полем.

## 19. Матрица состояний результата

| Состояние | Что произошло | Можно публиковать как MCP facts |
|---|---|---|
| `valid` | JSON и contract validation прошли | Да |
| `degraded` | Схема прошла, есть warnings/missing | Да, с честной границей |
| `empty_valid` | Ответ получен, `facts=[]`, `near=[]` | Да, как пустой поиск |
| `invalid_json` | Ответ не распарсился | Нет |
| `contract_invalid` | JSON есть, схема нарушена | Нет |
| `transport_missing` | Request есть, response нет | Нет |
| `timeout` | Provider/gateway не успел | Нет, сохраняется base/cached card |
| `identity_mismatch` | Enrichment вернул другой ЖК | Нет для enrichment |
| `fallback` | Пригодный результат получен другой попыткой | Да, с provenance |

`empty_valid` нельзя трактовать как `inventory_absent`: пустой список не
доказывает отсутствие квартир без соответствующего hard evidence.

## 20. Что не входит в MCP-контракт

Следующие решения принадлежат planner/runtime/presenter, а не MCP search:

- выбор маршрута и intent;
- operator handoff и запрос телефона;
- текст ответа клиенту и финальный вопрос;
- inline-кнопки и UI;
- изменение conversation state;
- решение о бронировании, звонке или продаже;
- маркетинговая оценка «лучший», «ликвидный», «высокий спрос»;
- генерация фактов из `query_summary`, prompt или fallback text.

## 21. Типы wire-полей

Контракт фиксирует не конкретные значения, а допустимые формы wire-данных:

| Поле/группа | Допустимые wire-формы | Каноническое использование |
|---|---|---|
| `rooms` | number, string, list | отдельные room-токены |
| `delivered` | boolean, `0`, `1` | `1` — evidence «сдан»; `0` — не сдан |
| `ready/state/status` | string, enum-like string | структурированный readiness fact |
| `location` | string, list[string] | отдельная human location |
| `district` | `msk`, `mo`, `newmsk` | только региональный код |
| prices | positive number, string range | numeric matching или display range |
| `ads`, `house`, `apartment_types` | object, list[object] | вложенная structured data |
| `mortgage_calc`, `mortgage` | object, list[object] | finance evidence |
| `egrn_top_novos`, `counter_novos` | object, list[object] | aggregate evidence |

Неизвестная форма не должна молча превращаться в факт. Runtime либо
нормализует её по контракту, либо сохраняет поле в `missing`/diagnostic warning.

## 22. Категории `missing`

`missing` описывает состояние подтверждения данных, а не наличие или отсутствие
квартир:

```text
requested_but_unavailable — поле запрошено, но MCP его не вернул;
requested_but_unconfirmed — поле есть в общей карточке, но не подтверждает
                             нужный hard/evidence уровень;
malformed_evidence        — поле пришло в неподдерживаемой форме;
hard_evidence_missing     — объект нельзя поместить в facts;
enrichment_timeout        — точечный запрос не успел завершиться;
provider_unavailable      — источник не ответил.
```

Для совместимости presenter может также видеть безопасные presentation-категории:
`finance`, `family_infrastructure`, `walk_infrastructure`,
`safety_infrastructure`, `sales`, `ads`, `location`, `budget`, `rooms`,
`readiness`, `finishing`, `details`. Новые произвольные категории не
добавляются моделью: runtime либо распознаёт поле/причину, либо нормализует к
`requested_but_unconfirmed`.

Правила:

- `missing` не равен `inventory_absent`;
- `missing` не равен transport error;
- отсутствие `ads.fullprice` не доказывает отсутствие квартир;
- отсутствие `school` не доказывает отсутствие школы;
- malformed hard evidence блокирует `facts`, пока не выполнено enrichment.

## 23. Ownership полей

| Поле/слой | Владелец | Правило |
|---|---|---|
| `facts` | search model + runtime | только allowlisted MCP facts |
| `near` | search model + runtime | runtime проверяет отличие и scope |
| `missing` | search model + runtime | runtime сохраняет только безопасные категории |
| `params` | runtime | `effective_hard + accepted preferences` |
| `diagnostics` | runtime | пересобирается из typed request |
| `schema_version` | canonical runtime | не выводится моделью |
| `provenance` | runtime/trace | источник и попытка результата |
| `client_text` | answer/presenter | не является MCP response |

Модель не может изменить state, effective constraints, relaxation audit,
diagnostics, provenance или список уже показанных объектов.

## 24. Machine-readable schema boundary

Нормативным источником executable-правил остаётся
`nmbot_v2/search_contract.py`. Документ и fixture являются описанием и
контрольными примерами.

Машиночитаемые contract artifacts:

- `schemas/v2_search_mcp_request.schema.json` — JSON Schema Draft 2020-12 для
  executable request envelope (`V2SearchRequest.to_payload()` shape), без live
  catalog values;
- `schemas/v2_search_mcp_response.schema.json` — strict response shape с exact
  top-level keys `facts`, `near`, `missing`, `params`, `diagnostics` и семью
  diagnostics keys;
- `docs/MCP_APARTMENT_CONTRACT_GOLDENS.md` — compact synthetic goldens для
  broad, named object, current-options fact-check, family+financing, near,
  missing, degraded и invalid cases.

Эти файлы — contract artifacts, но не заменяют executable validator. Они должны
синхронно покрывать:

```text
request envelope
search_goal
constraints
response top-level keys
diagnostics exact keys
fact/near item shape
hard evidence map
forbidden keys
```

Нельзя принимать JSON Schema, если она расходится с typed contract,
`validate_search_output()` или fixture. В strict search response нет
`schema_version`: `schema_version=3` из `nmbot_v2/contracts.py` относится к
`IntentPlanV3`, а не к search-agent response envelope.

### 24.1. Schema ↔ runtime consistency gate

`tests/test_mcp_contract_artifacts.py` — быстрый contract gate для JSON Schema и
синтетических артефактов. Он не ходит в live MCP и не проверяет каталог; задача
теста — поймать расхождение между machine-readable schema и executable runtime
contract до того, как оно попадёт в prompt/golden.

Проверяемые invariants:

- request `count` всегда `1..5`; `count=0` запрещён, как и в runtime validator;
- search response имеет только top-level keys `facts`, `near`, `missing`,
  `params`, `diagnostics`;
- `facts[]` и `near[]` валидируются разными item-схемами;
- `near[]` требует `is_near=true`, `why_close` и непустой `differences[]`;
- `facts[]` не требует near-полей и не может помечаться `is_near=true`;
- `missing[]` ограничен allowlisted MCP field names или безопасными категориями
  `requested_but_unavailable`, `requested_but_unconfirmed`,
  `malformed_evidence`, `hard_evidence_missing`, `enrichment_timeout`,
  `provider_unavailable` и совместимыми presentation-категориями из раздела 22;
- `diagnostics.notes` ограничен максимум пятью элементами.

Дополнительно `test_executable_request_to_normalized_response_contract_round_trip`
проверяет полный локальный путь: `V2SearchRequest.to_payload()` и query envelope
проходят request schema, затем normalized response проходит response schema и
`validate_search_output()`. Это consistency gate без live MCP и без текущих
значений каталога.

## 25. Процедура изменения версии

Любое изменение контракта проходит порядок:

1. описать Actual / Contract / Desired;
2. определить breaking или additive change;
3. обновить `search_contract.py`;
4. обновить prompt и fixture;
5. обновить normalizer/matcher;
6. добавить regression case;
7. прогнать strict validation и contract matrix;
8. обновить `contract`/`schema_version` при breaking change;
9. зафиксировать migration note.

Динамическая смена цены, наличия, школы, скидки или срока сдачи не требует
повышения версии контракта.

## 26. Статус покрытия контрактных кейсов

| Кейс | Контрактный источник | Статус фиксации |
|---|---|---|
| base search | fixture `base_search` | описан |
| family | fixture `family` | описан |
| financing overlay | fixture `family_financing_overlay` | описан |
| investment | fixture `investment` | описан |
| rental | fixture `rental` | описан |
| rooms/budget/location | fixture `rooms_budget_location` | описан |
| ready/finishing | fixture `ready_finishing` | описан |
| district/location | fixture `district_location_separation` | описан |
| exact vs near | fixture `exact_facts_vs_near` | описан |
| missing evidence | fixture `missing_data` | описан |
| relaxation | fixture `one_actual_constraint_relaxation` | описан |
| unknown preference | fixture `unknown_preference_ignored` | описан |
| later enrichment | fixture `broad_candidates_later_enrichment` | описан |
| current options fact-check | `search_mode=current_options_fact_check` | описан отдельно |
| machine-readable request schema | `schemas/v2_search_mcp_request.schema.json` | описан |
| machine-readable response schema | `schemas/v2_search_mcp_response.schema.json` | описан |
| compact goldens | `docs/MCP_APARTMENT_CONTRACT_GOLDENS.md` | synthetic only |

«Описан» означает наличие contract fixture/правила. Это не утверждение о
текущем live-покрытии MCP или о динамических данных каталога.

## 27. Правила, подтверждённые реальными запросами

Этот раздел сформирован по сохранённым interaction records из
`logs/dialogs-2026-07-04.jsonl`, `logs/dialogs-2026-07-05.jsonl`,
`logs/dialogs-2026-07-09.jsonl` и `logs/dialogs-2026-07-13.jsonl`. Эти записи
показывают реальные wire-варианты и служат regression evidence; они не заменяют
канонический executable contract.

### 27.1. Источник истины запроса

- Канонический источник hard-условий — структурированный request payload:
  `requested_hard`, `effective_hard`, `purpose`, `facets`, `selected_option_name`,
  `visible_options`, `exclude` и другие поля request schema.
- `effective_query` и естественный текст клиента — explanatory context для
  planner/search-agent. Они не заменяют structured hard-поля.
- Исторические записи часто содержат в `mcp_request` только `purpose` и `count`,
  хотя реальные ограничения находятся в `effective_query`. Такой payload нельзя
  считать корректным каноническим запросом: hard-условия должны быть вынесены в
  структурированные поля до вызова MCP.

### 27.2. Канонические типы и legacy-нормализация

- Числовые границы `min_price`, `max_price`, `area_min_m2`, `area_max_m2` внутри
  контракта — numbers. Строковые `price_range`/`area` допустимы только как
  display-поля и не участвуют в hard matching.
- `rooms` нормализуется в отдельные канонические токены (например, `s`, `1`,
  `2`, `3`), а не проверяется поиском по подстроке.
- Каноническая форма `missing` — всегда `string[]`. Legacy string превращается
  в массив из одного элемента, object — в безопасную строку/категорию после
  runtime normalization; отсутствие поля означает пустой список.
- Legacy `near[].why_close` переносится в `near[].differences[]`. Поле
  `why_close` может сохраняться для человекочитаемого ответа, но semantic
  validation использует именно structured differences.
- Legacy numeric flags (`delivered: 0/1`, `yard_without_cars: 0/1`) сначала
  приводятся к boolean semantics; свободный текст не становится evidence без
  подтверждённого structured поля.

### 27.3. Дубликаты и near

- В `facts` и `near` нельзя публиковать один и тот же объект несколько раз.
- Deduplication выполняется по стабильному `id`; если `id` отсутствует — по
  нормализованной паре `name + location`.
- `near` обязан отличаться от exact-кандидата конкретным hard mismatch,
  отсутствующим structured evidence или явно зафиксированным ограничением
  enrichment. Похожесть сама по себе не является причиной включения в `near`.

### 27.4. Матрица реальных сценариев

| `purpose` | Примеры реальных запросов | Обязательные request-поля/overlay |
|---|---|---|
| `search` | «Найди однушку до 8 млн в Москве», «Студия в Москве до 5 млн» | `rooms`, `max_price`, `district/location` |
| `family` | «Двушка для семьи в Москве» | `rooms`, family `need`/facets: школы, сады, парки, двор без машин |
| `rental` | «Нужен вариант под сдачу в аренду» | `purpose`, компактность, `finishing`, метро, `ready`, подтверждённый demand signal |
| `investment` | «Нужна однушка под инвестиции с ипотекой» | `purpose`, entry price, compact lots, mortgage overlay, EGRN/ads evidence |
| `mortgage`/overlay | «А семейная ипотека есть?» | `facets:["mortgage"]`, `mortgage_type` при известном типе |
| `fact_check` | «Точно есть двор без машин?» | `selected_option_name`, `visible_options`, `fact_to_check` |
| `repeat_search` | «Покажи другие варианты» | `visible_options`, `exclude`, сохранённые hard-условия предыдущего поиска |
| `operator` | «Позови менеджера по выбранному ЖК» | выбранный объект и контактный flow; не маскировать отсутствие объекта поиском |
| no-result/unsupported | Санкт-Петербург, бюджет до 5–8 млн, «двушка с отделкой у МКАД» | `missing[]` с причиной и допустимым явным предложением ослабления |

### 27.5. Repeat search и fact-check

- `repeat_search` передаёт `purpose="repeat_search"`, `visible_options` и
  `exclude`; исключения должны распространяться на MCP search, а не только на
  presenter. В ответ нельзя повторно возвращать исключённые объекты.
- `fact_check` — отдельный режим проверки выбранного объекта, а не новый
  широкий подбор. Минимальная связка: `selected_option_name` +
  `fact_to_check`; `visible_options` ограничивает допустимый набор объектов.
- Для fact-check нельзя превращать отсутствие поля в подтверждение. Ответ
  должен различать «подтверждено», «не подтверждено» и «данных нет».

### 27.6. Ипотека и инвестиционные утверждения

- Ипотечные факты требуют structured evidence: `mortgage_calc`, ставка,
  `min_fee`, `credit_month`, `discount`, `payment_by_installments` и цена,
  к которой относится расчёт.
- `family_mortgage` — тип программы, а не доказательство доступности каждому
  клиенту. Если eligibility или актуальные условия отсутствуют, это должно
  попасть в `missing[]`; модель не вычисляет и не обещает условия по памяти.
- Для rental/investment нельзя называть объект ликвидным или доходным только по
  локации, классу или числу объявлений. Нужен подтверждённый MCP-сигнал; прямые
  данные доходности при их отсутствии явно помечаются как недоступные.

### 27.7. No-result и запрет скрытого relaxation

- `missing[]` объясняет, какое условие не выполнено или какое evidence не
  подтверждено. Это не generic error и не разрешение молча ослабить поиск.
- Любое ослабление возможно только явно, через runtime
  `relaxation_audit`, с предложением пользователю выбрать, чем пожертвовать:
  например, отделкой или расстоянием от МКАД.
- Unsupported region (например, Санкт-Петербург при каталоге Москвы и МО)
  должен возвращать границу каталога, а не альтернативный объект из другой
  географии.

### 27.8. Два независимых уровня проверки

1. JSON Schema проверяет форму payload: типы, обязательные поля, допустимые
   top-level keys и структуру `facts/near/missing/diagnostics`.
2. `nmbot_v2.search_contract.validate_search_output()` проверяет семантику:
   hard matching, structured evidence, exact vs near, deduplication и
   relaxation invariants.

Прохождение JSON Schema само по себе не означает, что объект соответствует
запросу клиента. И наоборот, semantic validator не заменяет проверку wire-shape.

## Источники и владельцы контракта

- `docs/NOVOSTROYM_MCP_SCHEMA.md` — схема базы, поля, связи и SQL safety;
- `prompts/v2_search_mcp.txt` — постоянный system prompt search-agent;
- `docs/NMBOT_V2_MCP_PROMPT_BUILD_RULES.md` — сборка envelope и prompt gate;
- `docs/SCENARIO_MCP_CONTRACT.md` — сценарии, facets и обязательные evidence;
- `docs/IDEAL_IRINA_UX.md` — клиентский UX и правила state;
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md` — quality/release/first-failure gate;
- `tests/fixtures/v2_search_mcp_contract.json` — исполняемая fixture-копия
  wire-форм и сценарных assertions.
- `nmbot_v2/search_contract.py` — executable request, режимы, allowlist,
  evidence map и hard keys.
- `schemas/v2_search_mcp_request.schema.json` — machine-readable request shape
  для executable search envelope; generic only, без live catalog values.
- `schemas/v2_search_mcp_response.schema.json` — machine-readable strict
  response shape; без `schema_version`, потому что search response его не несёт.
- `docs/MCP_APARTMENT_CONTRACT_GOLDENS.md` — synthetic readable goldens и
  expected validator status.
- `docs/LLM_SCENARIO_EVAL_RUBRIC.md` — разделение planner/query profile,
  `mcp_request`/`mcp_response` и порядок изменений.

Канонический allowlist и hard-matchers должны жить в
`nmbot_v2/search_contract.py`; probe, quality gate и runtime не должны держать
собственные копии схемы.
