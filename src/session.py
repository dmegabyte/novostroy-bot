"""
Сессия диалога с кэшем данных MCP.

Логика:
- Первый запрос → поиск через MCP, кэшируем данные
- Уточнения → ответ из кэша, без нового поиска
- Изменение параметров (цена, район) → новый поиск
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger("session")

# ── Триггеры поиска ──────────────────────────────────────────

# Слова-маркеры уточнения (не нужен поиск)
REFINE_WORDS = frozenset({
    "планировк", "метраж", "площад", "этаж", "отделк", "ремонт",
    "срок", "сдач", "застройщик", "инфраструктур", "парковк", "двор",
    "высот", "корпус", "секци", "подъезд", "лифт", "террас", "балкон",
    "подроб", "детал", "расскаж", "опиши", "характеристик",
})

# Слова-маркеры нового поиска
SEARCH_WORDS = frozenset({
    "друг", "ещё вариант", "покаж", "подбер", "найд",
    "дешевл", "дороже", "выше", "ниже", "больше", "меньше",
})

# Районы Москвы и Подмосковья
MSC_DISTRICTS = [
    "люблино", "марьино", "братислав", "кузьмин", "текстиль",
    "люберц", "крюков", "зеленоград", "хамовник", "царицын",
    "отрадн", "солнцев", "крылат", "митин", "тушин", "щукин",
    "строгин", "кожухов", "некрасов", "новокосин", "вешняк",
    "рязан", "выхин", "жулебин", "лефортов", "басман",
    "арбат", "преснен", "твер", "замоскворец", "таган",
    "чертан", "южн", "северн", "восточн", "западн",
    "подоль", "мытищ", "химк", "красногор", "одинцов",
]

PRICE_PATTERN = re.compile(r"\d+\s*(млн|тыс|м²|кв|этаж|комн)")


# ── Промпты ──────────────────────────────────────────────────

SEARCH_PROMPT = (
    "Ты — Ирина, консультант по недвижимости. "
    "Найди варианты по запросу. "
    "Верни ТОЛЬКО факты: названия ЖК, цены, площади. "
    "Коротко, без лишних слов."
)

ANSWER_PROMPT = (
    "Ты — Ирина, консультант по недвижимости. Живой человек. "
    "Короткий тёплый ответ (2-3 предложения, на Вы). "
    "Не повторяй обращение из запроса. "
    "Если в данных есть варианты: «Нашла несколько вариантов». "
    "Если данных нет: «Не нашлось, извините». "
    "ЗАПРЕЩЕНО: «к сожалению». "
    "Не называй район или метро. Не выдумывай."
)


# ── Вспомогательные функции ──────────────────────────────────

def detect_needs_search(query: str, cache: dict | None) -> bool:
    """Определяет, нужен ли новый поиск или можно ответить из кэша."""
    if not cache:
        return True  # кэш пуст — нужен поиск

    q = query.lower().strip()

    # 1. Есть ЖК из кэша в запросе → уточнение (кэш)
    cached_names = cache.get("zhk_names", [])
    for name in cached_names:
        if name.lower() in q:
            return False

    # 2. Только слова-уточнения → кэш
    words = set(q.split())
    if words.intersection(REFINE_WORDS) and not words.intersection(SEARCH_WORDS):
        return False

    # 3. Есть числа (бюджет, метраж) → новый поиск
    if PRICE_PATTERN.search(q):
        return True

    # 4. Любое число → скорее новый параметр
    if re.search(r"\d+", q):
        return True

    # 5. Упоминание района/топонима → новый поиск
    for district in MSC_DISTRICTS:
        if district in q:
            return True

    # 6. Короткий запрос (< 5 слов) без поисковых слов → уточнение
    if len(q.split()) < 5 and not any(w in q for w in ["найд", "подбер", "друг"]):
        return False

    return True  # по умолчанию — поиск


def extract_zhk_names(data: str) -> list[str]:
    """Извлекает названия ЖК из данных MCP."""
    names: list[str] = []
    for line in data.split("\n"):
        for match in re.findall(r"«([^»]+)»", line):
            names.append(match)
        for match in re.findall(r"ЖК\s+([А-ЯЁA-Z][^\s,]+)", line):
            if match not in names:
                names.append(match)
    return list(set(names))


# ── Класс сессии ─────────────────────────────────────────────

@dataclass
class Session:
    """Одна сессия диалога с пользователем."""

    config: Any  # Config
    cache: dict | None = None
    refine_history: list[str] = field(default_factory=list)

    async def search(self, query: str) -> str | None:
        """Поиск через gateway-agent с MCP."""
        url = f'{self.config.overmind_url.rstrip("/")}/api/v1/tasks/api'
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.overmind_token}",
        }
        request_data = {
            "query": query,
            "service": "openrouter",
            "model": self.config.model_search,
            "system_prompt": SEARCH_PROMPT,
            "parameters": {
                "temperature": self.config.search_temperature,
                "max_tokens": self.config.max_tokens_search,
            },
            "external_api_key": self.config.openrouter_api_key,
            "mcp_servers": ["novostroym"],
        }
        payload = {
            "agent_name": "gateway-agent",
            "endpoint": "/process",
            "request_data": request_data,
            "timeout_seconds": self.config.gateway_timeout,
            "max_retries": 0,
        }

        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload, headers=headers) as resp:
                    task = await resp.json()
                    task_id = task.get("id")
                    if not task_id:
                        logger.error("Нет task_id: %s", task)
                        return None

                base = self.config.overmind_url.rstrip("/")
                for _ in range(15):
                    async with sess.get(
                        f"{base}/api/v1/tasks/api/{task_id}/status",
                        headers=headers,
                    ) as resp:
                        status = (await resp.json()).get("status")

                    if status == "completed":
                        async with sess.get(
                            f"{base}/api/v1/tasks/api/{task_id}/result",
                            headers=headers,
                        ) as resp:
                            r = (await resp.json()).get("result", {}) or {}
                        return r.get("response", "")
                    elif status in ("failed", "cancelled"):
                        logger.warning("Поиск %s: %s", status, task_id)
                        return None
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error("Ошибка поиска: %s", e)
            return None

        return None

    async def answer(self, query: str, data: str) -> str | None:
        """Сформировать человечный ответ через прямую модель."""
        headers = {
            "Authorization": f"Bearer {self.config.openrouter_api_key}",
            "Content-Type": "application/json",
        }

        # Чистый контекст
        context_parts = [f"Исходные данные: {data[:500]}"]
        if self.refine_history:
            history = "; ".join(self.refine_history[-5:])
            context_parts.append(f"Уточнения: {history}")
        context_parts.append(f"Новый запрос: {query}")

        payload = {
            "model": self.config.model_answer,
            "messages": [
                {"role": "system", "content": ANSWER_PROMPT},
                {"role": "user", "content": "\n".join(context_parts)},
            ],
            "temperature": self.config.answer_temperature,
            "max_tokens": self.config.max_tokens_answer,
        }

        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.config.openrouter_timeout,
                ) as resp:
                    r = await resp.json()
                    return r["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error("Ошибка ответа: %s", e)
            return None

    async def process(self, query: str) -> str:
        """Обработать запрос пользователя."""
        needs_search = detect_needs_search(query, self.cache)
        logger.info(
            "Запрос: %s | поиск: %s | кэш: %s",
            query, needs_search, bool(self.cache),
        )

        if needs_search:
            data = await self.search(query)
            if not data:
                return "Не нашлось, извините."

            self.cache = {
                "data": data,
                "zhk_names": extract_zhk_names(data),
                "query": query,
            }
            self.refine_history = []

        answer_text = await self.answer(query, self.cache["data"])
        if not answer_text:
            return "Извините, не смогла сформировать ответ."

        self.refine_history.append(query)
        return answer_text

    def reset(self) -> None:
        """Сбросить сессию."""
        self.cache = None
        self.refine_history.clear()
