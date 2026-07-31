# Response model eval — сравнение моделей ответчика

Дата: 2026-06-30

## Задача

Проверить, какая chat-модель лучше отвечает за Ирину, если MCP/search уже вернул факты.

Eval не вызывает MCP заново: он берёт готовые `user_text` + `search_response` из `logs/dialogs-*.jsonl` и прогоняет только ответную фазу через текущий `prompts/chat_v1.txt`.

## Файл

Скрипт: `scripts/nmbot_response_model_eval.py`

Постоянная таблица результатов хранится здесь же, ниже в разделе «Текущий ориентир по моделям». Отдельные файлы с такой же таблицей не нужны, чтобы не плодить дубли.

## Модели

Максимум 5 моделей за один прогон.

Дефолтный набор взят из текущего dev-бота `scripts/chat_tester_bot.py`:

1. `google/gemini-2.5-flash`
2. `google/gemini-3.1-flash-lite-preview`
3. `deepseek/deepseek-v4-flash`
4. `anthropic/claude-3-haiku`
5. `openai/gpt-4o-mini`

## Как пользоваться

### 1. Выгрузить cases из логов

```bash
python3 scripts/nmbot_response_model_eval.py export --limit 50
```

Результат: `data/response_eval/cases.jsonl`.

В каждом кейсе есть:

- `user_text` — запрос клиента;
- `search_response` — готовый ответ MCP/search;
- `original_response_text` — старый ответ модели из лога;
- `original_chat_model` — модель, которая отвечала тогда.

### 2. Прогнать chat-фазу по моделям

```bash
python3 scripts/nmbot_response_model_eval.py run \
  --cases data/response_eval/cases.jsonl \
  --models google/gemini-2.5-flash,deepseek/deepseek-v4-flash,openai/gpt-4o-mini \
  --limit 10
```

Результат: `data/response_eval/results.jsonl`.

Важно: команда делает реальные запросы в Overmind/OpenRouter и тратит токены.

### 3. Посчитать сводку

```bash
python3 scripts/nmbot_response_model_eval.py score \
  --results data/response_eval/results.jsonl
```

## Метрики

Скрипт считает простые UX-проверки по контрактам `docs/PRODUCT_TZ.md` и `docs/IDEAL_IRINA_UX.md`:

- ответ без ошибки;
- валидный JSON;
- есть поле `response`;
- нет markdown fence;
- нет HTML;
- нет ссылок на `novostroy-m.ru`;
- нет «Уважаемый» / «Дорогой»;
- нет канцелярита: «с удовольствием», «по вашему запросу», «к сожалению»;
- нет восклицательных знаков;
- максимум 3 варианта;
- максимум один вопрос;
- нет раннего оператора, если search уже дал факты;
- названия ЖК в кавычках не выходят за MCP/search_response.

Сводка `score` также показывает скорость по каждой модели:

- `avg_sec` — среднее время ответа;
- `min_sec` — самый быстрый ответ;
- `max_sec` — самый медленный ответ.

## Текущий ориентир по моделям

Прогоны от 2026-06-30: одинаковая база из 10 кейсов. Базовые модели — `data/response_eval/results.jsonl`, китайские модели — `data/response_eval/results_china.jsonl`, малые Qwen — `data/response_eval/results_qwen_small.jsonl`.

| Rank | Модель | Avg quality | Avg speed | Min speed | Max speed | Главные проблемы | Вывод |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | `google/gemini-2.5-flash` | **0.962** | 5.1 сек | 3.7 сек | 7.2 сек | `grounded_names:5` | **Лучший баланс качества и скорости** |
| 2 | `deepseek/deepseek-v3.2` | 0.946 | 11.4 сек | 6.8 сек | 16.2 сек | `grounded_names:7` | Лучший китайский кандидат, но медленнее Gemini |
| 3 | `google/gemini-3.1-flash-lite-preview` | 0.938 | **5.0 сек** | 3.7 сек | 7.2 сек | `grounded_names:7`, `no_early_operator:1` | Самая быстрая, но чуть хуже по качеству |
| 4 | `qwen/qwen3-235b-a22b-2507` | 0.938 | 10.6 сек | 3.7 сек | 25.5 сек | `grounded_names:5`, `valid_json:2`, `one_question_max:1` | Хороший дешёвый запасной кандидат |
| 5 | `qwen/qwen3-32b` | 0.938 | 16.7 сек | 6.8 сек | 47.3 сек | `grounded_names:7`, `one_question_max:1` | Малый Qwen с хорошим качеством, но заметно медленнее Gemini |
| 6 | `qwen/qwen3-14b` | 0.938 | 19.0 сек | 10.0 сек | 22.5 сек | `grounded_names:7`, `valid_json:1` | Малый Qwen с хорошим качеством, но не быстрый |
| 7 | `z-ai/glm-4.7-flash` | 0.938 | 50.0 сек | 20.1 сек | 103.7 сек | `grounded_names:7`, `valid_json:1` | Качество ок, но слишком медленно |
| 8 | `anthropic/claude-3-haiku` | 0.900 | 7.0 сек | 3.7 сек | 10.9 сек | `grounded_names:7`, `valid_json:2`, `one_question_max:2`, `no_early_operator:1`, `no_cliche:1` | Нормально, но хуже Gemini |
| 9 | `minimax/minimax-m2.7` | 0.892 | 12.9 сек | 6.8 сек | 25.5 сек | `grounded_names:7`, `valid_json:4`, `no_early_operator:1`, `max_three_options:1`, `one_question_max:1` | Качество ниже, JSON ломается чаще |
| 10 | `qwen/qwen3-30b-a3b-instruct-2507` | 0.885 | 9.0 сек | 4.1 сек | 13.5 сек | `grounded_names:7`, `valid_json:5`, `one_question_max:3` | Быстрый и дешёвый, но качество ниже из-за JSON |
| 11 | `qwen/qwen3.5-flash-02-23` | 0.877 | 51.6 сек | 6.8 сек | 125.4 сек | `grounded_names:6`, `valid_json:4`, `one_question_max:2`, `no_links:1`, `no_banned_greeting:1` | Дёшево, но медленно и хуже по качеству |
| 12 | `openai/gpt-4o-mini` | 0.862 | 6.0 сек | 3.7 сек | 7.8 сек | `valid_json:10`, `grounded_names:7`, `no_early_operator:1` | Не подходит без доп. настройки JSON |
| 13 | `qwen/qwen-2.5-7b-instruct` | 0.815 | 7.0 сек | 3.7 сек | 11.3 сек | `valid_json:10`, `grounded_names:7`, `one_question_max:3`, `no_cliche:2`, `no_early_operator:1` | Очень дешёвый, но для ответчика слабоват |
| 14 | `deepseek/deepseek-v4-flash` | 0.792 | 45.4 сек | 9.9 сек | 110.6 сек | `grounded_names:10`, `valid_json:6`, `one_question_max:5`, `no_html:2`, `no_early_operator:2` | Пока не использовать для ответчика |

`qwen/qwen3.5-9b` полный прогон не завершил: одиночная проба упёрлась в timeout 60 секунд на первом кейсе (`data/response_eval/results_qwen_small_9b_probe.jsonl`), поэтому в общую таблицу качества не включён.

Практический вывод: основная модель ответчика — `google/gemini-2.5-flash`. Быстрый запасной кандидат — `google/gemini-3.1-flash-lite-preview`. Если нужен дешёвый китайский запасной вариант — сначала пробовать `deepseek/deepseek-v3.2` или `qwen/qwen3-235b-a22b-2507`. Из малых Qwen лучшие по качеству — `qwen/qwen3-32b` и `qwen/qwen3-14b`, но они медленнее Gemini.

## Как использовать результат eval

- если одна модель стабильно выигрывает по quality, а остальные заметно хуже — это сигнал менять именно chat-модель или chat-prompt;
- если все модели спотыкаются об одни и те же ошибки, проблема, скорее всего, не в ответчике, а в `search_response` или в том, как кейсы собраны из `logs/dialogs-*.jsonl`;
- если нужна быстрая ручная проверка конкретного диалога, сначала открой `logs/dialogs-YYYY-MM-DD.md`, а потом уже сверяй кейс в `logs/dialogs-*.jsonl`.

Для единичного расследования удобная последовательность такая:

1. найти `dialog_id` / `turn_id` в человекочитаемом логе;
2. открыть raw JSONL-строку с тем же ходом;
3. при необходимости выгрузить кейс через `export` и прогнать `run/score`.

## Температурная проверка для `google/gemini-2.5-flash`

Проверка на той же базе из 10 кейсов, модель одна и та же, менялась только `temperature` в запросе к OpenRouter.

| Temperature | Avg quality | Avg speed | Главные проблемы |
|---:|---:|---:|---|
| 0.3 | **0.962** | 5.1 сек | `grounded_names:5` |
| 0.4 | 0.954 | 4.4 сек | `grounded_names:6` |
| 0.5 | 0.954 | 5.3 сек | `grounded_names:6` |
| 0.6 | 0.938 | **4.3 сек** | `grounded_names:7`, `no_cliche:1` |
| 0.7 | 0.931 | 5.3 сек | `grounded_names:7`, `no_early_operator:1`, `no_cliche:1` |
| 0.8 | 0.946 | 4.7 сек | `grounded_names:7` |

Практический вывод по температуре: `0.3` даёт лучшую точность, но `0.4` почти не хуже и чуть живее; `0.5` уже не даёт выигрыша по качеству. Если нужен запас по качеству — `0.3`; если хочется чуть меньше «зажатости» без большой потери качества — `0.4`.

## Опора

- `docs/PRODUCT_TZ.md`: сначала польза, потом уточнение, затем оператор; MCP — единственный источник фактов.
- `docs/IDEAL_IRINA_UX.md`: живой консультант, максимум 3 варианта, один следующий вопрос, без галлюцинаций.
- `prompts/chat_v1.txt`: текущий системный prompt ответчика.
- `logs/dialogs-*.jsonl`: источник реальных диалогов и готовых MCP/search ответов.
