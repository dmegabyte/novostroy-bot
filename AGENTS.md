# nmbot — Novostroy AI Бот

Контекст для opencode/ЧАТИ.

## Один источник правды

Единственная актуальная продовая версия бота — `novostroy-bot.service` на VPS (`/home/neiro/novostroy-bot`, запуск `python3 -m src.bot`). Локальная папка `/home/ser/ai/projects/nmbot` — это рабочая копия для разработки и тестов, а не отдельный боевой бот.

Название `tsbot` в проектной доке не используется.

## MemPalace — обязательно

Для этого проекта контекст хранится и обновляется через MemPalace. Перед любым ответом по проекту сначала проверяй проектный wing в MemPalace; не опирайся на память модели.

После завершения сессии:

1. коротко зафиксируй важные факты в NotebookLM;
2. запиши итог в личный diary MemPalace;
3. если появился новый устойчивый факт по проекту — добавь его в проектный wing.

## Архитектура (две среды)

```
Prod (VPS):  systemd novostroy-bot → python3 -m src.bot
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

1. Сначала можно проверять локально: `py_compile`, `h029`, `ux_e2e`, `h028`, simulator/live probe.
2. Затем обязательно сделать deploy/sync на VPS в `/home/neiro/novostroy-bot`.
3. Перезапустить `novostroy-bot.service`.
4. Проверить feature markers на VPS, а не только локально.
5. Финальная проверка — только через prod/VPS MINION: Telegram/live logs или prod smoke на `/home/neiro/novostroy-bot`.
6. После live/prod проверки вывести `python3 scripts/or_cost.py`.

Запрещено говорить «готово», если проверена только локальная версия. Формулировка должна быть честной: «локально зелёное, prod ещё не проверен».

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
- **Запуск**: `python3 -m src.bot`
- **Репозиторий**: `github.com/dmegabyte/novostroy-bot.git` (master)
- **Лог**: `/home/neiro/novostroy-bot/logs/bot.log`
- **Telegram**: через Cloudflare Worker `telegram-bot-proxy.d-megabyte.workers.dev`

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
| `scripts/chat_tester_bot.py` | TG бот dev (с /model /mcp /reset) |
| `scripts/nmbot_mcp_only_sim.py` | симулятор UX-гипотез на MCP-данных до правок боевого кода |
| `scripts/nmbot_test_agent.py` | CLI-агент автотестирования (codex + dialog + deploy) |
| `scripts/nmbot_deploy_smoke.py` | проверка live-процесса локального стенда |
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
