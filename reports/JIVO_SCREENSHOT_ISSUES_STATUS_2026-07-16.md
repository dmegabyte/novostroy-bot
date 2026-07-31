# Jivo screenshots — issue status

Дата: 2026-07-16.

Важно: скриншоты пользователя могут быть историческими. Один только timestamp на картинке не доказывает, на какой версии кода был диалог. Поэтому ниже я не называю screenshot-дефект исправленным, если нет текущего Jivo/VPS regression или live-воспроизведения именно этого сценария.

## Короткий ответ

Нет, по текущим фактам нельзя сказать, что все дефекты со скриншотов исправлены.

Что подтверждено сейчас:

- transport-слой Jivo bridge сейчас работает в асинхронном режиме: входящий webhook быстро получает `accepted_async`, а финальный `BOT_MESSAGE` отправляется отдельным запросом в Jivo API; есть fallback на текст `Секунду, уточняю...` при timeout/unavailable upstream. Source: VPS `nmbot_n8n_bridge_server.py:42-68`, `103-121`, `145-160`, `175-247`, `287-323`.
- сервисы `novostroy-bot-api.service` и `novostroy-bot-n8n-bridge.service` сейчас active/running. Source: live VPS `systemctl --user status`, проверка в этой сессии.
- broad-query уточнение после LLM clarify rollout live-проверено: после `/start` реальный Jivo broad-query дал уточняющий вопрос без второго сообщения клиента, trace `accepted_async -> upstream -> sent`. Source: `docs/JIVO_DIAGNOSTICS.md:73-93`.

Что не подтверждено:

- нет текущего reproduction/regression для четырёх конкретных screenshot-сценариев A–D;
- нет доказательства, что exact фраза `куда звонить?`, standalone phone-like message и `уточни` после оффера оператора уже проходят правильно в Jivo end-to-end.

## Контракты

- Бот не должен выдавать клиенту телефоны операторов, менеджеров, отделов продаж, застройщика или внешние контакты. Единственный разрешённый телефонный сценарий — попросить номер самого клиента. Source: `docs/BOT_ARCHITECTURE.md:670-675`.
- Если клиент прислал свой номер, `phone_capture` обрабатывается code-level guard; полный номер не уходит в LLM, публичные логи и prompt payload. Source: `docs/BOT_ARCHITECTURE.md:685-700`.
- Для `operator` бот должен коротко подтвердить передачу к человеку; для `default` — задать один короткий уточняющий вопрос, а не показывать ЖК до понимания задачи. Source: `docs/SCENARIO_MCP_CONTRACT.md:47-48`.

## Таблица дефектов

| ID | Screenshot evidence | Actual: screenshot/current runtime evidence | Contract | Desired | Status | Evidence/ref | Minimal next repair/test |
|---|---|---|---|---|---|---|---|
| A | После phone-like user message бот отвечает generic fallback `По запросу не удалось... передать контакт...` и повторяет его. | Screenshot показывает смешение phone-state и generic fallback. В текущем Jivo API phone capture срабатывает только если `state.awaiting_phone` уже true и номер валиден; standalone phone-like message дальше идёт в main `client.ask()`. В legacy Telegram runtime guard шире: валидный телефон ловится до LLM/search независимо от `awaiting_phone`, но это не Jivo API release-gate. | Если клиент прислал свой номер, полный номер не идёт в LLM/public logs/prompt payload; бот отвечает phone-capture farewell. | Любой валидный клиентский телефон в Jivo API должен ловиться до LLM, отвечать phone-capture farewell и сохранять handoff context безопасно; не повторять generic upstream fallback. | `not_fixed` | Jivo API: `nmbot_api_server.py:255-270`; Telegram runtime: `chat_tester_bot.py:4177-4195`, `7192-7216`; contract: `BOT_ARCHITECTURE.md:685-700`; safe fallback text: `chat_tester_bot.py:60-64`. | Добавить Jivo API regression: standalone phone-like message после `/start` и after operator offer. Проверить, что ответ — phone capture, а trace не содержит LLM/search stage для номера. |
| B | На `куда звонить?` бот повторяет generic fallback вместо просьбы оставить свой телефон. | Current source has explicit operator guard for words like `оператор`, `менеджер`, `связь`, `контакт`, `номер`, but exact phrase `куда звонить?` is not directly covered by that regex. Возможно, LLM-orchestrator поймёт это семантически, но текущего Jivo regression нет. | Никогда не выдавать наши телефоны; при просьбе `куда позвонить`/`телефон менеджера` попросить номер клиента или продолжить в чате. | Semantic routing должен отнести `куда звонить?` к operator handoff and phone request; generic fallback недопустим. | `partially_fixed` | Explicit guard: `chat_tester_bot.py:4060-4074`; Jivo API explicit operator branch: `nmbot_api_server.py:272-285`; contract: `BOT_ARCHITECTURE.md:670-675`. | Jivo regression для exact phrase `куда звонить?`: ожидать просьбу оставить свой номер, без operator numbers и без generic fallback. Если падает — чинить semantic decision/routing, не keyword bypass. |
| C | Бот спрашивает `Хотите, чтобы я уточнила ... у оператора?`, пользователь отвечает `уточни`, но бот выдаёт новый apartment list. | Current local fallback recognises only short yes-like replies as `operator_contact_accept`: `да`, `ага`, `ок`, `хорошо`, `давай`, etc. Слово `уточни` в этом code-level fallback не покрыто. При этом смысл follow-up должен определять LLM-orchestrator; текущей Jivo проверки именно этой пары нет. | Если пользователь согласился на operator handoff, бот должен перейти в просьбу оставить номер и сохранить контекст, а не запускать новый подбор. | `уточни` после явного оффера оператора должно сохранять operator intent/context и просить телефон. | `unverified` | Local fallback: `chat_tester_bot.py:2327-2331`, `2376-2388`; operator accept branch: `chat_tester_bot.py:7740-7762`; contract: `SCENARIO_MCP_CONTRACT.md:48`. | Jivo stateful regression: бот сам предлагает уточнить у оператора → user `уточни` → ожидать operator contact request. Если ломается — править LLM follow-up/orchestrator contract. |
| D | В диалоге появляется temporary `Секунду, уточняю...` / urgent-phone message; финальный результат виден только после later user `жду`. | Это transport symptom, отдельно от answer-quality. Current bridge действительно может отправить fast fallback при upstream timeout/unavailable и затем в normal path отправляет final event через async Jivo API. Документированная live-проверка broad-query после clarify rollout показала final answer without second client message, но она не доказывает исправление именно screenshot-dialog с temporary fallback. | Transport не должен требовать второго сообщения клиента для доставки финального ответа; answer-quality не должна маскироваться transport fallback. | Если upstream долго думает, temporary fallback допустим только как честный status; финальный answer должен доходить автоматически, без `жду`. | `partially_fixed` | Bridge fallback: `nmbot_n8n_bridge_server.py:42-68`; async dispatch: `103-121`, `145-160`, `175-247`, Jivo send: `287-323`; dedup note: `docs/JIVO_DIAGNOSTICS.md:68-71`; clarify live evidence: `docs/JIVO_DIAGNOSTICS.md:89-93`. | Replay/live Jivo test with slow upstream or trace from screenshot class: confirm final `BOT_MESSAGE` is sent after fallback without second user message. Keep this separate from phone/routing fixes. |

## Отдельно: что реально live-verified fixed

Есть один связанный, но не равный screenshot-дефекту факт: broad-query LLM clarify после rollout live-проверен в Jivo. Он снижает риск раннего generic fallback/listing при слишком широком запросе: search LLM возвращает `action="clarify"` и короткий вопрос; валидный clarify идёт клиенту напрямую, fallback-race и chat-stage не запускаются. Source: `chat_tester_bot.py:484-499`; `docs/JIVO_DIAGNOSTICS.md:73-93`.

Статус этого отдельного сценария: `fixed_and_live_verified`.

Но это не закрывает A, B, C и D как screenshot-дефекты.

## Приоритеты

1. Phone privacy / phone capture в Jivo API: валидный клиентский номер ловить до LLM не только при `awaiting_phone`; проверить, что полный номер не попадает в prompt/log/public response.
2. Operator contact semantic routing: `куда звонить?` и похожие фразы должны вести к просьбе оставить номер клиента, без выдачи operator numbers и без generic fallback.
3. Follow-up confirmation state: `уточни` после вопроса про оператора должно сохранять operator handoff, а не запускать новый подбор.
4. Transport regression: отдельно доказать, что temporary fallback не требует второго client turn для финального ответа.

Ремонт лучше делать через semantic decision/orchestrator/state contracts. Не стоит добавлять грубый keyword heuristic или LLM bypass, кроме обязательного code-level phone privacy guard.

## Privacy/sanitization check

- В отчёте нет raw phone из screenshots; использовано только `phone-like message` / `phone-like number`.
- В отчёте нет tokens, payload dumps, client IDs или provider secrets.
- В отчёте нет длинных непрерывных digit runs; даты и line refs оставлены только как короткие служебные ссылки.
