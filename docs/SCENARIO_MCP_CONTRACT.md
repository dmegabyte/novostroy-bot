# Scenario → MCP card → Answer contract

Этот документ фиксирует релизный контракт для Ирины: сценарий сначала задаёт, **что ищем**, MCP/search возвращает проверяемую карточку фактов, нормализатор не теряет эти факты, а answer layer у LLM сама пишет клиентский текст по этим фактам. Код может только подготовить материал, нормализовать карточку и проверять контракт, но не должен быть автором красивого ответа.

Prompt-архитектура описана отдельно в `docs/PROMPT_ARCHITECTURE.md`: main prompt — управляющий каркас, scenario/facet prompts — сценарная информация и приоритеты. Сценарные факты нельзя складывать в основной prompt.

## Универсальный gate

Сценарий считается валидным только если выполнены все шесть условий:

1. `mcp_request` содержит правильный `purpose`, нужные `facets` и scenario-specific `need`.
2. `facts[]` или `near[]` возвращают ЖК, подходящие под сценарий, с теми же опорными полями.
3. Normalizer превращает вложенные блоки MCP/card (`family_infrastructure`, `finance`, `ads`, `apartment_types`, `egrn_top_novos`, `counter_novos`, `mortgage_calc`) в читаемые поля, а не в строку вида `{'field': 1}`.
4. Answer layer у LLM использует scenario facts как первый приоритет, а базовые поля вроде отделки/готовности — только как дополнительный аргумент. Код не сочиняет `reason/message` вместо LLM; если нужен repair, он должен быть только safety fallback, не quality path.
5. Validator/test не ставит `good/100`, если обязательные request/search/answer опоры сценария отсутствуют.
6. Финальную оценку качества делает человек: нужно руками прочитать ответ и сравнить его с ожидаемым качеством сценария. Автоматический validator — только safety net, а не релизное решение.

## Prompt-writing rule for this contract

Когда меняются prompts, этот контракт должен сохраняться в трёх слоях, и не иначе:

1. `prompts/chat_v1.txt` хранит только каркас ответа, safety и JSON-контракт.
2. `prompts/scenarios/*_v1.txt` хранит сценарную матрицу: что важно, какие facts приоритетны, какие claims запрещены.
3. `prompts/facets/*_v1.txt` хранит только дополнительный слой поверх сценария.

Нельзя делать наоборот: сценарные facts не должны переезжать в main prompt, а main prompt не должен дублировать overlay-логику. Если правило уже есть в scenario overlay, оно не должно повторяться в `chat_v1.txt` в виде отдельной сценарной матрицы.

При ревью prompt-изменений всегда проверяй:

- main prompt не превратился в помойку из scenario-specific правил;
- scenario overlay вставляется как native document, а не как хвостовой append;
- facet overlay не ломает основной сценарий;
- evaluator не подменяет production prompt;
- код не начинает сочинять ответ вместо LLM.

## Per-scenario contract

| Scenario | Что ищем для клиента | `mcp_request` minimum | Must-return evidence in card | Answer priority | Fail if |
|---|---|---|---|---|---|
| `family` | Семейная среда: школа/сад, прогулки, безопасный двор, площадки, транспорт | `purpose=family`, `count>=3`, `need` includes `schools`, `kindergartens`, `parks`, `yard_without_cars` | `schools`/`kindergartens`, `parks`/`forest`, `yard_without_cars`/`children_ground`/`sports_ground`, `security`, `metro/transport`; удобный block `family_infrastructure`; `why_family` only from facts | Сначала семейная польза: рутина с детьми, прогулки, двор без машин, площадки. Отделка/готовность — ниже | request не содержит family need; card не содержит family evidence; ответ говорит «для семьи» без школ/садов/парков/двора |
| `investment` | Понятный вход и проверяемые инвест-сигналы без обещания доходности | `purpose=investment`, `count>=3`, `need` includes `entry_price`, `mortgage`, `egrn_sales`, `counter_novos`, `compact_lots` | `price_range`/`price`, `area`/compact lots, `ads`/`apartment_types`, `egrn_top_novos`, `counter_novos`, `mortgage_calc`/`mortgage`, `discount`, `ready`, `metro`; `why_investment` only from facts | Сначала цена входа/компактный формат/подтверждённые сделки или объявления/готовность/метро. Нельзя обещать доходность, рост цены, окупаемость | request бедный; card без entry/egrn/counter/compact evidence; ответ обещает доходность или даёт общий рекламный текст |
| `rental` | Арендо-пригодный вход: компактность, отделка, метро, готовность, район, подтверждённый спрос | `purpose=rental`, `count>=3`, `need` includes `compact`, `finishing`, `metro`, `ready`, `demand` | `ads`/`apartment_types`, `area`/rooms compact, `finishing`, `metro/transport`, `ready`, `location`, `counter_novos`/`egrn_top_novos`; `why_rental` only from facts | Сначала компактный формат, отделка, метро/локация, готовность, подтверждённый demand evidence. Без ставок аренды и окупаемости | card не даёт rental evidence; ответ пишет «высокий спрос» без `counter_novos`/`egrn`/`ads`; ответ обещает доходность |
| `search` | Обычный подбор по явным условиям клиента | `purpose=search`, `count>=3` если запрос не уточняющий | `facts` строго соответствуют обязательным условиям; `near` содержит 1–2 отличия | Честно показать точные варианты и близкие альтернативы | точный один, но near не объяснён; обязательные условия потеряны |
| `repeat_search` | Другие варианты, не повторять показанные | `purpose=repeat_search`, `exclude` includes visible options, `count>=3` | Новые `facts/near`, не совпадающие с `visible_options` | Ясно сказать, что это другие варианты, и чем они отличаются | повтор старого списка или нет `exclude` |
| `explain_selection` | Объяснить принцип уже показанного выбора | `purpose` сохраняет исходный сценарий или request возвращает facts по `visible_options` | Facts по тем же ЖК, которые клиент видел | Объяснить правило подбора и различия между ЖК | объяснение без facts по visible options |
| `fact_check` | Проверить один конкретный факт по выбранному ЖК | `purpose=fact_check`, `selected_option_name`, `fact_to_check` | Подтверждение или честное отсутствие именно этого факта | Коротко: да/нет/не подтверждено + что есть в карточке | факт придуман или ответ уходит в продажу вместо проверки |
| `default` | Уточнить, когда данных мало | `purpose=default` или без поиска | No shortlist required | Один короткий уточняющий вопрос | показывает ЖК до понимания задачи |
| `operator` | Передать к человеку/бронь/показ | `purpose=operator`, selected context if any | Selected option context if available | Коротко подтвердить и предложить оператора | придумывает юридические/бронь условия без facts |
| `off_topic` | Не-недвижимость | no MCP search required | none | Вежливо вернуть к недвижимости | отвечает на внешнюю тему |

## Mortgage facet

Mortgage is a facet, not a replacement scenario. For `family + mortgage`, primary `purpose` stays `family`, and request additionally contains:

- `facets:["mortgage"]`;
- `mortgage_type` when clear: `family_mortgage`, `it_mortgage`, `subsidized_mortgage`;
- `need`: `mortgage_calc`, `mortgage`, `discount`, `payment_by_installments`, `price`.

Answer may mention rates, initial payment, discount or installment only when these fields are present in the MCP/card.

## Release validation rule

Before release, every scenario run must pass three layers:

1. Request check: required `need/facets/exclude` are present.
2. Card check: `facts/near` contain scenario evidence.
3. Answer check: response uses that evidence in a human presentation and does not invent missing facts.
4. Manual quality review: ассистент читает ответ глазами клиента и явно отвечает, достигнут ли ожидаемый уровень сценария: да/нет/что не дотянуто.

If any layer fails, this is a contract failure, not a wording nit.
