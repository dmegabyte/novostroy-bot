# Jivo Bot API integration plan

Дата: 2026-07-13

Цель: подключить текущего бота nmbot/Ирину к Jivo так, чтобы Jivo присылал сообщения клиента в наш backend, а backend возвращал ответ бота или переводил чат на оператора.

Текущее production-уточнение: callback-сценарий теперь не переводится в `INVITE_AGENT`. Если клиент оставляет имя и телефон для обратного звонка, backend подтверждает заявку, создаёт private callback outbox и background job на Google Sheets, а в Jivo возвращает `BOT_MESSAGE`.

Источники:

- Jivo docs: `https://www.jivo.ru/docs/`
- Jivo Bot API: `https://www.jivo.ru/docs/bot/`
- Проектный контекст nmbot: внешний API должен быть тонкой обёрткой над текущей логикой `question -> answer`, с сохранением dialog state по пользователю/чату.

## 1. Что выбрать в Jivo

Используем **Jivo Bot API**.

Почему не Chat API:

- Bot API предназначен для подключения чат-бота к аккаунту Jivo.
- Jivo сам отправляет входящие сообщения бот-провайдеру через webhook.
- Bot API поддерживает нужные события: `CLIENT_MESSAGE`, `BOT_MESSAGE`, `INVITE_AGENT`, `AGENT_UNAVAILABLE`, `CHAT_CLOSED`.

## 2. Что нужно получить / настроить со стороны Jivo

Минимальный список:

1. Доступ к аккаунту Jivo, где будет подключаться бот.
2. Возможность подключить bot-operator / Bot API.
3. Публичный HTTPS endpoint нашего backend, который Jivo сможет вызвать:

   ```text
   https://<our-domain>/jivo/<provider_token>
   ```

4. Уникальный `provider_token` для этого бота.
5. Подтверждение, какие каналы Jivo будут идти в бота:
   - site widget;
   - Telegram;
   - WhatsApp;
   - другие каналы, если подключены.
6. Решение по оператору:
   - переводить ли на живого оператора через `INVITE_AGENT`;
   - что делать, если операторов нет (`AGENT_UNAVAILABLE`).

## 3. Что Jivo будет присылать нам

Главное входящее событие:

```json
{
  "event": "CLIENT_MESSAGE",
  "id": "9661ab9c-48b0-11ed-a3d6-859398ff9bd9",
  "site_id": "123456",
  "client_id": "1233",
  "chat_id": "2037",
  "agents_online": true,
  "sender": {
    "id": 1233,
    "name": "John Smith",
    "url": "https://test.com",
    "has_contacts": true
  },
  "message": {
    "type": "TEXT",
    "text": "Мне нужна квартира под аренду",
    "timestamp": 1665415879
  },
  "channel": {
    "id": "12345678",
    "type": "widget"
  }
}
```

Минимально важные поля:

- `event`
- `id`
- `site_id`
- `client_id`
- `chat_id`
- `agents_online`
- `sender.name`
- `sender.url`
- `sender.has_contacts`
- `message.type`
- `message.text`
- `message.timestamp`
- `channel.id`
- `channel.type`

`sender.name` — профильное имя посетителя, а не имя, вычисленное из номера.
После нормализации API передаёт его в runtime как `meta.sender_name`. Callback
может использовать это имя для phone-only заявки, если оно проходит safe-name
guard; пустые, тестовые и небезопасные значения не используются.

## 4. Какой ключ сессии использовать

Для состояния диалога используем стабильный ключ:

```text
jivo:<site_id>:<chat_id>:<client_id>
```

По этому ключу храним:

- `last_options`
- `selected_option`
- `last_bot_question`
- `last_turn`
- `awaiting_phone`
- `operator_context`
- active scenario / active task
- последние сообщения диалога

Это важно, чтобы короткие ответы клиента вроде `да`, `второй`, `позови оператора` работали так же, как в Telegram.

## 5. Наши backend endpoints

### 5.1 Внешний endpoint для Jivo

```http
POST /jivo/<provider_token>
Content-Type: application/json
```

Задачи endpoint:

1. Проверить `provider_token`.
2. Проверить `event`.
3. Для `CLIENT_MESSAGE` достать `message.text`.
4. Собрать `user_id = jivo:<site_id>:<chat_id>:<client_id>`.
5. Передать текст во внутренний bot API.
6. Вернуть `BOT_MESSAGE`; `INVITE_AGENT` остаётся только для live operator handoff, но не для callback-заявки.

### 5.2 Внутренний bot API

```http
POST /api/chat
Content-Type: application/json
```

Вход:

```json
{
  "user_id": "jivo:123456:2037:1233",
  "message": "Мне нужна квартира под аренду",
  "channel": "jivo",
  "meta": {
    "site_id": "123456",
    "client_id": "1233",
    "chat_id": "2037",
    "agents_online": true,
    "sender_name": "John Smith",
    "sender_url": "https://test.com",
    "jivo_channel_type": "widget"
  }
}
```

Выход:

```json
{
  "ok": true,
  "answer": "Поняла. Под аренду и бюджет 15 млн я бы смотрела несколько вариантов...",
  "intent": "main_search",
  "awaiting_phone": false,
  "handoff_to_operator": false,
  "selected_option": null,
  "buttons": []
}
```

## 6. Что отвечать Jivo

### 6.1 Обычный ответ бота

```json
{
  "event": "BOT_MESSAGE",
  "client_id": "1233",
  "chat_id": "2037",
  "message": {
    "type": "TEXT",
    "text": "Поняла. Под аренду и бюджет 15 млн я бы смотрела несколько вариантов...",
    "timestamp": 1665415880
  }
}
```

### 6.2 Ответ с кнопками

Если нужны кнопки:

```json
{
  "event": "BOT_MESSAGE",
  "client_id": "1233",
  "chat_id": "2037",
  "message": {
    "type": "BUTTONS",
    "title": "Какой вариант хотите посмотреть подробнее?",
    "text": "Выберите вариант",
    "force_reply": false,
    "buttons": [
      { "text": "Бусиновский парк", "id": "1" },
      { "text": "Саларьево парк", "id": "2" }
    ],
    "timestamp": 1665415880
  }
}
```

### 6.3 Перевод на оператора

Если бот дошёл до live-check / operator handoff:

```json
{
  "event": "INVITE_AGENT",
  "client_id": "1233",
  "chat_id": "2037"
}
```

Если операторов нет, Jivo пришлёт нам:

```json
{
  "event": "AGENT_UNAVAILABLE",
  "client_id": "1233",
  "chat_id": "2037"
}
```

Тогда бот должен продолжить мягко:

```text
Сейчас специалист может быть недоступен. Могу передать ваш контакт — менеджер сможет обсудить вопрос с вами по телефону.
```

## 7. Timeout и архитектурное ограничение

Историческое ограничение синхронного Jivo webhook-запроса — около 3 секунд;
это не timeout всего текущего async-контура. Bridge быстро подтверждает приём
события, а отдельные параметры ожидания разделены так:

- optional промежуточный статус — первый через 3 секунды и далее по интервалу;
- threshold ожидания upstream — 90 секунд по умолчанию;
- hard deadline bridge — 600 секунд по умолчанию;
- после этого отправляется один terminal `BOT_MESSAGE` с fallback.

Риск: nmbot может отвечать дольше из-за LLM/MCP.

Варианты реализации:

### Вариант A — простой MVP

Jivo ждёт ответ синхронно.

Подходит только для отдельного синхронного теста, если ответ укладывается в
историческое ограничение webhook.

### Вариант B — безопасная прод-архитектура

1. `/jivo/<token>` быстро принимает событие.
2. Кладёт задачу в очередь.
3. Сразу возвращает Jivo штатный ответ или короткую техническую заглушку.
4. Worker считает ответ.
5. Worker отправляет `BOT_MESSAGE` в Jivo.

Перед выбором варианта нужно проверить, разрешает ли текущий Jivo Bot API сценарий отправлять ответ асинхронно через обратный endpoint Jivo для конкретного аккаунта/бота.

## 8. Минимальный порядок разработки

### Шаг 1 — Adapter без runtime-изменений

Сделать функцию:

```python
handle_jivo_event(payload) -> jivo_response
```

Она пока не трогает Telegram-бота напрямую, только адаптирует формат.

### Шаг 2 — Внутренний `POST /api/chat`

Сделать тонкую API-обёртку:

```python
chat(user_id, message, channel="jivo", meta={}) -> answer_payload
```

Важно: логика диалога должна использовать тот же state contract, что Telegram.

### Шаг 3 — Jivo webhook endpoint

Сделать:

```http
POST /jivo/<provider_token>
```

Поддержать события:

- `CLIENT_MESSAGE`
- `AGENT_UNAVAILABLE`
- `CHAT_CLOSED`

Остальные события на первом этапе можно логировать и отвечать `200 OK`.

### Шаг 4 — State storage

Состояние хранить по ключу:

```text
jivo:<site_id>:<chat_id>:<client_id>
```

На первом этапе можно использовать тот же storage-подход, что у Telegram runtime, но лучше сразу сделать отдельный namespace `jivo`.

### Шаг 5 — Operator handoff

Если внутренний `/api/chat` вернул:

```json
{ "handoff_to_operator": true }
```

адаптер отвечает Jivo событием:

```json
{ "event": "INVITE_AGENT" }
```

### Шаг 6 — Tests first

Перед runtime deploy добавить тесты:

1. `CLIENT_MESSAGE` превращается в правильный `/api/chat` request.
2. Обычный ответ `/api/chat` превращается в `BOT_MESSAGE`.
3. `handoff_to_operator=true` превращается в `INVITE_AGENT` только для live operator handoff; callback flow использует private outbox / Sheets worker и не должен переводить чат автоматически.
4. `AGENT_UNAVAILABLE` даёт мягкий fallback с запросом контакта.
5. `CHAT_CLOSED` очищает или замораживает state.

### Шаг 7 — Smoke scenarios

Проверить живыми текстами:

1. Первый поиск: “Мне нужна квартира под аренду, 15 млн”.
2. Выбор ЖК: “Бусиновский парк”.
3. Короткое “да” после вопроса про условия покупки.
4. “Позови оператора”.
5. Закрытие чата и новый диалог.

### Шаг 8 — Deploy

Поднять отдельный сервис:

```text
novostroy-bot-api.service
```

Не смешивать с Telegram polling service.

После deploy проверить:

- `/health`
- тестовый `CLIENT_MESSAGE`
- `BOT_MESSAGE`
- `INVITE_AGENT` — только отдельный live operator handoff, не обычный callback
- логи ошибок
- latency, быстрый webhook ACK и correlated delivery `BOT_MESSAGE`

## 9. Что нужно решить до реализации / resolved history

Открытые вопросы:

1. Какой домен будет у webhook endpoint?
2. Где хранить `provider_token`?
3. Нужна ли дополнительная IP-allowlist для Jivo?
4. Нужны ли кнопки Jivo на первом этапе или достаточно TEXT?
5. Что делать при ответе дольше исторического синхронного webhook-окна?

    **Resolved / prepared, disabled by default:** bridge умеет отправлять нетерминальные
   `BOT_MESSAGE`-статусы через каждые три секунды до готовности финального
   ответа. Режим включается только флагом
   `NMBOT_BRIDGE_STATUS_UPDATES_ENABLED=1`; шаблоны и интервал задаются через
   `NMBOT_BRIDGE_STATUS_TEMPLATES` и
   `NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS`. До отдельного production deploy и
   Jivo delivery evidence режим не считается подключённым.
6. Нужен ли отдельный API token для внутреннего `/api/chat`?
7. Callback vs live operator handoff.

   **Resolved:** callback-сценарий уже решён как `name + phone -> private callback outbox -> Google Sheets`, а `INVITE_AGENT` остаётся только для live operator handoff. Поэтому это больше не открытый вопрос; пункт сохранён как historical decision record.

## 10. Рекомендуемый MVP

Минимальная версия:

1. `POST /jivo/<token>`
2. Поддержка только `CLIENT_MESSAGE`, `AGENT_UNAVAILABLE`, `CHAT_CLOSED`
3. Ответы только `TEXT`
4. State по `jivo:<site_id>:<chat_id>:<client_id>`
5. Operator handoff через `INVITE_AGENT` (только live operator path; callback заявка отдельно)
6. Тесты на 5 ключевых сценариев
7. Отдельный systemd service `novostroy-bot-api.service`

После MVP можно добавить:

- кнопки;
- асинхронную очередь;
- расширенную аналитику;
- прокидывание user metadata из Jivo Widget API;
- отдельный dashboard по Jivo-диалогам.

## 11. Реализованная локальная основа без секретов

Созданы файлы:

- `scripts/nmbot_api_server.py` — минимальный `aiohttp` HTTP-сервис:
  - `GET /health`;
  - `POST /api/chat`;
  - `POST /api/reset`;
  - `POST /jivo/{provider_token}`.
- `scripts/nmbot_env_secrets.py` — безопасный helper для добавления Jivo/API значений в `.env` без печати самих значений.

Пока это именно foundation/MVP:

- Telegram runtime не тронут;
- Jivo-секреты не нужны для smoke-проверки;
- `/api/chat` использует текущий `OvermindClient` и базовую state-память;
- полный Telegram follow-up handler пока не вынесен в общий engine, это следующий архитектурный шаг.

### 11.1 Локальная проверка без секретов

```bash
python3 -m py_compile scripts/nmbot_api_server.py scripts/nmbot_env_secrets.py
python3 scripts/nmbot_api_server.py --smoke
```

Ожидаемо:

```text
OK: nmbot_api_server smoke passed
```

### 11.2 Как добавить секреты позже

Когда будут реальные данные от Jivo/инфры:

```bash
python3 scripts/nmbot_env_secrets.py --env .env --key JIVO_PROVIDER_TOKEN --value '<token-from-jivo-or-provider>'
python3 scripts/nmbot_env_secrets.py --env .env --key NMBOT_API_TOKEN --value '<internal-api-token>'
python3 scripts/nmbot_env_secrets.py --env .env --key NMBOT_API_PUBLIC_BASE_URL --value 'https://<our-domain>'
python3 scripts/nmbot_env_secrets.py --env .env --key NMBOT_API_HOST --value '127.0.0.1'
python3 scripts/nmbot_env_secrets.py --env .env --key NMBOT_API_PORT --value '8088'
```

Helper печатает только статус `added/updated`, но не печатает значения.

### 11.3 Как запустить сервис локально

```bash
python3 scripts/nmbot_api_server.py --host 127.0.0.1 --port 8088
```

Проверка health:

```bash
curl http://127.0.0.1:8088/health
```

Jivo endpoint после настройки секрета:

```text
POST https://<our-domain>/jivo/<JIVO_PROVIDER_TOKEN>
```

Важно: для production нужен отдельный systemd unit `novostroy-bot-api.service`, HTTPS/ingress и решение по 3-секундному timeout Jivo.

### 11.4 Фактический production webhook

На 2026-07-14 активный n8n webhook для Jivo уже поднят и проверен:

```text
https://n8n.it-system.io/webhook/msknmbot
```

Текущий n8n workflow:

- имя: `Jivo / Irina bridge to VPS`
- workflow id: `qNDrkfC9YaWs5AFx`
- путь webhook: `msknmbot`

Старый путь `jivo-irina` больше не используется.

### 11.5 Проверка и диагностика

Что считается рабочим состоянием:

1. Прямой POST в `https://n8n.it-system.io/webhook/msknmbot` возвращает `200`.
2. В `bridge_request`-логе на VPS появляется входящее событие без токенов и без текста клиента.
3. Для `CLIENT_MESSAGE` bridge сначала быстро подтверждает входящий webhook, а затем ждёт ответ Ирины в фоне.
4. Если ответ успевает прийти, в Jivo уходит настоящий `BOT_MESSAGE`; если ответ не приходит слишком долго, только тогда используется fallback.
5. Для защиты от устаревших ответов bridge не отправляет старый ответ в чат, если в тот же `chat_id/client_id` уже пришло новое сообщение.

Если в реальном Jivo-виджете ответа нет, а ручной POST работает, значит Jivo ещё не отправляет события в наш webhook. Тогда проверяем:

- что Bot API назначен именно на этот существующий виджет / канал;
- что в Jivo указан актуальный webhook `https://n8n.it-system.io/webhook/msknmbot`;
- что указан тот же `provider_token`, который хранится у нас на стороне провайдера.

### 11.6 Текущий production-поток

Фактическая рабочая схема на 2026-07-15:

```text
Jivo widget → n8n webhook → VPS bridge → local nmbot API → Jivo BOT_MESSAGE
```

Ключевые параметры:

- bridge отвечает на входящее событие сразу, чтобы Jivo не упирался в короткое историческое webhook-окно;
- optional status interval — `3` секунды, upstream threshold — `90` секунд, hard deadline — `600` секунд;
- для одного `chat_id/client_id` есть защита от устаревших ответов;
- если upstream успел, Jivo получает настоящий ответ; если нет, отправляется fallback.

Проверка, что всё живое:

```text
bridge_request ... result: accepted_async
bridge_async_send ... upstream_result: upstream
bridge_async_send ... jivo_status: 200
```

Это и есть признак, что сообщение дошло до Jivo и было принято без ошибки.
