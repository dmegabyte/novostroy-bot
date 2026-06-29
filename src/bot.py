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
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
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
        "Привет! Я Ирина, помогу подобрать квартиру в новостройке.\n\n"
        "Могу искать по району, бюджету, количеству комнат и отделке. Например:\n"
        "• «двушка с отделкой в Солнцево до 15 млн»\n"
        "• «квартира в Котельниках»\n"
        "• «студия в пределах МКАД»\n\n"
        "Если ничего не найду — предложу оператора."
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


CONTACT_KEYWORDS = frozenset({
    "Поделиться контактом", "поделиться контактом",
    "оператор", "оператора", "оператору", "оператором",
})

# Последнее сообщение каждого юзера — для dedup
last_user_msg: dict[int, tuple[str, float]] = {}
DEDUP_WINDOW_SEC = 30
DEDUP_MAX_DIFF = 2  # Levenshtein distance


def _levenshtein(a: str, b: str) -> int:
    """Простой Levenshtein distance."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(
                cur[j] + (ca != cb),  # insert
                prev[j + 1] + 1,      # delete
                prev[j] + (ca != cb), # replace
            ))
        prev = cur
    return prev[-1]


def _is_duplicate(user_id: int, query: str) -> bool:
    """Слишком похоже на предыдущее сообщение того же юзера?"""
    import time
    if user_id not in last_user_msg:
        return False
    prev_msg, prev_time = last_user_msg[user_id]
    if time.time() - prev_time > DEDUP_WINDOW_SEC:
        return False
    # Сравниваем по длине и расстоянию
    if abs(len(prev_msg) - len(query)) > 3:
        return False
    if _levenshtein(prev_msg.lower(), query.lower()) <= DEDUP_MAX_DIFF:
        return True
    return False


async def handle_message(update: Update, _context) -> None:
    """Обработка сообщения пользователя."""
    if not update.message or not update.message.text:
        return

    import time
    user_id = update.effective_user.id
    query = update.message.text.strip()
    logger.info("Пользователь %d: %s", user_id, query)

    # Dedup: если сообщение почти такое же (опечатка) — игнорируем
    if _is_duplicate(user_id, query):
        logger.info("Игнорируем дубль: %s", query)
        return

    # Запоминаем сообщение (для следующего dedup)
    last_user_msg[user_id] = (query, time.time())

    await update.message.chat.send_action("typing")
    session = get_session(user_id)
    reply = await session.process(query)

    # Если в ответе бот предлагает связаться с оператором — показываем кнопку
    if any(kw in reply for kw in CONTACT_KEYWORDS):
        contact_btn = KeyboardButton("📞 Поделиться контактом", request_contact=True)
        keyboard = ReplyKeyboardMarkup(
            [[contact_btn]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text(reply, reply_markup=keyboard)
    else:
        await update.message.reply_text(reply)


async def handle_contact(update: Update, _context) -> None:
    """Обработка отправленного контакта."""
    contact = update.message.contact
    user = update.effective_user
    logger.info(
        "Контакт от %d: %s %s, тел: %s",
        user.id, contact.first_name, contact.last_name or "",
        contact.phone_number,
    )
    # Убираем клавиатуру
    await update.message.reply_text(
        "Спасибо! Оператор свяжется с Вами в ближайшее время.",
        reply_markup=ReplyKeyboardMarkup.remove_keyboard(),
    )


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
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
