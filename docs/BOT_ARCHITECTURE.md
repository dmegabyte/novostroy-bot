# NMBOT — как устроен бот

Дата: 2026-07-02

Статус: архитектурная документация. Актуальная production-схема: Jivo/API transport → runtime selector/adapter → изолированный runtime flow выбранной версии. Старые Telegram, stage-presenter и `sales_phrase` разделы ниже сохранены как LEGACY/HISTORICAL, если прямо так помечены; они не задают текущий Jivo production path.

> **Version boundary:** V0, V2 and V3 have separate client-facing identities:
> V0 — Валерия, V2 — Ирина, V3 — Светлана. Canonical separation rules live in
> `docs/NMBOT_RUNTIME_VERSIONS.md`. This document may describe architecture, but
> the active production version is known only from the live selector
> (`GET /api/runtime-version`) and the persisted selector file, not from prose.

## 1. Один источник правды

Продовая версия бота живёт на VPS:

```text
server:  neiro@193.107.155.236:1905
path:    /home/neiro/novostroy-bot
service: novostroy-bot-api.service + novostroy-bot-n8n-bridge.service
```

Локальный рабочий стенд проекта:

```text
/tmp/opencode-run-nmbot/project
```

В локальном стенде Jivo/API runtime живёт в:

```text
scripts/nmbot_api_server.py
scripts/nmbot_runtime_adapter.py
```

`scripts/chat_tester_bot.py` остаётся legacy/offline reference path и не является текущим Jivo production-flow.

Важно: если правка влияет на ответы Ирины, routing, state, MCP/search parsing, visible options или операторскую воронку, она считается незавершённой, пока не проверена на VPS.

## 2. Главные компоненты

```text
Jivo widget
  → n8n webhook
  → VPS bridge
  → shared nmbot API / transport
  → runtime selector
      ├─ V0 isolated two-prompt runtime
      ├─ V2 isolated typed runtime
      └─ V3 selector identity over V2 typed runtime + IntentPlanV3
  → Jivo BOT_MESSAGE
```

Legacy Telegram handler/docs are historical rollback/reference material and are not the current Jivo production path.

V0, V2 and V3 have separate release gates. V2/V3 verification must not count V0
tests/fixtures as proof; V0 verification must not inherit V2/V3
pending/evidence changes without an explicit V0 task.

Примечание: `stage presenter` оставлен как legacy/opt-in слой для отдельных экспериментов, но он не должен быть источником истины по умолчанию и не должен перетирать canonical response.

## 3. Внешние сервисы

### 3.1 Client channels

Current production client channel is Jivo through n8n bridge and local nmbot API. Telegram docs/handler are legacy rollback/reference material, not the Jivo release gate.

### 3.2 Overmind gateway

Все LLM/MCP-запросы идут через gateway-agent Overmind:

```text
Overmind gateway → OpenRouter → model / MCP novostroym
```

### 3.3 OpenRouter

В актуальном Jivo/API контуре модельные вызовы принадлежат выбранному runtime.
V0 строит канонический материал и deterministic fallback кодом, а отдельный
feature-gated V0 Answer Writer может оформить этот материал в plain-text ответ
Валерии. V2 и V3 могут
независимо подключать feature-gated `response_composer` через
`NMBOT_V2_RESPONSE_COMPOSER_MODE=off|shadow|publish` и
`NMBOT_V3_RESPONSE_COMPOSER_MODE=off|shadow|publish`; отсутствующее или
неизвестное значение означает `off`.

### Опциональный manager rewriter после готового ответа

Для V2/V3 локально подготовлен отдельный последний слой
`conversation_answer_manager_rewriter`. Он запускается уже после того, как
runtime выбрал готовый ответ: deterministic либо опубликованный composer.
Слой получает полный безопасный transcript текущего V2/V3-сеанса, последний
вопрос, готовый ответ и канонический evidence brief, после чего Gemini 2.5
Flash переписывает текст голосом живого менеджера.

Переключатели изолированы по версиям:
`NMBOT_V2_MANAGER_REWRITER_MODE=off|shadow|publish` и
`NMBOT_V3_MANAGER_REWRITER_MODE=off|shadow|publish`. `off` не вызывает слой,
`shadow` сохраняет исходный ответ для клиента, `publish` публикует непустой
rewrite. По подтверждённому продуктовому решению после rewrite нет смысловой
проверки фактов, чисел или числа вопросов; при timeout, provider error,
exception или пустом результате клиент получает уже подготовленный исходный
ответ. V0 в этот V2/V3 manager-rewriter маршрут не входит: у него отдельный
Answer Writer получает только V0 assignment и validated material. `/start` не передаёт старый transcript в
rewriter и очищает его вместе с state.

Статус: реализовано локально, production не включён. One-step release целится в
`V2=off`, `V3=publish` и API-only restart.

Composer работает только для поддержанных shortlist/selected WritingPlan
ответов. Основной вызов `google/gemini-2.5-flash` сразу возвращает простой JSON
`{intro, cards[{name,text}], recommendation, missing_note, final_question}` через
stage `conversation_answer_writer`. Если этот JSON валиден и проходит
механическую проверку, ответ публикуется без второго модельного вызова.

`inclusionai/ling-2.6-flash` вызывается один раз через
`conversation_answer_formatter` только когда Gemini вернул непустой,
содержательно полный, но синтаксически или механически повреждённый JSON,
который можно нормализовать без переписывания клиентской прозы. При timeout,
provider error или пустом/обрезанном ответе Gemini Ling не вызывается: runtime
делает один повтор тем же writer prompt, а затем использует deterministic fallback,
если повтор тоже неуспешен. Formatter не является свободным редактором прозы.

Formatter-модель можно заменить через `NMBOT_RESPONSE_FORMATTER_MODEL`. Writer
использует timeout `NMBOT_V2_RESPONSE_TIMEOUT` (по умолчанию 25 секунд),
formatter — `NMBOT_RESPONSE_FORMATTER_TIMEOUT` (по умолчанию 20 секунд). Оба
этапа идут только через single-shot gateway route.

Gateway проверяет статус formatter-задачи раз в одну секунду; остальные этапы
сохраняют общий интервал три секунды. Это уменьшает транспортную задержку Ling,
не увеличивая polling-нагрузку поиска, planner или Gemini-writer.

Механическая проверка требует точные имена и порядок карточек, точный CTA,
отсутствие новых чисел/ЖК. При Ling-normalization дополнительно требуется полное
сохранение исходной клиентской прозы Gemini; допускаются только изменения
пробелов, пунктуации, JSON-синтаксиса, нумерации и удаление повторного заголовка ЖК. В
`shadow` кандидат не публикуется. В `publish` он публикуется только после всех
проверок. При timeout, provider error, исключении или нарушении контракта
сохраняется заранее собранный deterministic ответ. State и runtime summary
всегда фиксируют фактически опубликованный текст.

Исторически через OpenRouter вызывались модели:

```text
search model: google/gemini-3.1-flash-lite-preview
search fallback models: google/gemini-3.5-flash, deepseek/deepseek-v4-flash
default chat model: google/gemini-2.5-flash
sales phrase model: google/gemini-3.5-flash (LEGACY/offline experiment, not production owner)
```

`main_search` работает через primary search + parallel fallback race:

1. основной запрос идёт в `google/gemini-3.1-flash-lite-preview` с MCP `novostroym`;
2. если gateway/OpenRouter вернул upstream/safe error, search-слой вернул валидный, но пустой результат (`facts=[]` и `near=[]`), или широкий подбор вернул подозрительно короткий shortlist, бот одновременно запускает ровно один повтор primary-модели и fallback-запросы в `google/gemini-3.5-flash` и `deepseek/deepseek-v4-flash`;
3. используется первый пригодный fallback-результат: без safe error, не пустой, а для broad search — не underfilled;
4. быстрый, но непригодный fallback не побеждает гонку; бот ждёт другой provider;
5. если оба fallback-ответа пустые/ошибочные/underfilled, бот возвращает безопасный fallback-ответ и не сочиняет объекты.

### 3.4 Model / fallback change gate

Любая правка модели, fallback, retry, reasoning или stage-routing обязана проходить этот gate до кода:

1. Сначала прочитать `docs/BOT_ARCHITECTURE.md`, `docs/RESPONSE_MODEL_EVAL.md`, `docs/EXPERIMENTS.md` и свежие NotebookLM notes.
2. Явно назвать слой изменения: `main_search`, `conversation_answer`, `chat`, `operator`, `transport` или `fallback`.
3. Зафиксировать `Actual / Contract / Desired` до внесения правки.
4. Если `payload_stage` не подтверждён логом, разрешена только диагностика, без поведения-фоллбеков.
5. Search fallback меняется только через search-контракт; chat fallback нельзя подменять search-контрактом.
6. После правки обязателен минимальный VPS/prod smoke.

### 3.5 Runtime ownership proof

Перед изменением gateway/runtime недостаточно найти одноимённый класс в репозитории. Нужно доказать владельца боевого вызова тремя источниками:

1. импорт и создание клиента в `scripts/nmbot_api_server.py`;
2. свежая строка `main_search` в `logs/model_payload_metrics-YYYY-MM-DD.jsonl`;
3. metadata `_gateway_client_impl` из результата gateway или события `main_search_fallback_exhausted`.

`scripts/chat_tester_bot.py` — legacy-контур и не считается владельцем Jivo V0/V2/V3 без такого live-доказательства. `_run_chat_v1` использует отдельный `legacy_overmind_client` и не имеет права заменять `app["overmind_client"]`.

### 3.6 Управляемый Bluesminds intercept для answer writer

В Jivo V2/V3 предусмотрен опциональный резервный канал только для слоя
`conversation_answer_writer`. Основной вызов Overmind/OpenRouter выполняется
первым и не изменяется. Если он вернул timeout/provider error/empty response,
при включённом intercept запрос с теми же `system_prompt` и `query` передаётся
в Bluesminds-модель `gpt-5.2-chat`. Ответ затем проходит обычную проверку
composer; при ошибке Bluesminds или нарушении контракта сохраняется штатный
deterministic fallback. `main_search`, formatter и V0 этим intercept не
затрагиваются.

По умолчанию intercept выключен:

```bash
python3 scripts/nmbot_bluesminds_interceptor.py status
python3 scripts/nmbot_bluesminds_interceptor.py on
python3 scripts/nmbot_bluesminds_interceptor.py off
```

Команда изменяет только `NMBOT_BLUESMINDS_INTERCEPTOR` в указанном dotenv-файле.
Настройки модели и таймаута:

```bash
NMBOT_BLUESMINDS_MODEL=gpt-5.2-chat
NMBOT_BLUESMINDS_TIMEOUT=60
```

Для работы нужен установленный reusable-пакет `bluesminds-cli` и
`BLUESMINDS_API_KEY` в окружении сервиса. После изменения dotenv-файла нужно
перезапустить соответствующий Jivo API unit; включение локального флага само по
себе не является production deploy.

Контракт broad search:

- для широких подборов runtime принудительно держит `count >= 3`;
- broad search — это `search`, `repeat_search`, `family`, `investment`, `rental`, а также широкие запросы с комнатностью, бюджетом, районом, метро, ипотекой, отделкой, готовностью, `facets`, `need` или `exclude`;
- не broad search: `fact_check`, `operator`, выбранный конкретный ЖК (`selected_option_name`);
- если broad search вернул только один объект (`0 < len(facts) + len(near) < 2`), это `underfilled shortlist`: бот пробует fallback search, а если fallback тоже дал мало — отвечает честно по доступному варианту и логирует metadata, но ничего не выдумывает.

Контракт candidate-first и report-only validation для V0/V2/V3:

- при broad search по конкретной локации V2/V3 могут сначала запросить широкий semantic shortlist без executable `location`, но исходные typed constraints остаются в state и используются для диагностики результата;
- `ищи лучше` / `покажи ещё` не имеют права молча удалить `location` или другой hard-критерий из state;
- business-validator поиска — наблюдатель, а не фильтр: missing hard evidence, hard mismatch, exclusion/repeat и exact/near mismatch сохраняют пригодную идентифицируемую карточку в исходном контейнере и создают bounded `search_validation_report`;
- правило одинаково для V0, V2 и V3. V0 вызывает shared search-contract явно, V3 использует общий V2 search engine;
- блокирующими остаются только safety/schema boundary (не-object карточка, отсутствие `id`/`alias`/`name`, неразрешённые поля) и exact named/current-option scope, чтобы ответ про конкретный ЖК не подменялся другим объектом;
- клиенту не показываются diagnostic codes. Успешные нарушения пишутся в `bot_error_events` без query, названий, значений локации, телефона, URL и raw payload.

Контракт scenario iteration:

- state хранит `active_scenario` — грубый ключ текущей пользовательской задачи и счётчик её итераций;
- сценариями считаются не только `down_payment`, но и ипотека, семейная ипотека, live-check, наличие, готовность, выбранный ЖК, purpose/follow-up сценарии;
- если клиент на третьей итерации всё ещё уточняет тот же сценарий и в state уже есть полезный контекст (`selected_option`, `visible_options`, `last_options` или `active_task`), runtime не запускает очередной бесконечный поиск, а делает мягкий `operator_handoff`;
- handoff объясняет причину человеческим языком: точные условия/наличие/ставки/без ПВ зависят от актуальной базы, банка, квартиры, цены и профиля клиента; оператор увидит контекст диалога;
- `awaiting_phone` не включается до явного согласия клиента оставить номер.

В тестах также использовались:

```text
openai/gpt-5.4-mini
openai/gpt-5.5
openai/gpt-4o
google/gemini-3.1-flash-lite-preview
```

LEGACY/HISTORICAL вывод по моделям: в старом Telegram/presenter эксперименте лучший баланс скорости и качества давал `google/gemini-3.5-flash` в роли `sales_phrase` модели. Это не текущий production-контракт модели. В актуальном Jivo runtime deterministic renderer остаётся безопасным fallback, а V2/V3 composer может быть включён отдельно через `NMBOT_*_RESPONSE_COMPOSER_MODE=shadow|publish` после проверки JSON; `off` или ошибка возвращают deterministic-текст.

### 3.4 MCP novostroym

MCP `novostroym` — источник фактов о новостройках.

Бот не должен придумывать факты. Всё, что попадает в ответ клиенту, должно прийти из MCP/search или быть безопасным смыслом из этих фактов.

Пример:

```text
MCP fact: рядом Мещерский парк и Чоботовский лес
Allowed comment: будет проще чаще гулять с детьми на свежем воздухе
```

Нельзя:

```text
MCP не дал парк → бот всё равно пишет “рядом парк”
```

## 4. Состояние диалога

Бот хранит state по пользователю.

Ключевые поля:

```json
{
  "params": {},
  "active_conversation_topic": null,
  "last_search_response": {},
  "last_options": [],
  "visible_options": [],
  "selected_option": null,
  "last_offer_type": null,
  "last_answer_kind": null,
  "awaiting_phone": false,
  "search_model": "google/gemini-3.1-flash-lite-preview",
  "search_fallback_model": "google/gemini-3.5-flash",
  "chat_model": "google/gemini-2.5-flash"
}
```

### Что значит каждое поле

| Поле | Зачем нужно |
|---|---|
| `params` | текущие параметры клиента: район, бюджет, комнатность, цель |
| `active_conversation_topic` | текущая тема живого диалога, которая должна переживать короткие follow-up |
| `last_search_response` | полный ответ MCP/search |
| `last_options` | нормализованные ЖК из последнего поиска |
| `visible_options` | ЖК, реально показанные клиенту в последнем списке |
| `selected_option` | ЖК, который клиент выбрал |
| `awaiting_phone` | бот ждёт номер телефона |
| `last_offer_type` | что бот предложил на прошлом шаге |
| `last_answer_kind` | тип прошлого ответа |

## 5. Основной поток первого подбора

Пример запроса:

```text
нужна двушка для семьи
```

Актуальный Jivo/API → runtime flow:

```text
1. Jivo event приходит через n8n webhook и VPS bridge в `scripts/nmbot_api_server.py`.
2. `scripts/nmbot_runtime_adapter.py` выбирает активный runtime по live selector / per-session override.
3. В выбранном runtime planner/search получает MCP-shaped facts и typed constraints; broad location search может использовать candidate-first retrieval.
4. Runtime безопасно нормализует карточки, сохраняет business-validation как report-only diagnostics и ведёт state/pending actions внутри namespace версии.
5. V2/V3 typed runtime собирает `ResponsePlan`; deterministic renderer остаётся
   fallback, а feature-gated composer соответствующей версии в режиме `publish`
   может опубликовать только механически валидный текст.
6. Adapter возвращает Jivo `BOT_MESSAGE`; `INVITE_AGENT` используется только для live operator handoff, не для callback-заявки.
```

Целевой формат:

```text
Подобрала три варианта для семьи.

1. ЖК «Лучи» — Солнцево, дом уже сдан, есть квартиры с отделкой, цены от 10,89 млн рублей.
   Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе.

2. ...

Какой ЖК хотите рассмотреть подробнее?
```

## 6. Stage orchestrator

Orchestrator решает не текст ответа, а стадию диалога.

Жёсткое правило слоя: **вся семантика пользовательского намерения распознаётся оркестратором**.
Нельзя чинить смысловые сценарии расширением regex в нижнем router/classifier.

Regex / deterministic guards допустимы только для механики, где нет смысла для распознавания:

- ввод номера телефона;

Всё остальное — LLM-orchestrator. Даже если фраза выглядит простой (`1`, `второй`, `подбери похожие`, `не надо`, `давай другой`, `хочу оператора`), это уже смысловой сценарий, а не кодовый regex.
Выбор варианта по номеру или названию тоже решает orchestrator: он должен вернуть exact `selected_option_name` из `visible_options`.

Фразы вроде `подбери похожие`, `найди похожие`, `ещё такие`, `похожие варианты`, `другие варианты` — это не механика. Это семантический запрос на новый сценарий подбора, поэтому его должен выбрать LLM-orchestrator.

```text
message + state → stage decision → presenter
```

Основные стадии:

| Stage | Когда | Presenter |
|---|---|---|
| `first_list` | клиент просит подобрать квартиру | `render_first_list` |
| `selected_object` | клиент выбрал ЖК из списка | `render_selected_object` |
| `selected_lot_search` | после выбора ЖК нужен точечный поиск квартир/планировок | exact-name bounded enrichment + dense card |
| `selected_lot` | клиент выбрал конкретный формат/лот | selected-lot presenter + live-data boundary |
| `operator_handoff` | клиент просит наличие, этажи, бронь, ипотеку, оператора | `render_operator_handoff` |
| `phone_capture` | клиент прислал телефон | code-level capture, без LLM |
| `refinement` | клиент уточняет бюджет, район, отделку | `render_refinement` или новый MCP-search |
| `comparison` | клиент просит сравнить варианты | `render_comparison` |
| `expand_more_options` | клиент семантически просит ещё похожие/другие варианты после shortlist | свежий MCP/search + `render_first_list` |
| `freeform_assist` | нестандартное сообщение | ограниченный LLM-ответ + validator |

### Stage contract: input → output

| Stage | Input contract | Expected action | Output contract |
|---|---|---|---|
| `first_list` | `user_text` + `params` + MCP facts | search / shortlist | `message` + `items[]` + `visible_options` + `params` + `final_question` |
| `selected_lot_search` | exact `selected_option_name` + viewpoint | `count=1` enrichment over `ads/apartment_types/house` | dense selected ЖК card + до 2 typed lot examples + один вопрос выбора |
| `selected_lot` | выбранный lot id/format from current examples | save exact lot and answer from its structured fields | lot details + optional availability/booking check CTA |
| `expand_more_options` | прежний контекст + `подбери похожие` / `ещё варианты` / `похожие` | fresh MCP/search, exclude already shown ЖК | новый shortlist + `visible_options` + `final_question` |
| `conversation_answer` | живой вопрос по теме + `state` + `active_conversation_topic` + `conversation_followup` | LLM answer in conversation mode | прямой ответ + `final_question` |
| `consultation_answer` | ипотека / ПВ / условия покупки + `answer_guidance` / `payment_financing_playbook` | direct answer first, then concrete next step | ответ по сути + граница данных + `final_question` |
| `operator_handoff` | наличие / этажи / бронь / ипотека без точных данных / явный запрос оператора | prepare handoff | короткий операторский ответ + `final_question` |
| `phone_capture` | телефон | code-level save, no LLM | подтверждение приёма телефона + передача контекста |
| `freeform_assist` | нестандартное сообщение, не относящееся к search/action | limited LLM reply + validator | ограниченный ответ + `final_question` |

### Stage card template

Любой новый этап или крупное изменение существующего этапа описываем в одном и том же формате. Если в этом шаблоне нет ответа — этап ещё не зафиксирован в доке.

```text
Stage name:
Purpose:
Input contract:
Prompt payload:
Expected action:
Output contract:
Prompt rules:
Forbidden:
Tests:
Docs:
```

Минимальное правило: когда меняется stage, меняются одновременно **формат входа**, **формат ответа** и **правила промпта**. Если меняется только текст без контракта — это всё равно должно попасть в `Prompt rules` и `Forbidden`.

Полный implementation contract выбранного ЖК и квартирной воронки:
`docs/NMBOT_SELECTED_ZHK_LOT_FUNNEL.md`.

### Golden path examples

На каждый важный stage держим 1 короткий эталонный пример.

#### first_list

```text
Input: нужна двушка для семьи
Expected action: search / shortlist
Output: 2–3 ЖК + short intro + `final_question`
Forbidden: сухой список без пользы, два финальных вопроса
```

#### conversation_answer

```text
Input: а что важно для аренды?
Expected action: conversation
Output: прямой ответ по сути + `final_question`
Forbidden: новый shortlist, уход в оператора без причины
```

#### consultation_answer

```text
Input: это без пв?
Expected action: consultation / payment-financing playbook
Output: ответ по сути + граница данных + конкретное действие сейчас + `final_question`
Forbidden: `я уточню потом`, обещание будущего ответа, выбор одного ЖК в ответ на `проверь все`
```

#### operator_handoff

```text
Input: а ипотека точно есть?
Expected action: operator_handoff
Output: короткий перевод к оператору + `final_question`
Forbidden: обещать ставку, платёж, одобрение без live-check
```

### Anti-patterns

Типовые ошибки, которые уже были и не должны возвращаться:

- stage presenter перетирает ответ модели;
- `response.question` живёт отдельно от `final_question`;
- `я уточню потом`, `как только будет информация`, `пока я уточняю`;
- короткий follow-up (`да`, `ок`, `проверь все`) стартует новый сценарий;
- `все проверь` превращается в выбор одного ЖК;
- `conversation_answer` превращается в новый shortlist;
- оплата / ипотека / ПВ отвечаются без playbook;
- больше одного финального вопроса;
- оператор вызывается как заменитель ответа, если ответ можно дать по сути.

### Change checklist

Перед любой новой фичей или крупной правкой проверяем:

- [ ] Какой stage / слой меняется?
- [ ] Какой input contract?
- [ ] Какой output contract?
- [ ] Кто отвечает: search / conversation / operator?
- [ ] Нужен ли `final_question`?
- [ ] Сохраняется ли `active_conversation_topic`?
- [ ] Есть ли regression test?
- [ ] Надо ли обновить docs?
- [ ] Нужен ли live/VPS probe?
- [ ] Описаны ли risks / mitigations?

### Risks / Mitigations

Для каждой новой фичи или крупной правки обязательно фиксируем:

```text
Risk:
Why it can happen:
Mitigation:
Check:
```

Минимум, который должен быть виден сразу:

- риск потери контекста;
- риск нового лишнего слоя;
- риск сломанного `final_question`;
- риск async-обещаний;
- риск того, что `conversation_answer` уйдёт в shortlist;
- риск того, что `проверь все` начнёт выбирать один ЖК;
- риск несоответствия локального и VPS поведения.

Если у фичи нет явных mitigations, она считается неготовой.

### Stop conditions

Это явные стоп-факторы: если хотя бы один пункт есть, фича не идёт дальше в код/деплой.

- нет `final_question`;
- нет regression test;
- stage не определён;
- есть async-обещание;
- не описан `input/output contract`;
- не понятен `expected action`;
- нет live/VPS проверки;
- не описаны risks / mitigations.

### Rollback note

Для каждой крупной правки заранее фиксируем откат.

```text
Rollback target:
Rollback signal:
Files to revert:
Post-rollback checks:
```

Минимум:

- что именно откатываем;
- по какому признаку откат нужен;
- какие файлы возвращаем назад;
- что проверяем после rollback;
- как понять, что откат действительно помог.

### Observability hooks

Для каждого важного stage сразу указываем, где смотреть здоровье поведения.

```text
Log to watch:
Regression test:
Live probe:
Failure symptom:
```

Примеры:

- `first_list` → смотреть response items и `final_question`;
- `conversation_answer` → смотреть, что тема не потеряна и нет shortlist;
- `consultation_answer` → смотреть, что нет async-обещаний и есть concrete next step;
- `operator_handoff` → смотреть, что есть короткий handoff и не придуманы условия;
- `phone_capture` → смотреть, что телефон сохранён без LLM.

### Ownership matrix

Для каждого слоя должно быть явно видно, кто за него отвечает.

| Layer | Owner | What owner must verify |
|---|---|---|
| planner | `followup_intent_classifier.py` / orchestrator | stage/action, `mode`, `conversation_followup`, `active_conversation_topic` |
| presenter | `scripts/chat_tester_bot.py` | `final_question`, visible text, no competing presenter layer |
| operator | operator_handoff / live-check path | no fabricated terms, concrete handoff |
| phone_capture | code-level phone parser | no LLM involvement, safe save |
| docs/tests | `docs/*`, `scripts/nmbot_test_agent.py` | contract and regressions stay in sync |

Если слой меняется, owner обязан обновить и тест, и доку.

### ADR / decision log

Важные архитектурные решения фиксируем отдельно, чтобы потом не восстанавливать их по памяти.

Минимальный формат записи:

```text
Decision:
Why:
Alternatives rejected:
What it changes:
What it does not change:
```

Когда это использовать:

- убрали stage presenter как конкурирующий слой;
- добавили `active_conversation_topic`;
- изменили `final_question` контракт;
- решили, что `все проверь` = all current options, а не один ЖК.

### Change impact map

Перед правкой всегда смотрим, что читает и что пишет изменение.

```text
Changed thing:
Reads from:
Writes to:
Consumers:
Tests to update:
Docs to update:
Live risk:
```

Минимум:

- что читает изменение;
- куда оно пишет;
- кто это потребляет;
- что надо обновить в tests/docs;
- где может сломаться live.

### Doc precedence

Если документы расходятся, используется такой порядок приоритета:

1. `docs/BOT_ARCHITECTURE.md` — операционный контракт и процесс работы.
2. `docs/IDEAL_IRINA_UX.md` — UX-эталон поведения Ирины.
3. `docs/CHANGELOG.md` — история изменений и причин.
4. Archive / secondary docs — только справка и история.

Если старый archive-док спорит с новыми правилами, он не считается источником истины.

### Deprecation policy

Если правило устарело, его нельзя просто молча оставлять рядом с новым.

Формат пометки:

```text
legacy / deprecated
Replaced by:
Why deprecated:
Date:
```

Правила:

- у каждого устаревшего блока должна быть явная замена;
- новый стандарт должен быть виден выше legacy-описания;
- если блок больше не актуален, его надо либо пометить `legacy`, либо перенести в archive doc;
- нельзя держать два равноправных правила, которые говорят разное.

### Release packet

На каждую chat-фичу, крупную правку поведения, routing, prompt’ов, presenter’ов или state готовим короткий релизный пакет.

```text
What changes:
Stage:
Input / Output:
Risks / Mitigations:
Rollback:
Tests:
Live probe:
Docs:
```

Минимум:

- что меняется;
- какой stage / слой затронут;
- какой input/output contract;
- риски и как их гасим;
- как откатить;
- какие тесты и live probe подтверждают;
- какие docs обновлены.

Это не опциональный комментарий, а общий контракт ЧАТИ для любых изменений в поведении бота.

Важно: внутри follow-up routing есть две разные логики, и их нельзя смешивать. Но выбрать между ними должен **orchestrator**, а не regex по отдельным фразам:

- `compare_others` — явное сравнение текущего сохранённого списка, без нового широкого поиска;
- `expand_more_options` — запрос на «ещё/похожие/другие варианты», который запускает свежий MCP/search и выкидывает уже показанные ЖК из результата.

Если lower-level router видит смысловую фразу, но orchestrator не выбрал stage/action, правильная реакция — считать это ошибкой orchestration contract, а не добавлять ещё один regex.

## 7. Stage presenter

Stage presenter собирает клиентский ответ по правилам конкретной стадии.

### 7.1 first_list

Задача: показать 2–3 ЖК и привести клиента к выбору одного ЖК.

Правила:

- максимум 3 ЖК;
- только список `1./2./3.`;
- каждый пункт: факты + одна польза;
- один финальный вопрос;
- без оператора, если варианты уже найдены.

Финальный вопрос:

```text
Какой ЖК хотите рассмотреть подробнее?
```

### 7.2 selected_object

Задача: клиент уже заинтересовался конкретным ЖК, значит надо коротко презентовать объект и вести к оператору.

Правила:

- не делать новый широкий MCP-поиск;
- использовать `selected_option` / `last_options`;
- если в `state['enriched_options']` уже есть обогащённая карточка, использовать её;
- если enrichment ещё не готов, выполнить короткий точечный enrichment по выбранному ЖК;
- дать 2–3 коротких абзаца;
- закончить операторским вопросом.

### 7.2.1 follow-up expansion after first_list

Если клиент после первого списка просит `подбери похожие`, `найди похожие`, `ещё такие`, `ещё варианты`, `похожие варианты`, `другие варианты` или `альтернативы`, бот не должен повторять тот же shortlist.

Это решение принимает stage orchestrator: он должен вернуть stage/action `expand_more_options` и `needs_mcp_search=true`. Нижний router не должен угадывать эту семантику regex’ом.

Правила:

- сделать свежий MCP/search с теми же или близкими условиями;
- исключить уже показанные ЖК из `visible_options` / `last_options`;
- показать новый shortlist максимум из 3 вариантов;
- если найден ровно 1 новый ЖК — можно перейти в `selected_object`;
- если клиент пишет `сравни` / `чем отличаются` / `разница` — это отдельная ветка `comparison`, а не expansion.

Цель:

- не крутить пользователя по кругу;
- расширять выбор, когда человек явно просит ещё похожие варианты;
- сравнение оставить только для уже показанных ЖК.

Fail-кейс:

- `подбери похожие` → clarification “продолжить подбор или изменить условия?”;
- повторный `подбери похожие` → тот же shortlist.

Почему fail: intent был понятен из контекста. Оркестратор должен выбрать fresh expansion, а не `continue_selection`.

Финальный вопрос:

```text
Хотите, позвать оператора проверить актуальные квартиры по этому ЖК?
```

### 7.2.2 sticky topic for short follow-up

Если клиент отвечает коротко: `да`, `ок`, `проверь все`, `все проверь`, `проверь ьвсе` (и похожие короткие продолжения), бот не должен терять текущую тему диалога.

Правило:

- `active_conversation_topic` сохраняется в state после консультационного ответа;
- короткий follow-up наследует эту тему, а не начинает новый сценарий;
- если тема была про `payment_terms` / `financing` / `down_payment`, короткое `проверь все` означает проверку всех текущих ЖК, а не просьбу выбрать один ЖК;
- если данных не хватает для точной проверки, бот не обещает `я уточню потом`, а сразу переводит на конкретное действие: operator_handoff / live-check всех текущих вариантов.

Цель:

- не терять контекст на коротких подтверждениях;
- не переводить оплату/ипотеку в новый список без причины;
- держать один и тот же предмет разговора до явного смена темы.

### 7.2.3 payment / financing playbook

Для вопросов про ипотеку, первоначальный взнос и условия покупки second-model presenter работает по playbook, а не по произвольной болтовне.

Если вопрос задан после уже показанного списка — например, `а они подходят по ипотеку?`, `эти варианты по ипотеке?`, `по ним есть ипотека?` — это не новый подбор и не смена primary scenario. Runtime должен передать в LLM `scenario_context`: основной сценарий остаётся прежним (`family`, `search`, `investment` и т. п.), ипотека идёт как facet поверх текущих `visible_options` / `last_options`, а местоимение `они` резолвится в текущие ЖК.

Ключевое правило по фактам: если в текущих карточках нет свежих ипотечных полей (`mortgage`, `mortgage_calc`, `payment_by_installments`, `discount` и т. п.), `scenario_context.facet_request.evidence_status` должен быть `no_current_mortgage_facts`. В этом случае LLM не обещает, что «все подходят под ипотеку» и не пишет «аккредитованы». Правильный ответ: мягко объяснить, что по этим ЖК можно проверить ипотечные условия, а точные условия зависят от банка, программы, объекта и данных клиента.

Также classifier / planner не пишут клиентский ответ в `clarification_question`. Для `consultation_answer`, `conversation_answer`, `operator_live_check`, `compare_options`, `recommend_options` это поле остаётся пустой строкой; клиентский текст пишет только answer-layer LLM.

Сценарий:

1. Дать прямой ответ по сути, что известно из фактов.
2. Чётко обозначить границу данных: что это не характеристика ЖК, а условие банка / программы / клиента.
3. Сразу предложить одно реальное действие сейчас: проверить конкретный ЖК, посмотреть все текущие варианты или передать вопрос оператору.
4. Не обещать будущий ответ и не говорить `я уточню`, `как только будет информация`, `пока я уточняю`, `вернусь позже`.

Правило для `все проверь` / `проверь все`:

- это означает: проверь все текущие ЖК, а не выбирай один;
- если нужен оператор, оператору уходит весь текущий контекст и сформулированный вопрос;
- бот не переспрашивает `какой ЖК хотите выбрать` в ответ на `проверь все`.

### 7.2.4 LLM planner before scenario handoff

Если клиент называет вариант из уже показанного списка перед operator-handoff, семантику выбора определяет только LLM-orchestrator.

Правило:

- `DIALOG_STATE_PLANNER_PROMPT` обязан распознать выбор по номеру, полному названию, частичному названию, разговорному сокращению или небольшой опечатке, если по `visible_options` / `last_options` понятно, какой один объект имелся в виду;
- в `selected_option_name` LLM возвращает каноническое точное `name` из памяти, а не текст клиента;
- код не делает regex/fuzzy-подбор ЖК сам: он только проверяет, что `selected_option_name` существует в памяти, и применяет `selected_option_action="set"`;
- `scenario_iteration_operator_handoff` запускается только после `plan_dialog_state` и `_apply_dialog_plan_to_state`, чтобы операторский текст получил выбранный ЖК, а не placeholder вроде `выбранные варианты`.

Если совпадение неоднозначно, planner должен выбрать `ask_clarification`, а не угадывать.

Smoke-контроль после изменений:

- `python3 scripts/nmbot_test_agent.py --suite dialog` на VPS должен проходить без ошибок;
- прицельный handler-like сценарий с уже показанным `ЖК «Публицист»` в `visible_options` / `last_options` и пользовательским текстом `публицист` должен дать цепочку: `followup_classifier` → `plan_dialog_state selected_option_action="set"` → `_apply_dialog_plan_to_state selected_option_set` → `scenario_iteration_operator_handoff`;
- финальный handoff должен упоминать `ЖК «Публицист»`, спрашивать номер и не содержать placeholder `выбранные варианты`.

### 7.3 operator_handoff

Задача: если клиент спрашивает наличие, этажи, корпуса, планировки, бронь, ипотеку, скидки или прямо просит оператора — не выдумывать, а вести к человеку.

Телефонный контракт:

- Ирина никогда не выдаёт клиенту номера телефонов операторов, менеджеров, отделов продаж, застройщика, WhatsApp/Telegram-ссылки или любые внешние контакты.
- Единственный разрешённый телефонный сценарий — попросить номер самого клиента для обратной связи.
- Если запрос — именно callback/обратный звонок, бот собирает `name + phone`, подтверждает заявку и кладёт её в private callback outbox / Google Sheets; `INVITE_AGENT` для callback не используется.
- Если клиент спрашивает `дайте номер`, `куда позвонить`, `телефон менеджера`, `как связаться с отделом продаж`, ответ остаётся operator_handoff: сохранить текущий контекст и попросить клиента оставить свой номер. Если клиент не хочет оставлять номер — продолжить диалог в чате.
- Нельзя придумывать контактные данные даже как пример. Контакты появляются только как входящий телефон клиента в `phone_capture` или callback-form state.

Пример:

```text
Это уже лучше проверить по актуальной базе. Оператор сможет посмотреть конкретные квартиры, наличие и условия.

Оставите номер телефона?
```

### 7.4 phone_capture

Если клиент прислал номер телефона, бот не отправляет его в LLM.

`phone_capture` — это приём телефона клиента, а не выдача наших телефонов клиенту. Полный номер клиента обрабатывает code-level guard; в LLM, публичные логи и prompt payload он не уходит.

Поток:

```text
message → phone detector → save phone → farewell
```

Ответ:

```text
Спасибо, номер получила. Заявку на обратный звонок сохранила — специалист свяжется с вами вместе с тем, что уже обсудили, чтобы не начинать всё заново.
```

Для callback-сценария это terminal confirmation заявки, а не перевод на живого оператора.

## 7.5 option enrichment / selected ЖК enrichment

Это дополнительный слой между first_list и selected_object.

Идея:

```text
first_list показал top-3 ЖК
  → bot фонит enrichment по каждому top-3 ЖК
  → selected_object берёт enriched card, если она уже готова
  → иначе делает короткий точечный enrichment перед ответом
```

Что кладём в enriched card:

- developer;
- location;
- metro / transport;
- rooms;
- area;
- price range;
- finishing;
- readiness;
- infrastructure (школы, сады, парки, двор без машин, магазины, аптеки, сервисы, если MCP их дал).

Что это даёт:

- selected-object ответ становится богаче, чем короткая карточка из first_list;
- бот может показать именно те факты, которые важны для семьи / метро / бюджета;
- если enrichment не успел прийти, бот всё равно отвечает безопасно по базовой карточке.

### 7.5.1 Selected-object fact focus и memory-first

После выбора ЖК диалог хранит три разных слоя, которые нельзя смешивать:

1. **Selected object** — точное каноническое имя и структурированная `OptionCard`.
2. **Dialog focus** — активный предмет разговора (`parking`, `apartment`,
   `mortgage` и т. п.), последний semantic intent, запрошенные и реально
   отвеченные fact fields.
3. **Fact evidence** — только структурированные MCP-поля выбранного ЖК и
   результат точечного enrichment. Предыдущий текст Ирины и dialog focus
   помогают понять продолжение, но не доказывают наличие факта.

Planner семантически разрешает короткие продолжения. Например, после вопроса
о паркинге `А сколько стоит?` означает `parking_price`; явное `А сама квартира
сколько стоит?` переключает focus на `apartment_price`. Если предмет нельзя
определить однозначно, planner задаёт уточнение. Код не делает phrase-specific
regex/fuzzy routing: он принимает только allowlisted subject/fact и точное
каноническое имя из текущей памяти.

Порядок получения фактов:

1. Сначала использовать сохранённые structured facts выбранной карточки.
2. Если запрошенного static fact достаточно, MCP повторно не вызывать.
3. Если fact отсутствует или является dynamic, разрешён только exact-object
   enrichment по каноническому ЖК: `count=1`, bounded `facts_needed`, без
   похожих объектов и широкого поиска.
4. Timeout, parse error, contract error или identity mismatch не стирают
   сохранённую карточку и не разрешают подменить её другим ЖК.

`parking` подтверждает только наличие проектного признака паркинга.
`parking_price`, `parking_inventory` и скидки — отдельные факты: из
`parking=true` нельзя выводить цену или наличие свободных машиномест.
Dynamic fact считается подтверждённым «сейчас» только если текущий exact
enrichment успешно применён и передал его в transient `fresh_facts`.
`fresh_facts` не сохраняется между ходами. Cached dynamic value можно показать
только как сохранённое значение с честной оговоркой о перепроверке.

Dialog focus обновляется из валидированного semantic plan, а
`last_answered_facts` — только из фактов, реально использованных в grounded
ответе. Новый поиск/reset очищает focus; смена выбранного ЖК не переносит
старый subject автоматически.

### 7.5.2 V2 executable recipe ownership

Актуальный V2 pipeline разделяет ответственность жёстко:

1. semantic planner понимает смысл, canonical reference, subject/facts,
   `domain_relation` и outcome закрытого вопроса; его semantic response может
   вернуть canonical selected facts, но не выбирает recipe;
2. runtime exact-валидирует значения, выводит stage/action/scope и один раз
   выбирает recipe в `nmbot_v2/scenario_recipes.py`;
3. resolved metadata прикрепляется к `ResponsePlan` и без повторного выбора
   попадает в deterministic fallback и `ResponseBrief`;
4. Runtime один раз выбирает route, recipe, порядок карточек, anchor и CTA. Затем
   deterministic renderer или feature-gated response composer меняет только
   безопасную форму уже собранного `ResponsePlan`; composer не может менять эти
   runtime-owned решения. В режиме `off`/при ошибке используется deterministic
   fallback, в `shadow` текст не публикуется, в `publish` публикуется только
   механически валидный результат.

В deployed V2 registry зафиксировано 30 recipe entries и два закрытых reply
contract: `financing_consent` и `selected_live_fact_consent`. Оба используют
одинаковый набор outcome `accept`, `decline`, `ask_or_clarify`, `unexpected`;
runtime валидирует outcome против реестра, а fallback/composer получают уже
готовый recipe contract.

`off_topic` — semantic relation, а не keyword route. Runtime выводит
`Stage.OFF_TOPIC`, запрещает search/enrichment/operator, сохраняет текущий
подбор и очищает только устаревший pending offer.

Dynamic selected facts используют closed reply contract
`selected_live_fact_consent`; invalid/missing outcome становится recovery и
никогда не означает согласие. `apartment_inventory` принимается только из
явного structured evidence и не выводится из `ads_count`.

Проверенный deploy-пакет registry V2: backup `backups/deploy-20260720-121023`,
`novostroy-bot-api.service` active, hashes deployed-файлов на VPS совпали с
локальными. Live smoke подтвердил off-topic boundary, one-object boundary для
missing selected live parking fact и follow-up resolution в `parking_price`; он
не подтверждал Sheet-доставку. Sheet delivery исторически подтверждена отдельно
для mortgage callback → Sheet сценария.

### 7.5.3 IntentPlan V3 semantic contract

С 2026-07-21 authoritative Jivo runtime использует `IntentPlanV3` как active
semantic contract. Planner возвращает один `goal`, `viewpoint`, ограничения и
запрошенные факты; код затем выполняет typed validation и mechanical
`goal → stage/action` transition. Planner не меняет state, не выбирает MCP
endpoint и не формирует клиентский ответ.

Production switch:

```text
NMBOT_INTENT_PLAN_VERSION=v3
```

Значение `v2` — документированный rollback path. Если переменная отсутствует,
runtime безопасно использует `v2`. В production V3 подтверждён первым
synthetic Jivo smoke: trace содержит `schema_version=3`,
`canonical_valid=True`, `fallback_used=False`, а API вернул `BOT_MESSAGE`.
Backup rollout: `backups/deploy-intent-v3-20260721-085140`.

Краткая карта ownership по версиям, включая независимые composer gates и
deterministic fallback, поддерживается в
`docs/NMBOT_RUNTIME_VERSIONS.md#architecture-approach-by-version`. Этот раздел
задаёт semantic contract V3 и не отменяет отдельную V3 smoke-проверку при
изменениях V3.

## 8. Sales phrase layer — LEGACY/HISTORICAL

Этот раздел сохранён как история старого presenter/phrase эксперимента. Он не
описывает route/recipe ownership текущего V2 Jivo runtime. В production V2
deterministic renderer остаётся authoritative fallback; `response_composer` может
быть подключён только отдельным feature-флагом и не получает права менять route,
recipe, порядок карточек, anchor или CTA.

`sales_phrase` — маленький LLM-слой, который пишет только одну короткую пользу по semantic card.

Он не пишет весь ответ.

Поток:

```text
option facts + scenario + allowed angles
  → sales_phrase model
  → benefit sentence
  → validator
  → presenter inserts benefit into answer
```

### Конфиг

```text
NMBOT_SALES_PHRASE=1
NMBOT_SALES_PHRASE_MODEL=google/gemini-3.5-flash
NMBOT_SALES_PHRASE_TEMPERATURE=0.2
```

### Что получает модель

```json
{
  "scenario": "family",
  "items": [
    {
      "idx": 1,
      "object": "ЖК «Лучи»",
      "facts": [
        "Солнцево",
        "дом уже сдан",
        "есть квартиры с отделкой",
        "рядом Мещерский парк и Чоботовский лес"
      ],
      "allowed_angles": [
        "рядом есть место для прогулок с детьми",
        "отделка уменьшает ремонтные хлопоты",
        "готовый дом проще планировать для переезда"
      ]
    }
  ]
}
```

### Что возвращает модель

```json
{
  "items": [
    {
      "idx": 1,
      "benefit": "Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе."
    }
  ]
}
```

## 9. Scenario comment enrichment

Это следующий слой поверх `sales_phrase`.

Он описан отдельно:

```text
docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md
```

Идея:

```text
scenario + MCP fact → allowed meaning → короткий клиентский комментарий
```

Пример:

```text
family + park/forest → будет проще гулять с детьми на свежем воздухе
investment + min_price → понятная точка входа для сравнения
metro_access + metro_walk_minutes → удобно ездить каждый день без машины
```

## 10. MCP/search

Search-фаза должна вернуть структурированный JSON:

```json
{
  "facts": [],
  "near": [],
  "missing": "",
  "params": {}
}
```

Главное правило: search должен копировать все полезные MCP-поля, а не только `name/location/price`.

Полезные поля:

```text
name
location
price_range
min_price
max_price
finishing
ready
delivered
area
metro
why_close
infrastructure
infrastructure_family
schools
kindergartens
parks
clinics
playgrounds
shops
services
```

Если поле не пришло — bot не использует его в ответе.

## 11. Validator

Validator нужен, чтобы модель не испортила ответ.

Проверки:

- нет фактов вне MCP/search;
- нет технических слов `MCP`, `в базе`, `по данным`, `сдача/готовность`;
- нет рекламных слов `лучший`, `идеальный`, `выгодный`, `перспективный`, `премиальный`;
- нет инвестиционных обещаний `доходность`, `аренда`, `ликвидность`, `рост цены`, `окупаемость`;
- если комментарий говорит про парк — парк должен быть во входных фактах;
- если говорит про школу/сад/поликлинику/метро — эти факты должны быть во входе;
- в first_list ровно один финальный вопрос;
- в selected_object должен быть операторский вопрос;
- не больше 3 ЖК в первом списке;
- у каждого ЖК отдельная польза.

Если validator отклоняет LLM-фразу, presenter использует безопасный fallback из карты фактов.

## 12. Модели и настройки

### Search

```text
google/gemini-3.1-flash-lite-preview
```

Задача: вызвать MCP и вернуть факты.

### Intent planner V3

```text
NMBOT_INTENT_PLAN_VERSION=v3
```

V3 использует strict JSON Schema и один planner call на ход. При ошибке
валидации или перехода runtime fail-closed и не меняет state.

### Chat / default

```text
google/gemini-2.5-flash
```

Используется как базовая модель общения в старом контуре.

### Sales phrase

```text
google/gemini-3.5-flash
temperature: 0.2
```

Задача: одна короткая польза по semantic card в legacy/offline контуре; не production dependency V2 renderer.

## 13. Команды и диагностика

### Жёсткое правило MCP/search

Для любого клиентского запроса о квартире, новостройке, ЖК или подборе вариантов бот не должен отвечать «из головы».

```text
квартирный запрос → обязательный MCP/search → нормализация фактов → ответ Ирины
```

Это правило фиксируется на уровне search prompt: все цены, площади, сроки, отделка, инфраструктура и варианты должны приходить из инструментального поиска. Если запрос неполный, поиск всё равно выполняется по доступным параметрам, а недостающие условия возвращаются как missing/params.

### Визуальная карта сценариев

```text
docs/BOT_SCENARIO_MAP.html
```

Это standalone HTML-схема для человека: блоки, стрелки, сценарии Stage 0/1/2/3/4/4.5/5/6, ветки `recommend_options` и operator handoff, реальные MCP/search поля, state-память, рабочие промты и примеры ответов.

Документ нужен, чтобы быстро понять текущую логику бота без чтения всего `scripts/chat_tester_bot.py`.

### История диалога в Telegram — LEGACY/HISTORICAL

### Публичный веб‑обзор проекта

Для просмотра проекта целиком используется тот же публичный статический сервис, где уже лежит MPN quality dashboard:

```text
http://193.107.155.236:8765/
```

Сборщик:

```text
scripts/build_public_overview.py
```

Он собирает безопасный публичный пакет:

- общий список сервисов `/index.html`;
- короткую главную NMBOT `/nmbot-project-7f3a9c/index.html` с hero,
  главным правилом, общей Jivo transport/selector схемой, отдельной V0 веткой и
  общей typed V2/V3 веткой;
- вкладку версий `/nmbot-project-7f3a9c/versions.html` по
  `docs/NMBOT_RUNTIME_VERSIONS.md`;
- страницу `Архитектура решений` `/nmbot-project-7f3a9c/architecture-v2.html`;
- безопасный каталог resources `/nmbot-project-7f3a9c/resources.html`, где есть
  только назначения источников, без raw-документов;
- тихий archive `/nmbot-project-7f3a9c/archive.html`; legacy больше не находится
  в primary navigation;
- отдельную историю `/nmbot-project-7f3a9c/history.html`, которая не загружается
  автоматически и делает только one-shot lazy request к `history.json` после
  явного нажатия пользователя.

NMBOT overview использует allow-list только как источник безопасных
архитектурных summaries. Полные документы, prompt-тексты, внутренние команды,
trace, `.env`, логи, backups, имена секретных env-ключей и operational snippets
не публикуются. Это сохраняет страницу полезной для объяснения V0/V2/V3, но не
превращает её в операционный или секретный контур.

Публичная история диалогов собирается отдельным санитайзером:

```text
scripts/publish_public_history.py
```

Он читает `logs/dialogs-YYYY-MM-DD.jsonl`, публикует только компактные поля для
sanitized snapshot и маскирует телефоны/email/токены. Публичная HTML-страница не
делает постоянный polling; если записей нет, список остаётся скрытым.

```text
/history [N]
/hisotry [N]
```

Обе команды показывали последние ответы бота для текущего Telegram-пользователя в legacy-контуре. `/hisotry` оставлен как alias с опечаткой, потому что пользователь попросил именно такую команду. Это не текущий Jivo release gate.

Источник данных — существующие dialog logs:

```text
logs/dialogs-YYYY-MM-DD.jsonl
```

В ответ попадает компактный trace:

```text
Вы: клиентский запрос
Бот: ответ Ирины
intent: выбранный dialog_intent
plan: dialog planner state patch, если был
MCP/search_response: компактный search/MCP ответ
buttons: отправленные кнопки, если были
cost: cost/debug мета, если была
```

Вывод ограничивается и режется на Telegram-safe chunks, чтобы длинный MCP/search JSON не ломал сообщение.

### Jivo Bot API — рабочий production-flow

Фактическая рабочая схема на 2026-07-15:

```text
Jivo widget → n8n webhook → VPS bridge → local nmbot API
  → runtime selector → V0 or V2 isolated pipeline → Jivo BOT_MESSAGE
```

The shared part ends at transport/API/selector. The active pipeline for the current process must be checked with `GET /api/runtime-version`; do not infer it from docs, branch names or the default fallback.

Поведение контура:

- входящий webhook подтверждается сразу, чтобы Jivo не упирался в короткий timeout;
- bridge ждёт ответ Ирины в фоне до hard timeout (по умолчанию `600` секунд),
  а `90` секунд остаются порогом прежнего одноразового статуса;
- если ответ успел — в Jivo уходит настоящий `BOT_MESSAGE`;
- если в тот же `chat_id/client_id` уже пришло новое сообщение, старый ответ не отправляется;
- fallback используется только если upstream действительно не успел или упал.

#### Опциональные промежуточные статусы

Bridge содержит выключенный по умолчанию транспортный режим повторных
пользовательских статусов. После включения первый статус отправляется через
`NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS` секунд, затем новые статусы отправляются
с тем же интервалом до готовности финального ответа. По умолчанию интервал равен
трём секундам, а безопасные общие шаблоны вращаются по кругу.

Статус — это нетерминальный `BOT_MESSAGE`: он не заменяет и не отменяет исходную
upstream-задачу. Ошибка доставки статуса изолируется, после неё bridge продолжает
ждать финальный ответ. Перед каждым статусом и финальным ответом действует
stale-event guard: если клиент уже прислал новое сообщение, устаревшая доставка
пропускается.

Режим включается только конфигурацией bridge:

```env
NMBOT_BRIDGE_STATUS_UPDATES_ENABLED=1
NMBOT_BRIDGE_STATUS_INTERVAL_SECONDS=3
NMBOT_BRIDGE_STATUS_TEMPLATES="Уже работаю над вашим запросом.|Проверяю нужную информацию.|Уточняю детали, чтобы ответить точнее.|Ещё немного — готовлю ответ."
```

Источник готового выключенного шаблона:
`deploy/systemd/novostroy-bot-n8n-bridge.env.example`. До отдельного Jivo live
smoke режим не считается подключённым к production.

Признак здорового прогона в логах:

```text
bridge_request ... result: accepted_async
bridge_async_send ... upstream_result: upstream
bridge_async_send ... jivo_status: 200
```

### Локальная проверка синтаксиса

```bash
python3 -m py_compile scripts/nmbot_api_server.py scripts/nmbot_runtime_adapter.py
```

### Стоимость OpenRouter

```bash
python3 scripts/or_cost.py
```

### Продовый статус

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "systemctl --user status novostroy-bot-api.service novostroy-bot-n8n-bridge.service --no-pager"
```

### Продовый лог

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "tail -50 /home/neiro/novostroy-bot/logs/bot_error_events-\$(date -u +%F).jsonl"
```

## 14. Deploy gate

Для любых изменений в ответах Ирины:

```text
1. Локально: py_compile.
2. Локально: smoke на реальных сценариях.
3. Backup runtime-файлов на VPS.
4. Upload на VPS.
5. Remote py_compile.
6. Restart novostroy-bot-api.service.
7. Проверить markers/status/logs на VPS.
8. Проверить prod smoke или live Jivo.
9. Показать локальный `bash scripts/openrouter_balance`.
10. Если это live/scenario/prod-smoke прогон, который идёт в отчёт или вывод пользователю, записать его в Google Sheet по правилам `docs/LLM_SCENARIO_EVAL_RUBRIC.md`.
```

Запрещено говорить “готово”, если проверена только локальная версия.

## 14.1 Project Operating Standard

Этот алгоритм обязателен для любой новой функции, которая влияет на ответы Ирины, routing, state, MCP/search parsing, visible options, prompt payload, `final_question` или операторскую воронку.

Цель: добавлять функционал без расползания архитектуры, новых конкурирующих presenter-слоёв и локальных заплаток.

### 1. RECON — сначала понять текущий контракт

Перед правкой нужно проверить:

- `docs/IDEAL_IRINA_UX.md` — UX-эталон Ирины;
- `docs/BOT_ARCHITECTURE.md` — текущую архитектуру слоёв;
- свежие NotebookLM notes по теме;
- существующие тесты в `scripts/nmbot_test_agent.py`.

Нельзя начинать с кода, если непонятно, какой слой должен отвечать за поведение.

### 2. CLASSIFY — определить слой изменения

Каждая задача должна попасть в один существующий слой:

| Тип задачи | Слой |
|---|---|
| новый подбор / изменение параметров | search / MCP / planner |
| живой вопрос клиента | `conversation_answer` / `consultation_answer` |
| ипотека / ПВ / условия покупки | payment-financing playbook |
| оператор / наличие / этажи / бронь | `operator_handoff` |
| телефон | code-level `phone_capture`, без LLM |
| формат ответа | presenter / `final_question` |
| сохранение темы | `active_conversation_topic` |

Если задача не попадает ни в один слой, сначала формулируется архитектурное решение. Новый слой добавляется только после явного решения, что существующие слои не подходят.

### 3. CONTRACT — описать контракт до кода

Перед реализацией нужно коротко зафиксировать:

```text
Что меняется:
Слой:
State:
Prompt payload:
Expected action:
final_question:
Operator rule:
Tests:
Docs:
```

Пример:

```text
Фича: клиент пишет “проверь все” после вопроса про без ПВ.
Слой: live conversation / payment playbook.
State: использовать active_conversation_topic.
Expected action: all_current_options + operator_live_check.
final_question: “Передать оператору все текущие ЖК и вопрос по первоначальному взносу?”
Нельзя: “я уточню и сообщу”.
Tests: sticky_payment_topic_survives_yes_and_check_all.
Docs: BOT_ARCHITECTURE + IDEAL_IRINA_UX.
```

### 4. TEST FIRST — сначала регрессия

До кода нужно найти или добавить тест, который проверяет новый контракт.

Минимальные suites:

```bash
python3 scripts/nmbot_test_agent.py --suite ux_e2e
python3 scripts/nmbot_test_agent.py --suite h029
python3 scripts/nmbot_test_agent.py --suite dialog
python3 scripts/nmbot_test_agent.py --suite deploy
```

Если теста нет, задача считается неполной: поведение нельзя будет удержать при следующих правках.

### 5. IMPLEMENT — минимальная правка в правильном слое

Правила реализации:

- не добавлять новый presenter, если есть существующий;
- не чинить смысл диалога regex’ом;
- не обходить planner;
- не делать async-обещания (`я уточню и сообщу`, `как только будет информация`);
- не отправлять ответ без `final_question`;
- не терять `active_conversation_topic` на коротких follow-up;
- не обещать ипотеку, ставку, платёж, наличие, бронь, скидки или этажи без operator/live-check.

### 6. VERIFY — локальный контур

Минимальная локальная проверка:

```bash
python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py
python3 scripts/nmbot_test_agent.py --suite ux_e2e
python3 scripts/nmbot_test_agent.py --suite h029
python3 scripts/nmbot_test_agent.py --suite dialog
python3 scripts/nmbot_test_agent.py --suite deploy
```

Если падает хотя бы один релевантный тест, нельзя говорить “готово”.

### 7. PROD GATE — VPS обязателен

После локального зелёного:

1. сделать backup runtime-файлов на VPS;
2. синхронизировать изменённые файлы;
3. выполнить `py_compile` на VPS;
4. перезапустить `novostroy-bot-api.service`;
5. проверить health, markers, status, error journal и bridge trace на VPS;
6. прогнать релевантные V2/Jivo suites;
7. сделать live Jivo probe или privacy-safe API smoke;
8. проверить `logs/bot_error_events-YYYY-MM-DD.jsonl`;
9. вывести `bash scripts/openrouter_balance` локально.

Формально зелёный локальный тест не означает готовность, пока prod-контур не проверен.

### 8. DOCS — обновить правила

Если новая функция меняет устойчивое поведение Ирины:

- обновить `docs/BOT_ARCHITECTURE.md`;
- если это UX-правило — обновить `docs/IDEAL_IRINA_UX.md`;
- если это релизная правка — добавить запись в `docs/CHANGELOG.md`;
- добавить NotebookLM note с кратким итогом.

### 9. DONE criteria

Фича считается завершённой только если:

- архитектурный слой выбран;
- контракт описан;
- regression test есть;
- локальные проверки зелёные;
- VPS проверки зелёные;
- live Jivo probe или privacy-safe API smoke прошёл;
- свежих error-events после deploy нет;
- документация актуальна.

## 14.2 Memory policy

Цель: не перегружать агента данными, но не терять важное.

### 1. Temporary memory

Держим только то, что нужно прямо сейчас:

- текущую цель;
- активный этап;
- 1–2 ключевых файла;
- последний контракт ответа.

### 2. Project memory

Туда пишем только устойчивое:

- архитектурные правила;
- stage contracts;
- UX-контракты;
- решения, которые не должны забываться следующими сессиями.

Главные места хранения:

- `docs/BOT_ARCHITECTURE.md`;
- `docs/IDEAL_IRINA_UX.md`;
- NotebookLM notes.

### 3. Archive / compress

Всё большое, промежуточное и уже закрытое:

- длинные исследования;
- сырой диалог;
- временные гипотезы;
- шум после того, как вывод уже понятен.

Это нужно сжимать через `compress`, а не держать в живом контексте.

### Практическое правило

- нужно только сейчас → держим в рабочей памяти;
- нужно потом в проекте → пишем в docs / NotebookLM;
- уже не нужно, но важно как история → compress.

### Что помогает сильнее всего

1. Stage contract — у каждого этапа свой input/output.
2. Project Operating Standard — RECON → CLASSIFY → CONTRACT → TEST FIRST.
3. Compress — как только кусок закрыт.
4. NotebookLM note — для долговременных решений.
5. Docs — для правил, которые должен помнить любой следующий агент.

## 14.3 What to read first / archive map

Чтобы не хватать старые файлы первым делом, используй такую карту:

### Read first

1. `docs/IDEAL_IRINA_UX.md` — что считается правильным UX.
2. `docs/BOT_ARCHITECTURE.md` — слой, stage contract, memory policy, deploy gate.
3. `prompts/chat_v1.txt` и `prompts/search_v1.txt` — актуальные контракты промптов.
4. `scripts/nmbot_test_agent.py` — актуальные regression / live checks.
5. Последние NotebookLM notes по текущей теме.

### Archive / secondary context

Считать вспомогательными, а не первым источником:

- `docs/IRINA_DIALOGUE_MAP_V1.md`;
- `docs/reason_layer_hypothesis_conclusions_2026-07-02.md`;
- `docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md`;
- старые notes, которые описывают уже заменённое поведение.

### Простое правило

- если в старом документе есть конфликт с `IDEAL_IRINA_UX.md` или `BOT_ARCHITECTURE.md`, источник истины — новые docs;
- если документ нужен только для истории решений, он не должен становиться первым входом для нового агента.

## 15. Основные документы

| Документ | Что описывает |
|---|---|
| `docs/IDEAL_IRINA_UX.md` | эталон UX Ирины |
| `docs/BOT_ARCHITECTURE.md` | текущие рабочие правила архитектуры, stage contracts, memory policy и deploy gate |
| `prompts/search_v1.txt` | правила MCP/search фазы |
| `prompts/chat_v1.txt` | базовые правила chat-фазы |
| `scripts/nmbot_test_agent.py` | актуальные regression / live checks |

## 15.1 Archive / secondary docs

Эти документы полезны для истории и отдельных деталей, но они не должны быть первым источником, если конфликтуют с `IDEAL_IRINA_UX.md` или этой докой:

- `docs/IRINA_DIALOGUE_MAP_V1.md` — старое описание стадий и presenters;
- `docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md` — история enrichment-контракта;
- `docs/reason_layer_hypothesis_conclusions_2026-07-02.md` — выводы по reason-layer гипотезам;
- `docs/EXPERIMENTS.md` — журнал гипотез и проверок.

## 16. Что сейчас считается хорошим ответом

### Первый список (current contract)

```text
response:
  message: "Да, нашла несколько понятных вариантов..."
  items:
    1. ЖК «Лучи» — Солнцево, дом уже сдан, есть квартиры с отделкой, цены от 10,89 млн рублей.
       Рядом Мещерский парк и Чоботовский лес — будет проще чаще гулять с детьми на свежем воздухе.
    2. ...
final_question: "Какой ЖК хотите рассмотреть подробнее?"
visible_options: [...]

```

### Выбранный ЖК (current contract)

```text
response:
  message: "ЖК «Лучи» — Солнцево, дом уже сдан. Есть квартиры с отделкой..."
  items: []
final_question: "Хотите, позвать оператора проверить актуальные квартиры по этому ЖК?"
visible_options: [...]
```

### Телефон (current contract)

```text
Спасибо, номер получила. Передам оператору ваш запрос вместе с тем, что уже обсудили, чтобы не начинать всё заново.
```

## 17. Что запрещено

- `сдача/готовность` в клиентском тексте;
- `верхняя точка бюджета`;
- `по данным`, `в базе`, `MCP`;
- “лучший”, “идеальный”, “выгодный”, “перспективный”, “премиальный”;
- доходность, аренда, ликвидность, рост цены;
- парк, школа, сад, поликлиника, метро, если этого нет в facts;
- обещание наличия, этажа, корпуса, скидки, ипотеки без оператора;
- больше одного финального вопроса;
- оператор в первом списке, если варианты уже найдены.

## 18. Куда развивать дальше

1. Довести `scenario comment enrichment` до кода.
2. Расширить `search_v1.txt`, чтобы broad family/infrastructure queries чаще возвращали инфраструктуру.
3. Добавить тесты на сценарии:
   - family + park/school/clinic;
   - investment без доходности;
   - metro only when metro fact exists;
   - selected_object → operator;
   - phone capture without LLM.
4. Добавить quality report по live dialogs: структура, отступы, польза, финальный вопрос, operator funnel.
