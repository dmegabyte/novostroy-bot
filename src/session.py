"""
Сессия диалога с кэшем данных MCP.

Логика:
- Первый запрос → поиск через MCP, кэшируем данные
- Уточнения → ответ из кэша, без нового поиска
- Изменение параметров (цена, район) → новый поиск
"""

from __future__ import annotations

import asyncio
import json
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
    "Найди варианты по запросу клиента через MCP-инструменты.\n"
    "ПРАВИЛА:\n"
    "1. Фильтруй варианты СТРОГО по типу квартиры (однушка/двушка/трёшка) "
    "и бюджету клиента.\n"
    "2. Для двушки ищи только площади 50-70 м². "
    "Для однушки — 30-45 м². Для студии — до 30 м². Для трёшки — 75-100 м².\n"
    "3. Верни только ЖК, где есть подходящие варианты. "
    "Если в ЖК только однушки, а клиент хочет двушку — НЕ включай его.\n"
    "4. Формат: «ЖК «Название»: от X млн руб., Y м², метро/район.» "
    "Один ЖК = одна строка.\n"
    "5. Максимум 5 ЖК. Если меньше — ок.\n"
    "6. Если ничего не подходит — верни «Нет подходящих вариантов»."
)

ANSWER_PROMPT = (
    "Ты — Ирина, консультант по продаже новостроек.\n"
    "Живой человек, профессионал продаж с 5+ годами опыта.\n"
    "Твоя задача — не просто перечислить варианты, а ПОМОЧЬ КЛИЕНТУ ВЫБРАТЬ "
    "и подвести к следующему шагу (консультация / показ / встреча).\n"
    "\n"
    "ФАКТЫ — ТОЛЬКО ИЗ ДАННЫХ:\n"
    "1. Используй ТОЛЬКО то, что написано в «Исходные данные». "
    "Не выдумывай ЖК, цены, площади, метро, скидки, застройщика, девелопера, условия ипотеки, которых там нет.\n"
    "2. Называй ЖК в кавычках «...». Цены и площади — копируй из данных.\n"
    "3. Если в данных чего-то нет (застройщик, ипотека, скидки, планировки, детали):\n"
    "   — Скажи: «В предоставленных данных этой информации нет».\n"
    "   — Сразу добавь: «Нажмите кнопку «Поделиться контактом», и наш оператор свяжется с Вами.»\n"
    "   Эту фразу нужно сказать ДОСЛОВНО, включая «Поделиться контактом» в кавычках.\n"
    "4. Если по запросу вообще ничего не нашлось — «Не нашлось, извините. "
    "Нажмите кнопку «Поделиться контактом», оператор поможет.»\n"
    "\n"
    "ПОВЕДЕНИЕ ПРОДАЖНИКА:\n"
    "1. СТРОГО на «Вы»: вы, вас, ваш, вам, вами, обратите, Ваш, Вам, Вы. "
    "ЗАПРЕЩЕНО: «для себя», «ищешь», «хочешь», «тебе». "
    "Это грубый переход на «ты», который убивает продажу. "
    "Не представляйся заново. Фокус на клиенте, не на себе.\n"
    "2. Используй живые фразы: «я нашла», «есть отличный вариант», "
    "«обратите внимание», «этот ЖК часто берут».\n"
    "3. Подсвечивай выгоды из данных: метро, готовность дома, рассрочка, ипотека.\n"
    "4. В конце КАЖДОГО ответа задавай ОДИН уточняющий вопрос:\n"
    "   — «а для себя ищете или инвестиция?»\n"
    "   — «когда планируете заехать?»\n"
    "   — «хотите посмотреть вживую? могу организовать показ на выходных»\n"
    "   — «важна ли близость к метро?»\n"
    "   — «рассматриваете ипотеку?»\n"
    "5. Если клиент уточняет — отвечай по делу из данных + подталкивай к действию.\n"
    "6. КОЛЕБАНИЯ («не знаю», «может быть», «потом», «подумаю»): "
    "НЕ соглашайся откладывать. Снизь порог действия:\n"
    "   — «давайте просто посмотрим планировки, ни к чему не обязывает»\n"
    "   — «могу записать Вас на бесплатный показ в выходные»\n"
    "   — «давайте я подберу ещё пару вариантов, чтобы было с чем сравнить»\n"
    "7. Не благодари «за вопрос», не извиняйся за мелочи.\n"
    "\n"
    "ФОРМАТ:\n"
    "• 3-5 предложений. Начинай сразу с пользы.\n"
    "• Без буллетов, без «к сожалению», без шаблонов."
)
# Копия этого промпта лежит в promptfoo/answer_prompt.txt.
# При правке — обнови ОБА файла и прогони promptfoo eval.


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
        """Поиск через n8n WF1 → Overmind gateway-agent с MCP novostroym.

        Поток:
          1. POST на n8n webhook → создаёт task в Overmind (gateway-agent route)
          2. Poll Overmind каждые 1.5 сек (max 30 сек) до completed/failed
          3. Вернуть response (ответ модели с результатами MCP)
        """
        n8n_url = f'{self.config.n8n_url.rstrip("/")}{self.config.n8n_endpoint}'
        n8n_headers = {"Content-Type": "application/json"}
        # n8n webhook открыт (как cc-daemons используют — без токена)

        import time
        import uuid
        req_id = f"bot-{int(time.time())}-{uuid.uuid4().hex[:8]}"

        n8n_payload = {
            "request_id": req_id,
            "query": query,
            "model": self.config.model_search,
            "system_prompt": SEARCH_PROMPT,
            "mcp_servers": ["novostroym"],
            "temperature": self.config.search_temperature,
            "max_tokens": self.config.max_tokens_search,
            "task_timeout_seconds": self.config.gateway_timeout,
        }

        try:
            async with aiohttp.ClientSession() as sess:
                # 1. POST в n8n → создать task в Overmind
                async with sess.post(
                    n8n_url, json=n8n_payload, headers=n8n_headers,
                    timeout=30,
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        logger.error("n8n %s: %s", resp.status, text[:300])
                        return None
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError:
                        logger.error("n8n: bad JSON: %s", text[:200])
                        return None

                task_id = body.get("task_id")
                if not task_id:
                    logger.error("n8n: no task_id: %s", text[:300])
                    return None

                # 2. Poll Overmind
                overmind_url = self.config.overmind_url
                overmind_headers = {
                    "Authorization": f"Bearer {self.config.gateway_poll_token}",
                }
                max_attempts = self.config.gateway_timeout // 2
                for attempt in range(max_attempts):
                    await asyncio.sleep(2)
                    async with sess.get(
                        f"{overmind_url.rstrip('/')}/api/v1/tasks/api/{task_id}/status",
                        headers=overmind_headers,
                        timeout=10,
                    ) as resp:
                        if resp.status >= 400:
                            logger.warning("poll %s: %s", task_id, resp.status)
                            continue
                        st = (await resp.json()).get("status")

                    if st == "completed":
                        async with sess.get(
                            f"{overmind_url.rstrip('/')}/api/v1/tasks/api/{task_id}/result",
                            headers=overmind_headers,
                            timeout=10,
                        ) as resp:
                            r = (await resp.json()).get("result", {}) or {}
                        return r.get("response") or ""
                    elif st in ("failed", "cancelled"):
                        err = r.get("error", "") if False else ""
                        logger.warning("task %s: %s", task_id, st)
                        return None
                logger.warning("task %s: timeout", task_id)
                return None
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
        context_parts = [f"Исходные данные:\n{data[:3000]}"]
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
