# NMBOT IntentPlan V3 — подробный план упрощения runtime

Дата: 2026-07-21  
Статус: Phase 5B подключена; V3 включён в production opt-in режимом, legacy switch оставлен для rollback  
Целевая среда: authoritative Jivo V2 (`novostroy-bot-api.service`)  
Основной принцип: **LLM владеет смыслом запроса; код владеет фактами, state, безопасностью и исполнением; renderer владеет формулировкой.**

### Текущий runtime-статус, 2026-07-21

- `IntentPlanV3` подключён в `scripts/nmbot_runtime_adapter.py` через
  `NMBOT_INTENT_PLAN_VERSION`.
- На production VPS установлено `NMBOT_INTENT_PLAN_VERSION=v3`.
- `novostroy-bot-api.service` перезапущен и live synthetic Jivo smoke вернул
  `BOT_MESSAGE`.
- Production trace подтвердил `schema_version=3`, `canonical_valid=True` и
  `fallback_used=False`.
- Backup: `backups/deploy-intent-v3-20260721-085140`.
- V2 остаётся доступным как rollback через `NMBOT_INTENT_PLAN_VERSION=v2`.

Это не означает завершение всей миграции: goal-owned ResponsePlan, полный
stateful production dialogue matrix и удаление legacy decision layers остаются
последующими фазами.

### Read-only current-options probe, 2026-07-21

Проверен один production-shaped batch-запрос через актуальный локальный typed
contract к тому же Overmind/MCP пути:

```text
current_option_names =
  Бусиновский парк,
  Лосиноостровский парк,
  Мичуринский парк
facts_needed = parks, schools
search_mode = current_options_fact_check
```

Результат:

- `facts=3`, `near=0`, `missing=0`;
- все три exact ЖК вернулись без посторонних объектов;
- по каждому пришли `school=1`, `kindergarten=1`, `park_near=1`,
  `water_near=1`;
- также пришли location, min/max price, delivered, ready quarter и built year;
- contract и exact-name scope validation — `valid`;
- одного batch-запроса достаточно, чтобы собрать плотный grounded shortlist.

Важное ограничение: MCP вернул только булевы/числовые признаки наличия парка и
водоёма. Название парка и расстояние до него не пришли, поэтому runtime не имеет
права выбирать «самый близкий парк» или придумывать название.

Найденный дефект находится на границе normalizer: `card_normalizer.py` проверяет
инфраструктурные флаги только через `value is True`, поэтому wire-значение `1`
теряется и `OptionCard.infrastructure` становится пустым. До исправления этой
границы renderer не видит уже подтверждённые факты. Это не underfill MCP и не
ошибка planner.

После сохранения числовых флагов существующий `_benefit_reason()` уже может
собрать уникальные family-акценты по карточкам. Но если факты одинаковы у всех
трёх ЖК, ответ обязан сказать, что по этому критерию варианты сопоставимы, а не
создавать искусственное различие.

---

## 1. Для чего нужен этот план

Сейчас один пользовательский ход проходит через несколько слоёв, способных
изменить его смысл:

```text
user
→ LLM planner
→ normalize_semantic_planner_result
→ derive_runtime_decision
→ SemanticPlan adapter
→ derive_transition
→ runtime action
→ scenario recipe
→ conversation fallback
→ renderer
```

Production-диалог 2026-07-21 показал конкретный сбой этой схемы:

```text
«какой лучше?»
→ planner распознал сравнение, но выбрал clarification

«поближе к паркам»
→ planner выделил parks=true, requested_facts=[parks], requires_enrichment=true
→ derive_runtime_decision раньше времени выбрал current_options
→ search/enrichment был запрещён
→ intent стал unknown
→ conversation fallback повторил три карточки и одинаковую заглушку
```

Live evidence:

- session ref: `sha256:52f092973872bded`;
- VPS `logs/dialogue_journal.jsonl:872-879`;
- VPS `logs/planner_trace-2026-07-21.jsonl:26-28`;
- VPS `logs/model_payload_metrics-2026-07-21.jsonl:50-53`.

Цель V3 — оставить только одну семантическую точку решения и не позволять
downstream-слоям повторно угадывать намерение клиента.

---

## 2. Обязательные ограничения

Исполнитель не имеет права нарушать следующие правила.

### 2.1. Не добавлять сложность

- Не добавлять вторую planner-модель.
- Не добавлять скрытый второй conversation state.
- Не добавлять regex-router для живых фраз.
- Не добавлять циклический agent loop.
- Не добавлять больше одного planner-вызова на ход.
- Не добавлять unbounded search/enrichment retry.
- Не создавать новый presenter поверх существующего renderer.

### 2.2. Не отдавать LLM кодовые обязанности

LLM не должна:

- изменять state напрямую;
- выбирать технический MCP endpoint;
- решать, какие поля считать подтверждёнными;
- придумывать факты;
- сохранять телефон;
- выполнять reset;
- разрешать handoff без consent;
- определять retry count;
- формировать raw provider payload.

### 2.3. Сохранить safety-инварианты

- Только MCP/search и сохранённые MCP-факты являются источником фактов.
- `facts` и `near` не смешиваются.
- Выбранный ЖК обязан совпадать с exact canonical name из `visible_options`.
- Не больше трёх клиентских карточек.
- Ровно один финальный вопрос.
- Ошибка planner/search/enrichment не сбрасывает успешный прежний state.
- Не показывать JSON, dict, enum, diagnostics, prompts и internal field names.
- Phone/reset/dedup остаются детерминированными transport guards.
- Operator contact flow остаётся детерминированной state machine.

### 2.4. Не закрывать задачу только локальными тестами

Готовность требует:

1. focused pytest;
2. full pytest;
3. production backup;
4. deploy только затронутых runtime-файлов;
5. restart только `novostroy-bot-api.service`, если bridge не менялся;
6. live health и hashes;
7. stateful direct API/Jivo smoke;
8. свежий `bot_error_events` delta;
9. реальный widget/bridge gate перед окончательным закрытием гипотезы.

---

## 3. Целевая архитектура

```text
Transport guards
(/start, phone, dedup, malformed transport)
        ↓
LLM IntentPlanV3
(единственный semantic owner)
        ↓
Schema / State Validator
(принимает или отклоняет; не меняет goal)
        ↓
Evidence Executor
(state, MCP, enrichment, consent, bounded retry)
        ↓
ResponsePlan
(готовые grounded cards, missing facts, next step)
        ↓
Deterministic Renderer
(только клиентская формулировка)
```

Технически функций может остаться больше пяти. Важно не количество функций, а
право менять смысл. После V3 только planner создаёт semantic goal.

---

## 4. Ownership matrix V3

| Решение | Единственный владелец | Запрещено остальным |
|---|---|---|
| Что хочет клиент | LLM planner | Переопределять goal по наличию карточек |
| Какие факты нужны | LLM planner | Удалять requested facts без validation error |
| Новые / текущие / выбранный ЖК | LLM planner | Незаметно менять scope |
| Допустим ли plan | validator | Исправлять plan молча |
| Где взять факты | Evidence Executor | Renderer не вызывает search |
| Что сохранить в state | runtime/state reducer | LLM не пишет state patch напрямую |
| Как звучит ответ | ResponsePlan + renderer | Planner не пишет клиентский текст |
| Consent/phone/handoff | code-level state machine | LLM не обходит pending flow |

---

## 5. Новый контракт `IntentPlanV3`

### 5.1. Размещение

Добавить типы в:

```text
nmbot_v2/contracts.py
```

Не создавать отдельный пакет или новый framework.

### 5.2. Enum `IntentGoal`

Использовать явные значения, чтобы простой model не комбинировал несколько
пересекающихся флагов:

```python
class IntentGoal(str, Enum):
    NEW_SEARCH = "new_search"
    REFINE_SEARCH = "refine_search"
    EXPAND_SEARCH = "expand_search"
    LOOKUP_OBJECT = "lookup_object"
    ANSWER_CURRENT = "answer_current"
    COMPARE_CURRENT = "compare_current"
    RECOMMEND_CURRENT = "recommend_current"
    ANSWER_SELECTED = "answer_selected"
    OPERATOR = "operator"
    CLARIFY = "clarify"
    RESUME_PENDING = "resume_pending"
    OFF_TOPIC = "off_topic"
```

Не добавлять одновременно `operation`, `action`, `dialog_action` и
`search_policy`. Они дублируют `goal` и должны вычисляться кодом.

### 5.3. Поля `IntentPlanV3`

```python
@dataclass(frozen=True)
class IntentPlanV3:
    schema_version: int
    goal: IntentGoal
    viewpoint: str
    selected_option_name: str | None = None
    named_object_reference: str | None = None
    requested_facts: tuple[str, ...] = ()
    constraints_delta: JsonDict = field(default_factory=dict)
    operator_consent: bool | None = None
    explicit_operator_request: bool = False
    clarification: str | None = None
    confidence: float = 1.0
    query_text: str | None = None
```

### 5.4. Кто заполняет поля

| Поле | Источник |
|---|---|
| `schema_version` | LLM, строго `3` |
| `goal` | LLM |
| `viewpoint` | LLM: `family/life/rental/investment/financing/unchanged` |
| `selected_option_name` | LLM, только exact name из переданного списка |
| `named_object_reference` | LLM, только если клиент явно назвал новый ЖК |
| `requested_facts` | LLM из allowlist |
| `constraints_delta` | LLM; только изменения текущего хода |
| `operator_consent` | LLM для semantic yes/no |
| `explicit_operator_request` | LLM |
| `clarification` | LLM только при `goal=clarify` |
| `confidence` | LLM, `0..1` |
| `query_text` | **только код**, оригинальный текущий user turn |

### 5.5. Что удалить из semantic output

Следующие поля нельзя просить у модели V3:

```text
action
dialog_action
target
scope
search_policy
needs_search
needs_enrichment
requires_enrichment
facts_needed
focus_action
intent_policy
context_source
reason
raw_legacy_operation
refers_to_existing_objects
requests_new_objects
```

Причина: эти поля либо дублируют goal, либо описывают техническое исполнение.

---

## 6. JSON Schema planner-а

### 6.1. Требование

Planner должен возвращать один JSON object без markdown и свободного текста.

Пример schema:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "goal",
    "viewpoint",
    "selected_option_name",
    "named_object_reference",
    "requested_facts",
    "constraints_delta",
    "operator_consent",
    "explicit_operator_request",
    "clarification",
    "confidence"
  ],
  "properties": {
    "schema_version": {"const": 3},
    "goal": {
      "enum": [
        "new_search",
        "refine_search",
        "expand_search",
        "lookup_object",
        "answer_current",
        "compare_current",
      "recommend_current",
      "answer_selected",
      "answer_open_question",
      "operator",
        "clarify",
        "resume_pending",
        "off_topic"
      ]
    },
    "viewpoint": {
      "enum": ["family", "life", "rental", "investment", "financing", "unchanged"]
    },
    "selected_option_name": {"type": ["string", "null"]},
    "named_object_reference": {"type": ["string", "null"]},
    "requested_facts": {
      "type": "array",
      "items": {"type": "string"},
      "maxItems": 12,
      "uniqueItems": true
    },
    "constraints_delta": {"type": "object"},
    "operator_consent": {"type": ["boolean", "null"]},
    "explicit_operator_request": {"type": "boolean"},
    "clarification": {"type": ["string", "null"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}
  }
}
```

Если текущий provider не поддерживает native strict schema, тот же контракт
проверяется локально. Не создавать второй формат JSON.

---

## 7. Правила planner prompt

Изменяемый файл:

```text
followup_intent_classifier.py
```

### 7.1. Системная инструкция модели

Planner отвечает только на вопрос:

> Что клиент хочет сделать на этом ходу?

Planner не пишет клиентский ответ и не решает, нужен ли технически MCP.

### 7.2. Обязательные различия goals

| Реплика | Goal |
|---|---|
| «подбери двушку» | `new_search` |
| «теперь до 15 млн» после списка | `refine_search` |
| «покажи ещё другие» | `expand_search` |
| «что по ЖК Дюна?» вне списка | `lookup_object` |
| «расскажи про варианты» | `answer_current` |
| «сравни эти ЖК» | `compare_current` |
| «какой лучше?» | `recommend_current` |
| «поближе к паркам» после вопроса о лучшем | `recommend_current`, facts=`parks` |
| «второй подробнее» | `answer_selected` + exact selected name |
| «позови оператора» | `operator` |
| смысл действительно неясен | `clarify` |
| «вернёмся к заявке» при pending contact | `resume_pending` |
| вопрос не по недвижимости | `off_topic` |

### 7.3. Правило clarification

`clarification` разрешено только если `goal=clarify`.

Запрещено возвращать одновременно:

```text
goal=recommend_current
clarification="..."
```

Если для рекомендации не указан критерий, вернуть `goal=clarify`, но передать в
planner context, что уточнение относится к рекомендации текущих вариантов.
Следующий ход обязан получить полный `last_turn`.

### 7.4. Few-shot минимум

Добавить примеры:

1. первый поиск;
2. refinement;
3. expand без повторов;
4. compare;
5. recommendation без критерия;
6. ответ критерием после clarification;
7. named lookup;
8. selected typo reference → exact current name;
9. operator accept/decline;
10. contact interruption/resume;
11. financing overlay;
12. off-topic.

---

## 8. Validator: проверять, но не переосмысливать

Основной файл:

```text
nmbot_v2/semantic_planner.py
```

### 8.1. Новая функция

```python
def validate_intent_plan_v3(
    raw: Mapping[str, Any],
    state: ConversationState,
    *,
    allowed_facts: set[str],
) -> IntentPlanValidation:
    ...
```

### 8.2. Проверки

- schema version = 3;
- goal входит в enum;
- viewpoint входит в enum;
- requested facts входят в allowlist;
- constraints проходят существующий canonical normalizer;
- selected name — exact member `visible_options`;
- `lookup_object` требует `named_object_reference`;
- `answer_selected` требует selected name;
- `clarify` требует один непустой вопрос;
- не-`clarify` требует `clarification=null`;
- operator consent допустим только для operator/pending flow;
- confidence ограничен `0..1`;
- неизвестные поля запрещены.

### 8.3. Результат validation

```python
@dataclass(frozen=True)
class IntentPlanValidation:
    ok: bool
    plan: IntentPlanV3 | None
    errors: tuple[str, ...]
    repairable: bool
```

### 8.4. Запрет

Validator не имеет права делать следующее:

```python
if goal == recommend_current:
    goal = clarify
```

Допустимы только outcomes:

```text
accepted
repair_required
rejected_safe_clarification
```

### 8.5. Repair

- Максимум один repair planner call.
- Repair получает только schema errors и безопасный bounded context.
- Если repair не помог, state не меняется.
- Клиент получает один безопасный уточняющий вопрос.
- Repair нельзя запускать для provider timeout: это техническая ошибка, а не ошибка schema.

---

## 9. Mechanical transition map

Файл:

```text
nmbot_v2/transition.py
```

Заменить semantic branching на статическую таблицу:

```python
GOAL_TRANSITIONS = {
    IntentGoal.NEW_SEARCH: (Stage.FIRST_LIST, TurnAction.SEARCH),
    IntentGoal.REFINE_SEARCH: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.EXPAND_SEARCH: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.LOOKUP_OBJECT: (Stage.REFINEMENT, TurnAction.SEARCH),
    IntentGoal.ANSWER_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.COMPARE_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.RECOMMEND_CURRENT: (Stage.CURRENT_OPTIONS, TurnAction.ANSWER_FROM_CURRENT_OPTIONS),
    IntentGoal.ANSWER_SELECTED: (Stage.SELECTED_OBJECT, TurnAction.ANSWER_SELECTED_OPTION),
    IntentGoal.OPERATOR: (Stage.OPERATOR_HANDOFF, TurnAction.OFFER_OPERATOR),
    IntentGoal.CLARIFY: (Stage.FREEFORM, TurnAction.FREEFORM),
    IntentGoal.RESUME_PENDING: (Stage.FREEFORM, TurnAction.FREEFORM),
    IntentGoal.OFF_TOPIC: (Stage.OFF_TOPIC, TurnAction.ANSWER_OFF_TOPIC),
}
```

### 9.1. Допустимые guards перед таблицей

Только:

- reset;
- pending reply contract;
- invalid selected name;
- operator consent state;
- malformed plan.

Нельзя проверять `has_visible` и на этом основании менять goal.

---

## 10. Evidence Executor

Основной файл:

```text
nmbot_v2/runtime.py
```

Поддерживающие файлы:

```text
nmbot_v2/fact_context.py
nmbot_v2/search_contract.py
scripts/nmbot_runtime_adapter.py
```

### 10.1. Общий алгоритм

```python
async def execute_goal(plan, state):
    transition = GOAL_TRANSITIONS[plan.goal]

    if plan.goal in SEARCH_GOALS:
        return await execute_search(plan, state)

    if plan.goal in CURRENT_GOALS:
        return await execute_current_goal(plan, state)

    if plan.goal == ANSWER_SELECTED:
        return await execute_selected_goal(plan, state)

    if plan.goal == OPERATOR:
        return await execute_operator_goal(plan, state)

    return deterministic_non_search_result(plan, state)
```

### 10.2. Поиск

| Goal | Поведение |
|---|---|
| `new_search` | search по текущему turn |
| `refine_search` | merge state params + current delta |
| `expand_search` | те же условия + excluded visible/previous names |
| `lookup_object` | exact named lookup, count=1, без подмены похожим ЖК |

Сохранить уже внедрённый bounded underfilled fill:

- primary search;
- максимум один supplemental search;
- те же hard constraints;
- excluded existing names;
- failure supplemental не отменяет primary result.

### 10.3. Current goals

Goals:

```text
answer_current
compare_current
recommend_current
```

Алгоритм:

```python
cards = state.visible_options[:3]

if not cards:
    return safe_clarification_without_state_mutation()

availability = facts_available_by_card(cards, plan.requested_facts)

if evidence_is_sufficient(plan.goal, availability):
    return answer_from_current_cards(cards)

enriched = await bounded_current_options_fact_check(
    cards=cards,
    facts_needed=missing_facts,
)

if enriched.added_evidence:
    return answer_from_current_cards(enriched.cards)

return answer_with_honest_missing_boundary(cards, missing_facts)
```

### 10.4. Достаточность evidence

- `answer_current`: достаточно хотя бы одной карточки с запрошенным фактом, если ответ не заявляет сравнение всех.
- `compare_current`: для сравнительного вывода факт должен быть известен минимум по двум карточкам.
- `recommend_current`: критерий должен быть известен минимум по двум карточкам; иначе нельзя называть лучшую.
- Если requested facts пусты, использовать только уже известные безопасные различия: цена, готовность, метро, отделка, расположение.
- Название ЖК со словом «парк» не является evidence наличия парка рядом.

### 10.5. Bounded current-options fact check

Нужен один typed search mode, а не три последовательных selected enrichment.

Расширить `V2SearchRequest`:

```python
search_mode: Literal[
    "broad",
    "named_object",
    "current_options_fact_check",
]
current_option_names: tuple[str, ...] = ()
facts_needed: tuple[str, ...] = ()
```

Правила `current_options_fact_check`:

- максимум три canonical names;
- максимум один gateway request;
- output facts/near может содержать только names из запроса;
- новый посторонний ЖК блокирует validation;
- порядок visible options не меняется;
- enrichment merge добавляет только подтверждённые поля;
- отсутствие результата не очищает старые карточки;
- provider/parse/validation error не меняет state;
- trace содержит counts и safe field names, но не raw payload.

### 10.6. Merge enriched cards

Использовать существующий `merge_option_cards()`.

Перед merge проверить canonical identity. После merge:

- сохранить `visible_options` order;
- сохранить `selected_option_name`;
- не затирать заполненное поле пустым значением;
- не переносить поле одного ЖК в другой;
- не превращать `near` в `facts`;
- записать только safe enrichment summary.

---

## 11. State rules

Файл:

```text
nmbot_v2/state.py
```

### 11.1. Не добавлять новый state без необходимости

Использовать существующие:

```text
params
visible_options
previous_options
selected_option_name
selected_enriched
active_topic
pending_followup
dialog_focus
last_assistant_question
last_answer_kind
```

### 11.2. Viewpoint

Если V3 возвращает `viewpoint=unchanged`, runtime обязан взять
`state.active_topic`. Нельзя превращать это в `unknown`, если active topic есть.

### 11.3. Ошибки

При planner/search/enrichment error:

- `accepted_state=False` для опасного изменения;
- предыдущие params/options/selection сохраняются;
- ответ объясняет техническую границу человеческим языком;
- один следующий вопрос;
- никакого автоматического reset.

### 11.4. Pending flow

Pending state machine имеет приоритет только для ответа на ожидаемый вопрос.

Если клиент задаёт содержательный вопрос о недвижимости во время contact flow:

- ответить на вопрос;
- не стирать `contact_name/contact_phone` pending;
- после ответа разрешить явный `resume_pending`;
- не переименовывать исходный запрос в mortgage/другой topic.

---

## 12. ResponsePlan и renderer

Основной файл:

```text
nmbot_v2/response.py
```

Дополнительный файл, который должен быть уменьшен или удалён после миграции:

```text
nmbot_v2/conversation.py
```

### 12.1. ResponsePlan получает готовую семантику

Добавить/использовать поля:

```text
goal
viewpoint
cards
requested_facts
available_facts
missing_facts
answer_kind
final_question
```

Renderer не определяет goal по `intent`, тексту пользователя или наличию
карточек.

### 12.2. Удалить generic client fallback

Запрещён fallback:

```text
«Отвечаю по текущему списку без нового поиска.»
```

Запрещено повторять для каждой карточки:

```text
«Можно выбрать этот вариант и запросить подробную информацию...»
```

Если evidence не хватает, один общий блок:

```text
«По текущим вариантам пока не вижу подтверждённых сведений о парках,
поэтому выбирать лучший наугад не буду.»
```

### 12.3. Отдельные templates по goal

- `answer_current`: прямой ответ без обязательного повтора всех карточек;
- `compare_current`: ось сравнения → отличия → итог;
- `recommend_current`: критерий → лучший подтверждённый вариант → почему;
- `answer_selected`: facts выбранного ЖК → missing boundary;
- `clarify`: acknowledgement + ровно один вопрос;
- `off_topic`: короткая граница + возврат к недвижимости.

### 12.4. Scenario recipes

Recipes остаются presentation-конфигурацией. Они не выбирают goal и не меняют
search policy.

Recipe lookup key:

```text
(goal, viewpoint, answer_kind)
```

Нельзя использовать recipe как дополнительный router.

### 12.5. Structured presentation JSON contract

После того как runtime получил canonical facts и собрал `ResponseBrief`,
communication model, если она используется для формулировки, возвращает только
структурированные части ответа. Модель не меняет goal, facts, state, recipe или
CTA.

```json
{
  "intro": "...",
  "options": [{"name": "...", "facts": "...", "description": "..."}],
  "recommendation": "...",
  "missing_note": "...",
  "final_question": "..."
}
```

Правила полей:

- `intro` — 1–2 живых предложения: хорошая новость и общая польза текущего
  ответа, без вопроса и без технических слов;
- `options` — максимум три canonical cards в исходном порядке;
- `options[].name` — точное имя из canonical card;
- `options[].facts` — компактные подтверждённые факты, релевантные текущему
  запросу;
- `options[].description` — мини-презентация по формуле
  `подтверждённый факт → реальное отличие → польза клиенту`;
- `recommendation` — обязательная непустая рекомендация для
  `recommend_current`; если исходный критерий одинаков у всех, используется
  подтверждённый secondary tie-breaker;
- `missing_note` — только мягкая человеческая граница, если она мешает выводу;
  внутренние технические verdicts запрещены;
- `final_question` — ровно один конкретный следующий вопрос.

Внутренний verdict переводится в клиентскую подачу:

```text
good news → benefit per ЖК → real difference → recommendation → next step
```

Для `recommend_current` запрещено заменять `recommendation` фразой «варианты
сопоставимы» или «точного расстояния нет». Эти сведения остаются внутренними
ограничениями. Единый golden reference: `docs/GOLDEN_DIALOGS.md`, Example 7.

### 12.6. Один JSON, разные сценарные профили

Customer JSON из §12.5 един для всех ответов. Нельзя создавать отдельные
response schema для `family`, `life`, `rental`, `investment`, неизвестного
вопроса или каждого нового пользовательского критерия. Сценарий меняет правила
заполнения полей, но не их состав.

Разделяются две независимые оси:

- `answer_goal` — что сделать сейчас: ответить, сравнить, рекомендовать,
  уточнить или ответить на открытый вопрос;
- `viewpoint` — с какой покупательской позиции выбирать факты и объяснять
  пользу: `family`, `life`, `rental`, `investment`, `financing` или `neutral`.

Примеры допустимых сочетаний:

```text
recommend_current + family + requested_facts=[parks]
compare_current   + life   + requested_facts=[metro, readiness]
answer_selected   + neutral + requested_facts=[developer]
answer_open_question + neutral + requested_facts=[wind_comfort]
```

Сценарий является quality overlay, а не обязательным пропуском к ответу.
Отсутствие известного viewpoint не означает, что понятный вопрос клиента нужно
заменить общей анкетой или вопросом про бюджет.

### 12.7. Presentation profile

Runtime после выбора goal и recipe собирает ограниченный presentation profile.
Это инструкция по заполнению единого JSON, а не второй router:

```json
{
  "answer_goal": "recommend_current",
  "viewpoint": "family",
  "requested_facts": ["parks"],
  "fact_priority": ["parks", "schools", "kindergartens", "apartment_price", "location"],
  "allowed_benefits": {
    "parks": "удобно для прогулок",
    "schools": "проще организовать семейные будни",
    "apartment_price": "помогает сохранить семейный бюджет"
  },
  "forbidden_inferences": [
    "точное расстояние до парка без distance evidence",
    "лучший район без соответствующего evidence",
    "инвестиционный спрос или доходность"
  ],
  "recommendation_rule": "requested_fact_then_secondary_tiebreaker",
  "final_question_policy": "compare_top_options_by_next_available_fact"
}
```

Profile формируется только кодом из accepted plan, executable recipe,
canonical cards и evidence result. Communication model не создаёт и не меняет
его.

Минимальные источники profile:

```text
accepted goal
+ viewpoint / active topic
+ requested_facts из буквального вопроса клиента
+ recipe.fact_priority / benefits / forbidden
+ available canonical fields
+ missing facts
+ validated CTA policy
```

### 12.8. Как заполняется `options[].facts`

В shared prompt не должно быть жёсткого правила «всегда покажи цену, парк,
школу и детский сад». Состав строки выбирается детерминированно:

1. факты, которые клиент запросил явно;
2. доступные факты из `fact_priority` текущего viewpoint;
3. один реальный comparative tie-breaker, если goal требует сравнения или
   рекомендации;
4. максимум 1–3 релевантных значения плюс локация, без пересказа всей карточки;
5. только поля, присутствующие в projected canonical card.

Сценарные приоритеты:

| Viewpoint | Приоритет фактов | Допустимая практическая польза |
|---|---|---|
| `family` | schools, kindergartens, parks, yard, price, readiness | семейные маршруты, прогулки, бюджет, планирование переезда |
| `life` | metro, location, readiness, finishing, price | ежедневный маршрут, ожидание, объём ремонта, бюджет |
| `rental` | room_formats, area, finishing, readiness, metro, price | подготовка квартиры и понятность маршрута; без прогноза спроса |
| `investment` | price, readiness, sales_count, ads_count, finishing | порог входа, горизонт ожидания, буквальные счётчики; без прогноза результата |
| `financing` | только доступные financing facts поверх base viewpoint | буквальные условия; без выдуманных ставок и одобрения |
| `neutral` | requested facts, затем location, price, readiness, metro, finishing | буквальное значение факта без придуманной buyer persona |

`description` строится по одному правилу:

```text
available fact → подтверждённое отличие → польза в текущем viewpoint
```

Если отличие не найдено, модель не обязана искусственно делать каждую карточку
уникальной. Она может объяснить роль локации или честно оставить общий факт, но
не придумывать качество района, расстояние, спрос или удобство.

### 12.9. Recommendation policy

Для `recommend_current` runtime передаёт модели уже ограниченную политику
выбора:

```text
1. Сначала сравнить варианты по requested_facts.
2. Если requested fact реально различает варианты — выбрать по нему.
3. Если он одинаков у всех — использовать доступный secondary tie-breaker.
4. Допустимые tie-breakers: цена, локация, готовность, метро, отделка или другой
   canonical факт, разрешённый viewpoint.
5. Если ни одного подтверждённого различия нет — не выдумывать победителя.
```

При отсутствии tie-breaker ответ остаётся полезным: `intro` сообщает общую
сильную сторону, `options` кратко объясняют варианты, `recommendation` предлагает
с чего начать только если такой выбор evidence-grounded, а `final_question`
запрашивает один признак, который действительно позволит развести варианты.

Модель не выбирает критерий рекомендации самостоятельно и не перекладывает
выбор обратно на клиента, если runtime уже передал допустимый tie-breaker.

### 12.10. Универсальный маршрут для непредусмотренного вопроса

Закрытый набор recipes не должен означать закрытый набор вопросов. Для
понятного вопроса о недвижимости, которому не соответствует специальный
сценарий, используется goal `answer_open_question` (целевой контракт; до
реализации не считать существующим runtime behavior).

Planner сохраняет буквальный смысл вопроса:

```json
{
  "goal": "answer_open_question",
  "question_subject": "ветровая обстановка между корпусами",
  "selected_option_name": "Бусиновский парк",
  "requested_facts": ["wind_comfort"]
}
```

Далее Evidence Executor проверяет по порядку:

1. есть ли ответ в текущей canonical card;
2. есть ли он в уже полученном safe context;
3. поддерживает ли этот fact typed MCP contract;
4. нужен ли один bounded fact-check;
5. является ли fact недоступным или неподдерживаемым.

После этого `ResponseBrief` получает универсальные поля:

```json
{
  "answer_goal": "answer_open_question",
  "user_question": "А зимой там сильно дует между домами?",
  "question_subject": "ветровая обстановка между корпусами",
  "requested_facts": ["wind_comfort"],
  "available_facts": [],
  "missing_facts": ["wind_comfort"],
  "response_policy": "operator_phone_request"
}
```

Допустимые `response_policy`:

- `answer_directly` — нужные сведения доступны;
- `operator_phone_request` — нужный факт недоступен: Ирина передаёт вопрос
  оператору и завершает ответ прямым запросом номера телефона;
- `clarify_once` — вопрос действительно двусмыслен и без уточнения нельзя
  определить объект или требуемый факт.

При `operator_phone_request` Ирина одной человеческой фразой обозначает границу,
говорит, что вопрос уточнит оператор, и последним предложением просит номер
телефона. Нельзя отправлять клиента к застройщику, на сайт, в офис или «уточнять
на месте»; нельзя подставлять нерелевантный family/life шаблон.

### 12.11. Fallback matrix

| Что удалось определить | Действие |
|---|---|
| goal + известный viewpoint | Применить соответствующий scenario overlay |
| goal + явный requested fact, viewpoint неизвестен | Использовать `neutral` profile и отвечать только по requested fact |
| понятный open question + доступный fact | `answer_open_question` + `answer_directly` |
| понятный open question + fact недоступен | `answer_open_question` + `operator_phone_request`; финальный запрос номера |
| непонятен объект или смысл вопроса | `clarify_once`, ровно один конкретный вопрос |
| вопрос вне недвижимости | существующий `off_topic` contract |

Главное запрещённое поведение:

```text
unknown scenario → передать все поля → позволить модели самой придумать цель,
buyer persona, критерий рекомендации и факты
```

Правильное поведение:

```text
unknown scenario → сохранить буквальный вопрос → извлечь subject/requested facts
→ ограничить evidence → ответить напрямую или передать missing-вопрос оператору
с финальным запросом номера
```

### 12.12. Полный pipeline composition

```text
user message
  → planner: goal + viewpoint + subject + requested_facts
  → exact validation
  → transition: stage/action
  → recipe resolver (optional quality overlay)
  → Evidence Executor / максимум один bounded fact-check
  → canonical projection
  → presentation profile
  → ResponseBrief
  → structured customer JSON
  → schema parsing + advisory evidence/claim validation warnings
  → deterministic assembly
  → safe fallback из того же ResponseBrief
```

Scenario recipe подключается один раз до `ResponseBrief`. Composer не запускает
повторный resolver и не определяет сценарий заново. Deterministic fallback и
communication model используют один presentation profile, поэтому при ошибке
модели смысл ответа, факты и CTA не меняются.

Semantic validator не заменяет распарсенный ответ fallback-ом. Нарушения
grounding, порядка, полноты, wording и quality сохраняются как warnings в trace
и используются для диагностики/eval. Fallback допустим только если JSON нельзя
распарсить или собрать по обязательной технической schema, либо если произошла
transport/provider error.

Release matrix для этого слоя должна покрывать:

```text
family / life / rental / investment / neutral
× answer_current / compare_current / recommend_current / answer_open_question
× evidence available / partial / missing
× selected one / current options
```

---

## 13. Изменения по файлам

### Обязательные runtime-файлы

| Файл | Изменение |
|---|---|
| `nmbot_v2/contracts.py` | `IntentGoal`, `IntentPlanV3`, validation result; universal open-question fields и presentation profile в `ResponseBrief` |
| `followup_intent_classifier.py` | V3 prompt/schema/few-shot, один structured plan |
| `nmbot_v2/semantic_planner.py` | type normalization + validation; удалить semantic rerouting |
| `scripts/nmbot_runtime_adapter.py` | raw V3 → accepted plan; safe trace; query_text injection |
| `nmbot_v2/transition.py` | mechanical `goal → stage/action` map |
| `nmbot_v2/runtime.py` | Evidence Executor и current-options fact check |
| `nmbot_v2/fact_context.py` | availability/missing by card |
| `nmbot_v2/search_contract.py` | typed current-options fact-check request/validation |
| `nmbot_v2/scenario_recipes.py` | optional viewpoint overlays: fact priority, benefits, forbidden inferences, recommendation/CTA policy |
| `nmbot_v2/response.py` | goal-owned ResponsePlan templates; deterministic fallback из того же presentation profile |
| `nmbot_v2/response_composer.py` | единый structured JSON; goal/viewpoint-aware validation без повторного routing |
| `prompts/v2_response_composer.txt` | shared composition rules; scenario-specific значения приходят только через `ResponseBrief` |
| `nmbot_v2/conversation.py` | удалить semantic fallback; оставить только нужные pure helpers или удалить файл |

### Тестовые файлы

| Файл | Покрытие |
|---|---|
| `tests/test_followup_canonical_contract.py` | V3 schema и planner examples |
| `tests/test_semantic_planner_transition.py` | goal не меняется downstream |
| `tests/test_nmbot_v2_runtime.py` | evidence policy и state acceptance |
| `tests/test_nmbot_runtime_adapter.py` | один planner call, gateway counts, trace |
| `tests/test_nmbot_v2_search_contract_runtime.py` | batch current-options fact check |
| `tests/test_nmbot_v2_recipe_transition_matrix.py` | goal/viewpoint response templates |
| `tests/test_nmbot_v2_contracts.py` | единая schema, dynamic profile, recommendation и open-question policies |
| новый matrix fixture | viewpoint × goal × evidence status × scope |
| `tests/test_nmbot_api_jivo_p1.py` | API/Jivo stateful behavior |
| новый fixture/replay в существующем replay-файле | production parks dialogue |

### Документация

После реализации обновить:

```text
docs/BOT_ARCHITECTURE.md
docs/LLM_DECISION_ARCHITECTURE_TZ.md
docs/NMBOT_V2_ANSWER_QUALITY_GATE.md
docs/EXPERIMENTS.md
task_plan.md
findings.md
progress.md
```

---

## 14. Пошаговая миграция

Каждую фазу выполнять отдельно. После первой ошибки остановиться, прочитать
соответствующий trace/test output и не продолжать следующие фазы.

### Фаза 0. Зафиксировать baseline

Действия:

1. Не менять runtime.
2. Записать текущий full pytest result.
3. Сохранить текущие production PIDs, hashes и error count.
4. Добавить transcript parks-dialogue в replay fixture.
5. Зафиксировать expected V3 plans для каждого хода.

Текущий ориентир на дату плана:

```text
681 passed
641 existing aiohttp NotAppKeyWarning
```

Done:

- replay воспроизводит старую ошибку;
- expected V3 decision записан отдельно от response text;
- ни один production-файл не изменён.

### Фаза 1. Добавить типы V3 без подключения к runtime

Файлы:

```text
nmbot_v2/contracts.py
tests/test_followup_canonical_contract.py
```

Действия:

1. Добавить enum и dataclass.
2. Добавить serialization/deserialization.
3. Запретить unknown fields.
4. Добавить unit tests для каждого goal.
5. Проверить round-trip JSON.

Done:

- старый runtime импортируется без изменений;
- V3 types полностью покрыты тестами;
- production behavior не изменился.

### Фаза 2. Добавить V3 validator

Файлы:

```text
nmbot_v2/semantic_planner.py
tests/test_semantic_planner_transition.py
```

Действия:

1. Реализовать `validate_intent_plan_v3`.
2. Добавить state-aware selected-name check.
3. Добавить requested-facts allowlist.
4. Добавить cross-field rules.
5. Проверить, что validator не меняет goal.

Обязательный regression:

```python
raw.goal == "recommend_current"
validated.plan.goal == IntentGoal.RECOMMEND_CURRENT
```

Done:

- accepted plan сохраняет goal;
- invalid plan не создаёт StateDelta;
- repairable и non-repairable errors различаются.

### Фаза 3. Написать V3 planner prompt

Файл:

```text
followup_intent_classifier.py
```

Действия:

1. Добавить отдельную V3 prompt-константу.
2. Добавить JSON Schema.
3. Добавить минимум 12 few-shot examples.
4. Не удалять V2 prompt на этом шаге.
5. Проверить prompt static tests.

Запрет:

- не включать client-facing response;
- не просить model решать `needs_search`;
- не просить model возвращать несколько competing intents.

Done:

- все fixture inputs дают однозначный expected goal;
- schema не содержит legacy duplicate fields.

### Фаза 4. Добавить mechanical transition V3

Файл:

```text
nmbot_v2/transition.py
```

Действия:

1. Добавить отдельную функцию `derive_transition_v3(plan, state)`.
2. Реализовать только guards + static map.
3. Старую `derive_transition` пока не удалять.
4. Добавить table-driven tests по всем goals.

Done:

- один goal всегда даёт один stage/action;
- наличие visible options не меняет goal;
- pending flow покрыт отдельными tests.

### Фаза 5. Реализовать Evidence Executor

Файлы:

```text
nmbot_v2/runtime.py
nmbot_v2/fact_context.py
nmbot_v2/search_contract.py
scripts/nmbot_runtime_adapter.py
```

Порядок:

1. Реализовать availability by card как pure functions.
2. Добавить sufficiency rules по goal.
3. Добавить typed current-options fact-check request.
4. Добавить strict name-subset validator.
5. Добавить bounded one-call execution.
6. Добавить safe merge в existing cards.
7. Добавить failure preservation tests.

Done:

- `recommend_current + parks` использует память, если parks известны;
- иначе делает максимум один fact-check call;
- failure оставляет прежние cards/state;
- новый посторонний ЖК блокируется;
- search call counts проверены тестом.

### Фаза 6. Сделать goal-owned ResponsePlan

Файлы:

```text
nmbot_v2/response.py
nmbot_v2/conversation.py
nmbot_v2/scenario_recipes.py
```

Порядок:

1. Добавить templates для current goals.
2. Передавать goal напрямую в ResponsePlan.
3. Удалить generic technical intro.
4. Удалить повторяющийся per-card fallback.
5. Запретить повтор full shortlist, если вопрос не требует списка.
6. Оставить recipes только presentation rules.

Done:

- parks-dialogue не повторяет карточки;
- response отвечает на критерий;
- missing evidence объясняется одним общим блоком;
- один final question;
- quality gate не ослаблен.

### Фаза 7. Подключить V3 в локальном runtime за временным switch

Файлы:

```text
scripts/nmbot_runtime_adapter.py
scripts/nmbot_api_server.py (только если switch читается здесь)
```

Временный switch:

```text
NMBOT_INTENT_PLAN_VERSION=v2|v3
```

Правила:

- default до local gate: `v2`;
- switch выбирает planner contract, а не два planner call;
- одновременно выполняется только один runtime path;
- health/trace показывает активную версию без secrets;
- после стабильного V3 switch удалить, не оставлять постоянный dual-runtime.

Done:

- V2 path не изменился;
- V3 path проходит replay;
- один planner call на turn;
- startup без env имеет безопасный documented default.

Production addendum:

- V3 включён на VPS через `NMBOT_INTENT_PLAN_VERSION=v3`;
- первый live synthetic turn прошёл через V3 и записал schema-3 planner trace;
- rollback switch проверен документированно, но не применялся.

### Фаза 8. Replay и focused regression

Обязательные сценарии:

1. `/start → family search`;
2. `какой лучше? → поближе к паркам`;
3. compare first/second;
4. refinement budget;
5. expand without repeats;
6. selected typo reference;
7. selected missing fact;
8. named ЖК outside current list;
9. mortgage consultation;
10. operator offer → yes → contact name;
11. operator decline;
12. substantive interruption during contact flow;
13. resume contact;
14. off-topic → return;
15. malformed planner JSON;
16. planner timeout;
17. search timeout;
18. current-options fact-check empty result;
19. current-options fact-check returns foreign ЖК;
20. duplicate inbound event.

Для каждого проверять:

```text
expected goal
stage/action
planner call count
search/enrichment call count
state before/after
visible names
selected name
question count
absence of internal leaks
```

### Фаза 9. Full local gate

Минимум:

```bash
python3 -m py_compile <all changed runtime py files>
python3 -m pytest <focused files>
python3 -m pytest
```

Дополнительные project suites — только существующие и разрешённые:

```text
dialog
stateful
compare
ux_e2e
deploy smoke
```

Не запускать promptfoo eval без отдельного прямого разрешения пользователя.

Stop:

- любой regression;
- planner calls > 1;
- search calls превышают documented bound;
- state изменился после failed execution;
- response question count != 1;
- old safe flow потерял selected/contact context.

### Фаза 10. Документация до production deploy

Обновить ownership, V3 schema, transition map, evidence policy, rollback и trace
поля. Legacy V2 decision tree пометить historical до удаления.

Не копировать два разных active contracts в разные документы.

Status: выполнено для текущего opt-in rollout; этот документ и
`docs/BOT_ARCHITECTURE.md` описывают V3 как production active contract, а V2
только как rollback path.

### Фаза 11. Targeted production deploy

Порядок:

1. Live preflight API/bridge exact units и ports.
2. Error count before.
3. Backup всех заменяемых runtime-файлов.
4. SCP.
5. Remote py_compile.
6. Local/remote SHA-256.
7. Restart только API, если bridge не менялся.
8. Verify API new PID, bridge same PID.
9. Health 8088/8093.
10. Первый synthetic turn.
11. Сразу прочитать trace/error result первого turn.
12. Только после зелёного первого turn продолжать stateful dialogue.

Status, 2026-07-21: выполнено для первого synthetic Jivo turn. Полная
многоходовая production matrix остаётся в Фазе 12.

### Фаза 12. Production dialogues

Минимальные live gates:

#### Parks recommendation

```text
/start
двушка для семьи из трех человек
какой лучше?
поближе к паркам
```

#### Compare/recommend

```text
двушка в Котельниках
сравни первый и второй
какой бы ты выбрала для быстрого заезда?
```

#### Selected typo/contact

```text
shortlist
Томилиский бульвар подробнее
да
```

#### Missing evidence preservation

```text
shortlist
какой лучше по критерию, которого нет в cards
```

Проверить:

- нет loops/repeated cards;
- goal не меняется downstream;
- missing evidence не вызывает state reset;
- planner/search call counts bounded;
- zero fresh error events;
- safe trace содержит V3 decision.

### Фаза 13. Удалить legacy semantic layers

Удалять только после production acceptance V3.

Порядок удаления:

1. `derive_runtime_decision` и `DerivedPlannerDecision`;
2. legacy operation aliases;
3. semantic branch ordering;
4. старый planner prompt/schema;
5. generic conversation fallback;
6. временный version switch;
7. мёртвые tests/fixtures, которые проверяют удалённый контракт;
8. stale docs.

После удаления снова выполнить полный local + production gate.

---

## 15. Trace V3

Для каждого turn записывать безопасную строку:

```json
{
  "schema_version": 3,
  "goal": "recommend_current",
  "viewpoint": "family",
  "selected_present": false,
  "requested_facts": ["parks"],
  "validation_status": "accepted",
  "stage": "current_options",
  "action": "answer_from_current_options",
  "evidence": {
    "available_count": 0,
    "missing_facts": ["parks"],
    "fact_check_attempted": true,
    "fact_check_added_count": 0
  },
  "call_counts": {
    "planner": 1,
    "search": 0,
    "current_fact_check": 1
  },
  "state_changed": false,
  "question_count": 1
}
```

Не писать:

- raw user text без redaction;
- prompt;
- provider payload;
- телефон/email;
- полный state;
- raw model reasoning.

---

## 16. Rollback

### Rollback target

Последний green V2 backup перед включением V3.

### Rollback signals

- API health red;
- fresh runtime errors;
- invalid state mutation;
- planner call count > 1;
- loops/repeated answers;
- selected ЖК потерян;
- operator/contact flow сломан;
- invented facts/internal leak;
- full suite regression;
- widget не получает BOT_MESSAGE.

### Rollback procedure

1. Остановить live batch после первого failure.
2. Зафиксировать UTC, session ref, PID, error rows.
3. Вернуть runtime-файлы из backup.
4. Remote py_compile.
5. Restart только затронутый service.
6. Verify hashes/health.
7. Повторить только первый failed smoke.
8. Не продолжать другие кейсы до зелёного результата.

---

## 17. Definition of Done

V3 считается завершённым, только если одновременно выполнено всё:

- `IntentPlanV3` является единственным active semantic contract;
- один planner call на обычный turn;
- validator не меняет goal;
- transition — static map + explicit guards;
- Evidence Executor автоматически проверяет missing facts;
- failed fact check сохраняет current dialogue;
- recipes/renderer не маршрутизируют intent;
- generic technical fallback удалён;
- legacy derive decision удалён;
- temporary version switch удалён;
- focused/full tests green;
- production direct API stateful gates green;
- real widget/bridge delivery подтверждён;
- fresh error delta = 0;
- docs отражают только один active contract;
- пользователь подтвердил закрытие гипотезы/todo.

---

## 18. Краткая инструкция простой модели-исполнителю

1. Работай только над одной фазой за раз.
2. Перед фазой прочитай перечисленные для неё файлы.
3. Не меняй файлы вне списка без отдельного evidence.
4. Сначала добавь failing regression, затем минимальный source fix.
5. После первой ошибки остановись и прочитай полный failure output.
6. Не исправляй тест удалением важной проверки.
7. Не добавляй новый router, если можно удалить старый.
8. Не делай второй LLM call.
9. Не меняй state при rejected/failed execution.
10. Не деплой до полного green local gate.
11. Перед deploy сделай backup и hashes.
12. После первого live turn сразу прочитай trace/error evidence.
13. Не закрывай todo без подтверждения пользователя.

---

## 19. Источники

Проект:

- `docs/BOT_ARCHITECTURE.md:220-253,314-429`;
- `docs/IDEAL_IRINA_UX.md:17-45,80-163`;
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`;
- `docs/IRINA_UX_RELEASE_CHECKLIST.md`;
- `docs/LLM_DECISION_ARCHITECTURE_TZ.md:130-239`;
- `nmbot_v2/contracts.py:120-148`;
- `nmbot_v2/semantic_planner.py:433-586`;
- `nmbot_v2/transition.py:18-57`;
- `nmbot_v2/runtime.py:43-89`;
- live VPS evidence session `sha256:52f092973872bded`.

Внешние рекомендации:

- Anthropic, *Building Effective Agents*: простые composable patterns,
  минимальная доказанная сложность, прозрачные interfaces и bounded workflows —
  <https://www.anthropic.com/research/building-effective-agents>;
- OpenAI, *Structured model outputs*: strict JSON Schema для type-safe interface
  между моделью и приложением —
  <https://platform.openai.com/docs/guides/structured-outputs>.
