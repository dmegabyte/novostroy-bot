# NMBOT — план упрощения разработки и эксплуатации

Дата: 2026-07-22  
Статус: proposal; изменений runtime пока нет  
Область: локальная разработка, проверка, диагностика и границы V0/V2/Jivo.

## 1. Зачем нужен этот план

Проект вырос: в нём есть несколько runtime-контуров, контрактов, тестовых
наборов, диагностических скриптов и исторических слоёв. Сложность не следует
снижать добавлением нового framework или ещё одного универсального агента.
Цель — сделать типовую работу предсказуемой:

```text
запрос на изменение
→ понятный владелец слоя
→ одна команда проверки
→ одна команда диагностики при инциденте
→ явный Jivo production gate
```

## 2. Факты и ограничения

### 2.1. Что уже есть

- `scripts/nmbot_architecture_preflight.py` проверяет архитектурные признаки.
- `scripts/nmbot_diag.sh` заявлен как единая точка диагностики.
- `scripts/nmbot_test_agent.py` и `scripts/nmbot_deploy_smoke.py` покрывают
  тестовые и deploy/smoke-сценарии.
- В документации есть ownership matrix, memory policy и operating standard.
- V0 и V2 описаны как независимые runtime-продукты и контракты.
- IntentPlan V3 задаёт целевую границу: planner владеет смыслом, валидатор
  принимает или отклоняет plan, executor получает факты, renderer только
  формулирует ответ.

### 2.2. Непереговорные ограничения

1. Не добавлять второй planner, скрытый state или regex-router для живой речи.
2. Не смешивать V0 и V2 ради временного удобства.
3. Не считать локальные тесты заменой Jivo production-проверки.
4. Не создавать «мега-скрипт», который сам меняет код, деплоит и скрывает
   ошибки. Автоматизация должна быть наблюдаемой и иметь безопасный dry-run.
5. Не удалять legacy-код до подтверждения реального владельца и rollback-пути.

## 3. Целевое состояние

### 3.1. Четыре стабильные команды

После работ у разработчика должны быть четыре понятные точки входа:

| Команда | Вопрос, на который отвечает | Режим по умолчанию |
|---|---|---|
| `nmbot doctor` | Что сломано или не готово в окружении/контракте? | read-only |
| `nmbot check <scope>` | Безопасна ли текущая локальная правка? | read-only |
| `nmbot smoke <scope>` | Работает ли конкретный пользовательский сценарий? | local, VPS только по явному флагу |
| `nmbot release verify` | Подтверждён ли Jivo production gate? | read-only, без deploy |

Это интерфейс над существующими проверками, а не их дубликат. До создания
обёртки необходимо доказать, что существующие команды нельзя сделать единым
контрактом через документацию или небольшой dispatcher.

### 3.2. Один владелец каждого решения

| Решение | Владелец | Не должны делать остальные слои |
|---|---|---|
| Смысл реплики клиента | IntentPlan V3 planner | повторно угадывать intent |
| Валидность plan/state | validator/reducer | молча менять goal |
| Поиск и enrichment | evidence executor | сочинять факты |
| Клиентская формулировка | renderer | выбирать route или факты |
| Проверка runtime | check/smoke scripts | менять production state |
| Deploy | отдельный явный runbook | быть побочным эффектом `check` |

### 3.3. Канонический реестр runtime и фичефлагов

До создания новых команд должен существовать один машино- и человекочитаемый
реестр `docs/NMBOT_RUNTIME_REGISTRY.md`. Для каждой версии и маршрута он
фиксирует:

| Поле | Значение |
|---|---|
| Версия/режим | V0, V2, V3 или другой явный режим |
| Production service и entry point | точные unit и запускаемый модуль |
| Выбор версии | env flag, default и допустимые значения |
| Внешние входы | Jivo/API/webhook и их owner |
| Статус | current, opt-in, rollback или historical |
| Проверка | targeted suite, smoke, Jivo evidence |
| Rollback | команда/backup и условие применения |
| Срок пересмотра | дата и ответственный за сохранение или удаление |

Реестр не заменяет исходный код и не является источником поведения. Это
паспорт, по которому `doctor` и `release verify` объясняют, что именно они
проверили. Нельзя выводить активную версию только из имени сервиса или из
старого документа; source of truth должна подтверждаться runtime marker и
свежим Jivo journal evidence.

### 3.4. Матрица внешних контрактов

Создать `docs/NMBOT_EXTERNAL_CONTRACTS.md`. В ней перечислить все границы,
которые нельзя случайно сломать упрощением:

- Jivo webhook/API и ожидаемый клиентский `BOT_MESSAGE`;
- Overmind/MCP search и typed search/evidence contract;
- callback flow: contact capture, private outbox, worker и Google Sheets;
- dialogue/error journals, их schema и runtime attribution;
- конфигурацию/feature flags без раскрытия секретов.

Для каждой границы обязательны producer, consumers, schema/version, fixture
или smoke, alert/log, способ compatibility rollback и владелец. Любая правка
такой границы обязана обновлять consumer-check до deploy.

### 3.5. Владение процессом и право остановки

Для всех фаз и скриптов закрепить RACI-light:

| Объект | Responsible | Approves/stop-go | Escalation |
|---|---|---|---|
| Operations map и docs | назначенный maintainer | владелец проекта | архитектурный review |
| Local `check`/fixtures | владелец контракта | maintainer | contract owner |
| VPS/Jivo release evidence | release owner | владелец проекта | rollback owner |
| Удаление legacy/flag | владелец runtime | отдельное approval | production owner |

Имена людей не фиксируются в этом плане: их заполняют в operations map. Но
никакой этап не переходит дальше без ответственного и явного stop/go решения.

## 4. Фаза A — инвентаризация без правок

**Цель:** измерить реальную сложность вместо предположений.

1. Собрать список всех entry points:
   - сервисы и API handlers;
   - CLI/diagnostic/deploy scripts;
   - тестовые suites;
   - V0/V2 runtime-модули;
   - production и legacy/rollback маршруты.
2. Для каждого entry point записать в таблицу:
   - владельца;
   - входные данные;
   - какие контракты читает;
   - какие файлы/сервисы меняет;
   - обязательную локальную и production-проверку;
   - является ли он актуальным, rollback или историческим.
3. Найти повторы:
   - одинаковые SSH/status/log-команды;
   - одинаковые наборы pytest;
   - повторное преобразование одного semantic plan;
   - документацию, противоречащую Jivo-authoritative контуру.
4. Отдельно составить реестр ручных операций за последние изменения:
   - сколько команд потребовалось;
   - какие команды повторялись;
   - где человек выбирал неправильный лог или runtime.
5. Зафиксировать baseline воспроизводимости:
   - способ создать локальное окружение;
   - фактические команды запуска pytest и их зависимости;
   - наличие/отсутствие CI-конфигурации;
   - время и стоимость локальных/сетевых/lifecycle проверок.
6. Составить первичный runtime registry и external contracts matrix, включая
   producer/consumer map для callback и журналов.

**Артефакт:** `docs/NMBOT_OPERATIONS_MAP.md` с таблицей, а не новый код.

**Критерий выхода:** для каждого production-затрагивающего файла и внешней
границы известны consumer, контракт, verification, rollback path, owner и
статус current/rollback/historical.

## 5. Фаза B — нормализация документации и маршрутов

**Цель:** убрать когнитивную нагрузку до написания скриптов.

1. Выбрать и явно отметить один current Jivo runtime в документации.
2. Вынести legacy/Telegram/rollback сведения в отдельный исторический раздел,
   чтобы они не выглядели как текущий release route.
3. Создать короткий runbook `docs/NMBOT_RUNBOOK.md`:
   - «бот не отвечает» → live VPS status + error-events;
   - «локальная правка» → preflight + targeted tests;
   - «изменён контракт» → consumer tests + fixture probe;
   - «готовим релиз» → backup + deploy + Jivo smoke + trace;
   - «нужен rollback» → конкретный безопасный путь.
4. Для каждой строки runbook давать одну основную команду и одну ссылку на
   подробную документацию. Не копировать shell-команды во множество файлов.
5. Сверить docs с фактическими сервисами и скриптами. При противоречии сначала
   зафиксировать Actual / Contract / Desired; не называть это багом, пока не
   подтверждён Desired.
6. Добавить `docs/NMBOT_COMMAND_MIGRATION.md`: старые команды, их замены,
   примеры трёх типовых задач и срок совместимости. Старая команда не исчезает
   без ссылки на замену или явного объявления historical.

**Критерий выхода:** новичок может выбрать правильный контур и первую команду
за минуту, не читая весь `BOT_ARCHITECTURE.md`.

## 6. Фаза C — минимальные скриптовые интерфейсы

**Цель:** убрать повторяющиеся ручные команды, сохранив прозрачность.

### 6.1. `nmbot doctor`

Создать только если инвентаризация подтвердит, что `nmbot_diag.sh` не закрывает
эти проверки единообразно. Скрипт должен:

- сообщать текущий режим (`local`, `VPS read-only`);
- проверять наличие нужных интерпретаторов/файлов/конфигурационных ключей без
  показа секретов;
- выводить состояние выбранного systemd unit и свежесть error-event log на VPS;
- показывать, какой runtime и версия контракта проверяются;
- возвращать machine-readable JSON по `--json` и понятный текст по умолчанию;
- ничего не перезапускать и не деплоить.

### 6.2. `nmbot check <scope>`

Сначала реализовать как тонкий dispatcher над существующими проверками.
Допустимые scopes: `contracts`, `v0`, `v2`, `runtime`, `docs`.

Порядок:

1. `py_compile` только затронутых модулей;
2. architecture preflight;
3. targeted pytest по scope;
4. contract/fixture probe, когда менялся search/normalizer boundary;
5. понятный итог: passed / failed / skipped и причина каждого skipped.

Скрипт не должен автоматически запускать полный pytest, сеть, SSH или deploy.
Для этого нужны явные флаги.

### 6.3. `nmbot smoke <scope>`

Переиспользовать существующие `nmbot_deploy_smoke.py` и тематические suites.
Добавить не новый тестовый движок, а manifest сценариев:

```text
scope → required fixtures/tests → optional live probe → expected evidence
```

Например, изменение selected-object flow требует stateful scenario и проверки
grounded ответа; изменение transport guard не обязано запускать search suite.

### 6.4. `nmbot release verify`

Read-only команда, собирающая evidence после явного deploy:

1. status затронутых Jivo services;
2. health endpoint;
3. hashes/feature markers нужных runtime-файлов;
4. stateful direct API или Jivo smoke;
5. delta `bot_error_events`;
6. отчёт, что подтверждено, а что ещё нет.

Команда не говорит «релиз готов», если выполнены только локальные проверки.

### 6.5. Test manifest и CI-политика

До реализации dispatcher создать versioned manifest, например
`tests/nmbot_check_manifest.yaml`. Его строка описывает:

```text
changed module/contract
→ required compile/preflight/tests
→ fixture evidence
→ optional network/Jivo evidence
→ estimated time/cost
→ blocking severity
```

`check` читает manifest, печатает выбранные проверки и не угадывает scope по
имени файла. Manifest тестируется как данные: добавление нового runtime или
контракта без строки manifest должно давать понятный failure.

Отдельным решением Фазы A назначить уровни автоматизации:

| Уровень | Когда запускается | Что запрещено |
|---|---|---|
| Быстрый local gate | перед локальным review | сеть, SSH, model calls по умолчанию |
| PR/CI gate | при доступном CI | deploy и реальные секреты |
| Nightly/controlled integration | по расписанию или вручную | скрытое изменение production |
| Release/Jivo gate | после явного deploy | подмена Jivo evidence локальным smoke |

Если CI пока отсутствует, это фиксируется как baseline и не маскируется
локальным alias. Сначала должен появиться воспроизводимый non-secret fast gate;
только затем допускается CI-интеграция.

### 6.6. Конфигурация, секреты и стоимость проверок

`doctor` проверяет только наличие и форму обязательной конфигурации, никогда не
печатает значения. Для каждого скрипта в runbook указываются:

- required env/config names;
- local/VPS область запуска;
- есть ли сеть, model call, денежная стоимость или запись во внешнюю систему;
- dry-run/fixture режим;
- безопасный способ получить секрет через утверждённый secret store.

Для model/network probes требуется явный флаг. Их лимиты по времени, стоимости
и допустимому retry задаются в manifest, а не остаются неявными в shell-скрипте.

## 7. Фаза D — сокращение архитектурной сложности

Эта фаза начинается только после карты зависимостей и отдельного решения на
каждое изменение.

1. Закрыть остаточные decision layers вокруг IntentPlan V3 по уже существующему
   плану миграции: downstream не должен менять semantic goal.
2. Оставить один типизированный путь от validated plan к Evidence Executor и
   ResponsePlan. Удалять adapters только после consumer-тестов и rollback plan.
3. Вынести исключительно общие технические утилиты в маленькие модули:
   structured logging, command runner, contract probe helpers. Не выносить
   бизнес-семантику из V0/V2 в «общую папку».
4. Уменьшать тестовое дублирование через общие fixtures и scenario manifest,
   сохраняя отдельные V0/V2 assertions.
5. Для каждого удаления legacy-слоя иметь:
   - доказательство отсутствия production consumer;
   - заменяющий контракт;
   - targeted regression;
   - способ rollback.
6. Ввести lifecycle register для legacy-кода и feature flags: owner, причина,
   дата добавления, expiry/review date, consumers, cleanup test. Просроченный
   flag не удаляется автоматически, но создаёт обязательный review item.

## 8. Порядок внедрения и контрольные точки

| Шаг | Изменение | Проверка | Решение продолжать |
|---|---|---|---|
| A | Operations map | review владельцев/контуров | Да, если нет неизвестных consumers |
| B | Runbook и актуализация docs | doc links + команды dry-run | Да, если current route однозначен |
| C1 | `doctor` или улучшение `nmbot_diag.sh` | local + VPS read-only | Да, если нет скрытых side effects |
| C2 | `check` dispatcher | intentional pass/fail fixtures | Да, если вывод объясняет результат |
| C3 | scenario manifest / smoke wrapper | targeted suites | Да, если scope не запускает лишнее |
| C4 | release verifier | production read-only evidence | Да, если нет ложного «green» |
| C5 | test manifest + fast non-secret gate | manifest validation + intentional failures | Да, если scope воспроизводим |
| C6 | CI policy/implementation, если нужна | isolated CI evidence | Да, если CI не требует secrets/deploy |
| D | удаление повторных layers | consumer + contract + Jivo gate | Только после отдельного approval |

Для каждого шага заранее назначаются owner, expected duration, evidence link и
stop condition. Если criterion не выполнен за оговорённый timebox, следующий
шаг не начинается: фиксируется причина и выбирается упрощённый вариант либо
план откатывается к предыдущему стабильному состоянию.

## 9. Метрики успеха

До начала Фазы A записать baseline, после каждой фазы сравнивать:

- число ручных команд для типовой локальной правки;
- число файлов, которые нужно открыть, чтобы определить владельца;
- время от инцидента до первого корректного лога;
- число дублирующихся скриптов/команд;
- число мест, где semantic goal может быть изменён;
- доля production-изменений с подтверждённым Jivo evidence;
- количество ложных «зелёных» статусов из-за одной локальной проверки.
- p50/p95 времени fast gate, targeted suite и release verification;
- число scopes без manifest-строки;
- доля checks, которые запускаются без сети, секретов и model costs;
- latency, fallback/reliability и coverage quality по действующему quality
  scorecard;
- число активных flags/legacy layers и число просроченных lifecycle reviews.

Целевой результат: меньше ручных шагов и точек решения, но не меньше
наблюдаемости и не слабее production gate.

## 10. Риски и защита от них

| Риск | Защита |
|---|---|
| Новый CLI скрывает важную проверку | `--verbose`, JSON-отчёт и список реально запущенных команд |
| Скрипт случайно меняет production | read-only default; side-effect команды только отдельным явным действием |
| Общий модуль смешивает V0/V2 | переносить только технические helpers, contracts оставить раздельными |
| Удаление legacy ломает rollback | consumer map + backup + явный rollback gate |
| Автоматизация даёт ложный green | в отчёте разделять local, fixture, VPS, Jivo evidence и skipped |
| План становится бесконечной реорганизацией | каждый шаг имеет измеримый результат и stop/go решение |
| CI делает проверку невоспроизводимой или требует секреты | сначала non-secret fast gate, затем изолированный CI |
| Manifest устаревает и выбирает неверный suite | обязательная строка для нового контракта + manifest validation |
| Runtime registry расходится с production | runtime marker + Jivo journal evidence, а не только docs |
| Внешний callback/journal ломается при упрощении | producer/consumer matrix + contract smoke до deploy |
| Feature flag остаётся навсегда | lifecycle register, owner и обязательный review date |

## 11. Пошаговый план реализации

Этот раздел — порядок исполнения. Работы идут строго последовательно: следующий
шаг нельзя начать, пока предыдущий не дал указанный артефакт и evidence. Все
шаги до шага 7 read-only: они не меняют runtime, feature flags, VPS и внешние
системы.

### Приоритет: обязательно сейчас или можно отложить

| Приоритет | Шаги | Почему |
|---|---|---|
| **Обязательно до первой правки runtime/архитектуры** | 0–6 | Без владельца, карты entry points, реестра runtime/flags, внешних контрактов, воспроизводимого baseline, runbook и manifest нельзя безопасно решать, что упрощать. Это минимальный фундамент, а не бюрократия. |
| **Обязательно только перед deploy затронутой зоны** | 8 | `smoke`/`release verify` обязателен, когда изменение идёт в Jivo runtime, callback, journal или внешний контракт. Не нужно реализовывать полный release wrapper заранее, если сейчас нет релиза. |
| **Условно обязательно** | 7 | Сначала сравнить существующий `nmbot_diag.sh` с контрактом диагностики. Улучшать его или добавлять тонкий `doctor` нужно только при доказанном ручном пробеле. |
| **Можно отложить** | 9 | CI полезен, но его нельзя строить, пока fast local gate нестабилен или зависит от сети/секретов. |
| **Делать отдельными итерациями после фундамента** | 10 | Сокращение runtime-слоёв — цель упрощения, но преждевременная правка без карты и контрактов создаст новый риск. |
| **Можно отложить до первой стабильной итерации** | 11 | Ежемесячный lifecycle cleanup нужен для устойчивости, но начинается после появления registry и первых флагов/legacy-решений в нём. |

**Минимальный результат первой итерации:** завершены шаги 0–6; принято
документированное решение по шагу 7; для следующей реальной правки определён
обязательный scope шага 8. До этого не создавать набор новых CLI-скриптов и не
рефакторить runtime-слои.

### Шаг 0. Назначить владельцев и границы работы

**Сделать**

1. Назначить maintainer плана, владельца current Jivo runtime и release owner.
2. Записать их в будущий `NMBOT_OPERATIONS_MAP.md` по RACI-light из раздела 3.5.
3. Подтвердить, что цель первой итерации — упростить работу разработчика, а не
   менять клиентскую логику или мигрировать V0/V2/V3.

**Готово, когда:** у каждого следующего артефакта есть responsible и stop/go
authority; список исключений из первой итерации записан явно.

### Шаг 1. Собрать карту entry points и consumers

**Сделать**

1. Read-only просмотреть `scripts/`, runtime-модули, тесты, service/runbook
   документы и конфигурационные шаблоны.
2. Создать `docs/NMBOT_OPERATIONS_MAP.md` с одной строкой на entry point.
3. Для каждой строки указать: путь, назначение, current/opt-in/rollback/
   historical статус, inputs, consumers, локальные проверки, Jivo gate,
   rollback и owner.
4. Отметить неизвестные consumers отдельным статусом `unknown`; не удалять и
   не объединять такие пути.

**Проверка:** вручную выбрать три типовых изменения — prompt, search-contract,
callback — и по карте получить их владельца и обязательные проверки без поиска
по репозиторию.

**Stop:** обнаружен entry point без известного owner или production consumer.
В этом случае сначала уточнить карту, а не писать новый скрипт.

### Шаг 2. Зафиксировать current runtime и фичефлаги

**Сделать**

1. Создать `docs/NMBOT_RUNTIME_REGISTRY.md` по форме раздела 3.3.
2. Для V0/V2/V3 и всех runtime overrides зафиксировать selector/default,
   service, entry point, journal marker, tests и rollback.
3. Сверить registry с текущим source, read-only VPS service status и свежим
   Jivo journal/trace; значения секретов не читать и не выводить.
4. Для каждого legacy/opt-in режима задать owner и review date.

**Проверка:** для произвольного Jivo dialogue record можно определить версию,
service, selector и соответствующий test/smoke путь.

**Stop:** docs, source и live marker расходятся. Сначала оформить
Actual/Contract/Desired и получить решение о desired runtime.

### Шаг 3. Описать внешние контракты и rollback-границы

**Сделать**

1. Создать `docs/NMBOT_EXTERNAL_CONTRACTS.md`.
2. Описать Jivo, Overmind/MCP, callback/Sheets, journals и config flags.
3. Для каждой границы добавить producer, consumer, schema/version, fixture,
live evidence, alert/log и compatibility rollback.
4. Связать каждую границу с конкретными строками operations map.

**Проверка:** у изменения callback worker, search wire-value или Jivo payload
есть заранее определённый consumer-check и нет необходимости угадывать лог.

**Stop:** контракт имеет внешний consumer, но не имеет безопасного fixture или
read-only smoke. Сначала добавить наблюдаемость/fixture, не менять контракт.

### Шаг 4. Снять baseline разработки и тестов

**Сделать**

1. Записать команды подготовки окружения, запуска targeted/full pytest и
существующих preflight/smoke scripts.
2. Зафиксировать, какие проверки локальные, какие требуют сеть/VPS/model call,
какие могут писать во внешнюю систему.
3. Замерить p50/p95 времени fast/targeted проверок и зафиксировать известные
нестабильные/warning-only места отдельно от новых сбоев.
4. Проверить наличие CI-конфигурации и записать факт, даже если её нет.

**Артефакт:** раздел baseline в `NMBOT_OPERATIONS_MAP.md` или отдельный
`docs/NMBOT_DEVELOPER_BASELINE.md`.

**Проверка:** другой разработчик может воспроизвести fast local gate без
секретов, сети и model costs.

### Шаг 5. Нормализовать документацию и команды

**Сделать**

1. Создать `docs/NMBOT_RUNBOOK.md` с пятью маршрутами из раздела 5.
2. Создать `docs/NMBOT_COMMAND_MIGRATION.md`: старая команда → новая точка
входа → совместимый период → owner.
3. Устранить противоречивые current-runtime инструкции только после
Actual/Contract/Desired решения из шага 2.
4. В каждый runbook-маршрут добавить основную команду, expected evidence и
следующий лог при первом failure.

**Проверка:** два человека по runbook выбирают одинаковую первую команду для
«бот не отвечает» и для «изменён search contract».

### Шаг 6. Создать test manifest и fast local gate

**Сделать**

1. Создать `tests/nmbot_check_manifest.yaml` с module/contract → required
checks, evidence, time/cost и severity.
2. Добавить validator manifest: неизвестный scope, отсутствующий тест или новая
runtime-граница без записи должны завершаться понятной ошибкой.
3. Реализовать fast local gate только как read-only dispatcher над существующими
`py_compile`, preflight и targeted pytest; не создавать второй test framework.
4. Добавить intentional pass/fail fixtures для самого dispatcher.

**Проверка:** один и тот же изменённый модуль всегда выбирает один и тот же
набор проверок; output показывает run/skipped/failed и причины.

**Stop:** dispatcher требует сеть, SSH, секрет или запускает full suite без
явного флага. Исправить границу до использования командой.

### Шаг 7. Улучшить существующую диагностику или добавить `doctor`

**Решение перед кодом:** по operations map сравнить `nmbot_diag.sh` с нужным
read-only контрактом. Если он достаточен — улучшить его; если нет — добавить
ровно один тонкий `nmbot doctor` wrapper.

**Сделать**

1. Реализовать local/VPS read-only mode, `--json`, runtime/version display,
service status и freshness error-event log.
2. Добавить проверку формы обязательной конфигурации без значений секретов.
3. Добавить tests/mock command runner для success, unavailable VPS и malformed
output.
4. Документировать side effects: по умолчанию их должно быть ноль.

**Проверка:** при недоступном VPS команда не выдаёт «всё работает», а честно
разделяет local result и непроверенный production status.

### Шаг 8. Добавить `smoke` и `release verify` поверх существующих проверок

**Сделать**

1. Подключить scenario manifest к существующим тематическим suites и
`nmbot_deploy_smoke.py`.
2. Добавить `release verify` только как post-deploy read-only evidence collector.
3. В отчёте жёстко разделить local, fixture, VPS, direct API и Jivo evidence.
4. Проверить, что callback, journal и runtime marker входят в release scope,
когда они затронуты manifest.

**Проверка:** намеренно пропущенный Jivo smoke даёт `incomplete`, а не green.

**Stop:** verify может перезапускать service, менять flag или отправлять данные
во внешнюю систему без отдельного явного действия.

### Шаг 9. Ввести CI только после стабильного fast gate

**Сделать**

1. Определить минимальный non-secret CI gate из шага 6.
2. Запускать CI в изолированной среде: без VPS deploy, внешних записей и
production secrets.
3. Отдельно определить manual/nightly integration и release/Jivo gates.
4. Добавить status/report links в runbook; CI не заменяет post-deploy evidence.

**Проверка:** CI воспроизводит fast local result и не требует ручной подстановки
секретов.

### Шаг 10. Сокращать runtime-слои малыми отдельными изменениями

**Сделать для каждого кандидата**

1. Построить Impact Chain Map: изменение → readers → transforms → writers →
validators.
2. Зафиксировать Actual/Contract/Desired и ссылку на operations map/contract.
3. Сделать минимальную правку в одной ownership boundary.
4. Запустить manifest-selected checks, consumer checks и Jivo gate.
5. Обновить lifecycle register: удалён/оставлен flag, owner, rollback,
review date.

**Проверка:** downstream больше не меняет semantic goal; число decision layers
или дублирующих adapters измеримо уменьшилось; Jivo evidence подтверждено.

### Шаг 11. Ежемесячный контроль и cleanup

**Сделать**

1. Просмотреть метрики из раздела 9 и просроченные lifecycle reviews.
2. Закрыть или переобосновать legacy/flags.
3. Обновить manifest и registry при новом runtime/контракте.
4. Выбрать один следующий узкий simplification candidate, а не запускать
массовый рефакторинг.

**Проверка:** в отчёте есть изменение метрик, решения по флагам и один
подтверждённый следующий шаг.

## 12. Первый практический шаг

Начать с read-only аудита Фазы A. Результат должен содержать:

1. таблицу entry points и владельцев;
2. 5–10 повторяющихся ручных действий с частотой;
3. список противоречий документации и фактического runtime;
4. рекомендацию: улучшить существующий скрипт или создать ровно один новый;
5. список изменений, которые нельзя делать без отдельного Jivo production gate.
6. draft runtime registry, external contracts matrix и список их неизвестных
   consumers;
7. baseline CI/pytest/environment и предложение только одного следующего
   automation step;
8. RACI-light: owner и stop/go authority для следующего шага.

Только по этой таблице выбирать конкретные скрипты. Предварительно наиболее
вероятный минимальный путь — не набор новых скриптов, а улучшение существующих
`nmbot_diag.sh`, `nmbot_architecture_preflight.py`, `nmbot_test_agent.py` и
`nmbot_deploy_smoke.py` плюс один документированный dispatcher, если он
действительно сокращает ручные шаги.

## Sources

- `docs/NMBOT_INTENT_PLAN_V3_IMPLEMENTATION_PLAN.md` — целевая ownership
  boundary, safety-инварианты и критерии production readiness.
- `docs/BOT_ARCHITECTURE.md` — runtime ownership, Jivo gate и сценарные
  контракты.
- NotebookLM `nmbot`: *Session 2026-07-06 — Project Operating Standard
  documented*; *Session 2026-07-06 — Ownership ADR impact map documented*;
  *Session 2026-07-16 — preflight markers tightened*.
- NotebookLM `nmbot`: *Session 2026-07-21 — runtime version recorded per
  journal turn*; *unified callback Sheets pipeline for V0/V2/V3*; *V0 V2
  documentation separation*.
- `docs/NMBOT_V2_PROJECT_QUALITY_SCORECARD.md` — quality, reliability/fallback
  and latency criteria that должны стать измеримым quality baseline.
