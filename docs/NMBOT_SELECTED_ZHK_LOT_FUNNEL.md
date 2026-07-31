# NMBot V2 — selected ЖК → lot funnel

Статус: **утверждённая продуктовая воронка; implementation contract**.

Эта воронка считается готовым целевым сценарием V2/Ирины. Она описывает путь
от первого shortlist до выбранной квартиры и передачи оператору. Само наличие
документа не означает, что все стадии уже реализованы в production: готовность
кода подтверждается только тестами и Jivo live gate из раздела Acceptance.

## 1. Цель

Не завершать подбор общей карточкой ЖК. После выбора объекта Ирина должна:

1. выполнить точечный lot-level поиск только по выбранному ЖК;
2. показать до двух конкретных квартир или планировок;
3. сравнить их по подтверждённым характеристикам;
4. помочь клиенту выбрать один лот;
5. только затем предложить проверку динамического наличия и брони;
6. передать оператору выбранный ЖК, лот и мотив клиента.

## 2. Полная цепочка

```text
client need
  → broad shortlist (до 3 ЖК)
  → client selects exact ЖК
  → selected ЖК state
  → bounded exact-name lot enrichment (count=1)
  → dense selected ЖК card + до 2 LotExample
  → client selects format/lot
  → selected lot state
  → live availability/booking boundary
  → operator consent
  → contact name
  → contact phone
  → callback payload with full selected context
```

Широкий повторный поиск после выбора ЖК запрещён. Enrichment может вернуть
только канонический `selected_option_name`; похожие ЖК и `near[]` на этой стадии
не показываются.

## 3. Stage contract

| Stage | Input | Action | Client output | State delta |
|---|---|---|---|---|
| `first_list` | задача, бюджет, параметры | broad MCP search | до 3 разных ЖК + вопрос выбора | `visible_options`, `params`, viewpoint |
| `selected_object` | exact ЖК из `visible_options` | сохранить выбор | короткое подтверждение без повторения shortlist | `selected_option_name` |
| `selected_lot_search` | selected ЖК + viewpoint | exact-name `count=1` enrichment | плотная карточка + до 2 лотов | enriched card, `lot_examples` |
| `selected_lot_comparison` | 1–2 lot examples | deterministic comparison | цена/площадь/этаж/отделка + честная разница | no destructive mutation |
| `selected_lot` | выбранный формат или lot id | сохранить выбранный лот | подробности одного лота | `selected_lot` |
| `selected_live_fact_check` | selected ЖК + lot | проверить dynamic evidence | подтверждение или граница данных | pending consent if needed |
| `operator_handoff` | согласие клиента | открыть контактную воронку | «Как к вам обращаться?» | operator consent accepted |
| `contact_name` | имя | сохранить имя | запрос телефона | pending phone |
| `contact_phone` | телефон | code-level capture | подтверждение передачи | callback/outbox record |

Названия новых lot-stages — целевой контракт. При реализации они могут быть
выражены отдельными `Stage` или строгими recipe/state-подсостояниями, но должны
оставаться наблюдаемыми в trace.

## 4. Exact MCP request

После выбора ЖК нужен один bounded запрос с `count=1`, exact canonical name и
явными группами полей:

```json
{
  "search_mode": "named_object",
  "selected_option_name": "<canonical ЖК>",
  "count": 1,
  "facts_needed": [
    "ads",
    "apartment_types",
    "house",
    "property_metro",
    "ready",
    "finishing",
    "developer",
    "counter_novos",
    "egrn_top_novos"
  ],
  "scope_policy": "exact_selected_name_only"
}
```

Приоритет для `rental`:

- `ads.fullprice`, `ads.area`, `ads.rooms`;
- `ads.floor`, `ads.floors_total`, `ads.renovation`, `ads.status`;
- `apartment_types.area`, `apartment_types.rooms`, `studio`, `euro`;
- `house.name`, `house.finishing_list`;
- `property_metro.on_foot`, `on_transport`, `by_car`;
- project price range, developer, location, readiness;
- `counter_novos.count_ads` и EGRN aggregates только как буквальные счётчики.

Если exact request вернул другой ЖК, identity mismatch отбрасывает enrichment и
сохраняет исходную карточку.

## 5. Canonical lot model

Нужна отдельная структура, а не строка внутри `OptionCard`:

```text
LotExample:
  id: str | int | None
  rooms: str | None
  area_m2: float | None
  floor: int | None
  floors_total: int | None
  full_price: int | None
  renovation: str | None
  status: str | int | None
  house_id: str | int | None
  house_name: str | None
  balcony: str | bool | None
  source: "ads" | "apartment_types"
```

`OptionCard` получает `lot_examples: tuple[LotExample, ...]`, максимум два
клиентских примера. Полный raw MCP payload в state и клиентский ответ не
копируется.

В текущем TEST-контуре запрос актуального наличия выбранного ЖК использует этот
же exact-enrichment с `facts_needed=("lot_examples",)`. Наличие подтверждается
только для normalized lot с непустым ID и активным/in-sale статусом (`ads.state=2`
и `ads.status=2` в базе); число, названное моделью, само по себе не является
подтверждением.

## 6. Provenance и безопасные выводы

### Можно говорить напрямую

- площадь, этаж, полную цену и отделку — только из конкретного `ads`;
- формат — только из structured `rooms/studio/euro`;
- маршрут до метро — только из `property_metro`;
- «в продаже» — только при соответствующем status/state evidence;
- корпус — только если lot явно связан с ним через `house_id/build_id`.

### Можно вычислять

- какой лот дешевле;
- разницу в полной цене;
- какой лот больше и на сколько квадратных метров;
- входит ли полная цена в бюджет клиента.

### Нельзя выводить

- высокий спрос, доходность, окупаемость или арендную ставку;
- что конкретная квартира всё ещё свободна, только из старой карточки;
- что два объявления относятся к корпусу, если в результате нет их связи с
  `house_id`;
- что `count_ads` означает продажи;
- что EGRN contracts гарантируют будущий спрос.

## 7. Dense card presentation

Порядок ответа:

1. **Зачем смотреть ЖК:** один главный аргумент под мотив клиента.
2. **Проект:** застройщик, локация, метро, готовность/отделка — только сильное.
3. **До двух лотов:** формат, площадь, этаж, полная цена, отделка.
4. **Сравнение:** цена против пространства или другой буквальный критерий.
5. **Один вопрос:** выбрать один из показанных лотов.

Не выводить клиенту технические счётчики, если они не помогают текущему выбору.
`count_ads` и EGRN остаются внутренним evidence по умолчанию.

## 8. Ideal full dialogue

```text
Клиент:
Есть что-нибудь под сдачу? У меня на руках 30 млн рублей.

Ирина:
Да, в этом бюджете я бы рассмотрела три варианта.

1. ЖК «2-й Иртышский» — проект бизнес-класса в Гольянове,
цены начинаются от 11,1 млн рублей. В ЖК предусмотрен паркинг.

2. ЖК «Полар» — проект бизнес-класса в Северном Медведкове,
стоимость начинается от 12,2 млн рублей.

3. ЖК «Томилинский бульвар» — проект ПИК в Котельниках,
цены начинаются от 7,5 млн рублей. До метро «Котельники»
около 20 минут пешком.

«Томилинский бульвар» — самый доступный вариант в этой подборке,
поэтому с него удобно начать предметное сравнение квартир.

Какой ЖК вас заинтересовал? Я посмотрю по нему конкретные форматы,
площади и цены.

Клиент:
Томилинский бульвар.

Ирина:
Хорошо, посмотрю по нему конкретные предложения.

Ирина после exact enrichment:
Нашла два предложения с отделкой.

Студия: 19 м², шестой этаж из 25,
стоимость 8 133 900 рублей.

Однокомнатная квартира: 32,8 м², семнадцатый этаж из 25,
стоимость 10 318 880 рублей.

Если хочется начать с меньшего бюджета, стоит посмотреть студию.
Если важнее дополнительное пространство — однокомнатную квартиру.

Какой вариант показать подробнее?

Клиент:
Студию.

Ирина:
Студия площадью 19 м² находится на шестом этаже и продаётся с отделкой.
Она примерно на 2,2 млн рублей дешевле показанной однокомнатной квартиры.
До метро «Котельники» около 20 минут пешком.

Хотите, чтобы оператор перепроверил её актуальность и возможность брони?

Клиент:
А вы сами не можете проверить?

Ирина:
Я могу показать характеристики и цену, которые сейчас есть в каталоге.
Но наличие и возможность бронирования могут измениться, поэтому перед
следующим шагом их лучше подтвердить у оператора.

Передать ему именно эту студию?

Клиент:
Да.

Ирина:
Как к вам обращаться?

Клиент:
Алексей.

Ирина:
Алексей, оставьте номер телефона. Оператор перепроверит студию
площадью 19 м² за 8 133 900 рублей и уточнит, доступна ли она
для бронирования.

Клиент:
+7 999 123-45-67

Ирина:
Спасибо, Алексей. Передала запрос по студии в «Томилинском бульваре».
Оператор проверит актуальное наличие, финальную стоимость и возможность
бронирования и свяжется с вами.
```

### Why this dialogue is the golden path

- shortlist даёт пользу до появления оператора;
- клиент сам выбирает exact ЖК;
- exact enrichment возвращает конкретные лоты, а не второй общий shortlist;
- Ирина сравнивает лоты по цене и площади, не обещая доходность;
- selected lot сохраняется до контактной воронки;
- оператор получает конкретный ЖК, студию, площадь, цену и причину проверки;
- имя запрашивается раньше телефона;
- после телефона ответ сразу подтверждает приём и не заставляет клиента ждать
  фоновой записи карточки.

Operator CTA допустим только после того, как Ирина показала конкретный лот и
дала клиенту пользу. До выбора ЖК или лота ранний operator CTA запрещён.

## 9. Empty/partial enrichment

- Есть два `ads` → показать два и сравнить.
- Есть один `ads` → показать один без выдуманного сравнения.
- Есть только `apartment_types` → показать форматы/площади, не называть их
  доступными квартирами.
- Есть только project-level card → плотная карточка ЖК и вопрос о формате.
- Нет lot evidence → честно сказать, что конкретные квартиры не подтверждены,
  и предложить операторскую проверку после согласия.
- Timeout/provider failure → сохранить исходную карточку; state не повреждать.

## 10. Operator callback payload

В callback/outbox передаются только необходимые данные:

```json
{
  "selected_option_name": "<ЖК>",
  "selected_lot": {
    "id": "<lot id if present>",
    "rooms": "<format>",
    "area_m2": 0,
    "full_price": 0
  },
  "client_goal": "rental",
  "requested_check": ["apartment_inventory", "booking"],
  "contact_name": "<captured>",
  "contact_phone": "<captured>"
}
```

Raw gateway response, internal prompt, task id, secrets и лишние персональные
данные в callback не передаются.

## 11. Impact chain for implementation

```text
search_contract available fields
  → exact enrichment request
  → card_normalizer ads/apartment_types/house parsing
  → LotExample contract
  → OptionCard.lot_examples
  → state selected_lot
  → recipe selection
  → deterministic dense-card renderer
  → quality checker
  → operator callback summary
```

Минимальная поверхность: сначала добавить typed `LotExample` и renderer, затем
подключить selected lot state. Нельзя хранить lot examples только в prose.

## 12. Acceptance criteria

### Static/unit

- exact request содержит canonical ЖК и `count=1`;
- другой ЖК отклоняется identity guard;
- нормализуются два `ads` с price/area/floor/renovation;
- lot без `house_id` не получает выдуманный корпус;
- `count_ads` не называется продажами или спросом;
- вычисление разницы цены детерминировано;
- max two client lot examples;
- ровно один финальный вопрос.

### Stateful

```text
rental shortlist
→ select ЖК
→ exact enrichment
→ select studio
→ ask availability
→ operator consent
→ name
→ phone
→ callback contains selected ЖК + selected lot
```

### Failure cases

- empty ads;
- only apartment types;
- identity mismatch;
- enrichment timeout;
- stale status;
- lot without full price;
- contradictory readiness;
- interrupted contact capture and resume.

### Production gate

1. local targeted tests;
2. backup and deploy only affected V2 files;
3. remote compile and service health;
4. first live selected-ЖК request inspected immediately;
5. Jivo dialogue through lot selection and contact capture;
6. no fresh error events;
7. manual reading against this golden dialogue.

## 13. Live hypothesis evidence, 2026-07-21

Read-only exact probe for `Томилинский бульвар` returned strict-valid
`facts=1`, `near=0` and two `ads`:

- studio, 19 m², floor 6/25, full price 8 133 900, with finishing;
- one-room, 32.8 m², floor 17/25, full price 10 318 880, with finishing.

It also returned project/developer/location/metro and aggregate counters.
`apartment_types` was missing. A house named `5-8` was present, but the compact
result did not expose a per-ad `house_id`, so the golden client text does not
claim both lots belong to that house.

Sources:

- `docs/MCP_APARTMENT_REQUEST_RULES.md`, section 8;
- `docs/NOVOSTROYM_MCP_SCHEMA.md`, `ads`, `apartment_types`, `house`,
  `property_metro`, EGRN and counters;
- `docs/CARD_PRESENTATION_RULE.md`;
- `docs/IDEAL_IRINA_UX.md`, steps 3, 4 and 6.
