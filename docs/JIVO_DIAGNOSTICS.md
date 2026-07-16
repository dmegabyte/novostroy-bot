# Jivo/nmbot диагностика

## Быстрые команды

Анализ production trace-лога без вызова Jivo или LLM:

```bash
bash scripts/nmbot_jivo_audit.sh --last 200
```

Локальный анализ файла:

```bash
python3 scripts/nmbot_jivo_trace_analyze.py /path/to/n8n_bridge_structured.jsonl --strict
```

Статическая проверка архитектурных контрактов:

```bash
python3 scripts/nmbot_architecture_preflight.py --strict
```

## Инварианты одного turn

Один входящий turn должен иметь ровно один terminal outcome:

- финальный ответ;
- явный handoff оператору;
- явная ошибка/timeout.

`accepted_async` — промежуточное подтверждение приёма, а не ответ клиенту. Само по себе оно не доказывает, что Jivo показал клиенту временную реплику: для UX-вывода нужна корреляция с конкретным диалогом и Jivo-side delivery/rendering evidence.

Trace должен позволять связать безопасные `trace_id`/`event_id_ref` с этапами:

```text
request_received -> upstream_request_start -> upstream_response
-> jivo_request_start -> jivo_response_returned(sent|delivered)
```

Скрипты намеренно не печатают текст клиента, payload, токены и Authorization.

## P1: staging-проверка Jivo adapter

На 2026-07-16 в staging добавлены per-session serialization и bounded event-id dedup для `CLIENT_MESSAGE`. Синхронизация потребовала также обновить `chat_tester_bot.py` и `followup_intent_classifier.py`: прежний staging-набор не содержал импортов, необходимых `nmbot_api_server.py`.

Проверенный минимум:

- `py_compile` трёх staging-файлов;
- изолированный localhost API: два одинаковых синтетических `/start` события вернули одинаковый `200 BOT_MESSAGE` без вызова LLM, Jivo или legacy Telegram;
- legacy `novostroy-bot-staging.service` не используется как release gate Jivo.

Это не production deploy и не Jivo end-to-end проверка. Перед переносом в production нужны отдельные Jivo staging/dialog/regression evidence и явное подтверждение на deploy.

## Внешний HTTP smoke staging

После localhost-проверки был кратковременно открыт отдельный staging-only HTTP listener на VPS. Один synthetic `/start` прошёл путь `external HTTP -> staging bridge -> staging API -> mock receiver` и дал один финальный `BOT_MESSAGE`. Повтор того же event-id был подавлен bridge-слоем и не создал второй outbound message.

Listener после проверки остановлен; production API и bridge не менялись. Это доказывает доступность внешнего HTTP-транспорта и duplicate suppression, но не является Jivo E2E: Jivo требует публичный HTTPS endpoint и provider configuration.

## P1: production rollout

На 2026-07-16 P1 перенесён в production после staging-проверок:

- API использует per-session serialization и bounded event-id response dedup;
- bridge использует bounded duplicate suppression перед повторной отправкой финального `BOT_MESSAGE`;
- TTL обоих защитных кэшей — 600 секунд, лимит bridge-кэша — 1024 записи;
- rollback-бэкап: `/home/neiro/novostroy-bot/backups/p1-dedup-20260716-104338/`.

После рестарта `novostroy-bot-api.service` и `novostroy-bot-n8n-bridge.service` оба health-check прошли. Два одинаковых локальных synthetic `/start` события вернули равные `200 BOT_MESSAGE` без вызова LLM, n8n или Jivo; bridge guard статически подтвердил один dispatch на event-id.

Это подтверждает защиту от повторной обработки, но не подтверждает исходный UX-симптом: реальный Jivo delivery и отображение финального ответа клиенту не запускались и требуют коррелированного диалога.

## LLM-first уточнение после поиска

На 2026-07-16 search-контракт расширен двумя полями: `action` (`search` или
`clarify`) и `clarification_question`. Search LLM по-прежнему сначала вызывает
MCP. Если после поиска она считает условий недостаточными для полезного
shortlist, то возвращает только `action="clarify"` и один короткий вопрос без
фактов, цен, вариантов или раннего предложения оператора.

`OvermindClient.ask()` принимает только валидный `clarify`: непустую строку не
длиннее 300 символов. Тогда вопрос LLM уходит клиенту напрямую, а fallback-race
и chat-stage не запускаются. Неизвестный или невалидный action сохраняет
прежний путь обработки.

Production rollout затронул только `prompts/search_v1.txt` и
`scripts/chat_tester_bot.py`; rollback-бэкап:
`/home/neiro/novostroy-bot/backups/deploy-llm-clarify-20260716-112711/`.
После рестарта `novostroy-bot-api.service` health-check прошёл. Реальный Jivo
повтор широкого запроса после `/start` показал клиенту уточняющий вопрос без
второго сообщения клиента; trace завершился
`accepted_async -> upstream -> sent` (upstream 11 086 мс). Это подтверждает
данный сценарий, но не заменяет регрессионные проверки для других типов поиска.

## Глубокая диагностика одного диалога

Для разбора безопасной цепочки конкретного Jivo turn используйте локальный
read-only инструмент:

```bash
python3 scripts/nmbot_jivo_dialogue_diagnose.py \
  /path/to/n8n_bridge_structured.jsonl --trace <trace_id> --strict
```

Опциональный `--audit-log /path/to/sanitized_turn_audit.jsonl` обогащает вывод
только разрешёнными полями: анонимные refs, intent, факт поиска и число
результатов, handoff, terminal event, latency и безопасная phone-мета
(`phone_detected`, длина, last4/ref). Текст, полный телефон, payload, token,
URL, client/chat id инструмент не выводит.

Диагноз показывает `Actual / Contract / Desired`, этап (`delivery_complete`,
`upstream_failure`, `delivery_missing`, `api_safe_fallback`, `main_search`,
`main_search_clarify`, `operator_handoff`, `phone_captured`, `chat_closed` или
`coverage_gap`) и следующий проверочный шаг. `accepted_async` — нормальный
transport-ack и не является ошибкой сам по себе. Без явного Desired инструмент
не называет расхождение багом; при успешной доставке без связанного audit-event
он сообщает о пробеле наблюдаемости.
