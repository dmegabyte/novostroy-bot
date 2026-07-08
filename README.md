# nmbot — Novostroy AI Бот

Проект Telegram-бота для подбора квартир в новостройках Москвы и области (Ирина).

## Единственная актуальная версия

- **Прод**: `novostroy-bot.service` на VPS
- **Путь прод-кода**: `/home/neiro/novostroy-bot`
- **Запуск**: `python3 scripts/chat_tester_bot.py`
- **Локальная копия**: `/home/ser/ai/projects/nmbot` — только для разработки, тестов и документации
- **Путаться не надо**: в этой доке не используется отдельный бот `tsbot`; ориентир один — `novostroy-bot`

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                    ПРОДАКШН (VPS)                              │
│  systemd: novostroy-bot.service                                │
│  репо:  github.com/dmegabyte/novostroy-bot.git                 │
│  путь:  /home/neiro/novostroy-bot                              │
│  пуск:  python3 scripts/chat_tester_bot.py                     │
│  хост:  neiro@193.107.155.236:1905                             │
│                                                                │
│  Пользователь → [Telegram Bot] → [Cloudflare Worker Proxy]     │
│                                → [Gateway Agent] → MCP search   │
│                                → [OpenRouter] → Gemini ответ    │
└─────────────────────────────────────────────────────────────────┘
                              ↑
                              | (тесты и промпты отсюда)
┌─────────────────────────────────────────────────────────────────┐
│                ЛОКАЛЬНЫЙ СТЕНД                                  │
│  путь:  /home/ser/ai/projects/nmbot                            │
│  бот:   scripts/chat_tester_bot.py  (рабочая копия)            │
│  тесты: scripts/nmbot_test_agent.py                            │
│  промпты: prompts/chat_v1.txt, prompts/search_v1.txt           │
└─────────────────────────────────────────────────────────────────┘
```

**Двухмодельный пайплайн (общий для dev и prod):**
1. `google/gemini-3.1-flash-lite-preview` — поиск через MCP `novostroym`, сбор фактов и ссылок.
2. `google/gemini-2.5-flash` — финальное общение с клиентом по найденным фактам, без прямого MCP.

## Диагностика (единая точка входа)

```bash
# Полный статус (VPS + local + лог)
bash scripts/nmbot_diag.sh

# Только статус продакшн-бота (PID, uptime, memory, коммит)
bash scripts/nmbot_diag.sh --quick

# Только последние строки лога
bash scripts/nmbot_diag.sh --logs
```

**Эквивалент вручную** (если нужно без скрипта):
```bash
ssh -p 1905 neiro@193.107.155.236 "systemctl --user status novostroy-bot.service --no-pager"
ssh -p 1905 neiro@193.107.155.236 "tail -20 /home/neiro/novostroy-bot/logs/bot.log"
```

## Структура проекта

```
nmbot/
├── .env                  # токены (заполнить из vault)
├── .gitignore
├── AGENTS.md             # контекст для opencode/ЧАТИ
├── README.md
├── requirements.txt
├── scripts/
│   ├── chat_cli.py            # CLI-клиент (двухшаговый запрос)
│   ├── chat_tester_bot.py     # runtime Telegram-бота: dev и текущий prod
│   ├── nmbot_diag.sh          # ★ единая диагностика (единственная точка входа)
│   ├── nmbot_deploy_smoke.py  # проверка live-процесса prod/VPS по умолчанию
│   ├── nmbot_test_agent.py    # CLI-агент автотестирования
│   ├── nmbot_quality.py       # оперативная проверка логов
│   ├── or_cost.py             # OpenRouter cost tracking
│   ├── or_monitor.py          # мониторинг + auto-block
│   └── run_bot.sh             # запуск dev-бота
├── logs/                     # логи dev-бота
│   ├── bot.log
│   ├── bot.err
│   ├── dialogs-*.jsonl
│   └── hypotheses.jsonl
├── prompts/
│   ├── chat_v1.txt            # Chat-промпт (Ирина)
│   └── search_v1.txt          # Search-промпт (MCP)
└── docs/
    ├── CHANGELOG.md
    ├── CODEX.md
    ├── EXPERIMENTS.md
    └── GOLDEN_DIALOGS.md
```

## Prod / Staging / Dev

| | Production (VPS) | Staging (VPS) | Dev (локально) |
|---|---|---|---|
| **Где** | `neiro@193.107.155.236:1905` | `neiro@193.107.155.236:1905` | `/home/ser/ai/projects/nmbot` |
| **Путь** | `/home/neiro/novostroy-bot` | `/home/neiro/novostroy-bot-staging` | `/home/ser/ai/projects/nmbot` |
| **Запуск** | `systemctl --user start novostroy-bot.service` | `systemctl --user start novostroy-bot-staging.service` | `bash scripts/run_bot.sh` |
| **Код** | `python3 scripts/chat_tester_bot.py` | `python3 scripts/chat_tester_bot.py` | `python scripts/chat_tester_bot.py` |
| **Git** | `master` | `staging` | рабочая копия |
| **Telegram** | prod bot token через Cloudflare Worker proxy | отдельный тестовый bot token | dev token напрямую Bot API |
| **Лог** | `/home/neiro/novostroy-bot/logs/bot.log` | `/home/neiro/novostroy-bot-staging/logs/bot.log` | `logs/bot.log` |
| **Диагностика** | `bash scripts/nmbot_diag.sh` | `systemctl --user status novostroy-bot-staging.service --no-pager` | `bash scripts/nmbot_diag.sh` |

Staging нужен, чтобы проверять изменения в отдельном Telegram-боте до prod. Не запускай staging на prod `TELEGRAM_BOT_TOKEN`: два poller-процесса на одном токене будут мешать друг другу.

## Быстрый старт (dev)

```bash
cd /home/ser/ai/projects/nmbot

# 1. Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Токены (из vault secret/projects/NOVOSTROY_AI)
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=...     # из .env.bot основного проекта
OVERMIND_URL=https://overmind.aiaxel.ru
OVERMIND_TOKEN=...         # NOVOSTROY_M_TOKEN из vault
OPENROUTER_API_KEY=...     # openrouter_token из vault
EOF

# 3. CLI-тест
source .venv/bin/activate
export $(grep -v '^#' .env | xargs)
python scripts/chat_cli.py "Найди однушку до 8 млн в Москве"

# 4. Telegram бот (dev)
python scripts/chat_tester_bot.py
```

## Токены (vault)

Все токены лежат в `secret/projects/NOVOSTROY_AI`:

| Поле .env | Ключ vault | Описание |
|-----------|-----------|----------|
| `OVERMIND_TOKEN` | `NOVOSTROY_M_TOKEN` | Bearer-токен для Overmind API (gateway-agent) |
| `OPENROUTER_API_KEY` | `openrouter_token` | Ключ OpenRouter |
| `TELEGRAM_BOT_TOKEN` | — | Из `.env.bot` основного проекта |

## Команды TG-бота (dev)

- `/start` — приветствие с настройками
- `/model` — выбрать модель поиска (inline-клавиатура)
- `/mcp` — включить/выключить MCP novostroym
- `/reset` — сброс настроек
- `/status` — текущие настройки

## Тестирование

```bash
# CLI-агент автотестов (12+ сценариев)
python3 scripts/nmbot_test_agent.py
python3 scripts/nmbot_test_agent.py --suite deploy   # + live deploy-smoke
python3 scripts/nmbot_test_agent.py --suite dialog   # контрольный диалог
python3 scripts/nmbot_test_agent.py --suite stateful # multi-turn: память, выбор, оператор
python3 scripts/nmbot_test_agent.py --suite compare  # multi-turn: сравнение, рекомендация, оператор
python3 scripts/nmbot_test_agent.py --json           # JSON для CI

# Read-only health: service/env/errors/client cards/payload sizes + answer latency
python3 scripts/nmbot_health.py
python3 scripts/nmbot_health.py --json
```

## Документация

- [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — история изменений (H001–H025)
- [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) — реестр гипотез и метрики
- [`docs/CODEX.md`](docs/CODEX.md) — свод правил диалога (codex)
- [`docs/GOLDEN_DIALOGS.md`](docs/GOLDEN_DIALOGS.md) — эталонные few-shot примеры
