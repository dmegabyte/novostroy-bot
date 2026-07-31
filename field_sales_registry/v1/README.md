# External Field Sales Registry v1

Этот каталог — прототип внешнего реестра полей для будущего sales-слоя. Он **not wired to runtime**: текущие V0/V2, selector, prompts, deploy и production его не читают.

## Зачем он нужен

Реестр отделяет знание о поле от текста ответа. В карточке поля хранится не «готовая реклама», а безопасная инструкция: что поле буквально означает, какие доказательства нужны, где оно может помочь клиенту, с чем его можно сочетать и какие выводы запрещены.

Источник решения: `docs/archive/working-history/2026-07-24/findings.md`, раздел `External Field Sales Registry — initial decision, 2026-07-21`; wire-смысл полей — `docs/NOVOSTROYM_MCP_SCHEMA.md`; текущие allowlist/normalizer-boundaries — `nmbot_v2/search_contract.py`, `nmbot_v2/contracts.py`, `nmbot_v2/card_normalizer.py`, `nmbot_v2/fact_context.py`.

## Граница прототипа

- Только JSON, Markdown и Python stdlib.
- Никаких вызовов MCP, моделей, runtime, deploy или production.
- Никаких неподтверждённых выгод: каждый angle содержит `requires_all` / `requires_any`, а карточка содержит `required_evidence` и `forbidden_claims`.
- Технические поля не попадают в реестр: diagnostics, params/search envelopes, prompt/model metadata, missing/validation/trace state, near-result markers, planner actions, pending/contact/callback data, seller contacts, raw numeric state/status enums и raw-поля без canonical normalizer.

## Будущий workflow

`MCP → normalization → registry → compact brief → offline Answer Composer simulator`

1. MCP отдаёт структурированные факты.
2. Canonical normalizer превращает wire-поля в клиентские факты.
3. Registry выбирает только карточки, чьи доказательства реально есть на объекте.
4. Compact brief передаёт будущему Answer Composer только разрешённые смыслы, evidence и запреты.
5. Offline Answer Composer simulator готовит sanitized model-input package, валидирует сохранённый candidate и собирает клиентский текст только из секций candidate.

## Selection и ranking

- Выбирать только карточки с подтверждённым `required_evidence`.
- Сначала брать факты, которые прямо отвечают сценарию клиента: `family`, `commute`, `budget`, `comfort`, `safety`, `investment`, `parking`, `readiness`, `general`.
- `sales_strength=strong` — это не обещание выгоды, а сигнал, что поле может быть заметным аргументом при наличии evidence.
- Слабые или нейтральные поля рендерятся буквально: «есть такое-то поле», без вывода о качестве объекта.
- Комбинации брать только из `combinations.json`, если есть все required cards/evidence.

## Freshness

Динамические поля требуют свежего ответа MCP и не кэшируются как статический факт: price, inventory, mortgage, discount, parking inventory, sales/ads counts, lot availability. В JSON это помечено `freshness.policy=dynamic_mcp_required` и `cache_static=false`.

`sales_count` означает только буквальное число сделок ЕГРН. `ads_count` означает только буквальное число витринных объявлений. Из них нельзя выводить спрос, ликвидность, доходность, рост цены или лёгкость будущей продажи.

## Read-only compact brief builder

`brief_builder.py` принимает не raw MCP и не `OptionCard`, а уже
нормализованный словарь `{field_id: value}`. Он загружает карточки реестра,
отбрасывает отсутствующие, устаревшие и небезопасные значения, ранжирует поля
по сценарию и формирует JSON по `brief_schema.json`.

Публичный API:

```python
build_compact_brief(
    facts,
    scenario,
    fresh_mcp=False,
    requested_fields=(),
    max_fields=5,
    object_name=None,
)
```

CLI-пример:

```bash
python3 field_sales_registry/v1/brief_builder.py \
  field_sales_registry/v1/example_input.json \
  --scenario family --fresh-mcp \
  --requested school,kindergarten \
  --max-fields 5 --object-name "Синтетический ЖК"
```

Приоритеты: явно запрошенные поля → точный сценарий → `general` → сила
карточки → стабильный `field_id`. Лимит жёстко ограничен двенадцатью полями.
Динамические карточки проходят только с `fresh_mcp=true`. Benefit берётся только
из подходящего сценария; текст из чужого сценария не подставляется.

Diagnostics содержат только безопасные имена полей и причины исключения, но не
значения. Неизвестные ключи не становятся фактами. Числовой raw `lot_status`,
вложенные объекты, нечисловые counters и `house_link` как sales-аргумент
отбрасываются.

Builder по-прежнему **not wired to runtime**: он не импортирует V0/V2, не вызывает
MCP, модель, сеть или production. Внешний adapter ниже следует той же границе.

## Read-only OptionCard adapter

`option_card_adapter.py` структурно читает только разрешённые атрибуты
`OptionCard`/`LotExample` или одноимённые ключи `Mapping`. Он не импортирует V0/V2
и не просматривает `__dict__` целиком.

```python
adapt_option_card(card, lot_index=None)
build_brief_from_option_card(
    card,
    scenario,
    fresh_mcp=False,
    requested_fields=(),
    max_fields=5,
    lot_index=None,
)
```

Первый вызов возвращает envelope по `adaptation_schema.json`: `object_name`,
canonical `facts` и diagnostics без исходных значений. Второй возвращает
`{"adaptation": ..., "brief": ...}` и передаёт facts существующему builder.

Lot-поля не выбираются молча: без явного `lot_index` они не попадают в facts.
При выборе индекса все цена, площадь, этаж, комнатность, отделка и безопасный
статус берутся из одного лота. Числовой статус, отрицательные цена/площадь и
несвязанные house data блокируются. Наличие связи с корпусом отражается только
boolean diagnostics; `house_id`, название корпуса и `house_link` не становятся
sales-фактом.

`OptionCard.mortgage_terms` остаётся единой строкой. Adapter намеренно не извлекает
из неё `mortgage_rate`, `down_payment` или `installment_months`: для этого нужны
отдельные структурированные canonical поля. Diagnostics сообщает только имена
неразобранных фактов и не копирует финансовый текст.

Этот adapter также **not wired to runtime**. Он не читает raw MCP, не вызывает
модель, сеть или production и используется только напрямую или в focused tests.

## Offline Answer Composer simulator

`answer_composer_simulator.py` — внешний offline-контур для будущего Answer
Composer. Он живёт только в `field_sales_registry/v1`, использует stdlib и sibling
files, не импортирует V0/V2/runtime/contracts/card_normalizer/prompts/API/selector,
не вызывает модель, MCP, сеть, SSH или eval.

Цепочка такая:

`compact brief + exact CTA → model input package → saved candidate JSON → validation → assembled client text or fail-closed errors`

Публичный API:

```python
build_model_input(composer_input)
validate_candidate(composer_input, candidate)
assemble_candidate(candidate)
simulate(composer_input, candidate)
```

Composer input строгий: `schema_version=1`, `answer_goal=present_selected`, exact
`cta_template` и compact `brief`. В model-input package попадает только безопасная
проекция brief: scenario, object_name, fields, combinations, constraints и CTA.
`diagnostics`, `source_fields`, raw source paths, prompt/model metadata и неизвестные
ключи не передаются.

Candidate строгий и содержит только:

```json
{"intro":"...","fact_summary":"...","benefit":"...","caveat":"...","final_question":"...","used_field_ids":["school"],"used_combination_ids":[]}
```

Валидатор fail-closed проверяет форму, exact CTA, один вопрос в конце, grounding по
выбранным field/combination IDs, неизвестные числа, внутренние термины, контакты и
URL, консервативные unsupported claims, mismatch названия ЖК, duplicate sections и
лимит assembled text около 1200 символов. Важно: это не semantic proof —
`manual_review_required=true` остаётся всегда.

CLI:

```bash
python3 field_sales_registry/v1/answer_composer_simulator.py \
  --input field_sales_registry/v1/example_answer_composer_input.json \
  --print-model-input

python3 field_sales_registry/v1/answer_composer_simulator.py \
  --input field_sales_registry/v1/example_answer_composer_input.json \
  --candidate field_sales_registry/v1/example_answer_composer_candidate.json
```

Committed examples:

- `answer_composer_prompt.md` — prompt contract with Purpose/Inputs/Outputs/Priority/Forbidden/Owner/Validation headings;
- `answer_composer_input_schema.json` and `answer_composer_candidate_schema.json` — strict schemas;
- `example_answer_composer_input.json`, `example_answer_composer_candidate.json`, `example_answer_composer_result.json` — deterministic family example.

## Offline Answer Composer scenario matrix

`answer_composer_matrix.json` расширяет одиночный family example до пяти
детерминированных synthetic сценариев: `family`, `financing`, `parking`,
`investment` и `lot`. Матрица остаётся composer-only: она не импортирует V0/V2,
не вызывает coverage audit, runtime, модель, MCP, сеть, SSH или eval. Все входы,
candidate и результаты synthetic, без контактов, пользовательского текста,
секретов и raw payload.

Usage:

```bash
# dry run: проверяет committed report на точное совпадение с regeneration
python3 field_sales_registry/v1/run_answer_composer_matrix.py

# регенерация JSON report + human Markdown report
python3 field_sales_registry/v1/run_answer_composer_matrix.py --write
```

Runner переиспользует публичный `simulate()` из `answer_composer_simulator.py` и не
дублирует validator logic. Он падает с non-zero exit code, если expected-valid case
стал invalid, committed report не совпал с regeneration, появился forbidden
raw/PII-like content или `manual_review_required` оказался не `true`.

`manual_review_required=true` — жёсткая граница для всех пяти сценариев. Зелёная
матрица означает только, что offline candidate прошёл синтаксические, grounding и
safety checks симулятора; она не доказывает семантическую полноту, актуальность
цены/условий/статуса и не подключает Answer Composer к production.

Committed outputs:

- `answer_composer_matrix_report.json` — deterministic simulator result per case;
- `reports/FIELD_SALES_REGISTRY_ANSWER_COMPOSER_MATRIX_20260721.md` — human report
  with client-visible texts, safety notes and source refs, without diagnostics or
  raw values beyond synthetic public numbers.

## Structured finance contract

`structured_finance_schema.json` описывает отдельный внешний envelope для трёх
числовых финансовых фактов. Это не raw MCP и не разбор строки `mortgage_terms`:

```json
{
  "schema_version": 1,
  "object_name": "Синтетический финансовый ЖК",
  "fresh_mcp": true,
  "facts": {
    "mortgage_rate": {
      "value": 6.0,
      "source_field": "mortgage_calc.min_percent"
    },
    "down_payment": {
      "value": 20.0,
      "source_field": "mortgage_calc.min_fee"
    },
    "installment_months": {
      "value": 18,
      "source_field": "payment_by_installments.month"
    }
  }
}
```

Ставка принимает только `mortgage_calc.min_percent` или
`mortgage.year_percent`. Первоначальный взнос принимает только
`mortgage_calc.min_fee` или `mortgage.min_fee` и всегда означает процент, а не
сумму денег клиента. Срок рассрочки принимается только из
`payment_by_installments.month`; `mortgage.credit_month` — срок ипотеки и не
подменяет рассрочку.

`structured_finance_adapter.py` принимает только числовые проценты и целое
положительное число месяцев. Строки, boolean, отрицательные и non-finite значения
закрываются без факта. Для объединения с карточкой должны одновременно совпасть
нормализованное имя ЖК, `fresh_mcp` envelope и `fresh_mcp` wrapper.

```python
adapt_structured_finance(payload)
build_brief_with_structured_finance(
    card,
    finance_payload,
    scenario,
    fresh_mcp=True,
    requested_fields=(),
    max_fields=5,
    lot_index=None,
)
```

Базовый OptionCard audit по-прежнему показывает 31 из 35 полей: он измеряет
только `option_card_adapter.py`. Отдельный structured finance path добавляет ещё
три поля, поэтому объединённый внешний прототип может представить 34 из 35;
`house_link` остаётся provenance-only. Ни один из этих слоёв не подключён к
runtime.

## Offline coverage audit

`coverage_audit.py` — детерминированная локальная проверка покрытия внешнего
реестра `field_sales_registry/v1` текущим read-only adapter. Она работает только
с локальными файлами этого каталога:

- `coverage_corpus.json` — маленький synthetic corpus без PII и без outreach/runtime
  envelope data;
- десять domain JSON modules реестра;
- `option_card_adapter.py` и `brief_builder.py` как sibling files, без package install;
- `coverage_report.json` и `reports/FIELD_SALES_REGISTRY_COVERAGE_20260721.md` как
  детерминированные outputs.

Usage:

```bash
# dry run: ничего не меняет, печатает компактную сводку
python3 field_sales_registry/v1/coverage_audit.py

# полный JSON в stdout, без записи файлов
python3 field_sales_registry/v1/coverage_audit.py --json

# регенерация committed JSON + Markdown report
python3 field_sales_registry/v1/coverage_audit.py --write
```

Audit падает с non-zero exit code, если:

- registry IDs не раскладываются строго в observed reachable union + expected gaps;
- adapter эмитит неизвестные ID или corpus не покрывает reachable ID;
- corpus содержит запрещённые ключи вроде outreach/runtime envelope categories или
  PII-like строки;
- JSON/Markdown report протаскивает contact-like значения или raw finance terms.

Текущий ожидаемый контракт: в реестре 35 полей, adapter-reachable 31 поле,
четыре намеренно unreachable поля: `mortgage_rate`, `down_payment`,
`installment_months` и `house_link`. Первые три требуют структурированных finance
facts вместо free-form `mortgage_terms`; `house_link` остаётся provenance-only
diagnostic и не является sales-фактом.

Этот audit также **not wired to runtime**: он не импортирует V0/V2, prompts, API,
selector, services, deploy-код, не читает logs/backups/state/raw fixtures и не
делает model/MCP/network/SSH/eval calls.

## Примеры безопасного использования

- Если есть `ready` и `finishing`: «Дом уже сдан, есть отделка — можно не ждать окончания стройки и заранее оценить объём работ до переезда».
- Если есть `school` и `kindergarten`: «Рядом отмечены школа и детский сад — это может упростить семейную логистику, а расстояние и наличие мест лучше проверить отдельно».
- Если есть `lot_full_price`, `lot_area`, `lot_floor`: «По конкретному лоту известны полная цена, площадь и этаж — можно сравнивать не общий диапазон ЖК, а конкретную квартиру».

## Чего нельзя говорить

- Нельзя обещать доходность, рост цены, ликвидность, высокий спрос или будущую продажу.
- Нельзя превращать `sales_count` и `ads_count` в инвестиционный прогноз.
- Нельзя использовать старый кэш для цены, наличия, ипотеки, скидок, паркинга и конкретных лотов.
- Нельзя показывать seller contacts, raw status/state enums, diagnostics, params, prompt/model metadata или trace state.
