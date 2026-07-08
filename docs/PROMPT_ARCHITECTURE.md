# Prompt architecture for nmbot

Этот документ фиксирует, как устроены prompt-файлы Ирины. Правило нужно считать архитектурным: **main prompt управляет формой и безопасностью, scenario/facet prompts добавляют сценарный смысл**.

## Главный принцип

1. `prompts/chat_v1.txt` — управляющий каркас ответа: стиль, JSON-контракт, safety, запрет выдуманных фактов, общий стандарт живой карточки.
2. `prompts/search_v1.txt` — контракт поиска: какие MCP-поля запросить и как вернуть `facts`, `near`, `missing`, `params`.
3. `prompts/scenarios/*_v1.txt` — сценарные overlays: что важно именно в этом сценарии, какие facts приоритетны, какие claims запрещены.
4. `prompts/facets/*_v1.txt` — дополнительные overlays поверх основного сценария: ипотека, скидки, рассрочка и другие поперечные слои.
5. `prompts/eval/prompt_master_v1.txt` — evaluator: оценивает результат, но не пишет клиентский ответ.
6. `prompts/text_style_v1.txt` — style-only слой: может улучшать живость текста, но не добавляет факты и не меняет сценарный смысл.

## Запрещено

- Нельзя класть scenario-specific факты в `chat_v1.txt`: школы/сады для family, доходность/ликвидность для investment, арендный спрос для rental и так далее.
- Нельзя дублировать одну и ту же сценарную матрицу в main prompt и scenario prompt. Один сценарный источник правды — соответствующий файл в `prompts/scenarios/`.
- Нельзя делать код автором красивого ответа. Код готовит материал, нормализует карточку, подключает overlays и валидирует контракт; `response.message`, `response.items[].reason`, `response.question` пишет LLM.
- Нельзя использовать evaluator prompt как production prompt.
- Нельзя считать prompt quality зелёным только по автоматическому score: нужен ручной review ответа глазами клиента.

## Правила по prompt-файлам

| Prompt | Назначение | Основные принципы | Ограничения |
|---|---|---|---|
| `prompts/chat_v1.txt` | Главный chat-каркас Ирины | Живой тон, JSON `response/items/question`, один вопрос, факты только из MCP/card, scenario overlay имеет приоритет при выборе акцентов | Не хранит сценарные facts matrix; не решает, что важно для family/investment/rental; не подменяет overlays |
| `prompts/search_v1.txt` | MCP/search contract | Всегда искать через MCP; вернуть JSON `facts/near/missing/params`; копировать все MCP-поля, нужные answer layer | Не пишет клиентский ответ; не придумывает facts; `why_*` только из подтверждённых полей |
| `prompts/scenarios/family_v1.txt` | Family overlay | Приоритет: школы, сады, парки/зелень, двор без машин, площадки, безопасность, транспорт; отделка/готовность только вторым слоем | Нельзя начинать family reason с отделки/цены/готовности, если есть family facts; нельзя писать общую «развитую инфраструктуру» без фактов |
| `prompts/scenarios/investment_v1.txt` | Investment overlay | Приоритет: цена входа, компактный формат, mortgage/discount если есть, EGRN/counter/ads только как подтверждённые сигналы | Нельзя обещать доходность, рост цены, окупаемость, ликвидность без доказательств; нельзя уходить в rental, если клиент не просил аренду |
| `prompts/scenarios/rental_v1.txt` | Rental overlay | Приоритет: компактность, отделка, метро/транспорт, готовность, район, подтверждённый demand evidence | Нельзя писать ставку аренды, доходность, окупаемость; нельзя писать «высокий спрос» без `ads/counter_novos/egrn_top_novos` |
| `prompts/scenarios/search_v1.txt` | Обычный подбор | Держит явные условия клиента, показывает точные варианты и честные near-отличия | Не добавляет сценарные claims без overlay; не скрывает важное отличие near |
| `prompts/scenarios/repeat_search_v1.txt` | Новые варианты | Не повторять `visible_options`, использовать `exclude`, давать новый сравнительный акцент | Нельзя возвращать тот же shortlist как новый |
| `prompts/scenarios/refine_search_v1.txt` | Уточнение фильтра | Сохранять прежний контекст и менять только уточнённые условия; объяснять отличия вариантов | Нельзя терять старые важные ограничения; нельзя сухое «подходит по фильтрам» |
| `prompts/scenarios/explain_selection_v1.txt` | Объяснение выбора | Критерий → факт → польза по уже показанным ЖК | Нельзя объяснять без facts по `visible_options`; нельзя придумывать мотивацию |
| `prompts/scenarios/fact_check_v1.txt` | Проверка факта | Коротко: подтверждено / не подтверждено / нет данных, строго по карточке | Нельзя превращать проверку в продажу; нельзя подтверждать отсутствующий факт |
| `prompts/scenarios/default_v1.txt` | Недостаточно данных | Один главный уточняющий вопрос | Нельзя преждевременно показывать ЖК без понятного запроса |
| `prompts/scenarios/operator_v1.txt` | Handoff к человеку | Мягко передать оператору live-вопросы: бронь, наличие, этаж, корпус, показ | Нельзя придумывать ДДУ, эскроу, юридические условия, наличие или бронь |
| `prompts/scenarios/off_topic_v1.txt` | Не-недвижимость | Вежливо ограничить область и вернуть к недвижимости | Нельзя отвечать на внешнюю тему |
| `prompts/facets/mortgage_v1.txt` | Mortgage facet | Второй слой поверх основного сценария: ставка/взнос/скидка/рассрочка только если есть в card | Не заменяет family/investment/rental/search; нельзя обещать условия без фактов |
| `prompts/eval/prompt_master_v1.txt` | Prompt-master evaluator | Оценивает request/search/card/response/scenario/safety; возвращает score/verdict/problem_level/next_fix | Не используется как клиентский prompt; не переписывает ответ |
| `prompts/text_style_v1.txt` | Стиль | Улучшает живость и читаемость уже безопасного текста | Не добавляет facts, не меняет scenario priority, не чинит контрактные ошибки |

## Как добавлять новый prompt

Перед созданием нового prompt нужно явно записать:

1. **Purpose** — зачем prompt существует и какой слой обслуживает.
2. **Inputs** — какие данные он получает: MCP card, scenario, visible options, selected option, user text.
3. **Outputs** — что он обязан вернуть и в каком формате.
4. **Priority rules** — какие факты важнее внутри этого слоя.
5. **Forbidden claims** — что нельзя говорить даже красивым языком.
6. **Owner layer** — main / search / scenario / facet / eval / style / code-material.
7. **Validation** — чем проверяется: compile, live run, validator, manual review, prod smoke.

## Правило написания prompt'ов

Каждый prompt в проекте пишется как отдельный контракт, а не как набор красивых инструкций.

Обязательные принципы:

1. **Один prompt — один слой ответственности.**
   Main prompt управляет формой, safety и контрактом ответа. Scenario prompt добавляет только сценарный смысл. Facet prompt добавляет только поперечный слой. Нельзя смешивать эти роли в одном файле.
2. **Сначала назначение, потом текст.**
   Перед текстом prompt должен явно отвечать: зачем он существует, какие данные получает и какой результат обязан вернуть.
3. **Факты живут в своём слое.**
   Scenario-specific facts, priority rules и forbidden claims хранятся в scenario/facet overlay, а не в `chat_v1.txt`.
4. **Prompt должен читаться как native document.**
   Если runtime вставляет overlay, он должен попадать в предусмотренный слот конструктора и выглядеть как часть исходного документа, а не как хвост в конце.
5. **Не дублировать одно и то же дважды.**
   Если правило уже живёт в scenario overlay, его не надо переписывать в main prompt. Это особенно важно для family/investment/rental.
6. **Не делать код автором ответа.**
   Код может подать контекст, нормализовать карточку, выбрать overlay и проверить контракт. `response.message`, `response.items[].reason`, `response.question` пишет LLM.
7. **Сначала запреты, потом стилистика.**
   Если prompt может породить выдумку, конфликт приоритетов или сухой список, это должно быть запрещено до всяких примеров и украшений.
8. **Проверка обязательна.**
   Любая новая или изменённая prompt-логика проходит compile/live/manual review. Если меняется answer behavior, нужен prod/VPS gate.

Краткая формула для каждого prompt-файла:

```text
Purpose → Inputs → Outputs → Priority rules → Forbidden claims → Owner layer → Validation
```

Если хотя бы один пункт не определён, prompt считается недописанным.

## Runtime integration rule

Production runtime должен собирать итоговый prompt как **конструктор**, а не приклеивать сценарий в конец:

```text
chat_v1.txt
  {{SCENARIO_OVERLAY}}  ← сюда вставляется prompts/scenarios/<purpose>_v1.txt
  {{FACET_OVERLAYS}}    ← сюда вставляются prompts/facets/*_v1.txt
```

Scenario/facet prompt должен оказаться на своём логическом месте внутри итогового system prompt, как будто он изначально был частью документа. Если scenario overlay не подключён в runtime или вставлен как неуправляемое приложение в конец, сценарий считается неполно внедрённым даже если файл prompt существует.
