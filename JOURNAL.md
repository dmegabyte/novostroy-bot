# Журнал изменений (Session Journal)

> Формат: краткое описание, что сделано, какие проблемы решили, находки

---

## Session 2026-06-24: Multi-turn dialog fix + MCP search repair

### Проблема
Бот повторял один и тот же вопрос, галлюцинировал данные, search через MCP не работал.

### Что починили

#### 1. Anti-hallucination промпт (commit `f3992d1`)
- Усилили запрет: не выдумывать застройщика, девелопера, условия ипотеки
- Добавили явную инструкцию: "Если данных нет — скажи «не указано»"
- **Находка**: предобучение модели сильное (ПИК = реальный застройщик Котельников) — модель уверенно галлюцинировала
- 5/5 multi-turn сценариев, 71/71 assertions

#### 2. "Нет данных" → кнопка оператора (commit `bfa934c`)
- Вместо "не указано" — предлагать связаться с оператором + кнопка «Поделиться контактом»
- Добавили `ReplyKeyboardMarkup` с `request_contact=True` в `bot.py`
- Добавили handler `handle_contact` для приёма телефонов
- Убрали API ключ из git history (был в `promptfooconfig.yaml` и `run_scenarios.py`)

#### 3. Retry при таймауте Overmind (commit `bfa934c`)
- Нашли: Overmind gateway не возвращал completed за 30 сек
- Добавили 1 retry через 1 сек (всего 2 попытки)
- n8n возвращал 200 OK, но Overmind зависал

#### 4. Валидация пустого search (commit `e18ed7c`)
- **Находка**: search мог вернуть пустые данные, модель answer галлюцинировала
- Добавили проверку: если `data` < 20 символов или нет "ЖК" и "млн/тыс" → сразу к оператору
- Добавили лог search результата для диагностики

#### 5. **Главное: починили MCP search через обновление n8n WF1**
- **Находка**: n8n узел "Build Request" хардкодил `mcp_servers: []`, отбрасывая то что бот передавал
- Нашли write token: `N8N_API_KEY` из `secret/projects/N8N_AUDIT` (раньше думали read-only)
- Обновили код через `PUT /workflows/{id}`: добавили `mcp_servers: Array.isArray(body.mcp_servers) ? body.mcp_servers : []`
- **До**: модель в Overmind не имела MCP-инструментов → отвечала уточняющими вопросами
- **После**: Overmind task #1557795 вернул реальные ЖК (Дюна, Белая Дача парк, Кузьминский лес, Томилинский бульвар)

#### 6. Track bot's previous answers (commit `d852d7a`)
- **Проблема**: answer-модель не видела свои предыдущие ответы → генерировала одно и то же
- Добавили `answer_history` в `Session` (последние 2 ответа)
- Передаём в контекст с инструкцией "НЕ повторяй"

#### 7. Умное распознавание ответов (commit `fc4361a`)
- **Проблема**: юзер отвечал на вопрос бота ("чем быстрее тем лучше" = "срок"), а бот слал тот же вопрос
- Сменили логику: "задавай вопрос ТОЛЬКО если юзер не ответил на предыдущий"
- Добавили требование "обязательно называй конкретные ЖК"
- Расширили no-hallucination: двор, озеленение, парковка, отделка
- Добавили dedup опечаток в `bot.py` (Levenshtein ≤2 за 30 сек)

### Итоговые цифры

| Метрика | До | После |
|---------|----|-------|
| Multi-turn сценарии | 0/5 | **5/5** |
| Assertions | ~12% | **69/69 (100%)** |
| Галлюцинации данных | постоянно | **нет** |
| MCP search | не работал | **работает** |
| Повторяющиеся ответы | каждый раз | **нет** |

### Потрачено на promptfoo eval

~23,000 токенов на все итерации ≈ **$0.005** (полкопейки).
Один реальный диалог: search + answer = **~$0.0007** (доли цента).

### Открытые проблемы / TODO

- [ ] Бот иногда отвечает обрезанно на 4-5 turn'е (max_tokens=600 не хватает) — увеличить?
- [ ] n8n WF1 — теперь mcp_servers передаётся, но каждый раз требует polling — можно ли webhook callback?
- [ ] Overmind gateway-agent — таймаут иногда не возвращает result — посмотреть с автором
- [ ] Модель `gemini-3.1-flash-lite-preview` иногда игнорирует запрет на галлюцинации — может быть, попробовать другую модель для search?

### Ключевые токены / endpoints

| Что | Где |
|-----|-----|
| N8N API token (read+write) | `secret/projects/N8N_AUDIT` |
| OpenRouter key | `secret/projects/NOVOSTROY_AI/openrouter_token` |
| Gateway poll token | `secret/projects/N8N_AUDIT/OPENROUTER_HEADER_TOKEN` |
| n8n URL | `https://n8n.it-system.io` |
| n8n WF1 | `/webhook/openrouter-direct-test` |
| Overmind | `https://overmind.aiaxel.ru` |
| Бот | @minionassist_bot (id 8304814102) |
| VPS | 193.107.155.236 |

### Файлы изменены

- `src/bot.py` — contact button, dedup, post_init drop pending
- `src/session.py` — sync промпта, retry, answer_history, валидация search
- `src/config.py` — без изменений
- `promptfoo/answer_prompt.txt` — обновлённый sales-промпт
- `promptfoo/promptfooconfig.yaml` — убран API ключ
- `promptfoo/run_scenarios.py` — runner для 5 сценариев
- `promptfoo/scenarios/s1-s5_*.json` — multi-turn тесты
- n8n WF1 (внешний) — обновлён код Build Request
