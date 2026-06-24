"""
Конфигурация бота.
Все настройки из переменных окружения (через .env или export).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # Telegram
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "TELEGRAM_API_BASE_URL",
            "https://telegram-bot-proxy.d-megabyte.workers.dev/bot",
        )
    )

    # Overmind gateway — для поиска через MCP
    overmind_url: str = field(
        default_factory=lambda: os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
    )
    overmind_token: str = field(
        default_factory=lambda: os.getenv("OVERMIND_TOKEN", "")
    )

    # OpenRouter — для ответчика
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )

    # Модели
    model_search: str = "google/gemini-3.1-flash-lite-preview"
    model_answer: str = "google/gemini-2.5-flash-lite"

    # Параметры
    search_temperature: float = 0.0
    answer_temperature: float = 0.8
    max_tokens_search: int = 400
    max_tokens_answer: int = 300
    gateway_timeout: int = 30
    openrouter_timeout: int = 10

    def validate(self) -> list[str]:
        errors = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN не задан")
        if not self.overmind_token:
            errors.append("OVERMIND_TOKEN не задан")
        if not self.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY не задан")
        return errors
