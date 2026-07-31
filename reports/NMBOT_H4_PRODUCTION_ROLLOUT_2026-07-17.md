# NMBOT H4 production rollout — 2026-07-17

## Итог

H4 выбран как лучший из проверенных маршрутов:

`legacy search_v1 → deterministic Validator → restricted Presenter v2`.

Первый поиск ограничивается базовыми проверяемыми фактами — прежде всего
локацией и ценой. Неподтверждённые семейные или инвестиционные утверждения не
добавляются. Дополнительное обогащение переносится на следующий шаг после
выбора конкретного варианта.

Production работает в enforced-режиме:

- `NMBOT_FOUR_LAYER_RUNTIME=1`;
- `NMBOT_FOUR_LAYER_ENFORCE=1`;
- `NMBOT_ROUTER_PROFILES=0`.

Compact search v2 и search-profile overlays в production не включены.

## Почему выбран H4

| Гипотеза | Результат |
|---|---|
| Legacy `search_v1` + Validator + Presenter | Стабильно возвращает структурированные facts и допускает детерминированную проверку. |
| Compact `four_layer_search_v2` | На проверенных сценариях часто возвращал пустые facts. |
| Search profile overlays | Снижали полноту выдачи и не добавляли подтверждённые семейные данные. |
| H4 progressive evidence | Дал grounded exact-match, честный no-match и безопасный investment-ответ. |

## Контракт слоёв

1. Search получает факты через существующий MCP-маршрут.
2. Validator классифицирует кандидатов и не передаёт rejected/unknown в Presenter.
3. `facts=[]` считается корректным `no_exact_matches`, а не ошибкой парсинга.
4. Presenter v2 видит только безопасный `DecisionContext`.
5. Semantic gate требует упоминания хотя бы одного разрешённого matched-варианта.
6. После одного контролируемого retry используется детерминированный renderer,
   составленный только из проверенных label/location/price.

## Rollout

Первый deploy был автоматически откатан из-за отсутствующей зависимости
`search_profiles.py`. Production был восстановлен до продолжения работ.

Успешный rollout:

- backup: `/home/neiro/novostroy-bot/backups/h4-rollout-20260716-214121`;
- runtime: `scripts/chat_tester_bot.py`;
- Presenter: `prompts/four_layer_presenter_v2.txt`;
- deterministic dependency: `search_profiles.py`;
- перезапущен только `novostroy-bot-api.service`;
- bridge не изменялся и не перезапускался;
- API и bridge active, health API — HTTP 200;
- свежих traceback/error/exception после рестартов нет.

Semantic Presenter gate был развернут отдельно:

- backup: `/home/neiro/novostroy-bot/backups/chat_tester_bot_presenter_gate_20260716T214825Z`;
- focused local regression перед deploy: 83 passed.

## Production Jivo E2E

### 1. Жёсткие локация и бюджет

Запрос требовал однокомнатную квартиру только в разрешённой локации и не выше
заданного бюджета.

Результат:

- клиенту показан один matched-вариант;
- в тексте присутствуют название, разрешённая локация и подтверждённая
  стартовая цена;
- другие районы и более дорогие варианты не показаны;
- один финальный вопрос;
- полная цепочка доставки завершена;
- safe trace ref: `7a0421f6…`;
- end-to-end latency: 33.433 s;
- API errors: 0.

### 2. Семейный progressive-evidence сценарий

Запрос содержал семейный контекст, предпочтение по комнатности, бюджет и прямой
запрет придумывать школы или детские сады без данных.

Результат:

- клиенту показаны только matched-варианты с названием, локацией и стартовой
  ценой;
- утверждений о школах, детских садах или семейной инфраструктуре не было;
- один финальный вопрос;
- полная цепочка доставки завершена;
- safe trace ref: `43cc0a1f…`;
- end-to-end latency: 21.272 s;
- API errors: 0.

### 3. Инвестиционная безопасность утверждений

Запрос требовал небольшую квартиру для аренды, близость к метро и разумную цену,
но запрещал обещать доходность, ликвидность или спрос без доказательств.

Результат:

- клиенту показаны варианты с названием, локацией, стартовой ценой и
  подтверждённой компактной площадью;
- обещаний доходности, ликвидности и высокого спроса нет;
- один финальный вопрос;
- полная цепочка доставки завершена;
- safe trace ref: `dd8a77d6…`;
- end-to-end latency: 15.389 s;
- API errors: 0.

## Статус качества

Проверенные классы H4 зелёные:

- hard location/budget filtering;
- grounded family first turn without invented infrastructure;
- investment claim safety;
- automatic Jivo delivery;
- Presenter semantic completeness.

Это не доказательство корректности всех возможных диалогов. Следующие классы
должны оставаться в регрессии: current-options follow-up, callback/Sheets,
recovery, no-match и длительный status-update → final transport path.

## Источники

- `scripts/chat_tester_bot.py` — Validator, enforced Presenter, semantic gate.
- `prompts/four_layer_presenter_v2.txt` — restricted Presenter contract.
- `search_profiles.py` — deterministic typed dependency; profiles выключены.
- `tests/test_four_layer_runtime_contract.py` — runtime class regressions.
- `scripts/nmbot_jivo_trace_analyze.py` — безопасная production trace-проверка.
- Jivo browser E2E на production widget, 2026-07-17.

Отчёт не содержит телефонов, токенов, payload, полных client/chat ids или
полных trace ids.
