"""
Telegram-бот для Novostroy AI.

Двухмодельный пайплайн:
1. Gemini 3.1 Lite (MCP-поиск) → данные
2. Gemini 2.5 Flash Lite (человечный ответ) → ответ

Сессия с кэшем: уточнения без нового поиска.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

import aiohttp
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .config import Config
from .session import Session

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bot")

# ── Хранилище сессий ─────────────────────────────────────────

sessions: dict[int, Session] = {}
config: Config | None = None


def get_session(user_id: int) -> Session:
    """Получить или создать сессию для пользователя."""
    if user_id not in sessions:
        sessions[user_id] = Session(config=config)
    return sessions[user_id]


# ── Обработчики ──────────────────────────────────────────────

async def start(update: Update, _context) -> None:
    """Команда /start."""
    user = update.effective_user
    await update.message.reply_text(
        f"Здравствуйте, {user.first_name}!\n"
        "Я Ирина, консультант по недвижимости.\n"
        "Задайте вопрос о квартирах в Москве и области.\n\n"
        "Команды:\n"
        "/start — начать\n"
        "/new — начать новый диалог (сбросить историю)\n"
        "/help — справка"
    )


async def new_dialog(update: Update, _context) -> None:
    """Команда /new — сброс сессии."""
    user_id = update.effective_user.id
    if user_id in sessions:
        sessions[user_id].reset()
    await update.message.reply_text("Начинаем новый поиск. Задайте вопрос.")


async def help_command(update: Update, _context) -> None:
    """Команда /help."""
    await update.message.reply_text(
        "Я помогаю подобрать квартиру в новостройках Москвы и области.\n\n"
        "Примеры запросов:\n"
        "• «Подберите однушку до 11 млн в Москве»\n"
        "• «Что за ЖК Wellbe?»\n"
        "• «Какие там планировки?» (уточнение)\n"
        "• «А есть дешевле 9 млн?» (новый поиск)"
    )


async def handle_message(update: Update, _context) -> None:
    """Обработка сообщения пользователя."""
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    query = update.message.text.strip()
    logger.info("Пользователь %d: %s", user_id, query)

    await update.message.chat.send_action("typing")
    session = get_session(user_id)
    reply = await session.process(query)
    await update.message.reply_text(reply)


# ── Запуск ───────────────────────────────────────────────────

def main() -> None:
    """Точка входа."""
    global config
    config = Config()

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error(err)
        print("Ошибки конфигурации:", "; ".join(errors))
        print("Проверьте .env файл или переменные окружения.")
        return

    request = HTTPXRequest(
        connection_pool_size=1,
    )

    # Очищаем pending updates при старте, чтобы не дублировать ответы
    # на старые сообщения после рестарта.
    async def drop_pending_updates(application: Application) -> None:
        try:
            async with aiohttp.ClientSession() as sess:
                # Берём последние 100 update'ов и сразу сдвигаем offset,
                # чтобы их не обрабатывать.
                async with sess.get(
                    f"{config.telegram_api_base_url}/getUpdates",
                    params={"offset": -100, "timeout": 0},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    data = await resp.json()
                if data.get("ok") and data.get("result"):
                    updates = data["result"]
                    if updates:
                        # offset следующего update'а = max(update_id) + 1
                        next_offset = max(u["update_id"] for u in updates) + 1
                        async with sess.get(
                            f"{config.telegram_api_base_url}/getUpdates",
                            params={"offset": next_offset, "timeout": 0},
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as resp:
                            await resp.read()  # подтверждаем offset
                        logger.info(
                            "Очищено %d pending update'ов (offset=%d)",
                            len(updates), next_offset,
                        )
        except Exception as e:
            logger.warning("Не удалось очистить pending updates: %s", e)

    app = (
        Application.builder()
        .token(config.telegram_bot_token)
        .base_url(config.telegram_api_base_url)
        .request(request)
        .post_init(drop_pending_updates)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_dialog))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
