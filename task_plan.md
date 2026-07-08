# Task Plan — H026 Ideal Irina UX

Goal: обеспечить лучший User Experience для nmbot: Ирина отвечает как живой консультант, продаёт объект по фактам MCP, задаёт правильный следующий вопрос, а каждый контрольный диалог сохраняется и оценивается.

## Phases

1. Define Ideal Irina UX concept — in progress.
2. Add persistent dialog review journal — pending.
3. Add UX checks to dialog suite — pending.
4. Run control dialog and append first review — pending.
5. Update prompt/CODEX if review finds concrete defects — pending.

## Constraints

- No hallucinations: only MCP/search_response facts.
- No eval.
- Every dialog review must be written to `logs/dialog_reviews.md`.

## Sources

- `docs/CODEX.md`
- `prompts/chat_v1.txt`
- NN/g chatbot UX article
- Intercom chatbot CX article

---

# Task Plan — 2026-07-02 MVP stage orchestrator

Goal: сделать минимальный stage-based слой для Ирины без переусложнения: `first_list`, `selected_object`, `operator_handoff`, а нестандартные случаи оставить текущему fallback/freeform. Главный результат — первый список выглядит как нормальный подбор, выбранный ЖК ведёт к оператору, технические поля не протекают клиенту.

## Phases

1. Recon current router/presenter points — in progress.
2. Implement minimal `StageDecision` helpers and semantic-card prompt wrapper — pending.
3. Wire only safe MVP branches: first_list / selected_object / operator_handoff — pending.
4. Add/adjust validators to block `сдача/готовность`, `верхняя точка`, early/live availability promises — pending.
5. Local tests on real cases: family first list, `ЖК Лучи`, `да`, availability question, nonstandard fallback — pending.
6. Prod deploy gate: backup, upload, py_compile, restart, markers, live/prod smoke, cost — pending.

## Constraints

- Search model remains `google/gemini-3.1-flash-lite-preview`.
- Do not let model invent facts: only semantic cards from MCP/search_response.
- Full dialogue map lives in code/docs, not in model prompt.
- MVP stages only: `first_list`, `selected_object`, `operator_handoff`; other cases use existing logic/fallback.
- No `eval`.
- Git commit impossible in current shadow workspace unless an actual git checkout is provided.

## Sources

- `docs/IRINA_DIALOGUE_MAP_V1.md`
- `docs/IDEAL_IRINA_UX.md`
- `prompts/chat_v1.txt`
- `scripts/chat_tester_bot.py`

## Errors Encountered

| Error | Attempt | Resolution |
|---|---|---|
| Previous reason-layer leaked technical phrases and weak budget text | Deployed comparison renderer | Replace/avoid with stage presenter and semantic cards |
| Project path is not a git repo | `git status` in `/tmp/opencode-run-nmbot/project` and `/home/ser/ai/projects/nmbot` | Cannot commit until user provides real git checkout |

---

# Task Plan — 2026-07-03 Scenario-aware first_list style

Goal: закрепить в коде стиль первого списка, который пользователь выбрал вручную: для семейного сценария показывать реальные школы/сады/парки/поликлиники из MCP/search facts, а не сухую карточку с ценой/сроком. При этом не выдумывать инфраструктуру, сохранять максимум 3 ЖК и ровно один следующий вопрос.

## Phases

1. Recon code/tests/docs for current first_list chain — complete.
2. Patch normalizer aliases for family infrastructure fields — complete.
3. Patch scenario-aware first_list fact order and concrete benefits — complete.
4. Add local tests for family first_list infrastructure style — complete.
5. Run local compile/tests and report prod status honestly — complete.

## Verification

- `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py` — passed.
- `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 31/31.
- Prod/VPS deploy completed on `/home/neiro/novostroy-bot` with backup `backups/deploy-20260703-122246`.
- VPS smoke `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 31/31.
- `python3 scripts/or_cost.py` on VPS after smoke: today `$1.20`, week `$8.39`, month `$5.41`, total `$35.84`.

## Constraints

- No hallucinations: use only fields present in current option/MCP search response.
- No `eval`.
- First list stays stage presenter only; do not rewrite whole routing.
- Local green is not prod green; after code change, final report must say whether VPS/prod smoke was done.

## Sources

- `docs/IDEAL_IRINA_UX.md`
- `docs/IRINA_DIALOGUE_MAP_V1.md`
- `docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md`
- `prompts/search_v1.txt`
- `scripts/chat_tester_bot.py`
- `scripts/nmbot_test_agent.py`

---

# Task Plan — 2026-07-03 Advice/operator follow-up fix

Goal: исправить live-dialog regression после семейного first_list: фразы клиента `а ты что посоветуешь?` / `твой совет какой` должны вести к человеческой рекомендации по текущему списку, а `как связаться с оператором?` должен переводить в операторскую воронку с текущим контекстом, даже если ЖК ещё не выбран. Семантику по-прежнему определяет LLM planner; код только безопасно исполняет выбранный action.

## Phases

1. Recon live bad-dialogue route and current follow-up planner — complete.
2. Patch LLM planner prompts for `recommend_options` and direct operator request — complete.
3. Add deterministic recommendation and operator-context presenters — complete.
4. Add H029 regression tests for advice/operator cases — complete.
5. Run local compile/tests — complete.
6. Deploy to VPS, restart service, run remote smoke and cost snapshot — complete.

## Local Verification

- `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py followup_intent_classifier.py` — passed.
- `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 33/33 locally.
- New tests verify: advice does not loop into “Давайте сравним”, family recommendation picks the strongest factual option, operator request without selected ЖК asks for phone and passes current context instead of asking `первый/второй/третий`.
- Prod/VPS deploy completed on `/home/neiro/novostroy-bot` with backup `backups/deploy-20260703-124828`.
- VPS service restarted and active: `novostroy-bot.service`, PID `2296210`, command `python3 scripts/chat_tester_bot.py`.
- VPS smoke `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 33/33 after corrected file sync.
- `python3 scripts/or_cost.py` on VPS after smoke: today `$1.34`, week `$8.53`, month `$5.55`, total `$35.98`.

## Constraints

- No hallucinations: recommendation uses only current `visible_options` / MCP facts.
- No regex semantic router: LLM planner chooses `recommend_options` / `operator_live_check`; code executes safely.
- No `eval`.
- Local green is not final; prod/VPS smoke is required before saying “готово”.

## Sources

- `docs/IDEAL_IRINA_UX.md`
- `docs/IRINA_DIALOGUE_MAP_V1.md`
- `followup_intent_classifier.py`
- `scripts/chat_tester_bot.py`
- `scripts/nmbot_test_agent.py`

---

# Task Plan — 2026-07-03 Bot-visible history command

Goal: добавить отдельную Telegram-команду `/hisotry` (и нормальный alias `/history`), чтобы прямо из бота можно было посмотреть последние запросы клиента, ответы Ирины и технический цикл поиска: intent/plan, MCP/search_response, buttons, cost.

## Phases

1. Recon existing dialog logs and Telegram command handlers — complete.
2. Implement history formatter, safe truncation and Telegram chunking — complete.
3. Add `/history` and `/hisotry` command handlers — complete.
4. Update docs for bot-visible history command — complete.
5. Add H029 regression tests — complete.
6. Run local verification, sync to VPS, remote smoke, git commit/push — in progress.

## Local Verification

- `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py followup_intent_classifier.py` — passed.
- `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 36/36.
- New H029 tests verify history output includes user text, bot answer, intent, plan, MCP/search facts, buttons and cost; long history is chunked for Telegram; both `/history` and `/hisotry` are registered.

## Remote Verification

- Prod/VPS backup created: `backups/deploy-20260703-131729`.
- Synced runtime/tests/docs to `/home/neiro/novostroy-bot`.
- Remote `python3 -m py_compile scripts/chat_tester_bot.py scripts/nmbot_test_agent.py followup_intent_classifier.py` — passed.
- `novostroy-bot.service` restarted and active, PID `2307297`, command `python3 scripts/chat_tester_bot.py`.
- Remote `python3 scripts/nmbot_test_agent.py --suite h029 --json` — passed 36/36.
- Remote `python3 scripts/or_cost.py`: today `$1.59`, week `$8.78`, month `$5.80`, total `$36.23`.

## Constraints

- Keep exact typo `/hisotry` because the user requested it.
- Also support `/history` as normal spelling.
- Do not expose secrets; history shows existing compact dialog trace/search response, not `.env` values.
- Long history output must be chunked for Telegram.
- No `eval`.
- Commit/push must happen from the real git checkout, because `/tmp/opencode-run-nmbot/project` is not a git repository.

## Sources

- NotebookLM note `2026-07-01 — dialog logs readable for humans and models` (`bd24bdb6ec92`).
- `scripts/chat_tester_bot.py` dialog log helpers and command handlers.
- `docs/BOT_ARCHITECTURE.md` command/diagnostics section.
- `docs/CHANGELOG.md`.

---

# Task Plan — 2026-07-03 Public project overview service

Goal: сделать один публичный веб-вход, где пользователь может выбрать существующий MPN quality dashboard или новый обзор проекта NMBOT/Ирина с ТЗ, активными промтами, MCP-полями, блок-схемой и реальными примерами.

## Phases

1. Recon existing public service — complete.
2. Build static overview from allow-listed docs/prompts — complete.
3. Publish into existing `/home/neiro/public` service on port `8765` — complete.
4. Verify public URLs and HTML markers — complete.
5. Git commit/push — in progress.

## Verification

- Existing public service confirmed: `/usr/bin/python3 -m http.server 8765 --bind 0.0.0.0 --directory /home/neiro/public`.
- Public index: `http://193.107.155.236:8765/index.html` — shows `MPN quality dashboard` and `NMBOT / Ирина — проект целиком`.
- NMBOT overview: `http://193.107.155.236:8765/nmbot-project-7f3a9c/index.html` — shows project docs, prompts, MCP/search example and embedded scenario map.
- Scenario map: `http://193.107.155.236:8765/nmbot-project-7f3a9c/map.html`.
- Remote HTML check passed: `REMOTE_PUBLIC_OVERVIEW_OK`.

## Constraints

- Reuse the existing public service; do not open a new service/port.
- Publish only allow-listed docs/prompts; never publish `.env`, logs, backups, pycache or arbitrary paths.
- Static output is generated by `scripts/build_public_overview.py` and copied to `/home/neiro/public`.

## Sources

- User-provided public URL: `http://193.107.155.236:8765/mpn-quality-7f3a9c/index.html`.
- VPS process list: `http.server 8765 --directory /home/neiro/public`.
- `scripts/build_public_overview.py`.
- `docs/BOT_SCENARIO_MAP.html`.

---

# Task Plan — 2026-07-03 Mandatory MCP/search rule

Goal: зафиксировать правило пользователя из раннего поискового бота: любой запрос о квартире должен вызывать инструментальный поиск (`get_flat_info` в старом контракте; MCP/search `novostroym` в текущем nmbot). Бот не должен отвечать по памяти модели.

## Phases

1. Confirm exact rule from user prompt and search project notes — complete.
2. Add mandatory tool/search rule to active search prompt — complete.
3. Add rule to architecture docs and scenario map — complete.
4. Rebuild public overview and verify marker — pending.
5. Sync to VPS/public service and git commit/push — pending.

## Constraints

- No hallucinations: only MCP/search facts.
- Do not publish secrets/logs/backups.
- Keep old meaning: apartment request must trigger tool search before answer.

## Sources

- User-provided early search-bot prompt with `get_flat_info` rule.
- `prompts/search_v1.txt`
- `docs/BOT_ARCHITECTURE.md`
- `docs/BOT_SCENARIO_MAP.html`
- `scripts/build_public_overview.py`

---

# Task Plan — 2026-07-03 Public history tab

Goal: добавить во внешний NMBOT overview вкладку `История`, где можно смотреть последние диалоги бота онлайн: запрос клиента, ответ Ирины, intent, plan, MCP/search trace, buttons и cost.

## Phases

1. Inspect existing dialog logs and public overview generator — complete.
2. Add sanitized history JSON publisher — complete.
3. Add history tab with browser auto-refresh — complete.
4. Deploy to existing public service and enable refresh timer — complete.
5. Verify public URL and JSON output — complete.

## Verification

- Local: `LOCAL_HISTORY_OVERVIEW_OK`.
- VPS timer: `nmbot-public-history.timer` active; first publish `items=30`.
- Remote: `REMOTE_HISTORY_TAB_OK 30`.
- Public URL: `http://193.107.155.236:8765/nmbot-project-7f3a9c/index.html` shows `История`.
- Public JSON: `http://193.107.155.236:8765/nmbot-project-7f3a9c/history.json` returns sanitized latest dialog items.

## Constraints

- Do not expose raw private logs directly.
- Mask phones, emails and tokens before publication.
- Keep static public service on port `8765`; no new web server.

## Sources

- NotebookLM note `2026-07-01 — dialog logs readable for humans and models` (`bd24bdb6ec92`).
- `logs/dialogs-YYYY-MM-DD.jsonl`.
- `scripts/build_public_overview.py`.
- `scripts/publish_public_history.py`.

---

# Task Plan — 2026-07-03 Public architecture v2 tab

Goal: создать подробное ТЗ новой архитектуры решений Ирины и вынести его в публичный NMBOT overview отдельной вкладкой/страницей, чтобы пользователь мог прочитать, как будет организован Planner LLM → MCP/search → Decision Context → Action Resolver → Presenter → Validator.

## Phases

1. Inspect existing public overview/docs and UX contracts — complete.
2. Write detailed architecture TZ — complete.
3. Add public overview nav/page generation — complete.
4. Build and verify locally — complete.
5. Sync to VPS public service and verify URL — in progress.

## Verification

- Local: `ARCHITECTURE_V2_LOCAL_OK`.
- Generated files checked: `public_site/nmbot-project-7f3a9c/index.html`, `public_site/nmbot-project-7f3a9c/architecture-v2.html`.
- Required markers present: `Новая архитектура Ирины`, `Decision Context Builder`, `Action Resolver`, `Safety Validator`, `finance_terms`, `real_estate_related_unknown`.

## Constraints

- Publish only docs from allow-list; no secrets/logs/backups.
- This is documentation/public overview only; no bot runtime restart needed.
- New architecture is a TZ/plan, not yet implemented in runtime.

## Sources

- NotebookLM notes `7c0bfabbe4e2`, `eb601cf0ffc3`, `ae8a061b669f`.
- `docs/IDEAL_IRINA_UX.md`.
- `docs/BOT_ARCHITECTURE.md`.
- `scripts/build_public_overview.py`.
