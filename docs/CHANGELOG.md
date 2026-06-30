# Changelog nmbot

Все значимые изменения в проекте. Формат: версия → что сделано → impact. Связь с гипотезами — `H###`, с гипотезами про промпты — `P###`.

---

## 2026-06-30 — Text choice follows visible list order ✅

### Текстовый выбор варианта больше не съезжает
- **Проблема:** после отключения inline-кнопок клиент выбирает вариант текстом (`1`, `2. ЖК «..."`). Resolver мог сверяться с raw-порядком MCP вместо порядка, который реально был показан в ответе. Из-за этого выбранный ЖК мог съехать.
- **Фикс:** `scripts/chat_tester_bot.py` теперь сохраняет `visible_options` из последнего видимого нумерованного списка и сначала мапит выбор клиента по этому порядку/названию. Если пользователь прислал строку с названием ЖК, приоритет у названия, а не у сырого индекса.
- **Автотест:** H028 получил `text_choice_uses_visible_list_order_and_name`: raw-порядок специально отличается от видимого, проверяется `1 -> Южные Сады` и `2. ЖК «Сиреневый парк» -> Сиреневый парк`.
- **Verification:** `py_compile` ✅, `nmbot_test_agent.py --suite h028` ✅ 5/5, `--suite h029` ✅ 11/11.

---

## 2026-06-30 — Irina no-buttons + readable paragraphs fix ✅

### Убраны видимые inline-кнопки и повтор «подробнее»
- **Проблема:** в live UX после списка вариантов всё ещё могли появляться inline-кнопки; текст списка визуально прилипал к вступлению; финальный вопрос не всегда был отдельным абзацем; после ответа «подробнее» по уже выбранному ЖК Ирина могла повторить ту же карточку.
- **Фикс:** `prompts/chat_v1.txt` теперь просит `buttons: []` и живой текстовый next step. `_format_numbered_list_spacing()` добавляет пустую строку перед первым пунктом списка, между пунктами и перед финальным вопросом. `_format_option_response()` и `_format_options_summary_response()` формируют более читабельные абзацы.
- **Фикс:** `_resolve_dialog_intent()` теперь ведёт «подробнее» / «расскажи подробнее» после выбранного ЖК в `operator_for_selected`, потому что новых подтверждённых фактов в памяти нет и повторять ту же карточку нельзя.
- **Safety:** пользовательский текст больше не говорит «база» / «в базе» для карточки или неподдержанного региона.
- **Verification:** `py_compile` ✅, `tests/scene_router_test.py` ✅ 8/8, `nmbot_test_agent.py --suite h028` ✅ 4/4, `--suite h029` ✅ 11/11.

---

## 2026-06-30 — Irina scenario style-router ⚙️

### Сценарные правила для text style layer
- **Что:** добавлен лёгкий `scene_classifier.py`, который после MCP/search и черновика ответа выбирает один фиксированный сценарий для стилизации: неподдержанный регион, широкий запрос, семья/жизнь, инвестиция, быстрый въезд, бюджетное давление, нет точного совпадения, вопрос про конкретный ЖК, сравнение, готовность к оператору или fallback.
- **Что:** добавлен `style_scenes.py` с короткими правилами сценариев; `text_style_tool.py` теперь принимает `scene_rules` и передаёт их стилисту вместе с общими правилами.
- **Safety:** если классификатор упал, вернул невалидный JSON, неизвестный сценарий или уверенность ниже `NMBOT_SCENE_CONFIDENCE` (`0.7` по умолчанию), используется `default_safe_reply`.
- **Impact:** основной ответчик по-прежнему отвечает за факты и смысл, а новый слой влияет только на подачу. Это снижает риск большого тяжёлого промпта и позволяет точечно чинить стиль под ситуации.

---

## 2026-06-30 — Response-model eval journal ✅

### nmbot/Irina: baseline + Chinese + small Qwen eval tables ✅
- **Что:** прогнал `scripts/nmbot_response_model_eval.py` на одной и той же базе из 10 кейсов и обновил `docs/RESPONSE_MODEL_EVAL.md` как единый журнал качества моделей без дублей.
- **Baseline (5 моделей):** `google/gemini-2.5-flash` — `0.962` / `5.1 сек`; `google/gemini-3.1-flash-lite-preview` — `0.938` / `5.0 сек`; `anthropic/claude-3-haiku` — `0.900`; `openai/gpt-4o-mini` — `0.862`; `deepseek/deepseek-v4-flash` — `0.792` / `45.4 сек`.
- **Chinese run (5 моделей):** лучший китайский кандидат — `deepseek/deepseek-v3.2` (`0.946`, `11.4 сек`), затем `qwen/qwen3-235b-a22b-2507` (`0.938`, `10.6 сек`). `z-ai/glm-4.7-flash` дал `0.938`, но был очень медленным (`50.0 сек`).
- **Small Qwen run:** `qwen/qwen3-32b` и `qwen/qwen3-14b` — оба `0.938`, но медленнее Gemini; `qwen/qwen3-30b-a3b-instruct-2507` — `0.885`; `qwen/qwen-2.5-7b-instruct` — `0.815`; `qwen/qwen3.5-9b` не завершил полный прогон (timeout на probe).
- **Impact:** теперь в журнале есть актуальная точка опоры по моделям: основной ответчик остаётся `google/gemini-2.5-flash`, запасные кандидаты — `google/gemini-3.1-flash-lite-preview`, `deepseek/deepseek-v3.2`, `qwen/qwen3-235b-a22b-2507`, а из малых Qwen — `qwen/qwen3-32b` / `qwen/qwen3-14b`.

---

## 2026-06-26 — H025: Обязательный контрольный диалог перед отдачей ✅

### H025 — `--suite dialog`: `/start` + «двувшка в котельниках» как live gate ✅
- **Проблема:** user feedback 14:07 — «сделай хотя бы один тестовый диалог, который ты можешь прогонять сам, только потом мне отдавай». До H025 были unit/deploy checks, но не было одного обязательного сквозного диалога на реальном сценарии пользователя.
- **Фикс:** `scripts/nmbot_test_agent.py` получил suite `dialog`. Он сам загружает `.env`, запускает `OvermindClient.ask()` с пустыми params как после `/start` и запросом `двувшка в котельниках`, затем проверяет: финальный ответ не пустой, это не индикатор «🔎 Осуществляю поиск...», нет `choices`/OpenRouter/traceback, не протёк старый бюджет `5 млн`, есть Котельники, двухкомнатный контекст и полезная квартирная фактура.
- **Verification:** первый прогон gate упал на реальных проблемах теста (нет `.env`, сломанный deploy Result) — исправлено. Финальный прогон: `--suite dialog` ✅ 1/1 (≈11с), `--suite deploy` ✅ 1/1, H024 ✅ 1/1, H023 ✅ 2/2, H021 ✅ 3/3. Контрольный ответ начался с: «В Котельниках есть несколько вариантов двухкомнатных квартир: 1. ЖК «Дюна» — от 10.9 до 25.3 млн руб...».
- **Урок:** перед тем как отдавать бота пользователю после live-инцидента, обязательны три слоя: unit/regression → deploy-smoke → один реальный контрольный диалог.

---

## 2026-06-26 — H024: Deploy-smoke + безопасная upstream-ошибка ✅

### H024 — Тест ловит stale live bot, Telegram больше не видит `choices`/OpenRouter ✅
- **Проблема:** после H023 тесты проверяли код на диске, но live Telegram-бот продолжал работать старым процессом. В логе `dialogs-2026-06-26.jsonl:42-44` после `/start` всё ещё протекал старый `max_price=5_000_000`, а upstream-сбой ушёл пользователю как техническое `❌ Ошибка: Ошибка при обращении к openrouter: 'choices'`.
- **Фикс:** `scripts/nmbot_deploy_smoke.py` проверяет live-процесс `scripts/chat_tester_bot.py`: процесс обязан существовать в единственном экземпляре и быть новее `scripts/chat_tester_bot.py`, `prompts/chat_v1.txt`, `prompts/search_v1.txt`. В `scripts/chat_tester_bot.py` upstream-ошибки теперь логируются raw-диагностикой через `LOGGER.error(...)`, но пользователю возвращается безопасный текст без `choices`/OpenRouter/traceback/JSON.
- **Автотесты:** `scripts/nmbot_test_agent.py` получил suite `h024` (санитизация ошибки) и suite `deploy` (live deploy-smoke, отдельно от `all`).
- **Verification:** `py_compile` ✅. `--suite h024` ✅ 1/1. Deploy-smoke **до рестарта** правильно упал: PID 11905 был старее `chat_tester_bot.py`. После рестарта live PID 12226 (`Fri Jun 26 13:59:56 2026`) deploy-smoke ✅ 1/1. Быстрые регрессии после рестарта: H021 ✅ 3/3, H023 ✅ 2/2, H024 ✅ 1/1.
- **Урок:** проверка «код прошёл тесты» ≠ «live bot обновлён». Для Telegram-бота deploy-smoke обязателен после правок обработчиков и промптов.

---

## 2026-06-26 — H019: Расширить facts[] — реальные поля из MCP

### H019 — Расширить `facts[]`: копировать в JSON ВСЕ доступные поля из MCP ✅
- **Что:** Search-промпт `P007-search` теперь просит LLM-search копировать в `facts[]` **все** доступные поля из MCP (metro, area, ready, link, developer — что MCP реально вернул), а не выжимку из 5 базовых. Chat-промпт `P008-chat` разрешает Ирине озвучивать metro/area/ready/dev, **если** они есть в `facts[]`. CODEX §7 расщеплён: ❌ выдумывать данные, ✅ использовать metro/area/ready/link, если они пришли от search.
- **Impact:** Ирина сможет **продавать квартиру** — подсвечивать метро, площадь, статус, отделку, цену — по **реальным** данным MCP, а не перескакивать на оператора только потому, что search-фаза не отдала нужное поле. Без нового кода, без нового MCP-вызова.
- **Файлы:** `prompts/search_v1.txt`, `prompts/chat_v1.txt`, `docs/CODEX.md §7`, `logs/prompts.jsonl` (P007 + P008), `logs/hypotheses.jsonl` (H019 closed), `scripts/nmbot_test_agent.py:290` (golden marker fix: `млн` → `руб`).
- **Verification:** `nmbot_test_agent` ✅ 12/12 pass (codex 5/5, h016 4/4, golden 3/3). Latency выросла на +14% (13.4с → 15.3с) — в пределах допуска +15%. Triage: golden_kotel_renov на первом прогоне упал не из-за H019, а потому что P007 копирует полную цену из MCP, P008 её озвучивает «от 10 905 590 до 25 300 120 руб.» — **это лучше** округлённого «от 10.9 млн». Маркер обновлён.

---

## 2026-06-25 — nmbot-test-agent + dialog memory

### H017 — `scripts/nmbot_test_agent.py` ✅
- **Что:** CLI-агент автотестирования. Прогоняет 12 сценариев через `OvermindClient`, проверяет codex + H016 + golden. JSON + human-readable отчёт, exit 0/1.
- **Impact:** любая правка промпта/handler теперь проверяется одной командой. 12/12 pass на момент закрытия.
- **Использование:**
  ```bash
  python3 scripts/nmbot_test_agent.py              # все 12
  python3 scripts/nmbot_test_agent.py --suite codex
  python3 scripts/nmbot_test_agent.py --json
  ```

### H016 — Dialog memory + operator funnel ✅
- **Что:** state помнит `last_options` (последние варианты Ирины). Резолвер `_resolve_dialog_intent` ловит «второй»/«первый»/«подешевле с ремонтом» и отвечает из памяти без нового Overmind-запроса. Оператор-funnel мягкий: «Хотите, предложу оставить номер для связи?» вместо «я уточню/передам».
- **Impact:** короткие follow-up («второй», «подешевле с ремонтом») теперь понимаются ботом, не приводят к повторному широкому поиску.

---

## 2026-06-25 — Quick actions + search indicator + split /start

### H015a — Shutdown stability ✅ partial
- **Что:** бот падает на SIGTERM (`RuntimeError: Cannot close a running event loop`). Workaround: `setsid bash scripts/run_bot.sh` (PID 22458, uptime 36+ часов с 2026-06-25).
- **Что не сделано:** постоянный фикс в коде (signal handler в `scripts/chat_tester_bot.py`) — отложен.
- **Impact:** бот в проде стабилен через workaround; код-фикс не блокирует. При переносе на systemd / Docker понадобится фикс.

### H020b — Reply Keyboard с кнопкой /start (открыта)
- **Что:** Persistent Reply Keyboard внизу чата с кнопкой `/start`. Ускоряет рестарт сессии.
- **Scope:** `scripts/chat_tester_bot.py` (добавить `ReplyKeyboardMarkup` в `start_command` и в обработчик сообщений).
- **Когда делать:** низкий приоритет — inline-кнопки (`/start` через callback) уже покрывают основной сценарий.
- **Выделено из H015** 2026-06-26 — две разные задачи, разнесены по h_id.

### H014 — Split /start into system + Irina intro ✅
- **Что:** `/start` отправляет два сообщения: системный блок (модели, MCP, команды) и блок Ирины (приветствие + что умею).
- **Impact:** пользователь видит и технические детали, и человеческое приветствие.

### H013 — Dynamic quick-actions buttons ✅
- **Что:** после каждого ответа — 1-4 inline-кнопки, зависящие от сценария (`G-first-step`, `A-found-some`, `C-narrow-empty`, `D-wide-empty`, `E-geo-mismatch`). Callback: `budget:5m`, `rooms:2`, `renovation:yes`, `district:mo`, `action:show_near`, `action:expand_district`, `action:operator`.
- **Impact:** пользователь отвечает кнопкой вместо текста — быстрее, меньше friction.
- **Урок:** `py_compile` не ловит семантические регрессии (`text=` vs `query=`). Нужны **runtime smoke-тесты** (см. H017).

### H012 — Visible search indicator + wide vs narrow логика ✅
- **Что:** «🔎 Осуществляю поиск...» + `editMessageText` на финальный ответ. P006-chat: «точно таких нет» только для **узких** запросов (rooms+max_price+has_renovation+floor), **широкие** (только район) — рассказываем как обычный facts.
- **Impact:** пользователь не ждёт в тишине 10+ секунд, Ирина не противоречит сама себе в широких запросах.

### H011 — AttributeError fix ✅
- **Что:** `_chat_with_retry` был вложен внутрь `_strip_markdown` (потеря отступа). Восстановлен как метод OvermindClient.
- **Урок:** `py_compile` не ловит потерю отступа внутри класса — нужен AST-чек в preflight.

---

## 2026-06-25 — Codex v1 + DRY prompts + cost tracking

### H010 — Few-shot golden dialogs ✅
- **Что:** 4 few-shot примера + анти-паттерны в `prompts/chat_v1.txt`. `docs/GOLDEN_DIALOGS.md` — 4 эталонных диалога.
- **Impact:** Ирина стабильнее держит тон, реже выдумывает.

### H009 — Dialog codex v1 ✅
- **Что:** `docs/CODEX.md` — 8 правил (тон, 3 ветки, оператор, отказ на не-движимость, ссылки, обращения, уточняющий вопрос). Inline-кнопка «📞 Связаться с оператором» + захват номера. Поиск возвращает `near` (приближённые) с `why_close`.
- **Impact:** «Уважаемый клиент» больше не появляется (раньше 9/26), «к сожалению, не нашлось» без альтернативы — больше нет (теперь near или оператор).

### H008 — Dialog coherence (4 scenarios) ✅
- **Что:** Тест 4 сценариев: persistence, robustness, coherence, honesty. `--params JSON` в chat_cli.
- **Impact:** 6/6 turn без ошибок, params накапливаются корректно.

### H007-B-prime — tokens_used в лог + bug fix ✅
- **Что:** chat_cli.py:355 — `md = meta["metadata"] if "metadata" in meta else meta` (был инвертирован). `_meta_cost` возвращает 4-tuple.
- **Impact:** cost-трекинг наконец работает (5 гипотез был «blocked» ложно из-за бага).
- **Урок:** не делать предположений по памяти — verify через probe.

### H007-A — Strip markdown ДО парсинга ✅
- **Что:** `_strip_markdown(chat_result)` ДО `_parse_chat_json` в обоих retry-циклах. Чистота кода.

### H006 — Strip markdown в лог-записях ✅
- **Что:** `response_text: _strip_markdown(response)` в handle_message/main().
- **Impact:** markdown-обёртка больше не попадает в лог.

### H005 — Markdown forbid в chat-промпте (1/3) ✅ partial
- **Что:** явный запрет markdown в `prompts/chat_v1.txt`. 1/3 срабатывает (только flash-lite).
- **Verdict:** недостаточно, решено парсить в коде = H006.

### H004 — Retry на невалидный JSON (defensive) ✅
- **Что:** `_chat_with_retry` в OvermindClient + retry-цикл в chat_cli. H003 убрал флактуацию, retry не сработал ни разу.
- **Impact:** defensive мера на будущее.

### H003 — max_tokens 5000→10000 ✅
- **Что:** chat_tester_bot.py:126 — `max_tokens = 10000`. `--chat-max-tokens` в chat_cli.
- **Impact:** JSON 3/3 (раньше 1/3 при 5000). +1.6с к latency — приемлемо.

### H002 — Prompts DRY + cost tracking ✅ partial
- **Что:** `prompts/search_v1.txt` + `prompts/chat_v1.txt` (single source of truth). Оба скрипта читают из файлов.
- **Impact:** codex-правки в одном месте. Cost tracking — `partial`, разблокирован в H007-B-prime.

### H001 — Baseline ✅
- 3 теста, 11.7с, 0 ошибок. Точка отсчёта.

---

## Файлы

| Файл | Назначение |
|---|---|
| `scripts/chat_tester_bot.py` | Telegram-бот dev (Ирина); актуальный bot token должен совпадать с prod `@minionassist_bot` |
| `scripts/chat_cli.py` | CLI-тестер, через `OvermindClient` |
| `scripts/nmbot_test_agent.py` | CLI-агент автотестирования (H017) |
| `scripts/nmbot_quality.py` | Оперативная проверка логов (пассивная) |
| `scripts/or_cost.py` | OpenRouter API: real cost |
| `scripts/or_monitor.py` | Мониторинг + auto-block на overrun |
| `scripts/run_bot.sh` | Запуск локального стенда (set -a; source .env) |
| `prompts/search_v1.txt` | Search-промпт (MCP novostroym) |
| `prompts/chat_v1.txt` | Chat-промпт (Ирина, codex + few-shot) |
| `docs/EXPERIMENTS.md` | Реестр гипотез + метрики |
| `docs/CODEX.md` | Свод правил диалога (9 разделов) |
| `docs/GOLDEN_DIALOGS.md` | Эталонные диалоги + анти-паттерны |
| `docs/CHANGELOG.md` | Этот файл |

## 2026-06-26 — H018: Живой диалог v2 — эмодзи-маркеры, HTML-разметка, развёрнутый codex ✅

### H018 — Эмодзи (0-2) + HTML `<b>` через postprocessor + нумерованные списки для facts ✅

- **Гипотеза:** диалог Ирины станет живее, если разрешить 0-2 эмодзи как маркеры состояния (👋🔎✅🤷🙂) + нумерованные списки 1./2./3. для 2+ вариантов + автоматическое оборачивание имён ЖК и цен в `<b>` через postprocessor (не LLM).
- **Файлы:** `prompts/chat_v1.txt` (P006-chat, planned→active), `docs/CODEX.md` (§1, §2, §3, §7 — ослаблены), `docs/GOLDEN_DIALOGS.md` (+2 эталона: 2+ вариантов с ✅, `/start` приветствие), `scripts/chat_tester_bot.py` (`_to_html()` postprocessor + 5 точек parse_mode=HTML), `scripts/nmbot_test_agent.py` (+2 проверки: `html_safe`, `single_emoji_per_msg`).
- **Verification:** `nmbot_test_agent` ✅ **12/12 pass** (codex 5/5, h016 4/4, golden 3/3). 0 регрессий. Latency 13.0с (-7% к baseline) — в пределах нормы. 2 новые проверки базовые для codex и применяются к h016+golden.
- **Triage:** LLM пишет **plain text** (CODEX §7 + chat_v1.txt явно). Постprocessor `_to_html()` экранирует `&/<,>` и оборачивает regex-паттерны в `<b>`: имена ЖК в `«...»` + цены `\d+ (млн|тыс|руб|рублей|млрд)`. **Не применять** к служебным plain-сообщениям (номер, оператор, индикатор).
- **Урок:** разделение «LLM-слой» (плоский текст) и «код-слой» (разметка) делает промпт проще, код надёжнее, ошибки дешевле. Маркеры-эмодзи работают как `state machine` (0-2 на сообщение), не как «украшение».

## 2026-06-26 — H021: Inline-кнопки budget из price_min ✅

### H021 — Бюджетные кнопки опираются на min(price_min) в last_options, не хардкод ✅

- **Гипотеза:** user feedback 13:00 «мне не понравилось что появились инлайн кнопки, я на них нажал а он сказал ничего такого нет». Кнопки бюджета генерировались жёстко `[5, 8, 12] млн` независимо от реальных цен в `last_options`. Если `min(price_min)=7.4 млн` — кнопка «до 5 млн» обещает пустой результат (обман по UX).
- **Файлы:** `scripts/chat_tester_bot.py` (`_pick_quick_actions` + helper `_budget_buttons_from_options`), `scripts/nmbot_test_agent.py` (suite `h021` + 3 unit-теста).
- **Verification:** `nmbot_test_agent` ✅ **15/15 pass** (h021 3/3 + codex 5/5 + h016 4/4 + golden 3/3). Latency 13.0с, 0 регрессий. 3 unit-теста проверяют: (1) min=7.4M → `[8, 10, 12]`, (2) min=3.5M → `[5, 7, 8] + без лимита`, (3) пустой `last_options` → fallback `[5, 8, 12]`.
- **Triage:** `price_min` уже был в `last_options` благодаря H019. H021 — чисто клиент-сайд, никаких изменений в промптах или search-фазе. Кнопка = обещание результата, и теперь обещание выполнимо.
- **Урок:** «код, который генерирует обещания пользователю, должен опираться на реальные данные, а не на хардкод» — это урок H021.

## 2026-06-26 — H023: `/start` сбрасывает старый бюджет + parser budget:Nm ✅

### H023 — Убрать протечку `max_price` после `/start` и поддержать динамические budget-кнопки ✅

- **Проблема:** после клика `budget:5m` и затем `/start` новый запрос «двушка в зеленограде» наследовал старый `max_price=5_000_000`, хотя пользователь бюджет не задавал. В логе это видно как `params_before` с `max_price=5000000` после `/start`.
- **Фикс:** `scripts/chat_tester_bot.py` — добавлены `_reset_dialog_state_preserve_settings()` и `_parse_budget_callback_value()`. `/start` теперь сбрасывает `params/last_options/asked_questions`, но сохраняет модели и MCP. Callback parser понимает `budget:5m/10m/15m/20m/none`, а не только 5/8/12.
- **Автотесты:** `scripts/nmbot_test_agent.py` — добавлены H023 unit-тесты: `start_resets_stale_dialog_params` и `budget_callback_parser_supports_dynamic_mln`.
- **Verification:** `nmbot_test_agent` ✅ **17/17 pass** (h021 3/3 + h023 2/2 + codex 5/5 + h016 4/4 + golden 3/3). 0 регрессий.

## Сводка гипотез

24 закрытых: H001-H014, H015a, H016-H019, H021, H023-H025. 1 открытый: H020b (Reply Keyboard — low prio, planned).
