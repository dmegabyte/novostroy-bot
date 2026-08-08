# NMBot V6 — единый search/enrichment/answer контур

Статус: **контракт и схема текущего V6-контура**.

Этот документ описывает ownership и последовательность V6. Он не заменяет
`MCP_APARTMENT_REQUEST_RULES.md`, `NOVOSTROYM_MCP_SCHEMA.md` и фактический
release/runtime trace.

Реализация selected-enrichment находится в V6 runtime adapter: после сохранения
broad `visible_options` следующий exact выбор вызывает существующий bounded
`_OvermindSearchAdapter.enrich_selected()`. Проверка контура покрыта
`tests/test_nmbot_v6.py`; production activation требует отдельного deploy и
Jivo smoke.

## 1. Цель

V6 должен получать не только название ЖК, а подтверждённую карточку выбранного
ЖК и конкретных лотов, после чего формировать человеческий ответ через
существующий writer. V5 не является fallback для V6.

## 2. Архитектурное решение: один search prompt

V6 не добавляет отдельный модельный classifier перед поиском. Для обычного
запроса используется один утверждённый search prompt, который одновременно:

- определяет `action`, `target`, `search_policy` и изменившиеся `params`;
- при `search_policy=required` вызывает MCP `novostroym/get_flat_info`;
- возвращает только JSON с `facts`, `near`, `missing`, `params` и action-полями.

V6 — это versioned runtime identity, bounded selected enrichment и evidence
gate. Это не второй semantic model layer. `/start`, телефон и уже начатый
operator flow остаются code-owned механическими исключениями.

Правило упрощения: сначала переиспользовать этот единый search prompt и
существующий canonical V2 MCP owner. Новый router/classifier/search adapter
можно добавлять только с доказательством, что текущий owner не может выполнить
контракт.

```text
Jivo/API
  → code-owned ingress/safety
  → один unified search prompt
      → MCP novostroym, если search_policy=required
  → deterministic validator/evidence gate
  → state reducer
  → existing presenter/writer, если нужен текст
  → Jivo BOT_MESSAGE
```

## 3. Основная схема

```text
Jivo / API
  │
  ├─ start/reset/phone/callback/operator?
  │      └─ да → code-owned flow, без search-модели
  │
  ▼
Unified Search Prompt
  │  Gateway-agent → OpenRouter → MCP novostroym
  ▼
1. Broad search
  │  район, комнаты, бюджет; shortlist до 3 ЖК
  ▼
Выбранный exact ЖК + novos_id
  ▼
2. Selected enrichment
  │  точный `novos_id`, комнатность, `facts_needed`
  │  ads, apartment_types, house, metro, ready,
  │  finishing, developer, infrastructure
  ▼
MCP result
  ▼
Evidence gate / normalizer
  │  aliases → canonical fields
  │  hard checks → OptionCard + LotExample
  │  ads.id/state/status → availability proof
  ▼
ResponseBrief
  │  только подтверждённые карточки и разрешённые claims
  ▼
Existing response writer
  ▼
Response validator
  ├─ valid → Jivo BOT_MESSAGE
  └─ invalid → deterministic rich fallback
```

## 4. Два MCP-запроса

### 3.1 Broad shortlist

Первый запрос отвечает на вопрос: **какие ЖК подходят?**

```json
{
  "search_mode": "broad",
  "count": 3,
  "location": "Люблино",
  "rooms": 2
}
```

Результат содержит кандидатов и структурированные ID. Broad-карточка может
содержать только проектные данные: название, локацию и диапазон цен. Это ещё не
полная карточка двушки.

### 3.2 Selected enrichment

После выбора ЖК второй запрос отвечает: **что именно есть внутри выбранного ЖК?**

```json
{
  "search_mode": "named_object",
  "selected_option_name": "Люблинский парк",
  "novos_id": 2018,
  "rooms": 2,
  "count": 1,
  "facts_needed": [
    "ads",
    "apartment_types",
    "house",
    "property_metro",
    "ready",
    "finishing",
    "developer",
    "school",
    "kindergarten",
    "park_near",
    "yard_without_cars",
    "transport"
  ],
  "scope_policy": "exact_selected_name_only"
}
```

Широкий поиск после выбора ЖК не повторяется. Selected enrichment не должен
возвращать похожие ЖК в `near`.

## 5. Evidence и наличие лота

Проектная цена (`price1`, `price2`, `price_range`) не равна наличию конкретной
квартиры.

Публичное утверждение «двушка в продаже» разрешено только при наличии:

```text
ads.id
ads.state = 2
ads.status = 2
```

Для клиентской карточки можно использовать только нормализованные поля:

- `developer`;
- `property_metro` / `metro`;
- `school`, `kindergarten`, `park_near`;
- `yard_without_cars`, `transport`;
- `rooms`, `area`, `fullprice`;
- `ready`, `finishing`;
- `LotExample` с ID, площадью, этажом, ценой и статусами.

Если поле не пришло из selected enrichment, оно попадает в `missing`; это не
доказательство отсутствия такого объекта.

## 6. Что получает writer

Writer не получает raw MCP, prompt, SQL, task ID или provider metadata. Он получает
code-owned `ResponseBrief`:

```json
{
  "answer_goal": "present_search_results",
  "user_question": "двушка в мкр Люблино",
  "canonical_cards": [
    {
      "name": "Люблинский парк",
      "location": "Люблино",
      "developer": "ПИК",
      "property_class": "комфорт-класс",
      "metro": "Братиславская — 15 минут пешком",
      "price": "от 19,08 млн ₽",
      "area": "56,4–56,9 м²",
      "finishing": "с отделкой",
      "infrastructure": [
        "3 школы",
        "7 детских садов",
        "парки",
        "двор без машин"
      ],
      "lot_examples": [
        {
          "id": 6527769,
          "area": 56.4,
          "floor": 13,
          "full_price": 19080458,
          "state": 2,
          "status": 2
        }
      ]
    }
  ],
  "allowed_claims": [
    "Используй только факты из canonical_cards",
    "Не добавляй неподтверждённые характеристики",
    "Не добавляй другие ЖК"
  ],
  "cta": "Какой вариант показать подробнее?"
}
```

Writer отвечает за стиль и связный текст. Он не восстанавливает пропущенные
данные и не подтверждает наличие самостоятельно.

## 7. Ошибка V6

```text
V6 attempt 1
  → gateway/validation error
V6 retry
  → gateway/validation error
code-owned operator offer
  → phone regex capture
  → callback outbox + journal
  → goodbye/confirmation
  → Jivo BOT_MESSAGE или operator handoff
```

На ошибке V6 не вызывает V5. State, телефон, callback, journal и Jivo delivery
остаются code-owned.

## 8. Подтверждённый diagnostic evidence

Контролируемая серия 2026-08-07:

- task `2489985`: broad `novostroym_search_zhk`, Люблино → `Люблинский парк`,
  `novos_id=2018`;
- task `2489989`: selected apartments, `novos_id=2018`, `rooms=2`, `status=2` →
  30 объектов и активный `ads` evidence;
- task `2490008`: exact profile, `novos_id=2018` → метро, транспорт, школы,
  детские сады, парки, двор без машин, застройщик, цены, площади и два
  активных `LotExample`.

Это подтверждает двухэтапную MCP-воронку. Diagnostic task IDs не являются
production proof без свежей Jivo/VPS проверки.
