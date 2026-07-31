# Журнал проблем и решений Jivo/nmbot

Дата: 2026-07-16  
Контур: Jivo Bot API → VPS bridge → nmbot API → Jivo `BOT_MESSAGE` / `INVITE_AGENT`  
Канал Telegram в этот журнал не входит: он legacy.

## Как читать журнал

- **Факт** — что было подтверждено кодом, логом или live-проверкой.
- **Решение** — какое изменение внесено.
- **Проверка** — чем подтвержден результат.
- Сырые телефоны, токены, payload и полные клиентские идентификаторы намеренно не записываются.

## Сводка

| ID | Проблема | Решение | Статус |
|---|---|---|---|
| JIVO-001 | Валидный телефон клиента попадал в общий fallback | Code-level phone capture до LLM/search | Исправлено и live-проверено |
| JIVO-002 | «Куда звонить?» не переводило к запросу телефона клиента | Typed LLM action `operator_contact` | Исправлено и live-проверено |
| JIVO-003 | «Уточни» запускало новый подбор квартир | Безопасный контекст предыдущего operator offer | Исправлено и live-проверено |
| JIVO-004 | После долгого запроса финал зависел от следующего сообщения | Status timeout без отмены upstream task + hard deadline | Код и обычная доставка live-проверены; slow-path не воспроизведён |
| JIVO-005 | Повторный Jivo event мог дать повторную обработку/доставку | Event-id dedup + per-session lock + bridge dispatch guard | Исправлено, staging и production smoke зелёные |
| JIVO-006 | Нельзя было быстро определить слой сбоя | Read-only trace/dialogue diagnosis tool | Исправлено, тесты и VPS CLI зелёные |
| JIVO-007 | `accepted_async` трактовался как клиентский ответ | Разделены transport ack, status delivery и final delivery | Исправлено в коде и анализаторе |
| JIVO-008 | Телефон автоматически переводит диалог на оператора, хотя это больше не нужно | Требуется отключить автоматический `INVITE_AGENT` и определить новый финал phone-сценария | Не исправлено; новое требование |
| JIVO-009 | Бессмысленное сообщение не переводится в полезный диалог | Требуется отдельный LLM-сценарий вовлечения и уточнения намерения без подбора ЖК и без оператора | Не исправлено; сценарий нужно спроектировать |
| JIVO-010 | «А для семейной ипотеки подойдут?» снова запускает список ЖК | Нужно сохранить ипотечный контекст и ответить по условиям/уточнить параметры, а не начинать новый подбор | Regression; не исправлено |
| JIVO-011 | Missing dynamic fact запускает consent-loop вместо результативной автоматической MCP-проверки | Нужен один наблюдаемый targeted MCP lookup до ответа; CTA обязан соответствовать реально выполняемому следующему действию | Исправлено и проверено локально/live 2026-07-21 |

---

## JIVO-001 — standalone телефон уходил в общий fallback

**Симптом:** клиент отправляет только свой номер, а бот отвечает общим текстом «не удалось найти информацию» или предлагает контакт без корректного phone-capture сценария.

**Actual:** раньше Jivo API захватывал телефон только при `state.awaiting_phone=True`; в остальных случаях сообщение доходило до LLM/search.

**Contract:** телефон клиента обрабатывается code-level guard; полный номер не попадает в LLM, public logs или prompt payload. Источник: `docs/BOT_ARCHITECTURE.md:670-700`.

**Решение:** в `scripts/nmbot_api_server.py:255-270` валидный номер распознаётся до любого LLM-вызова, сохраняется только в private state, а наружу возвращается безопасный phone-capture результат с handoff.

**Проверка:**

- локальный regression: phone bypasses Overmind;
- production live Jivo: standalone synthetic phone завершился `INVITE_AGENT`, HTTP 200;
- отдельный BOT_MESSAGE для handoff не ожидался.

**Статус:** исправлено и live-проверено.

---

## JIVO-002 — «куда звонить?» давало generic fallback

**Симптом:** вопрос о звонке трактовался как неудачный поиск, а не как просьба связать со специалистом.

**Actual:** keyword/operator guard не покрывал надёжно точную фразу «куда звонить?». Простое расширение regex было бы хрупким и могло потерять смысл вопроса.

**Contract:** бот не выдаёт телефон оператора или отдела; он просит номер самого клиента. Источники: `docs/BOT_ARCHITECTURE.md:670-675`, `docs/SCENARIO_MCP_CONTRACT.md:47-49`.

**Решение:** в `prompts/search_v1.txt:15-29` добавлено typed action `operator_contact`. LLM принимает смысловое решение, код валидирует action и возвращает короткий запрос телефона без запуска fallback-race/chat-stage.

**Проверка:**

- локальные tests: typed operator action и отсутствие chat-stage;
- production live Jivo: на «куда звонить?» бот показал просьбу оставить телефон клиента;
- trace: `BOT_MESSAGE`, HTTP 200, примерно 3 секунды;
- телефон оператора или внешний контакт клиенту не выдавался.

**Статус:** исправлено и live-проверено.

---

## JIVO-003 — «уточни» запускало новый список квартир

**Симптом:** после предложения «уточнить у оператора» короткое «уточни» теряло контекст и запускало новый подбор.

**Actual:** короткий ответ не всегда распознавался старым code-level fallback; в orchestration query также не было безопасного контекста предыдущего вопроса.

**Contract:** согласие на operator handoff должно сохранить смысл предыдущего предложения и перевести диалог к запросу телефона, а не начать другой сценарий. Источник: `docs/SCENARIO_MCP_CONTRACT.md:47-49`.

**Решение:** в `scripts/chat_tester_bot.py:484-519,673-727` добавлен allowlisted context только с `last_bot_question` и `last_offer_type`. В prompt добавлен semantic `operator_contact`, включая согласие на предыдущий operator offer.

**Проверка:**

- production live Jivo: после предложения специалиста сообщение «уточни» вернуло повторный запрос телефона;
- новый список квартир не появился;
- trace завершился `BOT_MESSAGE`, HTTP 200, примерно 3 секунды.

**Статус:** исправлено и live-проверено.

---

## JIVO-004 — временное сообщение и финал только после «жду»

**Симптом:** при долгом upstream-запросе клиент видел «Секунду, уточняю…», а финальный ответ появлялся только после следующего сообщения.

**Actual:** старый bridge применял `timeout=90` к тому же upstream request. При timeout upstream task отменялся, после чего bridge отправлял fallback; продолжить исходный расчёт было невозможно.

**Решение:**

- `NMBOT_BRIDGE_TIMEOUT_SECONDS` оставлен status threshold;
- добавлен `NMBOT_BRIDGE_HARD_TIMEOUT_SECONDS` с default 600 секунд;
- upstream task защищён через `asyncio.shield`;
- status fallback больше не отменяет исходную задачу;
- если финал готов позже, он автоматически отправляется в Jivo;
- hard timeout отменяет задачу чисто и не отправляет второй одинаковый fallback.

Источники кода: `scripts/nmbot_n8n_bridge_server.py:75-147,244-419`.

**Проверка:**

- локальные transport tests: 5 сценариев, включая slow→status→final и hard timeout;
- production live broad query автоматически получил ответ без второго сообщения;
- обычные live traces завершились `accepted_async → upstream → sent`;
- status-timeout path свыше 90 секунд отдельно не воспроизведён, поэтому он не обозначен как полностью live-proven.

**Статус:** исправление развернуто; обычная доставка live-подтверждена, slow-path остаётся отдельным наблюдаемым сценарием.

---

## JIVO-005 — повторная доставка одного события

**Симптом:** повторный Jivo webhook мог повторно вызвать API и затем повторно отправить тот же ответ в Jivo.

**Решение:**

- API: bounded event-id response dedup с TTL 600 секунд;
- API: per-session serialization через lifecycle-safe lock registry;
- bridge: bounded dispatch guard с TTL 600 секунд и максимумом 1024 записей;
- `/start` также проходит через serial/dedup path.

Источники: `scripts/nmbot_api_server.py:113-178,390-403,439-445`; `scripts/nmbot_n8n_bridge_server.py:25-28,104-142`.

**Проверка:**

- локальные P1 regression tests;
- loopback staging: два одинаковых inbound event дали ровно один final mock `BOT_MESSAGE`;
- production API-only smoke подтвердил одинаковый ответ при replay;
- production services active после rollout.

**Статус:** исправлено и проверено.

---

## JIVO-006 — сложный разбор причины занимал слишком много времени

**Симптом:** bridge trace показывал транспорт, но не давал быстро ответить, на каком слое произошла проблема: delivery, upstream, search, phone, operator или fallback.

**Решение:** создан read-only CLI `scripts/nmbot_jivo_dialogue_diagnose.py`, который коррелирует safe bridge JSONL с optional sanitized audit JSONL.

Инструмент классифицирует:

- async acknowledgement и final delivery;
- upstream/API failures;
- search clarify/fallback;
- phone capture и operator handoff;
- CHAT_CLOSED;
- coverage gaps.

Инструмент не печатает raw text, phone, token, payload, URL/body или полные client/chat IDs. `accepted_async` сам по себе не считается ошибкой.

**Проверка:** self-test и pytest зелёные; CLI развёрнут на VPS вместе с зависимостью `nmbot_jivo_trace_analyze.py`.

Источник: `docs/JIVO_DIAGNOSTICS.md`, `scripts/nmbot_jivo_dialogue_diagnose.py`.

**Статус:** исправлено.

---

## JIVO-007 — transport ack принимался за финальный ответ

**Симптом:** `accepted_async` ошибочно интерпретировался как клиентский placeholder или как terminal outcome.

**Actual:** `accepted_async` означает только, что bridge принял запрос и продолжит работу асинхронно. Финальная доставка — отдельный Jivo POST.

**Решение:**

- analyzer считает `accepted_async` nonterminal;
- status delivery получает отдельный `delivery_role=status` и `outcome=status_sent`;
- final delivery получает `delivery_role=final` и `outcome=sent`;
- status message не считается duplicate terminal.

Источники: `scripts/nmbot_jivo_n8n_bridge_server.py:278-299,423-475`; `scripts/nmbot_jivo_trace_analyze.py:17-105`.

**Проверка:** добавлен regression на цепочку status→final; общий локальный набор после rollout — 27 passed.

**Статус:** исправлено.

---

## Новые наблюдения по скриншоту — 2026-07-16

Скриншот добавляет три отдельных UX-проблемы. Значение телефона намеренно не записывается; зафиксирован только факт, что клиент прислал номер.

### Evidence record: последовательность со скриншота

Это не пересказ и не догадка, а дословная транскрипция видимой части скриншота. Источник: пользовательский скриншот, переданный в текущем диалоге. Время на скриншоте отображается как `15:41`; точный Jivo `event_id`, `trace_id` и raw JSON в локальном проекте не найдены.

```text
BOT:
1. Бусиновский парк — Западное Дегунино. 12.4 - 36.7 млн руб.. Этот ЖК подходит для семейной ипотеки и предлагает комфортный класс жилья с развитой инфраструктурой. Для семьи это значит, что можно сразу переехать в район, где всё рядом для жизни с детьми, а отделка поможет сэкономить время на ремонте.

2. Мичуринский парк — Очаково-Матвеевское. 14.3 - 37.6 млн руб.. Здесь тоже есть семейная ипотека, и это современный квартал с продуманной средой для жизни с детьми. Отделка позволит быстрее переехать, а инфраструктура сделает жизнь комфортной для всей семьи.

3. ЖК «Лучи» — Солнцево. 11.0 - 31.6 млн руб.. Популярный семейный проект с собственной инфраструктурой, который также подходит под семейную ипотеку. Это удобно, если вы ищете готовое решение для семьи в районе Солнцево, где всё необходимое будет под рукой.

BOT: Какой из этих вариантов хотите рассмотреть подробнее?

CLIENT: а для семейной ипотеки подойдут?

BOT:
Да, нашла несколько вариантов, которые подходят под семейную ипотеку. Это хорошие проекты для семьи, где можно сразу смотреть и условия покупки.

1. Бусиновский парк — Западное Дегунино. 12.4 - 36.7 млн руб.. Этот ЖК подходит для семейной ипотеки и предлагает комфортный класс жилья с развитой инфраструктурой. Для семьи это значит, что можно сразу переехать в район, где всё рядом для жизни с детьми, а отделка поможет сэкономить время на ремонте.

2. Мичуринский парк — Очаково-Матвеевское. 14.3 - 37.6 млн руб.. Здесь тоже есть семейная ипотека, и это современный квартал с продуманной средой для жизни с детьми. Отделка позволит быстрее переехать, а инфраструктура сделает жизнь комфортной для всей семьи.

3. ЖК «Лучи» — Солнцево. 11.0 - 31.6 млн руб.. Популярный семейный проект с собственной инфраструктурой, который также подходит под семейную ипотеку. Это удобно, если вы ищете готовое решение для семьи в районе Солнцево, где всё необходимое будет под рукой.

BOT: Какой из этих вариантов хотите рассмотреть подробнее?

CLIENT: [номер телефона — скрыт]

JIVO: Диалог переведен на оператора
```

### Разбор evidence record по шагам

| Шаг | Факт | Что должен был сделать runtime | Что произошло |
|---|---|---|---|
| 1 | До follow-up уже показаны три конкретных ЖК | Сохранить их как `last_options/visible_options` | В ответе снова использован общий список |
| 2 | Клиент спрашивает «а для семейной ипотеки подойдут?» | Распознать проверку текущих вариантов, а не новый поиск | Запущен повторный список из тех же трёх ЖК |
| 3 | Бот уже утверждает, что варианты подходят под семейную ипотеку | Дать проверяемое объяснение по условиям или обозначить границу данных | Повторено рекламное утверждение без новой проверки и без ответа «почему» |
| 4 | После повторного списка клиент отправляет номер | По текущему требованию не переводить автоматически оператору | Jivo показывает `Диалог переведен на оператора` |

### Что доказано и чего в evidence нет

**Доказано скриншотом:** повторная выдача тех же вариантов; отсутствие содержательного mortgage follow-up; автоматический operator handoff после телефона.

**Не доказано скриншотом:** какой именно `scenario`, `action`, `payload_stage`, MCP-ответ и state были внутри runtime. Поэтому причина на уровне конкретного classifier/router пока обозначается как гипотеза, а не как подтверждённый root cause.

**Для полноценного raw/audit record нужны:** безопасный `trace_ref`, порядок `CLIENT_MESSAGE → upstream → BOT_MESSAGE/INVITE_AGENT`, typed action, `scenario`, `facets`, `mortgage_type`, список safe option refs и финальный outcome. Сырые телефоны и полный payload по-прежнему не нужны.

### JIVO-008 — автоматический перевод на оператора нужно отключить

**Наблюдение:** после сообщения с номером клиента интерфейс показывает «Диалог переведен на оператора».

**Actual:** текущий phone-capture path сохраняет номер в private state и возвращает `handoff_to_operator=True`; Jivo API публикует `INVITE_AGENT`, поэтому диалог действительно передаётся оператору. Это ранее считалось правильным handoff-сценарием и было live-проверено.

**Desired:** автоматического перевода на оператора больше нет. Контакт должен стать заявкой на обратный звонок, после чего бот подтверждает сохранение заявки через `BOT_MESSAGE` и оставляет диалог активным.

**Локальное решение:** phone capture отделён от транспорта Jivo. Код до LLM создаёт идемпотентную запись в приватном durable outbox и возвращает `capture_contact`, `handoff_to_operator=False`. Полный номер хранится только в приватной записи; публичный результат содержит `queued|duplicate` и непрозрачный `lead_ref`. Outbox — локальная граница будущего CRM-адаптера, а не уже выполненная HTTP-отправка в CRM.

**Статус:** локально реализовано и покрыто regression-тестами; реальный CRM adapter и production rollout ещё не выполнены.

### JIVO-009 — бессмысленное сообщение должно выводить человека в диалог

**Наблюдение:** на скриншоте показан отдельный пользовательский ввод, который не содержит понятного запроса. Вместо специального вовлекающего ответа должен работать самостоятельный сценарий продолжения диалога.

**Actual:** текущий общий маршрут может приводить к подбору объектов, generic fallback или операторскому пути, хотя намерение пользователя ещё не определено.

**Contract:** LLM должна определить смысл или его отсутствие, а код — выполнить разрешённое действие; при недостатке данных допустим один короткий уточняющий вопрос. Источники: `docs/LLM_DECISION_ARCHITECTURE_TZ.md:525-563`, `docs/SCENARIO_MCP_CONTRACT.md:47-49`.

**Desired:** отдельный сценарий `meaningless/unclear` должен дружелюбно вернуть человека в разговор: не выдумывать факты, не присылать список ЖК, не переводить к оператору автоматически, а предложить понятные варианты следующего шага или задать один простой вопрос.

**Локальное решение:** перед MCP/search добавлен semantic planner. Typed decision `recover_dialogue` имеет `search_policy=forbidden`: код задаёт один короткий вопрос, увеличивает `recovery_count`, не запускает поиск и сбрасывает счётчик после успешного содержательного хода. Ошибка/низкая уверенность planner также безопасно ведёт в recovery, а не в новый список.

**Статус:** локально реализовано и покрыто first/repeated/reset и planner-failure regressions; production ещё не проверен.

### JIVO-010 — вопрос о семейной ипотеке снова вызвал список ЖК

**Наблюдение:** после списка объектов клиент спросил: «а для семейной ипотеки подойдут?». Бот снова прислал новый список ЖК вместо ответа по ипотечному признаку или уточнения условий.

**Actual:** на скриншоте виден повторный подбор объектов. Это означает, что текущий маршрут не сохранил или не применил смысл вопроса «подойдут ли для семейной ипотеки» как проверку ипотечной пригодности.

**Contract:** при содержательном follow-up нужно сохранять контекст предыдущего ответа и выбирать соответствующий typed action, а не начинать новый подбор без необходимости. Сценарий `clarify` уже существует для недостатка условий; текущий результат показывает, что ипотечный follow-up отдельно не защищён regression-проверкой.

**Desired:** ответить по семейной ипотеке на основании подтверждённых данных, либо честно уточнить недостающие параметры. Нельзя повторно выдавать список ЖК только из-за короткого follow-up-вопроса.

**Локальное решение:** no-MCP planner работает до search. `consultation_answer`, `conversation_answer`, compare/recommend/continue/select по существующим вариантам преобразуются в `answer_current_options`, `target=current_options`, `search_policy=forbidden`. Ответ пишет отдельный consultation presenter из безопасного allowlisted state; `visible_options/last_options` сохраняются без нового shortlist. Если подтверждённых ипотечных фактов нет, presenter обязан обозначить границы данных, а не обещать доступность программы.

**Статус:** локально исправлено для exact family-mortgage regression и общего current-options класса; production ещё не проверен.

### JIVO-011 — missing parking повторяется вместо результативной MCP-проверки

**Симптом:** после выбора ЖК клиент спросил, можно ли купить парковочное место. Бот ответил, что наличие паркинга не подтверждено, и предложил «проверить точную актуальность». После первого `да` бот повторил отсутствие паркинга и добавил отсутствие цены машиноместа; после второго `да` перешёл к сбору контакта оператора.

**Evidence:** пользовательская транскрипция production Jivo-диалога от 2026-07-20. VPS `logs/planner_trace-2026-07-20.jsonl:185` подтверждает корректный semantic plan: `selected_reference="Левел Павелецкая Сити"`, `resolved_subject="parking"`, `requested_facts=["parking"]`, `facts_needed=["parking"]`, `requires_enrichment=true`, canonical plan valid.

**Actual:** `TurnProcessor` для `ANSWER_SELECTED_OPTION` автоматически вызывает `search_service.enrich_selected()` (`nmbot_v2/runtime.py:103-112`). Adapter проверяет cache и затем выполняет bounded selected-option enrichment с `facts_needed` (`scripts/nmbot_runtime_adapter.py:1017-1065`). В наблюдаемом ходе enrichment не опубликовал `parking`, поэтому runtime установил pending `selected_live_fact_consent` (`nmbot_v2/runtime.py:263-272`), а response/recipe показали CTA `Проверить точную актуальность по этому ЖК?`. Следующий consent route сам MCP lookup не гарантирует: он может повторить missing/clarification, а затем перейти к operator handoff.

**Contract:** если запрошенного dynamic fact нет в сохранённой карточке, бот должен до клиентского ответа автоматически выполнить один targeted MCP lookup по выбранному exact ЖК и конкретному `requested_fact`. Нельзя просить разрешение «проверить», если следующий ход не запускает эту проверку. Нельзя повторять тот же missing после `да` без нового evidence.

**Desired:** один клиентский вопрос про парковку → автоматический targeted MCP lookup → либо grounded ответ о parking/parking_price, либо честная фиксация, что MCP не подтвердил факт, и одно конкретное предложение operator live-check. Дополнительное согласие допустимо только перед оператором, но не перед MCP-поиском.

**Анализ:** semantic planner и selected-object routing в этом случае сработали правильно; проблема находится после определения `facts_needed`. Требуется отдельно проверить enrichment metadata: cache hit/miss, фактический gateway attempt, timeout (`NMBOT_V2_ENRICHMENT_ITEM_TIMEOUT`), exact identity match, parser/merge и публикацию `fresh_facts`. Без этих данных нельзя утверждать, что источник не содержит parking: текущий результат доказывает только, что факт не дошёл до `OptionCard`.

**Предложенное решение:**

1. Добавить safe trace результата selected enrichment: `requested_facts`, `attempted`, `source`, bounded `skipped/error_code`, `fresh_facts`; без raw payload и названий/ID клиента.
2. На missing в base/cached card всегда выполнять ровно один targeted enrichment по exact selected ЖК; stale cache без requested fact не считать ответом.
3. Проверить и настроить bounded timeout по реальной VPS-латентности, не добавляя бесконечный retry/repair loop.
4. Если targeted lookup вернул факт — ответить сразу и не создавать pending consent.
5. Если lookup завершился успешно, но evidence отсутствует — не использовать CTA «проверить актуальность»; предложить один конкретный operator live-check.
6. Если lookup упал технически — выдать безопасный recovery и записать error event; не трактовать transport failure как доказанное отсутствие паркинга.

**Acceptance:** первый вопрос автоматически создаёт ровно один MCP enrichment attempt; `да` не требуется для MCP; при найденном parking нет operator offer; при подтверждённом missing есть максимум один operator CTA; после согласия нет повторного missing; один клиентский вопрос в каждом ответе; trace различает `not_found` и технический failure.

**Решение:** default selected-enrichment timeout увеличен с `0.8` до `20.0`
секунд без retry-loop. Adapter публикует bounded outcome trace (`applied`,
`not_found`, `timeout`, `technical_failure`, `unavailable`) и считает успехом
только публикацию запрошенного факта. После успешного lookup без evidence runtime
создаёт один operator-consent pending для любого missing requested fact; первое
`да` сразу переводит в `contact_name`. Technical failure получает отдельный
recovery и не выдаётся за отсутствие факта. Клиентский CTA теперь прямо говорит,
что запрос будет передан оператору.

**Verification:** regression покрывает lookup дольше прежних 0.8 секунды,
grounded answer, confirmed missing, technical timeout и первое consent без
повторного enrichment. Targeted gate: `131 passed`; repository gate:
`638 passed`. Production backup:
`/home/neiro/novostroy-bot/backups/deploy-20260720-212613`; три runtime hash
совпали, API/bridge health green, свежих ошибок нет. Exact stateful Jivo smoke
выбрал `Левел Павелецкая Сити`, затем отдельным ходом спросил про парковку;
ровно один MCP-backed enrichment вернул подтверждённый `parking`, поэтому CTA
оператора корректно не появился.

**Статус:** исправлено, задеплоено и проверено в точном stateful production
Jivo-контуре. Ветка confirmed-missing → первое `да` подтверждена локальным
детерминированным regression, поскольку live MCP в контрольном ходе вернул факт.

---

## Production rollout

Развёрнуты только Jivo runtime-файлы:

- `scripts/nmbot_api_server.py`
- `scripts/chat_tester_bot.py`
- `prompts/search_v1.txt`
- `scripts/nmbot_n8n_bridge_server.py`
- `scripts/nmbot_jivo_trace_analyze.py`

Backup: `/home/neiro/novostroy-bot/backups/jivo-rollout-20260716T122921Z`.

Перезапущены только:

- `novostroy-bot-api.service`
- `novostroy-bot-n8n-bridge.service`

После rollout оба сервиса `active/running`; health API `8088` и bridge `8093` успешны; свежие journal-проверки не обнаружили traceback/error.

## Открытые пункты

1. Нужен отдельный live trace с upstream-длительностью более 90 секунд, чтобы подтвердить именно status fallback → поздний final.
2. Для phone capture желательно добавить приватный sanitised per-turn audit: `phone_detected`, длина/last4/ref, но не полный номер.
3. При каждом новом production-инциденте сначала запускать `nmbot_jivo_dialogue_diagnose.py`, затем проверять конкретный trace, а не делать вывод по одному `accepted_async`.
4. Реализовать настоящий CRM adapter поверх local callback outbox: контракт payload/auth/idempotency/retry/receipt пока не задан и не должен додумываться.
5. Перед production rollout сделать backup и синхронизировать только затронутые Jivo runtime-файлы; затем проверить phone callback без `INVITE_AGENT`, recovery и current-options mortgage в реальном Jivo.

## Локальная архитектурная итерация после JIVO-008—010

Реализовано локально, без VPS/Jivo/CRM/LLM/network:

- pre-MCP `TurnDecision`: semantic planner выбирает search или запрещённый для поиска action до вызова MCP;
- `capture_contact`: приватный durable outbox, атомарная запись, права `0700/0600`, идемпотентность по session+event либо session+phone;
- `recover_dialogue`: контекстный вопрос и счётчик повторов без списка ЖК и оператора;
- `answer_current_options`: консультация/сравнение/рекомендация по сохранённому списку без повторного поиска;
- безопасная выдача runtime meta: без raw Jivo payload/IDs и полного planner payload;
- planner exception переводится в recovery, а не в общий upstream fallback;
- phone-like sanitizer не удаляет семизначные цены: телефонный шаблон ограничен диапазоном 10–15 цифр.

Локальная проверка: `py_compile` успешен; focused suite — `35 passed`. Предупреждения только тестовые `aiohttp NotAppKeyWarning` из string app keys.

## Источники

- `reports/JIVO_SCREENSHOT_ISSUES_STATUS_2026-07-16.md` — исходный разбор A–D.
- `docs/BOT_ARCHITECTURE.md:670-700` — phone privacy и operator contract.
- `docs/SCENARIO_MCP_CONTRACT.md:47-49` — default/operator decision contract.
- `docs/JIVO_DIAGNOSTICS.md` — trace and rollout diagnostics.
- `docs/JIVO_BOT_API_INTEGRATION_PLAN.md:493-516` — текущий async Jivo flow.
- Live Jivo widget verification: 2026-07-16, после rollout.
