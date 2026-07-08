# nmbot — Novostroy AI Бот

Контекст для opencode/ЧАТИ.

## Один источник правды

Единственная актуальная продовая версия бота — `novostroy-bot.service` на VPS (`/home/neiro/novostroy-bot`, фактический runtime сейчас: `python3 scripts/chat_tester_bot.py`). Локальная папка `/home/ser/ai/projects/nmbot` — это рабочая копия для разработки и тестов, а не отдельный боевой бот.

Название `tsbot` в проектной доке не используется.

## MemPalace — обязательно

Для этого проекта контекст хранится и обновляется через MemPalace. Перед любым ответом по проекту сначала проверяй проектный wing в MemPalace; не опирайся на память модели.

После завершения сессии:

1. коротко зафиксируй важные факты в NotebookLM;
2. запиши итог в личный diary MemPalace;
3. если появился новый устойчивый факт по проекту — добавь его в проектный wing.

## Архитектура (две среды)

```
Prod (VPS):     systemd novostroy-bot → python3 scripts/chat_tester_bot.py
Staging (VPS):  systemd novostroy-bot-staging → python3 scripts/chat_tester_bot.py
Локальный стенд: scripts/chat_tester_bot.py
```

- **Бэкенд**: gateway-agent (Overmind) → OpenRouter + MCP novostroym
- **Модель поиска**: `google/gemini-3.1-flash-lite-preview` (OpenRouter) + MCP novostroym
- **Модель общения**: `google/gemini-2.5-flash` (OpenRouter), отвечает по найденным фактам
- **MCP алиас**: novostroym
- **Токены**: .env (заполнить вручную)

## UX North Star — обязательно

Единый эталон UX: `docs/IDEAL_IRINA_UX.md`.

Перед любой правкой, которая влияет на ответы Ирины, промпты, Telegram handler, inline-кнопки, память диалога, MCP/search parsing или автотесты, сначала прочитай `docs/IDEAL_IRINA_UX.md` и сверяй решение с ним.

Нельзя отдавать бота пользователю только потому, что жив PID/getUpdates. Готовность = Telegram end-to-end работает, полный `python3 scripts/nmbot_test_agent.py` зелёный, и ответы соответствуют `docs/IDEAL_IRINA_UX.md`.

## Prod Deploy Gate — обязательно

Любая правка, которая влияет на ответы MINION/Ирины, промпты, routing, state, MCP/search parsing, visible options или операторскую воронку, считается незавершённой, пока она не прошла **боевой VPS-контур**.

Главное правило: **работоспособный Telegram-бот в проде важнее формально зелёных локальных тестов**. Нельзя доводить отчёт до «зелёного» состояния, если нет убеждённости, что `novostroy-bot.service` после правки реально запускается, принимает сообщения и не ломает пользовательский диалог. Ошибки прод-бота недопустимы.

Запрещены временные заплатки, которые только прячут симптом в конкретном тесте, но не меняют принцип работы слоя. Если проблема в контракте MCP/search parsing, публикации фактов, routing или state, исправление должно чинить общий контракт слоя и проверяться на плохом и хорошем реальном прогоне.

Правило процесса:

1. Сначала можно проверять локально: `py_compile`, `h029`, `ux_e2e`, `h028`, `dialog`, `stateful`, simulator/live probe.
2. Затем обязательно сделать deploy/sync на VPS в `/home/neiro/novostroy-bot`.
3. Перезапустить `novostroy-bot.service`.
4. Проверить feature markers на VPS, а не только локально.
5. Финальная проверка — только через prod/VPS MINION: Telegram/live logs или prod smoke на `/home/neiro/novostroy-bot`.
6. После live/prod проверки вывести `python3 scripts/or_cost.py`.

Запрещено говорить «готово», если проверена только локальная версия. Формулировка должна быть честной: «локально зелёное, prod ещё не проверен».

## Staging / Git workflow

Цель staging — проверять изменения в отдельном Telegram-боте и отдельном systemd-сервисе, не трогая prod-процесс.

- **Prod branch**: `master`, путь `/home/neiro/novostroy-bot`, сервис `novostroy-bot.service`.
- **Staging branch**: `staging`, путь `/home/neiro/novostroy-bot-staging`, сервис `novostroy-bot-staging.service`.
- **Runtime одинаковый**: `python3 scripts/chat_tester_bot.py`.
- **Различаются только окружения**: `.env`, Telegram bot token, логи, systemd-service.
- **Нельзя** запускать staging на prod `TELEGRAM_BOT_TOKEN`: Telegram polling у двух процессов на одном токене будет конфликтовать.

Текущий безопасный порядок:

1. Локально внести правку и прогнать дешёвые проверки.
2. Положить правку в `staging` / staging worktree.
3. Запустить `novostroy-bot-staging.service` только с отдельным тестовым Telegram bot token.
4. Проверить staging-диалог, логи и regression-сценарии.
5. Только после этого переносить изменение в `master` и деплоить prod.

Важно: если GitHub credentials на VPS не настроены, staging может существовать как локальная VPS-ветка/worktree. Для полноценного удалённого workflow нужно отдельно настроить push-доступ и создать remote branch `origin/staging`.

Минимальный prod deploy checklist:

```bash
# 1. Backup перед заменой runtime-файлов
ssh -p 1905 neiro@193.107.155.236 \
  "cd /home/neiro/novostroy-bot && ts=\$(date +%Y%m%d-%H%M%S) && mkdir -p backups/deploy-\$ts && cp scripts/chat_tester_bot.py prompts/chat_v1.txt followup_intent_classifier.py backups/deploy-\$ts/ && echo backups/deploy-\$ts"

# 2. Sync runtime-файлов, py_compile, restart
# 3. Проверка markers/status/logs
ssh -p 1905 neiro@193.107.155.236 \
  "systemctl --user status novostroy-bot.service --no-pager && tail -30 /home/neiro/novostroy-bot/logs/bot.log"
```

## Диагностика (единая точка входа)

### Журнал основных ошибок бота — смотреть первым при аларме

Если пользователь пишет «бот не отвечает», «прод сломан», «сообщения висят», сначала проверяй свежий VPS и **журнал событий ошибок**:

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "cd /home/neiro/novostroy-bot && tail -50 logs/bot_error_events-$(date -u +%F).jsonl"
```

Файл: `/home/neiro/novostroy-bot/logs/bot_error_events-YYYY-MM-DD.jsonl`.

Туда обязаны попадать все основные причины падения или некорректной работы:
- `gateway_create_failed` — gateway task не создался;
- `gateway_missing_task_id` — gateway не вернул task id;
- `gateway_task_error` — gateway вернул ошибку;
- `gateway_empty_response` — ответа нет;
- `gateway_non_text_response` — upstream вернул объект/массив вместо текста;
- `gateway_timeout` — задача не завершилась вовремя;
- `chat_response_parse_failed` — chat JSON не распарсился после retry;
- `message_ask_exception` — exception при основном запросе;
- `handler_non_text_response` — Telegram handler получил не текст перед отправкой;
- `telegram_unhandled_exception` — необработанное падение Telegram update.

Важно: `systemctl active` и `getUpdates 200 OK` не доказывают, что бот отвечает пользователю. Если есть свежие записи в `bot_error_events-*.jsonl`, разбирать их перед выводом «бот работает».

```bash
# всё в одном
bash scripts/nmbot_diag.sh

# быстро: только PID + uptime + memory VPS-бота
bash scripts/nmbot_diag.sh --quick
```

### Быстрые проверки руками

```bash
# Статус продакшн-бота на VPS
ssh -p 1905 neiro@193.107.155.236 "systemctl --user status novostroy-bot.service --no-pager"

# Последние строки лога
ssh -p 1905 neiro@193.107.155.236 "tail -20 /home/neiro/novostroy-bot/logs/bot.log"

# Тикетная диагностика: процесс жив?
ssh -p 1905 neiro@193.107.155.236 "pgrep -af 'python.*bot'"

# Локальный dev-smoke
python3 scripts/nmbot_deploy_smoke.py
python3 scripts/nmbot_test_agent.py --suite deploy
```

## Production (VPS)

- **Сервер**: `neiro@193.107.155.236:1905`
- **Сервис**: `novostroy-bot.service` (systemd --user)
- **Путь**: `/home/neiro/novostroy-bot`
- **Запуск**: `python3 scripts/chat_tester_bot.py`
- **Репозиторий**: `github.com/dmegabyte/novostroy-bot.git` (master)
- **Лог**: `/home/neiro/novostroy-bot/logs/bot.log`
- **Telegram**: через Cloudflare Worker `telegram-bot-proxy.d-megabyte.workers.dev`

## Staging (VPS)

- **Сервер**: `neiro@193.107.155.236:1905`
- **Сервис**: `novostroy-bot-staging.service` (systemd --user)
- **Путь**: `/home/neiro/novostroy-bot-staging`
- **Запуск**: `python3 scripts/chat_tester_bot.py`
- **Ветка**: `staging`
- **Лог**: `/home/neiro/novostroy-bot-staging/logs/bot.log`
- **Telegram**: только отдельный тестовый bot token в `/home/neiro/novostroy-bot-staging/.env`

## Локальный стенд

- **Путь**: `/home/ser/ai/projects/nmbot`
- **Бот**: `scripts/chat_tester_bot.py`
- **Запуск**: `bash scripts/run_bot.sh` или `python scripts/chat_tester_bot.py`
- **Лог**: `logs/bot.log`, `logs/bot.err`
- **Промпты**: `prompts/chat_v1.txt`, `prompts/search_v1.txt`
- **Тесты**: `python3 scripts/nmbot_test_agent.py`

## Запуск (dev)

```bash
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)

# CLI
python scripts/chat_cli.py "Запрос"

# TG бот (dev)
python scripts/chat_tester_bot.py
```

## Токены (.env)

| Переменная | Откуда |
|-----------|--------|
| OVERMIND_TOKEN | vault secret/projects/NOVOSTROY_AI → NOVOSTROY_M_TOKEN |
| OPENROUTER_API_KEY | vault secret/projects/NOVOSTROY_AI → openrouter_token |
| TELEGRAM_BOT_TOKEN | из .env.bot основного проекта |

## Скрипты

| Скрипт | Назначение |
|--------|-----------|
| `scripts/nmbot_diag.sh` | ★ единая диагностика prod + dev |
| `scripts/chat_cli.py` | CLI-клиент (двухшаговый запрос: поиск → ответ) |
| `scripts/chat_tester_bot.py` | фактический runtime Telegram-бота; используется в dev и сейчас запущен в prod |
| `scripts/nmbot_mcp_only_sim.py` | симулятор UX-гипотез на MCP-данных до правок боевого кода |
| `scripts/nmbot_test_agent.py` | CLI-агент автотестирования (codex + dialog + deploy) |
| `scripts/nmbot_deploy_smoke.py` | проверка live-процесса prod/VPS по умолчанию; local-режим через `NMBOT_DEPLOY_MODE=local` |
| `scripts/nmbot_quality.py` | оперативная проверка логов |
| `scripts/run_bot.sh` | запуск локального стенда |
| `scripts/or_cost.py` | OpenRouter cost tracking |
| `scripts/or_monitor.py` | мониторинг + auto-block |

## Project agents

| Агент | Назначение |
|---|---|
| `nmbot-ux-architect` | Специалист по UX/промптам/routing/state Ирины. Работает архитектурно: сначала определяет класс проблемы и слой решения, симулирует гипотезу, потом предлагает/делает правку, проверяет автотестами и live-dialog глазами. |

## Experiment Loop

Каждое изменение в боте привязывается к гипотезе и логируется. Полная схема — `docs/EXPERIMENTS.md`.

- **Hypothesis Simulation Gate:** перед изменением UX-логики Ирины сначала смоделировать поведение через `scripts/nmbot_mcp_only_sim.py` или аналогичный read-only симулятор. Цель — увидеть живой диалог, найти слабые места и только потом менять `chat_tester_bot.py` / промпты.
- **Гипотезы** (`H###`): реестр в `docs/EXPERIMENTS.md` и `logs/hypotheses.jsonl`.
- **Версии промптов** (`P###`): `logs/prompts.jsonl`.
- **Диалоги**: `logs/dialogs-YYYY-MM-DD.jsonl`.
- **Текущая активная гипотеза:** **H001 — Baseline**.
