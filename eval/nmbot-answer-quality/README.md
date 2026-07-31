# nmbot answer quality eval

Локальный regression-контур для качества live-ответов nmbot.

Источник истины по критериям:

- `docs/LLM_SCENARIO_EVAL_RUBRIC.md`
- `docs/CARD_PRESENTATION_RULE.md`
- `scripts/live_run_table_validator.py`

## Слои проверки

1. `scripts/live_run_table_validator.py` — быстрый preflight перед таблицей: опечатки, forbidden words, facts/visible mismatch, слабые `why_*`, version, promptmaster-style verdict.
2. `scripts/nmbot_response_model_eval.py` — replay answer-layer на уже сохранённых MCP/search входах: без Telegram и без нового MCP.
3. `eval/nmbot-answer-quality` — promptfoo regression по уже сохранённым ответам.
4. Позже можно добавить `llm-rubric`, но только осознанно: это будет стоить токены. Preview-контур ниже бесплатный.

## Как запустить

Сначала подготовить строки из live-run лога:

```bash
python3 scripts/live_run_table_validator.py \
  logs/live_model_run_2026-07-04_rerun_115218.txt \
  --version v2 \
  --jsonl-out logs/live_model_run_2026-07-04_rerun_115218.rows.v2.jsonl
```

Если проверяем сокращение prompt'а, сначала приложить speed-снимок health:

```bash
python3 scripts/nmbot_health.py --json > /tmp/nmbot_health.json
python3 scripts/live_run_table_validator.py \
  logs/live_model_run_2026-07-04_rerun_115218.txt \
  --version prompt-shortening-baseline \
  --health-json /tmp/nmbot_health.json \
  --jsonl-out logs/live_model_run_2026-07-04_rerun_115218.rows.prompt-shortening-baseline.jsonl
```

В JSONL попадут `prompt_metrics` и `answer_latency_metrics`, чтобы сравнивать prompt size / answer latency вместе с promptmaster verdict.

Для сравнения baseline prompt против compact-кандидата без полного pipeline:

```bash
python3 scripts/nmbot_response_model_eval.py run \
  --cases data/response_eval/cases.jsonl \
  --baseline-prompt prompts/chat_v1.txt \
  --prompt prompts/chat_v1_compact.txt \
  --models google/gemini-2.5-flash \
  --limit 8 \
  --dry-run \
  --results data/response_eval/prompt_replay.compact-v1.dry-run.jsonl

python3 scripts/nmbot_response_model_eval.py run \
  --cases data/response_eval/cases.jsonl \
  --baseline-prompt prompts/chat_v1.txt \
  --prompt prompts/chat_v1_compact.txt \
  --models google/gemini-2.5-flash \
  --limit 8 \
  --results data/response_eval/prompt_replay.compact-v1.jsonl

python3 scripts/nmbot_response_model_eval.py score \
  --results data/response_eval/prompt_replay.compact-v1.jsonl
```

`--dry-run` только собирает prompt-variants и пишет `prompt_variant`/`system_prompt_chars`, не вызывает модель и не тратит токены. Обычный `run` уже делает реальный replay answer-layer на тех же сохранённых MCP/search фактах.

Потом собрать promptfoo cases:

```bash
python3 eval/nmbot-answer-quality/scripts/build_cases.py \
  logs/live_model_run_2026-07-04_rerun_115218.rows.v2.jsonl
```

Скрипт обновит текущий `tests/live-run-cases.yaml` и дополнительно сохранит версионный архив в `tests/versions/live-run-cases.v2.yaml`.

Бесплатный preview/regression без LLM-запросов:

```bash
npx promptfoo@latest eval --config eval/nmbot-answer-quality/promptfooconfig.preview.yaml
```

## Что считается проблемой

- опечатки и forbidden words;
- `facts >= 3`, но ответ показал меньше 3 вариантов;
- `why_*` звучит как реклама без evidence;
- promptmaster-style verdict = `bad`;
- scenario-specific ответ не содержит нужный смысл: family без школ/садов/парков/двора, investment без причины инвест-смысла, rental без арендо-пригодности;
- технические слова в клиентском ответе.
