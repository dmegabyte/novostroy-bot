# NMBot — clean-room интеграция независимого worker deploy

Дата: 2026-08-25  
Статус: planning; не является описанием текущего production  
Область: callback CRM/Sheets worker, release tooling и его локальная проверка

## 1. Цель

Закрепить в каноническом `main` архитектурную границу:

```text
API producer → durable callback outbox → CRM/Sheets worker
```

Worker должен собираться, выпускаться и откатываться независимо от API и
bridge. Интеграция выполняется в чистом изолированном Git worktree и не меняет
работающий production.

Это не создание второго проекта и не переписывание всего deploy-механизма.
Итогом должен стать один проверяемый commit, который затем можно отдельно
перенести в канонический `main`.

## 2. Definition of done

Работа завершена, только если одновременно выполнено следующее:

1. Чистый integration worktree создан от точного committed `main HEAD`.
2. В него не попали незакоммиченные пользовательские файлы, старые artifacts,
   snapshots, логи, `.env`, private data или диагностические эксперименты.
3. Release scopes ограничены тремя значениями: `api`, `bridge`, `worker`.
4. Worker artifact содержит только точную worker closure и release identity.
5. Worker deploy не выполняет команды для API и bridge.
6. Worker rollback возвращает предыдущий `worker-current` и прежнее состояние
   worker service.
7. Текущий и предыдущий producer/consumer понимают поддерживаемые версии outbox.
8. Неопределённая внешняя доставка не повторяется вслепую.
9. Старый combined callback-worker path не расширяется; его удаление или
   блокировка выполняется отдельным изменением после интеграции.
10. Итоговый diff проверен по allowlist путей, тесты проходят, Git status чистый.
11. Production deploy не является частью этой работы.

## 3. Непереговорные ограничения

- Не cherry-pick всей накопленной candidate-ветки.
- Не переносить историю исправлений вместо минимального итогового контракта.
- Не редактировать dirty основной worktree, не делать stash и не трогать
  пользовательские изменения.
- Не создавать второй постоянный репозиторий или параллельный pipeline.
- Не вводить Kubernetes, GitOps controller, Docker migration или новый
  универсальный deploy framework.
- Не удалять legacy rollback path до отдельного решения и проверки.
- Не менять outbox idempotency, private persistence и правило `uncertain`
  delivery без отдельного data-contract доказательства.
- Не использовать production как среду поиска import/entrypoint ошибок.

## 4. Целевая архитектура

```text
api-releases/<id>       ← api-current       ← API unit
bridge-releases/<id>    ← bridge-current    ← bridge unit
worker-releases/<id>    ← worker-current    ← callback worker unit
```

Каждый component manifest фиксирует:

- `scope` и `contour`;
- immutable `release_id`;
- source snapshot SHA и source commit;
- release-tool/schema version;
- точный список файлов и entrypoints;
- единственный разрешённый systemd service;
- запрещённые services;
- config shape без значений секретов;
- component health/identity checks.

Deploy primitive выполняет только:

```text
validate manifest
→ stage immutable artifact
→ run extracted-artifact preflight
→ atomically switch one component-current
→ restart one owned service
→ verify component
→ restore previous component-current on failure
```

## 5. План действий

### Фаза 0 — read-only инвентаризация

1. Зафиксировать точный `main HEAD` и показать dirty status основного worktree.
2. Не менять, не stage и не stash существующие пользовательские файлы.
3. Сравнить canonical main с проверенным worker candidate по символам и
   контрактам, а не по всей истории Git.
4. Для каждого потенциального файла выбрать один вердикт:
   - `keep-main` — версия main уже выполняет контракт;
   - `minimal-port` — нужен узкий итоговый diff;
   - `new-owned-file` — файл принадлежит worker boundary;
   - `reject` — эксперимент, диагностика, legacy или чужой owner.
5. До mutation согласовать окончательный owned-path allowlist.

**Stop:** если нельзя доказать owner файла или downstream impact, файл не
переносится.

### Фаза 1 — чистый integration worktree

1. Создать новый detached/feature worktree от committed `main HEAD` вне dirty
   основного каталога.
2. Проверить нулевой `git status --short`.
3. Записать вне application source provenance: base commit, candidate reference,
   список разрешённых путей и expected verification commands.
4. Не копировать generated artifacts и source snapshots в Git.

**Результат:** чистая изолированная поверхность, которую можно удалить без
воздействия на main или production.

### Фаза 2 — data contract до release tooling

1. Зафиксировать поддерживаемую outbox schema и правила чтения старых записей.
2. Добавить fixtures минимум для:
   - старой Sheet-only записи без `crm_delivery`;
   - новой записи с независимыми Sheet/CRM branches;
   - terminal `uncertain` без автоматического повтора;
   - пустого успешного CRM `HTTP 2xx`;
   - duplicate event/idempotency.
3. Проверить матрицу:

   | Producer | Worker | Ожидание |
   |---|---|---|
   | current | current | поддерживается |
   | current | previous | поддерживается либо deploy запрещён manifest gate |
   | previous | current | старые записи читаются без CRM backfill |

4. Если N/N−1 совместимость невозможна, manifest обязан содержать явный
   compatibility range и блокировать несовместимый cutover.

**Stop:** независимый deploy нельзя интегрировать без доказанного
producer/consumer compatibility contract.

### Фаза 3 — минимальный worker artifact

1. Определить одну каноническую allowlist worker runtime files.
2. Построить import/resource closure от worker entrypoint.
3. Исключить API server, bridge server, prompt/runtime experiments и
   диагностические скрипты.
4. Добавить extracted-artifact проверки:
   - все imports работают;
   - systemd `ExecStart` существует внутри artifact;
   - requirements покрывают imports;
   - manifest files точно совпадают с allowlist;
   - API и bridge находятся в `forbidden_services`.
5. Связать artifact с точным commit и snapshot SHA.

**Stop:** отсутствующий entrypoint/import обнаруживается локально; production
deploy для discovery запрещён.

### Фаза 4 — один ограниченный worker deploy primitive

1. Использовать отдельные `worker-releases` и `worker-current`.
2. Разрешить restart только callback worker unit выбранного contour.
3. Проверять точное совпадение contour/root/env/unit/manifest.
4. Не читать и не печатать secret values.
5. При первой ошибке до cutover удалить только новый staging.
6. При ошибке после cutover атомарно вернуть previous `worker-current` и
   восстановить прежнее active/inactive состояние worker.
7. Не включать API/bridge restart даже как fallback.

### Фаза 5 — автоматические regression gates

Обязательные тесты:

1. Worker manifest имеет `scope=worker` и точную file closure.
2. Command trace deploy/rollback не содержит API/bridge services.
3. `client-production` и `primary` нельзя перепутать.
4. Неизвестный contour/profile/schema fail-closed.
5. Старый release tool отвергает неизвестную новую manifest schema.
6. Worker first install, ordinary upgrade и rollback проходят в FakeRemote.
7. Ошибка на каждом шаге сохраняет предыдущий рабочий target.
8. Outbox N/N−1 fixtures проходят.
9. CRM payload содержит только разрешённые `phone`, `name`, `request`.
10. Receipt/status output не раскрывает contact, endpoint или response body.

### Фаза 6 — review и один чистый commit

1. Запустить focused tests, затем полный разрешённый release-tool suite.
2. Выполнить `py_compile`, JSON/schema validation и `git diff --check`.
3. Сравнить фактический diff с owned-path allowlist.
4. Выполнить отдельный integration review:
   - release boundary;
   - rollback completeness;
   - source/artifact provenance;
   - data compatibility;
   - отсутствие cross-service side effects.
5. Создать один commit, отражающий итоговый контракт, а не историю отладки.
6. После commit повторить status и зафиксировать полный SHA.

**Результат:** переносимый commit из чистого worktree. Основной dirty worktree и
production всё ещё не затронуты.

### Фаза 7 — canonical main и документация

1. Перенос в main выполняется отдельно после review и явного разрешения.
2. Обновить runbook, operations map, release identity/atomic release reference.
3. Сделать новый worker-only маршрут единственным документированным путём.
4. Combined callback-worker profile пометить deprecated, но пока не удалять.
5. Зафиксировать owner и условие окончательного удаления legacy path.

### Фаза 8 — отдельное выведение combined profile

Только после того, как canonical main содержит новый путь и локальные проверки
подтверждены:

1. Fail-closed запретить combined profile для `client-production`.
2. Сохранить чтение исторических manifests для rollback/аудита.
3. Удалять код combined deploy только отдельным reviewed commit.
4. Не совмещать это изменение с runtime feature или production release.

## 6. Предварительная карта владельцев

Это не окончательная allowlist; она уточняется в Фазе 0.

| Owner | Возможные пути |
|---|---|
| Contour identity | `config/nmbot_deployment_contours.json` |
| Release boundary | `scripts/nmbot_atomic_release.py` |
| Worker entrypoint | `scripts/nmbot_callback_sheet_worker.py` |
| Durable contract | `scripts/nmbot_crm_outbox.py` |
| CRM adapter/control | `scripts/nmbot_callback_crm.py`, `scripts/nmbot_callback_crm_control.py` |
| Sheet/summary adapters | `scripts/nmbot_google_sheets.py`, `scripts/nmbot_callback_summary.py` |
| Safe receipt | `scripts/nmbot_callback_crm_delivery_check.py` |
| Dependencies | `requirements.txt` — только доказанные worker imports |
| Contract tests | focused callback/CRM/release tests |

Любой путь вне этой таблицы требует отдельного owner/impact доказательства.

## 7. Что не переносить

- `scripts/nmbot_dialogue_report.py` из candidate workspace;
- artifacts `r1..rN`, tarballs и manifests как source files;
- `snapshot-provenance.json` и overlay provenance в application tree;
- `.env`, tokens, credentials, private callback records;
- V7 modules только ради удовлетворения неиспользуемого API import;
- временные deploy diagnostics и одноразовые SSH wrappers;
- старые primary-only unit/root assumptions;
- тестовые assertions, не соответствующие действующему контракту.

## 8. Риски и управление ими

| Риск | Предотвращение |
|---|---|
| Случайно перенести лишний candidate code | clean-room port + owned-path review |
| Producer/worker schema skew | N/N−1 fixtures + compatibility manifest |
| Worker затрагивает API/bridge | forbidden services + command-trace tests |
| Wrapper снова превращается в framework | три фиксированных scope; без profile proliferation |
| Неполный artifact | entrypoint/import closure preflight |
| Невозможный rollback | failure injection на каждом шаге |
| Старый путь снова используется | один canonical runbook route; deprecation gate |
| Main содержит unrelated dirty changes | отдельный worktree; никакого stash/cherry-pick whole branch |

## 9. Stop/go точки

Отдельное решение требуется перед каждым переходом:

1. **GO local mutation:** утверждена exact owned-path allowlist.
2. **GO commit:** diff и verification прошли review.
3. **GO merge/main integration:** commit переносится в canonical history.
4. **GO legacy deprecation:** новый путь доказан, rollback сохранён.
5. **GO production:** не входит в этот план и требует отдельного fresh release
   gate, artifact и подтверждения.

Любой локальный failure возвращает работу к read-only диагностике. Он не является
основанием проверять следующую гипотезу на production.

## 10. Итоговый пользовательский маршрут

Целевой интерфейс — тонкая команда над существующими проверяемыми шагами:

```text
nmbot worker-release prepare --contour client-production
→ receipt: commit, snapshot SHA, artifact SHA, tests
→ отдельное confirm
nmbot worker-release deploy --receipt <id> --confirm
→ receipt: previous/current, worker health, rollback target
```

Команда не выбирает contour сама, не меняет код, не вызывает Jivo/CRM и не
скрывает промежуточные receipts.

## 11. Источники решения

- `docs/NMBOT_RELEASE_IDENTITY.md`, раздел `Deploy and rollback boundary` —
  immutable ID, snapshot, isolated worktree, preflight и explicit stop/go.
- `docs/NMBOT_EXTERNAL_CONTRACTS.md`, callback CRM contract — независимые
  delivery branches и запрет blind retry uncertain delivery.
- `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md` — запрет mega-script и требование
  наблюдаемой автоматизации с dry-run.
- `scripts/nmbot_atomic_release.py` — canonical release owner, который нужно
  менять напрямую, а не дополнять вторым framework.
- Google SRE, Release Engineering:
  <https://sre.google/sre-book/release-engineering/>.
- Twelve-Factor, Build, release, run:
  <https://12factor.net/build-release-run>.
- systemd unit dependency semantics:
  <https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html>.

Внешние источники подтверждают общий pattern, но не являются доказательством
текущего NMBot production. Реализация и production status доказываются только
локальным source/test evidence и отдельными свежими release receipts.
