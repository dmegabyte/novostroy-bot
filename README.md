# Novostroy AI Bot

Telegram-бот для подбора квартир в новостройках Москвы и области.

## Архитектура

Двухмодельный пайплайн:

```
Пользователь → [Telegram Bot]
                    ↓
         [Session — кэш истории]
                    ↓
         [Gateway Agent] → Gemini 3.1 Lite (T=0) + MCP novostroym
                    ↓                   
         [OpenRouter] → Gemini 2.5 Flash Lite (T=0.8) → ответ
                    ↓
              Пользователь
```

- **Поиск:** Gemini 3.1 Flash Lite через gateway-agent с MCP novostroym
- **Ответ:** Gemini 2.5 Flash Lite напрямую через OpenRouter API (без gateway)
- **Сессия:** кэш данных MCP — уточнения обрабатываются без нового поиска
- **Telegram:** через Cloudflare Worker `telegram-bot-proxy` (прозрачный прокси Bot API)

## Быстрый старт

```bash
# 1. Скопировать .env и заполнить
cp .env.example .env
# → TELEGRAM_BOT_TOKEN, OVERMIND_TOKEN, OPENROUTER_API_KEY

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Запустить
python -m src.bot
```

## Переменные окружения

| Переменная | Описание |
|-----------|----------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота |
| `TELEGRAM_API_BASE_URL` | URL Cloudflare Worker для прокси Bot API |
| `OVERMIND_URL` | URL gateway-agent (по умолч. https://overmind.aiaxel.ru) |
| `OVERMIND_TOKEN` | Токен gateway-agent |
| `OPENROUTER_API_KEY` | Ключ OpenRouter API |

## Cloudflare Worker

Прокси для Telegram Bot API:
- Worker: `telegram-bot-proxy.d-megabyte.workers.dev`
- Код: `tools/telegram-proxy-worker.js` (в `cc-daemons` репо)
- Деплой: `cd worker && wrangler deploy`

## Команды бота

- `/start` — начало работы
- `/new` — сбросить историю диалога
- `/help` — справка

## Примеры диалогов

```
👤 Подберите однушку до 11 млн в Москве
💬 Нашла несколько вариантов однокомнатных квартир в Москве до 11 млн.

👤 А что по планировкам?
💬 (из кэша — без нового поиска)

👤 А есть варианты дешевле 9 млн?  
💬 (новый поиск — изменился бюджет)
```

## Деплой на сервере

```bash
# Клонировать
git clone https://github.com/dmegabyte/novostroy-bot.git
cd novostroy-bot

# Установить зависимости (system-wide)
pip install -r requirements.txt --break-system-packages

# Создать .env с реальными токенами
cp .env.example .env && nano .env
chmod 600 .env

# Создать user-level systemd сервис
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/novostroy-bot.service << 'EOF'
[Unit]
Description=Novostroy AI Telegram Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/neiro/novostroy-bot
ExecStart=/usr/bin/python3 -m src.bot
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/neiro/novostroy-bot/.env
StandardOutput=append:/home/neiro/novostroy-bot/logs/bot.log
StandardError=append:/home/neiro/novostroy-bot/logs/bot.log

[Install]
WantedBy=default.target
EOF

# Запустить
systemctl --user daemon-reload
systemctl --user enable --now novostroy-bot

# Проверить
systemctl --user status novostroy-bot
tail -f logs/bot.log
```

## Структура

```
novostroy-bot/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── run.sh
└── src/
    ├── __init__.py
    ├── bot.py         # Telegram бот (3 команды + диалог)
    ├── config.py      # Конфиг из переменных окружения
    └── session.py     # Сессия с кэшем и триггерами
```

## Prompt eval (promptfoo)

Качество answer-промпта проверяется через [promptfoo](https://promptfoo.dev/).

```bash
cd promptfoo/
promptfoo eval
```

5 тестов проверяют:
1. **T1** — двушка + диапазон: выдаёт только ЖК из данных, не выдумывает
2. **T2** — уточнение по конкретному ЖК: использует данные без галлюцинаций
3. **T3** — студии в данных, клиент хочет двушку: честно говорит что двушек нет
4. **T4** — уточнение цены: цитирует данные, не выдумывает цены
5. **T5** — "что за двушки?": перечисляет ЖК из данных

**Результат:** 5/5 passed. Промпт не галлюцинирует.
