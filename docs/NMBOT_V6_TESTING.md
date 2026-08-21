# Уровни тестирования V6

Это единая карта проверок V6. Каждый уровень отвечает только за свой слой.
Зелёный Prompt1 или Prompt2 не означает, что весь бот или Jivo-контур работает.

| Уровень | Владелец | Вход | Модель? | MCP? | VPS/Jivo? | Что доказывает | Чего не доказывает | Команда |
|---|---|---|---|---|---|---|---|---|
| `prompt1` | Prompt1 | synthetic JSON без tool-вызова | Да | Нет | Нет | решение `continue/clarify/request_phone` и JSON-контракт реальной модели | MCP, runtime, релиз и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer prompt1 --execute --confirm-model` |
| `prompt2` | Prompt2 | готовый synthetic material | Да | Нет | Нет | `response/final_question`, grounding и приоритеты реальной модели | router/state, MCP, релиз и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer prompt2 --execute --confirm-model` |
| `runtime` | V6 runtime | focused pytest с fake/stub моделями | Нет | Нет | Нет | parser, state, URL, телефон и маршрутизацию | реальный ответ модели, MCP и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer runtime --execute` |
| `contour` | release/bridge | TEST-only Jivo smoke | Да | Косвенно | Да | работу опубликованного TEST-релиза через bridge и терминальное событие | полноту всех бизнес-сценариев | `python3 scripts/nmbot_v6_test_layers.py --layer contour --execute --confirm-live` |

`--list` показывает эту карту в JSON. Без `--execute` runner ничего не запускает:
он только печатает точную команду и границу доказательства.

## Как выбрать уровень

- Меняется формулировка или payload Prompt1 — запускай `prompt1`.
- Меняется ответ или `final_question` Prompt2 — запускай `prompt2`.
- Меняется state, parser, URL, телефон или router — запускай `runtime`.
- Нужно доказать TEST-релиз, bridge или доставку Jivo — запускай `contour`.

Для изменения prompt нужны два результата: соответствующий prompt-уровень и
`runtime`. После деплоя отдельно нужен `contour`. Synthetic prompt-тесты — это
диагностика модели, а не доказательство текущего TEST или production.

Prompt-уровни тратят токены и требуют одновременно `--execute` и
`--confirm-model`. Если ключа нет в окружении, CLI читает только
`OPENROUTER_API_KEY` из `--env-file` (по умолчанию `.env`) и никогда его не
печатает. `contour` требует отдельный `--confirm-live`, вызывает существующий
`scripts/nmbot_v6_jivo_smoke.py` и должен запускаться в TEST-окружении, где есть
`/home/neiro/novostroy-bot/.env`.

## Проверенный пример 2026-08-17

Фактический прогон новой системы:

- Prompt1: `3/3 passed`, eval `eval-v0S-2026-08-17T10:08:13`;
- Prompt2: `5/5 passed`, eval `eval-NKE-2026-08-17T10:12:07`;
- runtime: `135 passed`;
- тесты самого runner: `9 passed`.

В Prompt2 справочный вопрос без намерения подбора вернул пустой `final_question`.
При явном подборе, где единственной недостающей деталью была площадь, модель
спросила: `Какая площадь студии вас интересует?`. Когда одновременно неизвестны
бюджет и площадь, модель воздержалась от угадывания. Это один диагностический
прогон с assertions, а не доказательство стабильности и не live TEST.

## Журнал метрик Prompt2: contextual follow-up

Этот журнал нельзя трактовать как текущий production-статус. Он фиксирует
сопоставимые синтетические прогоны Prompt2, чтобы не потерять baseline и причины
отклонения кандидатов. Все прогоны выполнены через layered runner при temperature
`0.2`, без кэша и последовательно (`max-concurrency=1`). В каждом прогоне был
один и тот же набор из пяти кейсов и одинаковые assertions. Production Prompt2
в этих экспериментах не изменялся и ни один кандидат не деплоился.

| Дата/время UTC | Вариант | Eval ID | Результат | Что проверялось | Решение |
|---|---|---|---:|---|---|
| 2026-08-17 10:12 | production Prompt2 + contextual addendum, исправленный набор | `eval-NKE-2026-08-17T10:12:07` | **5/5** | цена без лишнего вопроса; единственная недостающая площадь; две недостающие детали; ambiguity; specialist CTA | Исторический diagnostic baseline; не доказательство стабильности |
| 2026-08-17 10:23 | production Prompt2 + тот же addendum, повтор baseline | `eval-Tz5-2026-08-17T10:23:30` | **3/5** | повторная проверка тех же пяти контрактов | **Не принимать**: модель выбрала бюджет при двух недостающих деталях и повторила альтернативы в ambiguity-вопросе |
| 2026-08-17 10:27 | integrated candidate v1: правила встроены в пункты 2 и 6 | `eval-eU1-2026-08-17T10:27:31` | **4/5** | ambiguity исправлен; multi-missing abstention не выдержан | **Не принимать**: модель снова выбрала бюджет при двух деталях |
| 2026-08-17 10:29 | integrated candidate v2: обязательный `NEXT_SLOT_CANDIDATES` gate | `eval-4Ip-2026-08-17T10:29:19` | **4/5** | ambiguity без альтернатив; multi-missing gate | **Не принимать**: модель снова выбрала бюджет при двух деталях |
| 2026-08-17 10:34 | integrated candidate v2 на Gemini 3.6 Flash | `eval-obo-2026-08-17T10:34:08` | **0/5** | тот же Prompt2-набор; OpenRouter принял `google/gemini-3.6-flash` | **Не принимать**: модель вернула рассуждения и текст вне требуемого JSON |
| 2026-08-17 10:35 | integrated candidate v2 на Gemini 2.5 Flash | `eval-m45-2026-08-17T10:35:36` | **3/5** | тот же Prompt2-набор; OpenRouter принял `google/gemini-2.5-flash` | **Не принимать**: multi-missing и ambiguity-кейсы не прошли |
| 2026-08-17 10:43 | integrated candidate v2 на GPT-5.6 Luna | `eval-tqL-2026-08-17T10:43:19` | **3/5** | тот же Prompt2-набор; OpenRouter ID `openai/gpt-5.6-luna` | **Не принимать**: standalone price и specialist CTA вернули рассуждения вместо чистого JSON |
| 2026-08-17 11:00 | Luna + усиленный prompt-only запрет видимого reasoning, прогон 1 | `eval-XXW-2026-08-17T11:00:24` | **3/5** | требование первого символа `{`, последнего `}`, запрет `Thinking:`/`Analysis:`/Markdown | **Не принимать**: unique-area снова вернул `Thinking:`; ещё один JSON-кейс не прошёл строгую проверку grounded-значения из-за другого форматирования числа |
| 2026-08-17 11:01 | Luna + тот же запрет, независимый прогон 2 | `eval-enB-2026-08-17T11:01:43` | **3/5** | неизменные model, temperature, пять кейсов и assertions | **Не принимать**: `Thinking:` появился уже в standalone-price и unique-area; prompt-only запрет не обеспечивает JSON-only output |
| 2026-08-17 11:05 | Luna + JSON-only wording, `temperature=0`, прогон 1 | `eval-4SI-2026-08-17T11:05:19` | **4/5** | тот же набор; изменена только температура с `0.2` на `0` | **Не принимать**: multi-missing вернул `Thinking:`; JSON-only гарантия не достигнута |
| 2026-08-17 11:05 | Luna + JSON-only wording, `temperature=0`, прогон 2 | `eval-2SX-2026-08-17T11:05:45` | **3/5** | независимый no-cache повтор при неизменном конфиге | **Не принимать**: `Thinking:` появился в standalone-price и multi-missing; результат нестабилен |
| 2026-08-17 11:24 | Gemini 2.5 Flash, `temperature=0`, прогон 1 | `eval-8rC-2026-08-17T11:24:02` | **3/5** | тот же набор; OpenRouter ID `google/gemini-2.5-flash` | **Не принимать**: JSON чистый, но multi-missing и ambiguity не прошли |
| 2026-08-17 11:24 | Gemini 2.5 Flash, `temperature=0`, прогон 2 | `eval-GLc-2026-08-17T11:24:21` | **4/5** | независимый no-cache повтор при неизменном конфиге | **Не принимать**: JSON чистый; multi-missing снова выбрал бюджет вместо `ANSWER_ONLY` |

### Сводка по моделям и стоимости

| Модель OpenRouter | Результат на одинаковых 5 кейсах | Цена входа / выхода за 1M токенов | Итог |
|---|---:|---:|---|
| `google/gemini-3.1-flash-lite-preview` | **5/5**, затем повтор **3/5** | `$0.25 / $1.50` | Нестабильный результат; в production не переносить новый candidate |
| `google/gemini-2.5-flash` | **3/5** | `$0.30 / $2.50` | Не прошли multi-missing и ambiguity |
| `google/gemini-3.6-flash` | **0/5** | `$0.75 / $3.75` | Рассуждения и текст вне JSON |
| `openai/gpt-5.6-luna` | **3/5** в трёх зафиксированных вариантах/повторах | `$0.10 / $0.60` | Дешевле Gemini 3.6; усиленный prompt-only запрет не устранил видимый reasoning |
| `google/gemini-2.5-flash` | **3/5**, затем **4/5** при `temperature=0` | `$0.30 / $2.50` | JSON стабильно чистый; multi-missing стабильно нарушает правило abstention |

По опубликованным карточкам OpenRouter GPT-5.6 Luna примерно в 6–7,5 раза
дешевле Gemini 3.6 Flash. Однако по этому малому набору нельзя объявлять Luna
лучшей моделью: обе получили только `3/5`, а production Prompt2 рассчитан на
строгий JSON без рассуждений вне объекта.

### Контрольные метрики

- Prompt1: **3/3**, `eval-v0S-2026-08-17T10:08:13`.
- Runtime: **135 passed**.
- Runner tests: **9 passed**.
- Prompt2 candidate v1/v2: требование `5/5` в двух независимых прогонах **не выполнено**.
- Gemini 3.6 Flash: **0/5**, eval `eval-obo-2026-08-17T10:34:08`; дополнительно зафиксировано нарушение JSON/output-контракта.
- Gemini 2.5 Flash: **3/5**, eval `eval-m45-2026-08-17T10:35:36`; прошли price, unique-area и specialist CTA.
- GPT-5.6 Luna: **3/5**, eval `eval-tqL-2026-08-17T10:43:19`; OpenRouter-маршрут работает, но output-контракт нарушен в двух кейсах.
- GPT-5.6 Luna с усиленным JSON-only wording: два независимых прогона по **3/5**, eval `eval-XXW-2026-08-17T11:00:24` и `eval-enB-2026-08-17T11:01:43`; `Thinking:` сохранился.
- GPT-5.6 Luna с тем же wording и `temperature=0`: **4/5**, затем **3/5**, eval `eval-4SI-2026-08-17T11:05:19` и `eval-2SX-2026-08-17T11:05:45`; нулевая температура снизила вариативность только в одном прогоне, но не устранила `Thinking:`.
- Gemini 2.5 Flash при `temperature=0`: **3/5**, затем **4/5**, eval `eval-8rC-2026-08-17T11:24:02` и `eval-GLc-2026-08-17T11:24:21`; оба раза без `Thinking:`, но multi-missing не выдержан.
- Следствие: contextual follow-up правило пока не переносится в production Prompt2.

### Зафиксированные дефекты кандидатов

1. При `missing=["max_price", "preferred_area_m2"]` модель иногда самовольно
   выбирает бюджет, хотя безопасный результат — `ANSWER_ONLY` с пустым
   `final_question`.
2. В ambiguity-сценарии модель может повторить варианты клиента в вопросе
   (`10 или 15`), несмотря на запрет альтернатив. Assertion это обнаруживает.

Эти результаты являются **negative evidence** для prompt-only решения, а не
основанием менять runtime, payload или добавлять сценарные правила. До нового
кандидата необходимо сохранить тот же набор из пяти кейсов и те же assertions.
