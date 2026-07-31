# Jivo callback → Google Sheets: план реализации

## 1. Цель и границы

Когда клиент согласился на обратный звонок и оставил **имя и телефон**, Ирина:

1. сразу подтверждает, что специалист свяжется с клиентом;
2. не переводит чат через `INVITE_AGENT`;
3. в этот же момент создаёт фоновую задачу на саммари и запись заявки в Google Sheets;
4. не ждёт LLM, Google Sheets или повторных попыток доставки перед ответом клиенту.

## 1.1 Current status

Этот план уже реализован в production-контуре:

- callback-сценарий собирает `name + phone` и подтверждает заявку сразу;
- callback не переводится через `INVITE_AGENT`;
- приватный outbox и worker пишут строку в Google Sheets;
- visible sheet хранит ровно 4 бизнес-колонки, а технические refs живут отдельно;
- безопасное имя из `sender.name` / profile metadata может дать `contact_name`;
  если имени нет или оно небезопасно, phone-first flow остаётся private draft и
  бот просит имя.

Resolved decision: callback-заявка никогда не превращается в `INVITE_AGENT`.
`INVITE_AGENT` используется только для live operator handoff; callback идёт через
private outbox / Google Sheets и возвращает клиенту `BOT_MESSAGE`.

### 1.2 Единый контур версий

Jivo-версии V0, V2 и V3 используют один и тот же путь записи заявки:

```text
runtime version → contact capture → private outbox → summary worker →
scripts/nmbot_google_sheets.py → Google Sheets
```

Различается только runtime-метка. Для V3 сохраняются `runtime=v3` и
`engine=v2`: Светлана использует тот же V2-движок, но заявка не теряет версию
бота. Все версии записывают в видимую вкладку одинаковые четыре поля:
`Дата и время (МСК)`, `Телефон`, `Имя`, `Саммари диалога`.

Техническое правило: Google Sheets client и append-вызов принадлежат только
`scripts/nmbot_google_sheets.py`; остальные runtime- и publisher-модули
используют его общий adapter. Это сохраняет единый контракт записи и
идемпотентность outbox/worker.

В видимой вкладке таблицы должны быть ровно четыре поля:

| Дата и время (МСК) | Телефон | Имя | Саммари диалога |
|---|---|---|---|

Дата и время — момент, когда клиенту было подтверждено создание заявки, а не момент успешной записи в таблицу.

### Не входит в первую итерацию

- автоматическое обновление уже созданной строки последующими сообщениями клиента;
- отправка в иной CRM или операторский чат;
- автоматический звонок;
- повторный запрос контакта после успешной заявки;
- хранение полного телефона в LLM, prompt payload, bridge log или публичной диагностике.

## 2. Контракты и инварианты

### 2.1 Приватность

- Полный телефон существует только в private state на время сбора и в private outbox/защищённой Google Sheet.
- Телефон не передаётся в LLM, MCP, prompt, Jivo structured JSONL, публичные логи и сообщения об ошибках.
- В технических логах допустимы только `phone_captured`, длина, last4 и непрозрачный `lead_ref`.
- Имя не является секретом, но не должно попадать в публичные диагностические события без необходимости.
- Снимок для LLM-саммари очищается от телефона, email, идентификаторов Jivo, токенов и raw payload.

### 2.2 Надёжность

- Один подтверждённый callback = одна бизнес-заявка и одна строка в видимой таблице.
- Повтор Jivo event, retry worker или рестарт процесса не создают вторую строку.
- Ответ клиенту не зависит от доступности Google API.
- Если саммари или Sheets временно недоступны, заявка остаётся в durable outbox и повторяется.
- Нельзя считать `values.append` доставкой, пока не сохранён delivery receipt/row reference.

### 2.3 Владение слоями

| Слой | Ответственность |
|---|---|
| Code-level scenario | Сбор и валидация имени/телефона, state machine, privacy, idempotency, enqueue |
| LLM | Только саммари очищенного снимка диалога; не видит телефон |
| Worker | Очередь, retry/backoff, Sheets append, delivery ledger |
| Google Sheets | Внешнее представление заявки |
| Jivo adapter | Только `BOT_MESSAGE`; без `INVITE_AGENT` для callback |

## 3. Сценарий клиента

### 3.1 Состояния

```text
normal
  └─ клиент согласился на callback / требуется живое уточнение
       → awaiting_contact

awaiting_contact
  ├─ пришло надёжное имя, телефона нет → awaiting_contact_phone
  ├─ пришёл валидный телефон, имени нет → awaiting_contact_name
  ├─ есть имя и телефон → callback_confirmed + enqueue
  └─ клиент отказался / продолжил предметный диалог → normal, контакт не создаётся

callback_confirmed
  └─ дальнейший диалог остаётся normal; первая заявка уже неизменяемо зафиксирована
```

`awaiting_contact` — логическое состояние. Реализация может начать с имени или телефона, но не должна подтверждать заявку, пока нет обоих полей.

### 3.2 Имя

1. Если Jivo передал непустое безопасное имя профиля в `sender.name`, передать его
   как `meta.sender_name`, сохранить как `contact_name` и использовать в
   обращении; отдельно не спрашивать. Имя не извлекается из номера телефона.
   Тестовые и небезопасные значения не принимаются.
2. Если имени нет, спросить коротко: «Как я могу к вам обращаться?»
3. Имя, явно написанное клиентом в ответ на этот вопрос, считается клиентским именем.
4. Не извлекать имя эвристически из произвольной длинной фразы. Если ответ не похож на имя, вежливо переспросить один раз.
5. После повторной неясности предложить продолжить переписку без заявки, а не бесконечно повторять вопрос.

### 3.3 Телефон

1. Валидный номер определяется code-level guard после нормализации: 10–15 цифр.
2. Короткие числа, цены, площади и номера квартир не считаются телефоном.
3. Если телефон пришёл первым и безопасного имени профиля нет, он сохраняется
   только в private draft; бот просит имя. Если безопасное имя профиля есть,
   callback можно сразу поставить в outbox.
4. Если имя пришло первым, бот просит номер.
5. Если клиент отказывается оставлять телефон, заявка не создаётся; контекст диалога сохраняется, разговор продолжается.

### 3.4 Подтверждение

Только после получения обоих полей:

> Спасибо, {имя}. Заявку на обратный звонок сохранила — специалист свяжется с вами.

После этого нельзя отправлять `INVITE_AGENT`, нельзя выдавать внешний номер и нельзя блокировать ответ ожиданием worker.

Для callback-flow confirmation означает именно заявку на обратный звонок, а не операторский перевод.

## 4. Атомарная точка создания заявки

До отправки подтверждающего `BOT_MESSAGE` runtime обязан создать private immutable snapshot заявки и durable outbox record.

```text
name + phone available
  → build sanitized lead snapshot
  → atomically enqueue pending_summary record
  → persist dialog state with lead_ref
  → return confirmation BOT_MESSAGE
  → background worker starts/continues summarize_and_append
```

Если enqueue не удался, бот **не говорит**, что заявка сохранена. Он отвечает безопасно: просит продолжить переписку, а runtime пишет только безопасный error class.

### Snapshot

Private record содержит:

- `lead_ref` — непрозрачный идентификатор;
- `created_at_utc` и `created_at_msk`;
- client-provided/profile `contact_name`;
- полный `phone`;
- безопасный контекст: параметры поиска, выбранный ЖК, текущие варианты, известные факты, последний вопрос, причина callback;
- очищенное окно диалога для саммари;
- status/delivery attempts.

В snapshot не попадают raw Jivo payload, token, chat/client ID, URL и полный телефон в поле, доступном LLM.

## 5. Outbox и жизненный цикл

### 5.1 Статусы

```text
pending_summary
  → summarizing
  → summary_ready
  → sheet_appending
  → sheet_delivered

любой временный сбой → retrying
исчерпаны попытки / permanent contract error → failed
```

`sheet_delivered` — terminal. Повторная обработка terminal записи ничего не делает.

### 5.2 Idempotency

- Бизнес-ключ: Jivo session + inbound event id, если event id есть.
- Fallback: session + нормализованный телефон + contact-name hash в пределах callback flow.
- `lead_ref` детерминирован от idempotency key и не раскрывает контакт.
- Outbox создаётся atomic write с fsync и правами directory `0700`, file `0600`.
- Повтор того же Jivo webhook возвращает прежний `lead_ref`, а не новую заявку.

### 5.3 Delivery ledger

`values.append` может фактически записать строку, а HTTP-ответ потеряться. Поэтому нужен отдельный private delivery ledger:

- `lead_ref`;
- request attempt и timestamp;
- result status;
- Google row/range из успешного ответа, если он есть;
- retry schedule.

Рекомендуемая схема: защищённая служебная вкладка `_delivery` в той же таблице или отдельный private datastore. В видимой вкладке остаются четыре бизнес-колонки. До повторного append worker ищет `lead_ref` в ledger; без этого нельзя безопасно гарантировать отсутствие дублей после uncertain timeout.

## 6. Саммари

### 6.1 Когда запускается

Сразу после durable enqueue и отправки клиенту подтверждения. Не ждёт `CHAT_CLOSED`, quiet period или следующего сообщения.

Саммари описывает snapshot на момент подтверждения. Последующие сообщения не изменяют первую запись автоматически; update-flow — отдельная будущая функция.

### 6.2 Что получает LLM

Только очищенный snapshot:

- исходный запрос и уточнения без PII;
- выбранный ЖК/варианты и подтверждённые факты;
- вопрос клиента и причина обратного звонка;
- что нужно проверить специалисту.

### 6.3 Что не получает LLM

- телефон;
- имя, если оно не требуется для смысла саммари;
- Jivo IDs, profile metadata, raw payload;
- токены, ссылки доступа и технические ошибки.

### 6.4 Failure policy

1. Временный LLM failure → retry по bounded backoff.
2. После лимита попыток → deterministic summary из безопасных полей snapshot.
3. Заявка всё равно должна быть записана в таблицу; пустое саммари недопустимо.
4. Текст ошибки LLM никогда не записывается в таблицу как саммари.

## 7. Google Sheets adapter

### 7.1 Контракт записи

Worker использует server-to-server service account и Google Sheets `spreadsheets.values.append`.

Видимая строка:

```json
[
  "2026-07-16 16:30:00 МСК",
  "+7…",
  "Имя клиента",
  "Краткое безопасное саммари"
]
```

Дата хранится в snapshot как UTC + timezone `Europe/Moscow`; форматируется worker перед append. В таблице используется timezone МСК.

### 7.2 Доступы и секреты

- Sheets API включается в отдельном Google Cloud project.
- Service account получает Editor **только** к указанной таблице.
- JSON credentials лежат вне Git и вне исходников, с правами `0600`.
- В `.env.example`, логах, тестах и сообщениях нельзя размещать service-account JSON, private key или spreadsheet sharing link с доступом.
- Adapter валидирует spreadsheet ID и tab name из конфигурации, а не из текста клиента.

### 7.3 Конфигурация до запуска

Нужно явно задать:

- `NMBOT_CALLBACK_SHEET_ID`;
- `NMBOT_CALLBACK_SHEET_TAB`;
- путь к credentials/service-account mechanism;
- private outbox root;
- retry limits/backoff;
- timezone `Europe/Moscow`.

Ни один endpoint, auth header или payload CRM не должен быть выдуман: Google adapter появляется только после подтверждения service-account доступа и названия вкладки.

## 8. Retry, ошибки и наблюдаемость

### Retryable

- network timeout;
- 429;
- 5xx;
- временная ошибка получения саммари.

### Non-retryable до ручного исправления

- 401/403 (неверные credentials/нет доступа);
- 404 spreadsheet/tab;
- schema/config validation error;
- malformed private record.

### Backoff

Exponential backoff с jitter, ограниченное число автоматических попыток. Worker хранит `next_attempt_at`; не делает busy-loop и не удерживает Jivo request.

### Безопасная диагностика

На каждую заявку разрешены: `lead_ref`, stage, attempt number, error class, latency, status. Запрещены: phone, имя, саммари целиком, Google auth response/body, Jivo IDs и payload.

Метрики:

- `callback_enqueued`;
- `summary_succeeded` / `summary_fallback_used`;
- `sheet_delivered`;
- `sheet_retrying`;
- `sheet_failed`;
- возраст oldest pending record.

## 9. Файлы и impact chain

```text
Jivo CLIENT_MESSAGE
  → nmbot_api_server.py / run_chat
  → contact state machine
  → LocalCallbackOutbox (private snapshot)
  → BOT_MESSAGE confirmation
  → callback sheet worker
      → sanitized summary provider
      → Google Sheets adapter
      → private delivery ledger
```

Планируемые компоненты:

| Файл | Роль |
|---|---|
| `scripts/nmbot_api_server.py` | Сценарий имени/телефона, enqueue, немедленный BOT_MESSAGE |
| `scripts/nmbot_crm_outbox.py` | Версия snapshot schema, state transitions, atomic persistence |
| `scripts/nmbot_callback_sheet_worker.py` | Poll/retry/summary orchestration |
| `scripts/nmbot_google_sheets.py` | Изолированный Google API adapter |
| systemd `nmbot-callback-sheet-worker.service` | Отдельный durable worker, не Jivo API unit |
| tests | Mocks для summary/Sheets, state transitions, privacy/idempotency |

## 10. Детальные риски и защита

| Риск | Последствие | Защита |
|---|---|---|
| Телефон пришёл без имени | Неполная заявка | Private draft телефона, запрос только имени, без confirmation |
| Имя профиля неточно | Некорректное обращение | Использовать profile name как prefill; при явном исправлении клиента заменить |
| Дубликат webhook | Две заявки | Deterministic idempotency key + atomic outbox |
| Append succeeded, response lost | Дубль в Sheet | Private delivery ledger с `lead_ref` до retry |
| Sheets недоступен | Потеря лида/долгий ответ | Durable outbox + worker retry; Jivo response не ждёт API |
| LLM summary упал | Лид не записан | Retry, затем deterministic safe summary |
| Worker crash/restart | Зависшая заявка | Persistent status/next_attempt_at, restart-safe scan |
| Неверный доступ service account | Бесконечные повторы | Non-retryable status + safe alert, no busy-loop |
| PII в LLM/логах | Privacy breach | Strict sanitizer + tests + allowlisted audit fields |
| Outbox диск заполнен | Ложное подтверждение | Enqueue-before-confirmation, health metric, safe client fallback |
| Длинное саммари | Нечитаемая таблица | Bounded prompt/output, truncate with safe deterministic fallback |
| Поздние сообщения клиента | Неясная семантика записи | Первая запись immutable; update-flow отдельно |
| Две заявки одного клиента | Потеря намеренного повторного обращения | Idempotency only для replay одного event/callback flow, не глобально по телефону |
| Spreadsheet tab переименован | Persistent failure | Config validation at worker startup + alert |

## 11. Тестовый план без внешних вызовов

### Deterministic unit tests

1. Имя из профиля + телефон → один enqueue и confirmation.
2. Телефон → запрос имени → имя → один enqueue.
3. Имя → запрос телефона → телефон → один enqueue.
4. Дубликат event → тот же `lead_ref`, одна запись outbox.
5. Нет имени/нет телефона → нет confirmation и нет enqueue.
6. Телефон не проходит в LLM input, public result, filename или safe log.
7. Callback result Jivo — `BOT_MESSAGE`, не `INVITE_AGENT`.
8. Worker summary success → append payload с четырьмя колонками и МСК.
9. Summary failure → retry/fallback summary, Jivo не затронут.
10. Sheets timeout after uncertain append → ledger prevents duplicate.
11. 401/403 → failed/alert without repeated busy-loop.
12. Restart worker → pending job resumes.

### Integration tests с mock adapters

- Fake summary provider;
- Fake Sheets adapter с success, 429, 5xx, timeout-after-write;
- temp private outbox + ledger;
- проверка permissions `0700`/`0600` на Linux.

### До production

1. Локальные compile и deterministic tests.
2. Изолированный VPS worker с fake Sheets adapter, без Jivo/LLM/Google.
3. Проверка первого реального callback только после отдельного разрешения и готового service-account доступа.
4. После первого callback сразу проверить `lead_ref` и worker log; при ошибке остановить дальнейшие тесты согласно First-Failure rule.

## 12. Порядок реализации

1. Версионировать outbox record для `contact_name`, `created_at_msk`, snapshot и delivery states.
2. Реализовать contact state machine в Jivo API и регрессии name/phone order.
3. Добавить worker interface и fake adapters; покрыть idempotency/recovery.
4. Добавить Google Sheets adapter только после service-account setup и подтверждения tab name.
5. Создать systemd worker unit с минимальными filesystem permissions.
6. Провести isolated VPS test с mocks.
7. Отдельно согласовать и выполнить один production callback smoke.

## 13. Открытые решения перед кодом

1. Название рабочей вкладки Google Sheet.
2. Где будет расположен private delivery ledger: hidden `_delivery` tab или отдельный private datastore.
3. Какое имя считать profile prefill: любое непустое Jivo name или только имя, подтверждённое клиентом при первом обращении.
4. Политика retention: срок хранения private outbox, delivery ledger и телефонов.
5. Текст consent/уведомления о callback, если он требуется бизнесом или юристами.
6. Service account и доступ к предоставленной таблице.

## 14. Техническая карта реализации

### 14.1 Что уже есть

| Компонент | Где находится | Что берём |
|---|---|---|
| Jivo API | `scripts/nmbot_api_server.py` | Входящий `CLIENT_MESSAGE`, session state, ответ `BOT_MESSAGE` |
| Private callback outbox | `scripts/nmbot_crm_outbox.py` | Atomic private JSON-запись, `lead_ref`, event idempotency, safe context |
| Диалоговый state | `data/nmbot_api_state.json` через `JsonStateStore` | Параметры поиска, текущие варианты, последний вопрос, contact-flow state |
| Ответы/саммари | `scripts/chat_tester_bot.py` / gateway | Existing controlled LLM access; новый summary adapter получает только sanitized snapshot |
| Jivo transport | `scripts/nmbot_n8n_bridge_server.py` | Доставляет готовый `BOT_MESSAGE`; к Google Sheets не относится |
| Google Sheet | Внешний ресурс | Видимая вкладка заявок и скрытая техническая вкладка `_delivery` |

### 14.2 Какие файлы меняем или добавляем

| Файл | Изменение | Зачем |
|---|---|---|
| `scripts/nmbot_api_server.py` | Добавить state machine `awaiting_contact_name` / `awaiting_contact_phone`; вызвать enqueue только при наличии имени и телефона | Управляет клиентским сценарием и немедленно отвечает клиенту |
| `scripts/nmbot_crm_outbox.py` | Версия record `v2`: `contact_name`, МСК timestamp, sanitized dialogue snapshot, delivery state, lease/retry fields | Durable источник правды между Jivo и worker |
| `scripts/nmbot_callback_sheet_worker.py` | **Новый** автономный worker | Находит pending records, делает summary, пишет Sheet, сохраняет delivery status |
| `scripts/nmbot_google_sheets.py` | **Новый** изолированный adapter | Единственное место с Google auth и `values.append` |
| `scripts/nmbot_callback_summary.py` | **Новый** sanitizer + deterministic fallback summary | Гарантирует, что LLM не увидит телефон/ID/payload |
| `systemd/user/nmbot-callback-sheet-worker.service` | **Новый** unit | Поднимает worker отдельно от API/bridge |
| `tests/test_nmbot_callback_flow.py` | **Новый** | Name/phone order, confirmation, no `INVITE_AGENT`, no PII leakage |
| `tests/test_nmbot_callback_worker.py` | **Новый** | Retry, idempotency, summary/Sheet mocks, restart recovery |

`scripts/nmbot_n8n_bridge_server.py` менять не нужно: он по-прежнему получает готовый API result и публикует только Jivo response.

### 14.3 State в Jivo API

В `JsonStateStore` добавляются только поля сценария, не техническая история worker:

```json
{
  "contact_name": "Иван",
  "contact_name_source": "jivo_profile|client_message",
  "contact_phone_draft_meta": {"captured": true, "digits_len": 11},
  "contact_flow": "awaiting_contact_name|awaiting_contact_phone|normal",
  "last_callback_ref": "cb_..."
}
```

Полный phone draft не должен надолго оставаться в обычном dialog state. До подтверждения он хранится в отдельном private draft record с теми же правами `0600`; после создания заявки source of truth — outbox record. В state остаются только safe meta и `lead_ref`.

### 14.4 Из чего формируется заявка

`nmbot_api_server.py` на момент подтверждения передаёт в `LocalCallbackOutbox.enqueue_callback(...)`:

```text
session key                     → только для private idempotency hash
Jivo event id                   → primary dedup key
имя                             → private Sheet field
нормализованный телефон         → private Sheet field
created_at_utc / created_at_msk → дата заявки
safe lead context               → параметры, selected/current options, known facts,
                                  причина callback, последний вопрос
sanitized dialog snapshot       → только материал для саммари
```

Без raw Jivo payload, `client_id`, `chat_id`, токенов, URL, заголовков и полного номера в логируемых метаданных.

### 14.5 Формат private outbox record v2

Полная запись лежит только в outbox с правами `0600`:

```json
{
  "schema": "nmbot.callback_sheet_outbox.v2",
  "lead_ref": "cb_opaque_reference",
  "idempotency_key_sha256": "...",
  "created_at_utc": "2026-07-16T12:00:00Z",
  "created_at_msk": "2026-07-16T15:00:00+03:00",
  "contact": {"name": "...", "phone": "..."},
  "summary_input": {"safe_context": {}, "dialog": []},
  "summary": {"status": "pending", "text": "", "attempts": 0},
  "sheet_delivery": {
    "status": "pending_summary",
    "attempts": 0,
    "next_attempt_at": "...",
    "sheet_row_ref": ""
  }
}
```

`lead_ref` не зависит от номера в открытом виде. Имя и телефон никогда не входят в filename, public result или structured trace.

### 14.6 Worker: алгоритм одного цикла

`nmbot_callback_sheet_worker.py` запускается отдельно и повторяет:

```text
1. Scan private outbox for records with next_attempt_at <= now.
2. Atomically take lease on one record.
3. If summary is pending:
   a. build sanitized summary input;
   b. call summary provider once;
   c. on failure use retry or deterministic safe summary;
   d. atomically save result.
4. Before append, read private delivery ledger by lead_ref.
5. If already delivered: mark outbox delivered and stop.
6. Append exactly four business cells into callback tab.
7. Persist append result/row reference in delivery ledger.
8. Mark outbox sheet_delivered.
9. On temporary failure: increment attempts, calculate next_attempt_at.
10. On permanent config/auth failure: mark failed and emit safe alert.
```

Lease нужен, чтобы два worker-process после restart или accidental duplicate unit не обработали один record одновременно. Lease имеет TTL; просроченный lease может быть безопасно подобран следующим циклом.

### 14.7 Google Sheets adapter

`nmbot_google_sheets.py` имеет минимальный интерфейс:

```python
class CallbackSheetAdapter:
    def append_callback(self, *, created_at_msk, phone, name, summary, lead_ref) -> AppendResult: ...
    def lookup_delivery(self, *, lead_ref) -> DeliveryLookup: ...
    def record_delivery(self, *, lead_ref, row_ref, delivered_at) -> None: ...
```

Реализация:

1. Строит Google credential client из пути, указанного в environment.
2. Добавляет четыре значения в заданную видимую вкладку через `spreadsheets.values.append`.
3. Пишет `lead_ref`, delivery status и row reference в защищённую `_delivery` вкладку либо отдельный private ledger.
4. Никогда не пишет Google request/response body в лог.

Для первой реализации предпочтителен отдельный local private ledger: он не зависит от прав на скрытую вкладку и не добавляет технические поля в бизнес-таблицу. При необходимости его позже можно заменить `_delivery` tab без изменения Jivo сценария.

### 14.8 Environment и filesystem

Новые переменные определяются в private environment file, но не коммитятся:

```text
NMBOT_CALLBACK_OUTBOX_DIR=/var/lib/nmbot/callback-outbox
NMBOT_CALLBACK_LEDGER_DIR=/var/lib/nmbot/callback-ledger
NMBOT_CALLBACK_SHEET_ID=<spreadsheet id>
NMBOT_CALLBACK_SHEET_TAB=<business tab name>
NMBOT_CALLBACK_GOOGLE_CREDENTIALS=/secure/path/service-account.json
NMBOT_CALLBACK_TIMEZONE=Europe/Moscow
NMBOT_CALLBACK_MAX_ATTEMPTS=8
NMBOT_CALLBACK_RETRY_BASE_SECONDS=30
```

Требования:

- outbox и ledger directories — `0700`;
- записи и Google credentials — `0600`;
- worker запускается под тем же ограниченным user, которому доступны только эти каталоги;
- credentials path проверяется на startup; значение secret не выводится.

### 14.9 Systemd worker

Unit запускает только worker, не API и не bridge:

```ini
[Service]
ExecStart=/usr/bin/python3 /home/neiro/novostroy-bot/scripts/nmbot_callback_sheet_worker.py
Restart=on-failure
RestartSec=10
UMask=0077
```

Полный unit дополнительно задаёт private EnvironmentFile и filesystem restrictions после проверки совместимости VPS. API service и bridge service не перезапускаются при рестарте worker.

### 14.10 Порядок развертывания

```text
1. Local code + fake adapters + deterministic tests.
2. Создать Google service account и выдать ему Editor только на нужную таблицу.
3. Проверить название business tab и timezone МСК.
4. Создать private directories, credentials и systemd worker на VPS.
5. Запустить worker с fake adapter на isolated VPS path.
6. Проверить один synthetic outbox record: summary → append → ledger → delivered.
7. Deploy Jivo API contact state machine и worker отдельно, с backup.
8. Разрешённый первый real callback: сразу проверить outbox/worker safe log/Sheet row.
9. При первом failure остановить дальнейшие real callbacks и разобрать stage.
```

### 14.11 Известные пробелы, которые нельзя угадывать

- Sheet tab name и service-account email;
- где хранить Google credentials на VPS;
- retention/удаление phone и private records;
- требуется ли юридический текст согласия;
- допустимо ли использовать Jivo profile name без дополнительного подтверждения;
- как обрабатывается повторная осознанная заявка одного клиента после первой delivered записи.

## 15. Источники

- Active Jivo phone/outbox runtime: `scripts/nmbot_api_server.py:504-541`, `scripts/nmbot_crm_outbox.py:1-195`.
- Privacy and contact contract: `docs/BOT_ARCHITECTURE.md:670-701`.
- Lead context: `docs/PRODUCT_TZ.md:1461-1496`.
- Google Sheets append reference: <https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append>.
- Google service account guidance: <https://developers.google.com/identity/protocols/oauth2/service-account>.
