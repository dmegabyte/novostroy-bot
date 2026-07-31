# Санитизированный аудит Jivo-диалогов за 2026-07-16

Отчёт санитизирован: в нём нет сырых телефонов, имён клиентов, текстов сообщений, токенов, заголовков авторизации, полных payload, сырых user_id и provider token. Диалоги и события обозначены анонимными ссылками вроде `Jivo-A`, `Jivo-B`, `Smoke-A`, `Trace-A`.

## 1. Scope, timezone, источники и санитизация

Проверка была read-only на live VPS `neiro@193.107.155.236:1905`. Время VPS подтверждено как UTC; дата аудита — 2026-07-16 UTC. Никаких Jivo, LLM, n8n или Telegram вызовов не делалось, сервисы не перезапускались, деплой не выполнялся.

Источники live VPS:

| Источник | Что доказывает | Ограничение |
|---|---|---|
| `/home/neiro/novostroy-bot/logs/n8n_bridge_structured.jsonl` | Факты входящих Jivo webhook, статусы upstream, отправку финального события в Jivo API, latency, channel type, длину сообщения. | По контракту не хранит текст сообщения и сырые id. |
| `/home/neiro/novostroy-bot/logs/n8n_bridge.log` | Дублирующий safe-log bridge request / async send. | Тоже без текста и без payload. |
| `/home/neiro/novostroy-bot/logs/dialogs-2026-07-16.jsonl` | Внутренние события бота: планирование, один logged `user_message`, ответ, search/meta. | Покрывает не все Jivo webhook-события дня; сырой текст в отчёт не вынесен. |
| `/home/neiro/novostroy-bot/logs/model_payload_metrics-2026-07-16.jsonl` | Факт обращений к моделям по стадиям без payload. | Не даёт содержание диалогов. |
| `/home/neiro/novostroy-bot/logs/client_cards-2026-07-16.jsonl` | Проверка downstream-карточек после телефона. | Файл за день отсутствует. |
| systemd status `novostroy-bot-api.service`, `novostroy-bot-n8n-bridge.service` | Оба Jivo-сервиса живы на момент аудита. | Не является доказательством качества ответов. |

Source refs по коду:

- Bridge: `/home/neiro/novostroy-bot/scripts/nmbot_n8n_bridge_server.py:75-121` — быстрый webhook ack и async dispatch; `:145-158` — запрос к локальному боту и последующая отправка в Jivo; `:271-323` — отправка только `BOT_MESSAGE` / `INVITE_AGENT` / `INIT_RATE`; `:362-381` — structured trace без текста сообщения; `:402-408` — safe refs вместо сырых id.
- Jivo API adapter: `/home/neiro/novostroy-bot/scripts/nmbot_api_server.py:246-270` — code-level phone capture в `run_chat`; `:272-285` — запрос оператора переводит state в `awaiting_phone`; `:390-431` — обработка Jivo `CLIENT_MESSAGE` и возврат `INVITE_AGENT`, если `handoff_to_operator`.
- Phone helpers / card path: `/home/neiro/novostroy-bot/scripts/chat_tester_bot.py:4136-4157` — farewell, normalization, extraction; `:4310-4333` — payload карточки содержит телефон и meta; `:4336-4347` — запись `client_cards-YYYY-MM-DD.jsonl`; `:4350-4408` — background save и safe log meta.

## 2. Counts за день

### Jivo bridge structured log

| Метрика | Значение |
|---|---:|
| Всего structured events | 105 |
| Уникальных trace | 18 |
| `CLIENT_MESSAGE` trace | 17 |
| `CHAT_CLOSED` trace | 1 |
| Live Jivo chat refs без smoke | 2 |
| Static smoke trace | 1 |
| Финальных `BOT_MESSAGE` sent в Jivo API | 16 |
| `INVITE_AGENT` в Jivo API | 0 |
| Явных upstream HTTP errors | 0 |
| `event_not_sendable` | 1, это `CHAT_CLOSED`, не бот-ответ |

### Внутренний bot dialog log за день

| Метрика | Значение |
|---|---:|
| JSONL rows | 3 |
| Уникальные anonymous uid refs | 2 |
| Уникальные logged dialog refs | 1 |
| Полные `user_message` rows с bot response | 1 |
| `mcp_request_patch` rows | 1 |
| `user_message_retry_skipped` rows | 1 |
| Phone-like сообщений в этом файле | 0 |
| `phone_captured` rows | 0 |
| `client_cards-2026-07-16.jsonl` | отсутствует |

Вывод по покрытию: bridge видел больше Jivo-событий, чем попало в `dialogs-2026-07-16.jsonl`. Поэтому детальный разбор содержания всех Jivo-сообщений невозможен без сырого dialogue-safe лога: structured bridge сознательно хранит только длину и refs.

## 3. Per-dialogue timeline без PII

| Ref | Тип источника | Время UTC | События | Search / routing evidence | Terminal outcome |
|---|---|---|---|---|
| `Smoke-A` | synthetic/static smoke | 08:30 | 1 `CLIENT_MESSAGE`, widget, короткое сообщение | `static_smoke`, upstream имитирован | `static_smoke`, HTTP OK |
| `Jivo-A` | live Jivo webhook traffic, actual human не доказан | 08:33–10:11 | 13 `CLIENT_MESSAGE` + 1 `CHAT_CLOSED`, channel mostly `chat_page` | Все client turns ушли в upstream; один turn длился около 86.7 сек, остальные быстрее. Внутренний dialog log содержит один `retry_skipped` около 08:33. | 13 финальных `BOT_MESSAGE` sent; `CHAT_CLOSED` не отправлялся обратно как bot event (`event_not_sendable`) |
| `Jivo-B` | live Jivo webhook traffic, actual human не доказан | 10:32–10:37 | 3 `CLIENT_MESSAGE`, channel `chat_page` | Все три turns ушли в upstream и вернули `BOT_MESSAGE`. | 3 финальных `BOT_MESSAGE` sent |
| `Dialog-A` | internal bot dialog log | 09:46 | 1 logged `mcp_request_patch` + 1 `user_message` | `dialog_intent=main_search`, search evidence present, bot response saved, `is_error=false` | bot response produced; no phone capture |

Категории поведения:

- `Smoke-A` — синтетика по прямому marker `static_smoke`.
- `Jivo-A` и `Jivo-B` — реальные live webhook-события из Jivo bridge, но без raw text нельзя честно утверждать, что это именно живые клиенты, а не ручные/P1 проверки через Jivo UI.
- `Dialog-A` — внутренний logged bot turn с `main_search`; это единственный turn, где есть evidence по routing/search внутри `dialogs-2026-07-16.jsonl`.

## 4. Phone-number audit

### Что найдено в live evidence

| Проверка | Результат |
|---|---|
| Phone-like в `dialogs-2026-07-16.jsonl` | 0 |
| `phone_captured` events | 0 |
| `client_card_saved` events | 0 |
| `client_cards-2026-07-16.jsonl` | файла нет |
| `INVITE_AGENT` через Jivo bridge | 0 |
| Доказательство попадания телефона в downstream CRM/operator | нет evidence |

Важно: «нет evidence» не равно «не сработало». Bridge structured log не хранит текст Jivo-сообщений, поэтому по нему нельзя увидеть, был ли в Jivo-сообщениях телефон. Внутренний `dialogs-2026-07-16.jsonl` телефонов не показывает, а файла карточек за день нет.

### Как телефон должен проходить по коду

| Шаг | Код / source ref | Факт |
|---|---|---|
| Нормализация | `chat_tester_bot.py:4143-4145` | Оставляет цифры и `+`. |
| Детект | `chat_tester_bot.py:4152-4157` | Валидирует длину цифр от 10 до 15. |
| Jivo `run_chat` capture | `nmbot_api_server.py:255-270` | Если `awaiting_phone` и номер найден, state получает `last_phone`, ответ — phone farewell, `handoff_to_operator=true`. |
| Jivo handoff | `nmbot_api_server.py:429-431` | Если `handoff_to_operator` и agents online не false, adapter возвращает `INVITE_AGENT`. |
| Telegram/card path | `chat_tester_bot.py:4310-4408` | Карточка клиента может быть записана в `client_cards`, но live evidence за 2026-07-16 такого файла не имеет. |

Риск по privacy: в Jivo API path `state["last_phone"]` хранит нормализованный полный номер в state (`nmbot_api_server.py:256-259`). В отчёт номер не попал, но это место стоит отдельно проверить как P1 privacy-hardening.

## 5. Actual / Contract / Desired

| Тема | Actual evidence | Contract evidence | Desired |
|---|---|---|---|
| Сырой текст Jivo-сообщений | В structured bridge его нет; есть только length/ref/channel. | Bridge source явно обещает не логировать text/payload (`nmbot_n8n_bridge_server.py:362-381`). | Добавить отдельный sanitized dialogue audit log: intent/category/phone_detected boolean/masked phone ref, без raw text. |
| Завершение Jivo turns | 16 non-smoke client messages завершились `BOT_MESSAGE sent`; upstream HTTP OK. | Bridge должен ack быстро и потом отправить bot event (`:75-121`, `:145-158`). | Оставить, но мониторить latency и stale/duplicate outcomes. |
| Operator handoff | За день `INVITE_AGENT` не найден. | Adapter возвращает `INVITE_AGENT` только при `handoff_to_operator` и online agents (`nmbot_api_server.py:429-431`). | Логировать safe `handoff_intended` / `invite_agent_sent` counters. |
| Телефон | В доступном day evidence phone capture не найден. | Детект есть в helpers и Jivo `run_chat` (`chat_tester_bot.py:4143-4157`, `nmbot_api_server.py:255-270`). | Добавить safe evidence: `phone_detected=true`, `phone_len`, irreversible phone_ref, state transition, invite/card result. |
| Downstream CRM/operator side effect | Нет файла client cards за день, нет `INVITE_AGENT`; CRM evidence не найден. | Card path есть в `chat_tester_bot.py:4310-4408`; Jivo invite path есть в adapter. | Явно выбрать контракт: Jivo phone должен создавать card, invite operator, CRM lead или только Jivo handoff. Сейчас это неочевидно. |
| Фактические client vs test диалоги | Есть one `static_smoke`; остальное — live Jivo webhook traffic, но human/client не доказан. | Safe refs не несут test flag, кроме `static_smoke`. | Добавить `traffic_kind`: `smoke`, `p1_manual`, `production_client`, `unknown`. |

## 6. Наблюдаемые ошибки/риски и улучшения

### P0

Нет P0, который можно честно назвать багом по live evidence: 16 non-smoke Jivo client messages дошли до финального `BOT_MESSAGE sent`, явных upstream HTTP errors за день в bridge structured log нет.

### P1

1. **Недостаточно audit evidence по реальным диалогам.** Сейчас нельзя детально восстановить, как бот искал и ошибался во всех Jivo-сообщениях, потому что bridge правильно не хранит text, а internal dialog log содержит только один полный bot turn. Рекомендация: добавить sanitized per-turn audit event после `run_chat`: `dialog_ref`, `turn_ref`, `intent`, `search_called`, `result_count`, `phone_detected`, `handoff_to_operator`, `terminal_event`, без text/payload.
2. **Телефон в Jivo state хранится как полный нормализованный номер.** Это видно по `nmbot_api_server.py:256-259`. Рекомендация: хранить full phone только в строго защищённом downstream-хранилище, а в state/logs — `phone_ref`, `phone_len`, masked last two pairs. Это privacy-hardening, не доказанный incident.
3. **Нет явного downstream outcome для Jivo phone path.** За день нет `INVITE_AGENT` и нет client card file. Рекомендация: определить контракт: после телефона Jivo должен делать `INVITE_AGENT`, card, CRM lead или всё вместе; затем логировать safe outcome.

### P2

1. **Latency у одного Jivo turn около 86.7 сек.** Bridge это выдержал async-режимом, но для клиента это может быть долго. Рекомендация: safe metric alert по p95 / max latency, без payload.
2. **`CHAT_CLOSED` попал в upstream и затем стал `event_not_sendable`.** Это не клиентская ошибка, но шумит в терминальных outcome. Рекомендация: bridge/API может short-circuit `CHAT_CLOSED` раньше или считать его отдельным terminal kind.
3. **Нужна маркировка тестового трафика.** Сейчас уверенно отделяется только `static_smoke`; P1/manual через Jivo UI выглядит как live traffic. Рекомендация: отдельный test channel/ref или safe flag в bridge log.

## 7. Ограничения и самый дешёвый следующий шаг

Ограничения:

- Сырой Jivo dialogue log за 2026-07-16 не найден и не выводился.
- Structured bridge log не содержит текстов сообщений, телефонов и сырых id по design.
- Поэтому нельзя честно перечислить «все тексты диалогов», конкретные клиентские запросы, имена клиентов или телефоны; нельзя доказать, что `Jivo-A/Jivo-B` — именно реальные клиенты, а не ручные тесты.
- В отчёте не делались выводы «бот ошибся в ответе X», потому что содержимое X недоступно в safe evidence.

Самый дешёвый следующий verification step: включить на один день sanitized audit JSONL рядом с Jivo API, где после каждого turn пишется только безопасная структура: anonymous session ref, turn ref, timestamp, message length bucket, phone_detected boolean, intent, search_called boolean, search_result_count, handoff flag, terminal event, latency. После первого live turn сразу проверить этот audit log и остановиться, если shape неверный.
