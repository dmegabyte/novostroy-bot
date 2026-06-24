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

    # N8N WF1 webhook (gateway-agent → MCP novostroym, open)
    n8n_url: str = field(
        default_factory=lambda: os.getenv("N8N_URL", "https://n8n.it-system.io")
    )
    n8n_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "N8N_ENDPOINT", "/webhook/openrouter-direct-test"
        )
    )

    # Overmind — для poll результата
    overmind_url: str = field(
        default_factory=lambda: os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
    )
    gateway_poll_token: str = field(
        default_factory=lambda: os.getenv("GATEWAY_POLL_TOKEN", "")
    )

    # OpenRouter — для ответчика (прямой вызов, без gateway)
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "")
    )

    # Модели
    # Search: gemini-3.1-flash-lite-preview — есть в n8n gateway route + поддерживает MCP
    model_search: str = "google/gemini-3.1-flash-lite-preview"
    # Answer: идёт напрямую в OpenRouter, поэтому модель любая
    model_answer: str = "google/gemini-2.5-flash-lite"

    # Параметры
    search_temperature: float = 0.0
    answer_temperature: float = 0.3
    max_tokens_search: int = 600
    max_tokens_answer: int = 600
    gateway_timeout: int = 60
    openrouter_timeout: int = 30

    def validate(self) -> list[str]:
        errors = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN не задан")
        if not self.gateway_poll_token:
            errors.append("GATEWAY_POLL_TOKEN не задан")
        if not self.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY не задан")
        return errors
