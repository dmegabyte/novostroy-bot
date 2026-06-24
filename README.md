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
| `OVERMIND_URL` | URL gateway-agent (по умолч. https://overmind.aiaxel.ru) |
| `OVERMIND_TOKEN` | Токен gateway-agent |
| `OPENROUTER_API_KEY` | Ключ OpenRouter API |

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
