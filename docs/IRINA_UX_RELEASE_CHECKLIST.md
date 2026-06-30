# Irina UX release checklist

Дата: 2026-06-30

Этот чеклист нужен, чтобы UX-правки Ирины не отдавались пользователю после проверки “кусочками”.

## Правило

Нельзя говорить “готово” после изменения клиентского ответа, промпта, выбора вариантов, форматирования, operator handoff или no-buttons UX, пока не пройден полный gate:

```bash
python3 -m py_compile scripts/chat_cli.py scripts/chat_tester_bot.py scripts/nmbot_test_agent.py text_style_tool.py scene_classifier.py followup_intent_classifier.py style_scenes.py tests/scene_router_test.py
python3 tests/scene_router_test.py
python3 scripts/nmbot_test_agent.py --suite h028
python3 scripts/nmbot_test_agent.py --suite h029
python3 scripts/nmbot_test_agent.py --suite ux_e2e
python3 scripts/nmbot_test_agent.py --suite deploy
```

Если правка затрагивает LLM-ответы или MCP/search/chat поведение, дополнительно:

```bash
python3 scripts/nmbot_test_agent.py --suite dialog
```

## Что обязан ловить `ux_e2e`

Сценарий должен проверять весь живой путь, а не отдельную функцию:

1. Ирина показывает список из 1–3 вариантов без inline-кнопок.
2. Перед первым пунктом списка есть пустая строка.
3. Перед финальным вопросом есть пустая строка.
4. Выбор `1/2/3` идёт по видимому списку, который увидел клиент.
5. Выбор текстом вроде `2. ЖК «...»` матчится по названию ЖК.
6. Карточка выбранного ЖК не показывает сырые поля: `msk`, голые числа цены, JSON, MCP, внутренние ключи.
7. `подробнее` после выбранного ЖК не повторяет ту же карточку, а ведёт к оператору, если новых подтверждённых фактов нет.
8. Короткие ответы `да/нет/возможно` после вопроса Ирины не повторяют карточку, а уходят в follow-up classifier.
9. Сроки в прошлом не продаются как будущая сдача: `2025` при текущем 2026 означает, что по срокам объект уже должен быть сдан.
10. После предложения оператора фразы вроде `зачем` и `продолжить подбор` не зацикливаются на одном clarify-тексте: бот объясняет причину оператора или возвращает к подбору.

## Формула готовности

“Готово” по UX Ирины означает:

commit + push + deploy + service active + fresh live commit + deploy smoke + UX e2e pass.

Если хотя бы один пункт не пройден — нельзя отдавать результат как готовый.

## Source refs

- `docs/PRODUCT_TZ.md` — сначала польза, потом уточнение, затем оператор; MCP — единственный источник фактов.
- `docs/IRINA_FIRST_REPLY_GUIDE.md` — no-buttons UX, абзацы, один следующий вопрос.
- `scripts/nmbot_test_agent.py --suite ux_e2e` — регрессия полного пути после no-buttons.
- `followup_intent_classifier.py` — безопасный классификатор коротких ответов клиента по контексту диалога.
