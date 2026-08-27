# Техническое задание: каноническое ядро NMBot V6

**Статус:** draft для утверждения.  
**Дата:** 2026-08-27.  
**Изменение production:** отсутствует.  
**Назначение:** собрать заново минимальное понятное ядро V6, используя действующие
файлы только как источник проверенных контрактов и примеров, а не как код для
слепого переноса.

## 1. Итог, который должен получить проект

После завершения работ у NMBot должны остаться:

1. один runtime — V6;
2. один канонический пакет приложения;
3. два действующих V6 prompt без смысловых и побайтовых изменений;
4. один immutable artifact, который собирается один раз;
5. один и тот же archive SHA для isolated TEST и primary;
6. один release-owner с раздельными командами `build`, `preflight`, `install`,
   `activate`, `rollback` и `recon`;
7. один понятный путь проверки: локально → isolated TEST → Jivo smoke → primary;
8. предыдущий production release как проверенный rollback до завершения миграции.

Главный критерий упрощения — не минимальное число строк, а минимальное число
понятий и путей: один runtime, один artifact schema, один builder, один promotion
path, один rollback contract.

## 2. Проблема, которую решаем

### Actual

- Полезное V6-ядро окружено историческими runtime, release и diagnostic слоями.
- TEST и primary используют несовместимые artifact/manifest contracts.
- Успешно проверенный TEST artifact нельзя продвинуть в primary без новой сборки.
- Большие универсальные gateway, outbox и release owners содержат функции, которые
  обычный V6-turn не использует.
- Из-за нескольких путей сборки одна продуктовая правка превращается в цепочку
  source snapshot, overlay, rebuild и повторной корреляции.

### Contract

- Ирина сначала даёт полезный подтверждённый ответ, затем один следующий шаг и
  предлагает специалиста только при необходимости.
- Факты берутся только из search/MCP `facts`, `near`, `missing`.
- Телефон нельзя просить до согласия на контакт.
- Jivo contract остаётся `CLIENT_MESSAGE → bridge → V6 API → BOT_MESSAGE`;
  `INVITE_AGENT` разрешён только для явной передачи оператору.
- TEST не может доставлять CRM lead.
- Release ID и artifact SHA неизменяемы; production требует rollback.

### Desired

Новое ядро реализует тот же продуктовый контракт без импорта старого runtime-кода.
Старые файлы используются как reference и источник regression tests. Один artifact
проходит полный TEST и затем без пересборки продвигается в primary.

## 3. Обязательные продуктовые сценарии

Новая реализация обязана сохранить пять сценариев:

| ID | Сценарий | Обязательный результат |
|---|---|---|
| `new_search` | Новый подбор | реальный поиск, до 3 вариантов, один следующий вопрос |
| `refine_search` | Уточнение | сохранён контекст; локальная фильтрация либо новый поиск при изменении области |
| `selected_property` | Выбранный ЖК | ответ по точному выбранному объекту, без подмены похожим |
| `finance_consultation` | Ипотека/ПВ без выбранного объекта | без фиктивного поиска; честная граница; специалист; телефон только после согласия |
| `direct_specialist` | Прямой запрос человека | короткое подтверждение маршрута и запрос телефона |

Общие acceptance:

- 0 неподтверждённых фактов;
- не больше 3 вариантов в первом shortlist;
- ровно один следующий вопрос;
- 0 запросов телефона до согласия;
- отсутствие raw dialogue, телефона, token и prompt в журналах;
- опечатки и короткие follow-up не сбрасывают тему;
- `/start` очищает пользовательский контекст;
- пустой результат даёт честную ближайшую альтернативу, а не тупик.

## 4. Scope

### Входит в работы

- новое независимое V6-ядро;
- перенос двух prompt байт-в-байт;
- state, contract, runtime, gateway transport, URL card, private outbox, journal;
- HTTP/Jivo API shell и health/release identity;
- strict privacy-safe smoke;
- один artifact manifest и release-owner;
- isolated TEST и primary promotion одного archive SHA;
- regression и compatibility tests;
- migration/rollback runbook;
- удаление заменённого runtime и старых release paths после успешной миграции.

### Не входит в первую миграцию

- изменение prompt-текстов или модели;
- новый V7, selector, fallback runtime или compatibility adapter;
- переписывание действующего Jivo bridge;
- переписывание внешнего Gateway/Overmind API;
- переписывание callback worker, Sheets или CRM delivery;
- изменение внешних Jivo/MCP/CRM payload contracts;
- ручное редактирование VPS, `.env` или systemd units;
- удаление предыдущего production release до доказанного rollback window.

## 5. Целевая архитектура

### 5.1. Канонический production source

Предлагаемая структура:

```text
nmbot_core/
  __init__.py
  app.py          # HTTP/Jivo handlers, auth, wiring, health
  runtime.py      # линейный turn flow и переходы сценариев
  contract.py     # строгие model/search response contracts
  state.py        # private dialogue state и reset
  gateway.py      # только create/poll/result для двух V6 stages
  url_card.py     # безопасный bounded URL/card parser
  outbox.py       # atomic enqueue и dedup, без CRM delivery worker
  journal.py      # privacy-safe dialogue/release receipts
  identity.py     # только безопасное чтение release identity

prompts/
  v6_simple_search_agent.txt
  v6_simple_response_composer.txt

scripts/
  nmbot_smoke.py  # strict TEST/primary correlation
  nmbot_release.py # единый release-owner

requirements.txt
```

Это архитектурный budget, а не самоцель: не более 12 authored production-файлов
без отдельного решения. Tests, docs и generated release identity в лимит не входят.
Превышение допустимо только если новый файл убирает доказанную связь, состояние или
failure path.

Перед merge старый пакет `nmbot_v6` и заменённые `scripts/nmbot_v6_*` удаляются из
канонической ветки. История остаётся в Git и в предыдущем immutable release.

### 5.2. Ответственность модулей

| Owner | Делает | Не делает |
|---|---|---|
| `app.py` | auth, HTTP/Jivo parsing, dependency wiring, health | продуктовые решения, поиск, CRM delivery |
| `runtime.py` | сценарий, state transition, consent, один следующий шаг | transport и произвольный parsing payload |
| `contract.py` | validate/normalize Prompt1, search и Prompt2 outputs | fallback, retry, сетевые вызовы |
| `state.py` | private state, reset, selected option, consent | журнал и внешняя доставка |
| `gateway.py` | один V6 create/poll/result request, timeout, safe trace | main-search race, provider fallback, скрытый retry |
| `url_card.py` | allowlisted URL, bounded fetch, безопасная карточка | CLI и общий web crawler |
| `outbox.py` | private atomic enqueue, dedup, `queued|duplicate` | lease, Sheets, CRM send |
| `journal.py` | hashes, lengths, bounded codes, append-only receipt | raw dialogue/contact/prompt |
| `identity.py` | read current immutable release identity | build и manifest creation |
| `nmbot_smoke.py` | target/release lock и сквозная корреляция | route switch, selector mutation, retry |
| `nmbot_release.py` | build/preflight/install/activate/rollback/recon | prompt behavior и incident-specific repair |

### 5.3. Сохраняемые внешние границы

- **Bridge:** остаётся отдельным сервисом и передаёт только Jivo contract. В первую
  миграцию его код не меняется.
- **Gateway:** внешний private API остаётся без изменений; новый transport реализует
  только уже используемый V6 once-call.
- **CRM worker:** остаётся внешним consumer private outbox. Новое ядро отвечает
  только за безопасный enqueue и dedup.
- **Configuration:** profile/ports/secrets/state paths задаются снаружи artifact;
  TEST и PROD используют разные private state/journal/outbox paths.

## 6. Требования к совместимости

1. SHA-256 обоих prompt совпадает с утверждённой V6-базой.
2. Model input/payload и трактовка Prompt1/Prompt2 response не меняются в рамках
   migration PR.
3. Схемы state, journal и outbox либо совместимы, либо имеют отдельный
   deterministic one-way migration с rollback copy.
4. Existing bridge получает тот же HTTP/Jivo response contract.
5. CRM worker видит тот же enqueue contract и не создаёт duplicate lead.
6. Health возвращает `runtime=V6`, exact profile и release ID.
7. TEST CRM delivery отключён в коде, а не только конфигурацией.
8. Production artifact не импортирует старый runtime/package.

## 7. Единый artifact и release-owner

### 7.1. Artifact contract

Manifest должен содержать минимум:

- schema/version;
- immutable `release_id`;
- exact source Git SHA/tree и clean status receipt;
- archive SHA-256;
- file list и SHA-256 каждого файла;
- dependency lock SHA;
- entrypoints;
- supported profiles `TEST|PROD`;
- required secret-free config keys/shapes;
- prompt SHA-256;
- build/test receipts;
- previous compatible release ID только как deployment receipt, не как build input.

Build идёт только из clean canonical Git. VPS snapshot используется как backup и
drift receipt, но не как source и не как overlay для новой сборки.

### 7.2. Release commands

Один owner предоставляет отдельные fail-closed операции:

```text
build       создать artifact один раз
inspect     проверить manifest/archive без внешних вызовов
preflight   проверить target/topology без mutation
install     загрузить exact archive в inactive release/slot
activate    атомарно переключить route/current
rollback    вернуть previous immutable release
recon       дать privacy-safe receipt текущего состояния
```

Наличие одного CLI не объединяет approvals. Build, TEST install, external smoke,
primary activation и rollback остаются отдельными gates.

### 7.3. TEST/PROD invariant

- TEST и PROD запускают один archive SHA;
- меняются только profile и внешние private paths/secrets;
- artifact не пересобирается и не патчится после TEST;
- любой изменённый byte получает новый release ID;
- первый failed gate завершает release attempt.

## 8. Полный перечень работ

### Этап 0. Зафиксировать baseline

**Работы**

- утвердить это ТЗ;
- зафиксировать source SHA, prompt hashes и текущий 5/5 synthetic manifest;
- составить matrix старых public contracts → новых owner modules;
- снять read-only primary topology/source/release receipt перед migration work;
- определить предыдущий immutable release для rollback.

**Результат:** baseline receipt без source/runtime mutation.  
**Stop:** неизвестен current target, prompt SHA или rollback release.

### Этап 1. Создать skeleton нового ядра

**Работы**

- создать `nmbot_core` без импорта `nmbot_v6`;
- перенести prompt exact bytes;
- определить явные dataclasses/types для TurnInput, state, Prompt1, search card,
  Prompt2, terminal response;
- создать dependency wiring без selector/fallback registry.

**Проверки**

- import/py_compile;
- prompt byte/hash equality;
- forbidden-import test;
- architecture file-budget test.

### Этап 2. Реализовать deterministic owners

**Работы**

- `contract.py`: строгий parsing/validation;
- `state.py`: reset, params, visible options, selected option, consent;
- `journal.py`: privacy-safe append;
- `outbox.py`: atomic enqueue/dedup;
- `identity.py`: safe current release read;
- перенести phone normalization и URL-card behavior.

**Проверки**

- unit/property tests на invalid payload;
- symlink/path/size limits;
- no raw dialogue/contact in journal;
- duplicate lead = один queue record;
- URL allowlist/redirect/timeout/HTML limits.

### Этап 3. Реализовать direct V6 gateway

**Работы**

- оставить только create → bounded poll → result;
- поддержать ровно `v6_simple_prompt1|v6_simple_prompt2`;
- сохранить действующий payload/trace contract;
- исключить hidden retry, main-search race и provider fallback.

**Проверки**

- payload equality fixtures;
- timeout/error mapping;
- safe task refs;
- доказательство нулевого retry;
- никаких provider calls в local suite.

### Этап 4. Реализовать runtime пяти сценариев

**Работы**

- линейный phone guard → Prompt1 → optional search → Prompt2;
- новый подбор и уточнение;
- selected-property exact identity;
- finance consultation без фиктивного поиска;
- direct specialist и consent-to-phone;
- honest missing/near behavior;
- один следующий вопрос.

**Проверки**

- focused unit tests по каждому transition;
- synthetic 5/5 manifest;
- stale context/reset;
- 0 early phone;
- 0 identity substitution;
- max 3 options/one question.

### Этап 5. Собрать app/API shell

**Работы**

- private API auth и Jivo handler;
- runtime dependency wiring;
- health/runtime/profile/release identity;
- egress-safe BOT_MESSAGE/INVITE_AGENT response;
- independent TEST/PROD state paths.

**Проверки**

- auth before work;
- API contract tests;
- exact health identity;
- TEST CRM hard-disabled;
- startup/shutdown and corrupted state cases.

### Этап 6. Вернуть strict smoke как release verification

**Работы**

- target enum `isolated-test|primary`;
- обязательный expected release;
- bounded journal and bridge log reads from allowlisted paths;
- correlation `event/chat → intended upstream → API BOT_MESSAGE → egress → terminal Jivo`;
- privacy-safe output only.

**Проверки**

- wrong profile/release/path fails before send;
- missing journal/bridge/terminal stage rejects release;
- no route mutation/retry;
- fixture for current bridge receipt shape.

### Этап 7. Реализовать единый artifact builder

**Работы**

- один canonical manifest;
- deterministic archive order/metadata;
- exact allowlist без legacy files;
- generated release identity;
- archive unpack/import/startup admission.

**Проверки**

- dirty Git rejected;
- same source → same content hashes;
- unexpected/missing file rejected;
- manifest/archive/file hashes verified;
- no secret/env/private state in archive.

### Этап 8. Реализовать единый promotion controller

**Работы**

- profile-aware target registry;
- TEST install в isolated inactive release;
- primary install в inactive release/slot;
- atomic activate и exact rollback;
- lock, backup, route/current identity and protected-service checks;
- durable operation receipts.

**Проверки**

- TEST/PROD принимает один manifest;
- install не активирует;
- failed health не переключает route;
- failed post-check возвращает previous release;
- protected bridge/client services unchanged;
- stale operation status reconciles only fail-closed.

### Этап 9. Integration regression

**Локально, без provider/Jivo**

- py_compile;
- targeted tests каждого owner;
- полный supported pytest baseline;
- artifact build/inspect/unpack/import;
- five synthetic scenarios;
- old-vs-new contract fixtures для payload/state/journal/outbox/API.

**Отдельно разрешаемые external checks**

- bounded model diagnostic 5/5 на exact local candidate;
- isolated TEST install exact archive;
- exactly one strict Jivo smoke;
- после smoke — 5/5 agreed product scenarios, stop on first failure.

### Этап 10. First migration rehearsal

**Работы**

- свежий read-only primary snapshot/topology/release receipt;
- установка artifact в isolated TEST;
- health/identity/profile/hash check;
- временный owner-controlled bridge route;
- strict correlated smoke;
- обязательный rollback route после rehearsal;
- сохранить receipts и сравнить с baseline.

**Stop:** любой mismatch source/artifact/topology/correlation/terminal outcome.

### Этап 11. Primary promotion

**Работы**

- отдельное production approval;
- fresh primary recon;
- подтвердить exact TEST-tested archive SHA;
- backup current release/config metadata;
- install в inactive slot;
- atomic activation;
- one strict primary smoke;
- health/journal/terminal post-check;
- rollback при первом failure.

**Критично:** primary получает тот же archive SHA, который прошёл TEST. Никаких
overlay, rebuild или manual copy.

### Этап 12. Observation и retirement

**Работы**

- согласовать observation window перед production promotion;
- следить только за privacy-safe health/error/journal receipts;
- подтвердить отсутствие duplicate leads и regression пяти сценариев;
- после отдельного решения удалить superseded release paths, selectors, builders,
  deployers и старый runtime source из canonical branch;
- сохранить previous immutable release и Git tag/ref на agreed rollback period;
- обновить architecture, external contracts, build plan, runbook и decisions.

**Не удалять:** production data, journals, outbox, rollback artifact или config
backup без отдельного retention решения.

## 9. Разделение изменений по owner layer

Чтобы не повторить старый цикл, реализация делится минимум на четыре независимых
change set:

1. `feat/canonical-v6-core` — новое ядро и local tests, без deploy tooling;
2. `ops/unified-v6-release` — artifact/release owner и его tests, без product change;
3. `ops/canonical-v6-migration` — topology/runbook/rehearsal receipts;
4. `cleanup/retire-superseded-runtime` — только после успешной primary promotion.

Prompt/model behavior не меняется ни в одном migration change set. Если testing
обнаружит product defect, он получает отдельный `fix/` branch, новый source SHA и
новый release ID.

## 10. Definition of Done

Работа считается завершённой только если одновременно выполнено всё:

### Код и архитектура

- [ ] в canonical source один runtime package;
- [ ] production source не импортирует старый runtime;
- [ ] два prompt имеют утверждённые SHA-256;
- [ ] bridge и CRM external contracts не изменены;
- [ ] один artifact builder и один manifest schema;
- [ ] TEST и primary deploy принимают один artifact;
- [ ] заменённые release paths удалены после migration gate.

### Качество

- [ ] targeted tests всех owners зелёные;
- [ ] supported full suite зелёный;
- [ ] synthetic product scenarios 5/5;
- [ ] 0 hallucinated facts;
- [ ] 0 phone-before-consent;
- [ ] один CRM lead без duplicate;
- [ ] URL-card safety contract сохранён;
- [ ] journal/privacy tests зелёные.

### Release

- [ ] artifact собран один раз из clean reviewed Git SHA;
- [ ] archive/manifest/file hashes проверены;
- [ ] isolated TEST запускает exact archive;
- [ ] strict TEST Jivo correlation принята;
- [ ] primary получает тот же archive SHA;
- [ ] strict primary smoke принят;
- [ ] rollback проверен и previous release сохранён;
- [ ] post-check содержит timestamp, contour, release ID и terminal outcome.

### Документация

- [ ] `CURRENT_ARCHITECTURE.md` описывает только итоговую систему;
- [ ] `NMBOT_EXTERNAL_CONTRACTS.md` соответствует фактическим schemas;
- [ ] `NMBOT_VERSION_BUILD_PLAN.md` содержит один artifact path;
- [ ] `NMBOT_RUNBOOK.md` содержит точные preflight/activate/rollback commands;
- [ ] `DECISIONS.md` фиксирует принятую migration и retirement старых путей.

## 11. Риски и защита

| Риск | Защита |
|---|---|
| Переписать скрытый полезный edge case | contract fixtures и 5/5 scenarios до удаления старого source |
| Незаметно изменить prompt/model payload | byte/hash equality и payload fixtures; prompts не редактировать |
| Смешать core и release defect | отдельные branches/owners; stop после первого failure |
| Потерять state/lead | private backup, schema admission, one-way migration только с rollback copy |
| TEST отличается от PROD | один archive SHA, разные только profile/private paths |
| Сломать Jivo/CRM | не переписывать bridge/worker в первой миграции; strict correlation |
| Снова получить большой универсальный tool | release CLI имеет только текущие операции и не содержит incident repairs |
| Удалить rollback слишком рано | retirement только после отдельного observation/retention решения |

## 12. Предлагаемые решения для утверждения

1. Название runtime остаётся **V6**; новый version number не создаётся.
2. Канонический Python package называется `nmbot_core`.
3. Prompt переносятся байт-в-байт и в migration не редактируются.
4. Bridge и CRM worker остаются внешними границами первой миграции.
5. Clean Git становится source of truth; VPS snapshot — только evidence/backup.
6. Один artifact используется в TEST и primary без rebuild.
7. Старый runtime удаляется из canonical source только перед merge готовой замены;
   previous production artifact сохраняется до отдельного retirement решения.
8. Целевой budget — не более 12 authored production-файлов; превышение требует
   отдельного обоснования, но LOC не является acceptance gate.

## 13. Источники действующих контрактов

- `AGENTS.md:1-76` — один V6 runtime, anti-cycle budget, safety и verification.
- `docs/NMBOT_PRODUCT_CONTRACT.md:6-104` — пять сценариев и product acceptance.
- `docs/CURRENT_ARCHITECTURE.md:3-15` — runtime, bridge и TEST/PROD boundaries.
- `docs/NMBOT_EXTERNAL_CONTRACTS.md:3-23` — Jivo, gateway, CRM и privacy.
- `docs/NMBOT_VERSION_BUILD_PLAN.md:10-175` — build once, stop/go, TEST/primary.
- `docs/NMBOT_RUNBOOK.md:13-42,69-155` — immutable artifact, isolated TEST,
  bridge switch и rollback boundaries.
- `docs/IDEAL_IRINA_UX.md:17-45,51-268` — grounding, conversational flow,
  selected property, missing data, operator timing и UX checklist.
- `tests/fixtures/v6_product_scenarios.json:1-32` — synthetic 5/5 inputs.

`IDEAL_IRINA_UX.md` сейчас находится в полном project-docs контуре, но отсутствует
в clean V6 root. До реализации нужно либо вернуть его в canonical docs, либо
зафиксировать `NMBOT_PRODUCT_CONTRACT.md` как его достаточную V6-проекцию. Молчаливо
терять этот UX-контракт нельзя.
