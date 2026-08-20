# Client Production Jivo contour

## Current status — 2026-08-20

The public Jivo entry is https://jivo.chat/Q5FRTBLR32. The real public
`client-production` contour has been migrated to immutable V6 release
`v6-client-production-20260820t1515z`.

Fresh VPS post-check proved that both client-production services are active,
their systemd units run from `current`, `current` points to that release, API
`:8188` and bridge `:8193` health checks pass, release identity matches, and
the actual runtime selector is `V6`.

No new end-user Jivo/model smoke was sent after migration because the account
balance was unavailable. Therefore deployment and service health are proven;
a fresh customer-answer check remains pending. The earlier 2026-07-24 setup
notes below are historical context, not current status.

Этот документ — точка продолжения работ по отдельному клиентскому production-контуру. Действующий Jivo-контур `/home/neiro/novostroy-bot` и его systemd units менять нельзя.

## Цель

Поднять второй независимый Jivo-контур с профилем `client_production`:

- отдельный Jivo bot/webhook;
- отдельные API и bridge processes;
- отдельные env, state, runtime selector, outbox, logs и release identity;
- административный выбор runtime version, currently V6;
- отсутствие версий и технических маркеров в клиентских сообщениях;
- сбой нового контура не должен затронуть действующий тестовый контур.

## Что уже реализовано

- Общий client-facing egress guard: `scripts/nmbot_egress_policy.py`.
- Guard подключён в API и последней bridge-точке перед отправкой `BOT_MESSAGE`.
- Клиентские `/start_0`, `/start_2`, `/start_3` не переключают runtime в production-профиле.
- Старые session overrides игнорируются и удаляются; runtime задаёт только глобальный admin selector.
- Отдельный admin CLI: `scripts/nmbot_client_production_runtime.py`.
- Fail-closed startup preflight: `scripts/nmbot_client_production_preflight.py`.
- Изолированные systemd templates:
  - `deploy/systemd/novostroy-bot-client-production-api.service`;
  - `deploy/systemd/novostroy-bot-client-production-n8n-bridge.service`;
  - `deploy/systemd/novostroy-bot-client-production.env.example`.
- Bridge проверяет внутренний token и Jivo path token до фоновой обработки.
- Hard timeout заканчивается одним безопасным terminal `BOT_MESSAGE`.
- Секреты не попадают в bridge logs; фоновые задачи завершаются или отменяются при shutdown.
- Env writer использует atomic write и права `0600`.
- Live production profile использует free manager rewriter только для V3: `NMBOT_V2_MANAGER_REWRITER_MODE=off`, `NMBOT_V3_MANAGER_REWRITER_MODE=publish`.
- На VPS создан root `/home/neiro/novostroy-bot-client-production` и env-файл с правами `0600`.
- Сгенерированы отдельные provider/API/bridge secrets; значения не хранятся в документации.
- Новые API и bridge units запущены и проходят health-check без restart loop.
- Создан и активирован отдельный n8n workflow `Jivo / Irina client-production bridge to VPS`.
- Безопасный webhook probe через новый route завершился `HTTP 200`; execution завершился успешно.

Планируемая схема:

```text
Jivo bot #2
  -> отдельный HTTPS/webhook
  -> novostroy-bot-client-production-n8n-bridge.service :8193
  -> novostroy-bot-client-production-api.service 127.0.0.1:8188
  -> runtime V6
  -> BOT_MESSAGE через общий egress guard
```

## Изоляция на VPS

| Назначение | Значение |
|---|---|
| Root | `/home/neiro/novostroy-bot-client-production` |
| Env | `.env.client-production` |
| API | `127.0.0.1:8188` |
| Bridge | `0.0.0.0:8193` |
| API unit | `novostroy-bot-client-production-api.service` |
| Bridge unit | `novostroy-bot-client-production-n8n-bridge.service` |
| Active runtime | `V6` |

Фактический live status на 2026-07-24 09:28 UTC: оба новых units `active`,
`NRestarts=0`, API health и bridge health зелёные. Тестовый API/bridge также
остались healthy.

Все mutable paths должны находиться только под новым root. Это проверяет preflight.

## Какие секреты создаём сами

Для контура были сгенерированы криптографически случайные значения и записаны без вывода в терминал/чат:

- `JIVO_PROVIDER_TOKEN` — уникальный path token нового Jivo-бота;
- `NMBOT_API_TOKEN` — bridge/admin → private API;
- `NMBOT_N8N_BRIDGE_TOKEN` — внешний webhook/n8n → bridge.

Файл `.env.client-production` должен иметь права `0600`. Значения секретов нельзя добавлять в git, README, task output или логи.

## Historical initial Jivo/infrastructure setup

1. Подтвердить `JIVO_PROVIDER_ID` нового бота. Если используется тот же provider account, проверить возможность безопасно использовать существующий Provider ID; не угадывать значение.
2. Создать/подключить новый Bot API operator в Jivo.
3. Настроить отдельный публичный HTTPS webhook.
4. Указать в Jivo тот же сгенерированный `JIVO_PROVIDER_TOKEN`.
5. Определить каналы/виджеты, которые направляются именно в нового бота.

Текущий webhook `https://n8n.it-system.io/webhook/msknmbot` принадлежит действующему контуру и не изменялся.

Для нового контура создан отдельный активный route:

```text
https://n8n.it-system.io/webhook/msknmbot-prod/jivo_prod_9jr6O6BreVALjGKnX9Xagl
```

Workflow ID: `jJVAxNz4MQefwADR`.
Он направляет события только на bridge `193.107.155.236:8193`.
This was the initial setup state on 2026-07-24. It is superseded by the live
public delivery evidence and V6 migration status above.

## Historical initial rollout checkpoint

This section records the 2026-07-24 checkpoint before public activation. It is
not the current production status.

- отдельный VPS-контур запущен: API `:8188`, bridge `:8193`;
- selector was then configured for `V3`;
- V2 manager rewriter выключен, V3 работает в `publish`/free mode;
- отдельный n8n workflow активен и направляет события только в новый bridge;
- безопасный `CHAT_CLOSED` probe прошёл: HTTP 200, execution `1353238` — `success`;
- старый test-контур и route `msknmbot` не менялись.

At that historical checkpoint, the planned manual Jivo steps were:

1. Создать отдельного **Bot API operator** в том же аккаунте Jivo.
2. Передать ему provider token нового контура по защищённому каналу. Значение берётся
   непосредственно из VPS env-файла; в документации и чате его не хранить.
3. Назначить operator на нужный production-канал/виджет.
4. Отправить одно реальное тестовое сообщение через новый Jivo operator.
5. Проверить correlated trace: один `CLIENT_MESSAGE`, один terminal `BOT_MESSAGE`,
   отсутствие технических маркеров и отсутствие новых записей в старом контуре.

The historical public smoke was not proof of the later V6 migration. The
current post-migration limitation is stated at the beginning of this document.

Отдельно: provider token и n8n API key, которые ранее были показаны в чате, считаются
скомпрометированными. Перед полноценным запуском их нужно перевыпустить и обновить
соответствующие защищённые конфиги.

## Проверки, уже выполненные локально

Focused contour/security suites:

```text
36 passed
```

Проверены egress guard, stale overrides, Jivo token boundary, startup preflight, hard-timeout terminal fallback, async-task cleanup, env permissions и unit isolation. Изменённые Python-файлы проходят `py_compile`.

Расширенный API/runtime suite сейчас не полностью зелёный:

```text
179 passed, 3 failed
```

Три падения находятся в старых assertions количества `main_search` calls: тест ожидает два вызова, фактически получает три. Они воспроизводятся отдельно и не относятся к новому contour-коду, но перед production release их нужно либо исправить, либо оформить как подтверждённое baseline-исключение с владельцем решения.

## Последовательность финального подключения

Порядок обязателен; при первом сбое остановиться.

1. В Jivo создать отдельного Bot API operator в том же аккаунте.
2. Указать ему webhook из раздела выше и provider token из VPS env через защищённый канал.
3. Назначить operator на нужный боевой виджет/канал.
4. Отправить ровно один первый `CLIENT_MESSAGE`; проверить correlated bridge/dialogue/error trace и один terminal `BOT_MESSAGE`.
5. Проверить отсутствие технических маркеров в клиентском тексте и неизменность тестового контура.

## Runtime admin

После запуска статус:

```bash
python3 scripts/nmbot_client_production_runtime.py status
```

Переключение, например на V3:

```bash
python3 scripts/nmbot_client_production_runtime.py set V3 --confirm
```

CLI работает только с `client_production`, loopback API и портом 8188. Клиент не может переключить runtime сообщением.

Manager rewriter в live `client_production` profile задан явно: V2 остаётся `off`, V3 работает в режиме `publish`. Это «free» переписывание финального prepared answer без смысловой поствалидации: слой может сделать текст живее, но при техническом сбое или пустом результате обязан сохранить исходный prepared answer. Preflight fail-closed блокирует запуск, если `NMBOT_V2_MANAGER_REWRITER_MODE` не равен `off` или `NMBOT_V3_MANAGER_REWRITER_MODE` не равен `publish` после той же нормализации whitespace/case, которую использует runtime.

## Rollback

До первого write сохранить:

- список отсутствовавших файлов;
- hashes существовавших файлов;
- env/config backup;
- прежнее состояние units;
- состояние действующего контура.

При ошибке:

1. не продолжать batch;
2. остановить только новые client-production units;
3. восстановить изменённые файлы/config, удалить newly-added targets при необходимости;
4. проверить действующие production API/bridge, их health и свежие journals;
5. зафиксировать слой первой ошибки: config, systemd, ingress, API, bridge, Jivo или runtime.

## Критерий готовности

Контур считается развёрнутым только когда одновременно доказаны:

- оба новых units `active` без restart loop;
- API/bridge health зелёные;
- selector показывает нужную версию;
- preflight и remote compile/import зелёные;
- отдельный Jivo webhook принимает событие;
- первый correlated turn заканчивается одним terminal `BOT_MESSAGE`;
- client-facing текст не содержит технических маркеров;
- действующий тестовый Jivo-контур не изменён.

## Источники

- `docs/NMBOT_RUNBOOK.md:122-146` — production deploy gate.
- `docs/JIVO_BOT_API_INTEGRATION_PLAN.md:25-45,452-516` — Jivo webhook/provider contract.
- `scripts/nmbot_client_production_preflight.py` — fail-closed configuration contract.
- `scripts/nmbot_client_production_runtime.py` — admin-only runtime selector.
- `tests/test_nmbot_client_production_contour.py` — isolation and egress tests.
- `tests/test_nmbot_n8n_bridge_transport_timeout.py` — terminal delivery behavior.
