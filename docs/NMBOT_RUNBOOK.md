# NMBOT runbook — first command routes

Compact persistent entry map: `AGENTS.md`. Telegram-only historical context:
`docs/legacy/TELEGRAM_LEGACY.md`.

Commands marked **VPS** use network/SSH and observe production. Commands marked **local read-only** do not deploy, restart, call models or write external systems. Do not copy secrets into commands or reports.

`bash scripts/nmbot_diag.sh --local` is local read-only and never invokes SSH or deploy smoke. `bash scripts/nmbot_diag.sh --local --json` emits the local runtime-version and configuration-shape report without secret values. `bash scripts/nmbot_diag.sh --vps --json` is VPS read-only; unavailable or malformed evidence is reported as `unverified`, never healthy.

## Public Jivo PROD entry

Public production Jivo chat: https://jivo.chat/Q5FRTBLR32

Source: confirmed by the release owner on 2026-08-20. This link opens the
client chat; it is not proof of API health, active runtime version or message
delivery. Use the VPS read-only diagnostics below for those checks.

### Current public V6 release

On 2026-08-20 the actual `client-production` contour was migrated to immutable
release `v6-client-production-20260820t1515z`. Fresh VPS post-check confirmed:

- `current` points to `releases/v6-client-production-20260820t1515z`;
- `novostroy-bot-client-production-api.service` and
  `novostroy-bot-client-production-n8n-bridge.service` are active and run from
  `current`;
- API `:8188/health` and bridge `:8193/health` are healthy;
- external release identity matches the release, and the actual selector is
  `V6` at `data/runtime_selector.json`.

This proves deployment and service health, not an end-user response. A new
public Jivo/model smoke was intentionally not sent because the account balance
was unavailable. Do not use the TEST-only smoke runner for this contour.

## Quick V2 contour comparison

To check whether the test and client-production contours run the same V2
source and active runtime selector, run (**VPS read-only**):

```bash
python3 scripts/nmbot_v2_version_compare.py
```

The command calculates SHA-256 hashes for `nmbot_v2/*.py` and
`prompts/v2_*.txt` in both contours, then calls each protected
`/api/runtime-version` endpoint. It prints `"ok": true` only when both source
sets and both active runtime versions match; a mismatch or unavailable contour
exits non-zero. It never writes to VPS, restarts services or prints tokens.
Use `--verbose` only when the per-file hashes are needed for investigation.

Reference: `scripts/nmbot_v2_version_compare.py`; `docs/NMBOT_RUNTIME_VERSIONS.md`.

## 1. Bot not responding / bot not responding

First command (**VPS read-only**):

```bash
bash scripts/nmbot_diag.sh --logs
```

Expected evidence: fresh `bot_error_events-YYYY-MM-DD.jsonl`, bridge structured log and dialogue audit summary if available. If the diagnostic cannot reach VPS, say production status is unverified rather than green.

First failure log: `/home/neiro/novostroy-bot/logs/bot_error_events-$(date -u +%F).jsonl` on VPS.

### Minimal owner-first diagnostic protocol

1. Сначала выполнить один запрос к непосредственному владельцу ошибки, а не
   прогонять весь pipeline.
2. Для первого запроса сразу сохранить безопасные `HTTP status`, `task_id`,
   provider error code и `parse_status`; prompt, model output, tokens и secrets
   в диагностический отчёт не копировать.
3. Не менять live workflow, пока не найдена и не прочитана schema следующего
   downstream-сервиса.
4. На одну гипотезу разрешён один targeted probe. Если два минимальных шага не
   дали подтверждения, остановиться и запросить новый источник или решение.
5. Не добавлять новый pipeline, proxy или fallback, пока минимальный прямой
   маршрут к владельцу ошибки не доказан.

Для batch-сценариев после локализации слоя действует общая First-Failure
процедура: `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`.

Reference: `scripts/nmbot_diag.sh`; operations map rows for diagnostics, API, bridge and error journal.

### Restricted gateway forensic log

Ordinary diagnostics must still never contain raw model output. For a bounded
investigation, `scripts/nmbot_gateway_client.py` can separately retain every
gateway task result received before parsing:

- enable only with `NMBOT_GATEWAY_FORENSIC_LOG_ENABLED=1`; default is off;
- private files: `logs/forensic/gateway-result-YYYY-MM-DD[-NNN].jsonl`;
- directory mode is `0700`, file mode is `0600`;
- `NMBOT_GATEWAY_FORENSIC_LOG_RETENTION_DAYS` controls retention (default `7`);
- `NMBOT_GATEWAY_FORENSIC_LOG_MAX_BYTES` controls rotation (default `10485760`);
- records contain full raw gateway results and are high-sensitivity evidence:
  model output may repeat client text or internal instructions;
- never copy these records into `bot_error_events`, reports, Sheet, chat or task
  output; inspect only the required correlated task locally on the VPS;
- disable the flag immediately after the single targeted reproduction and
  remove retained forensic files when the investigation no longer needs them.

The forensic writer is best-effort and must not alter parsing, retry, fallback
or the client-visible response. A gateway task that finishes only after the
runtime stops polling is not fetched and therefore has no captured result.

## 2. Local change

First command (**local read-only**):

```bash
python scripts/nmbot_check.py docs --dry-run
```

Then run the relevant local scope only: `docs`, `contracts`, `v0`, `v2`, `runtime` or `audit`. Expected evidence is printed as run/skipped/passed/failed. This does not prove production behavior.

## Callback CRM delivery

CRM callback delivery is **off by default**. A lead is durably recorded first;
Google Sheets and CRM then have separate delivery branches, so one external
failure never suppresses the other. The CRM worker sends only `phone`, `name`
and `request` in the background. Endpoint credentials belong only in the
protected environment and must never appear in commands, logs, Git or reports.

Check or change one contour explicitly:

```bash
python3 scripts/nmbot_callback_crm_control.py --contour PROD status
python3 scripts/nmbot_callback_crm_control.py --contour PROD set on --dry-run
python3 scripts/nmbot_callback_crm_control.py --contour PROD set on --confirm
python3 scripts/nmbot_callback_crm_control.py --contour PROD set off --confirm
```

The control-file location comes from `NMBOT_CALLBACK_CRM_CONTROL_FILE`. Missing,
unreadable or malformed control means off. Enabling applies only to leads
created afterwards; it does not backfill old records. A timeout after a CRM POST
is marked `uncertain` and is not retried automatically, preventing duplicates.
Before a real enablement, deploy the reviewed artifact through the normal release
route and obtain separate approval for the external CRM call.

## Broad inventory gate

Фильтр не показывает ЖК без подтверждённого продаваемого лота. Подтверждение
требует структурированных `ads.id`, `ads.state=2` и `ads.status=2`. Управление
вынесено в отдельный CLI:

```bash
python3 scripts/nmbot_inventory_gate.py status
python3 scripts/nmbot_inventory_gate.py enable
python3 scripts/nmbot_inventory_gate.py disable
```

CLI меняет только `NMBOT_BROAD_INVENTORY_GATE_ENABLED` в `.env`; по умолчанию
фильтр включён. Для проверки без записи используйте `--dry-run`, для другого
dotenv-файла — `--env-file PATH`.

При включённом фильтре runtime пишет в безопасный `runtime_summary.inventory_gate`
только агрегаты: `source_count`, `visible_count` и
`excluded_unqualified_count`, а также `enabled/status`. Названия ЖК, телефоны,
payload и секреты в этом событии не сохраняются.

## TEST feature flags

Для TEST-контура три часто меняемых флага переключаются штатным скриптом;
ручное редактирование удалённого `.env` запрещено:

```bash
python3 scripts/nmbot_test_feature_flags.py --status
python3 scripts/nmbot_test_feature_flags.py \
  --set NMBOT_BROAD_INVENTORY_GATE_ENABLED=off \
  --set NMBOT_MAIN_SEARCH_FALLBACK_ENABLED=on \
  --set NMBOT_OPENROUTER_EXCLUDE_REASONING=off \
  --confirm
```

Разрешённый allowlist скрипта:

- `NMBOT_BROAD_INVENTORY_GATE_ENABLED` — фильтр ЖК без подтверждённого лота;
- `NMBOT_MAIN_SEARCH_FALLBACK_ENABLED` — fallback основного поиска;
- `NMBOT_OPENROUTER_EXCLUDE_REASONING` — исключение reasoning из OpenRouter.

Значения задаются только как `on|off`. Скрипт делает backup TEST `.env`,
использует удалённый безопасный env-helper, перезапускает только
`novostroy-bot-api.service` и проверяет health, активность сервиса и runtime V5.
`--dry-run` не меняет конфигурацию. После изменения скриптом проверяется статус;
Jivo smoke выполняется отдельно и автоматически этим инструментом не запускается.
Скрипт фиксирован на TEST-контуре и не предназначен для production.

For source-only simplification mapping, use:

```bash
python scripts/nmbot_check.py audit --dry-run
python scripts/nmbot_check.py audit
```

The audit can also be viewed directly with `python scripts/nmbot_project_audit.py --human` or through the thin wrapper `python scripts/nmbot.py audit --human`. Audit findings are `unreferenced_candidate` / `needs_review` only. They are not production facts and they must never be used as proof that a file is unused.

For recurring prompt/runtime work, print the local documentation context pack before editing:

```bash
python scripts/nmbot.py context --pack prompt/rental --human
```

This command reads `docs/NMBOT_CONTEXT_PACKS.md` and prints required docs/files/checks only. It does not execute checks, deploy, restart, call models/providers/VPS/API/Jivo, or prove production behavior.

### Declarative prompt/model experiments

For the local declarative experiment workflow, first load its context pack and
inspect the registered stages:

```bash
python3 scripts/nmbot.py context --pack experiment/local --brief --human
python3 scripts/nmbot.py experiment stages --json
```

Then follow `stages → start → diff → check → report → compare` in
`docs/EXPERIMENTS.md`. This is a local bookkeeping/static-check route, not a
model evaluation or production/Jivo gate. Candidate prompt/model overlays are not
applied to registered focused/full checks.

For a manual review of similar recipe semantics, run:

```bash
python3 scripts/nmbot.py recipes overlap --human
```

This explicitly calls the local loopback Ollama embedding endpoint and prints review candidates with structured field intersections. It does not call an external provider, alter recipes, run the bot runtime, or prove production behavior. If Ollama is unavailable, stop and treat the report as unavailable rather than inferred.

Reference: `tests/nmbot_check_manifest.yaml`; `docs/NMBOT_COMMAND_MIGRATION.md`; `docs/NMBOT_CONTEXT_PACKS.md`; `docs/NMBOT_RECIPE_OVERLAP.md`; `scripts/nmbot_context_pack.py`; `scripts/nmbot_recipe_overlap.py`; `scripts/nmbot_project_audit.py`; `scripts/nmbot.py`.

## 3. Changed contract

First command (**local read-only**):

```bash
python scripts/nmbot_check.py contracts --dry-run
```

Expected evidence: the manifest lists exact callback/Jivo/API tests before they run. If the changed boundary is search/V2, also run `python scripts/nmbot_check.py v2 --dry-run` and then the accepted scope.

First failure log: inspect the first failing targeted pytest output before running any broader batch.

Reference: `docs/NMBOT_EXTERNAL_CONTRACTS.md`.

### CI local fast gate

GitHub Actions runs the same local-only boundary for push, pull request and manual dispatch:

```bash
python scripts/nmbot_check.py docs contracts quality
```

The workflow installs `requirements.txt` and uses Python 3.12 because no root project Python-version file is present. This CI gate is non-secret and non-production: it does not use SSH, deploy, restart services, call Jivo/API/providers/models, upload artifacts or write external systems. It is only local docs/contracts/offline-quality evidence and must not replace manual/nightly integration, release verification or live Jivo proof.

## 4. Preparing release

First command (**local read-only**):

```bash
python scripts/nmbot_release_preflight.py
```

Expected evidence: local file hashes, manifest scope plan for `docs/contracts`, local/fixture/VPS/direct-API/Jivo evidence buckets, and overall `incomplete`. This command is pre-release local evidence only, not post-deploy proof. By default it does not run tests. Use `python scripts/nmbot_release_preflight.py --run-checks` only when you explicitly want the local `scripts/nmbot_check.py` scopes to run.

Before an approved deploy, assign an immutable release identifier and inspect its
local manifest with `python3 scripts/nmbot_release_identity.py show`. The actual
deploy command requires `--release-id ID`; this records the ID with every Jivo
journal turn after deployment. `release_id` is source attribution, not Jivo
smoke or production-health proof.

Обязательный порядок для любого code/config/prompt release: сначала завершить
проверки и зафиксировать одобренные изменения отдельным Git commit, затем собрать
immutable artifact из commit, выполнить preflight, TEST envelope compatibility
probe и только после этого deploy. После deploy обязательна строгая Jivo smoke,
которая подтверждает принятый опубликованный результат, а не только HTTP 200 или
доставленный fallback.
Нельзя деплоить незакоммиченные изменения или собирать release из незафиксированного
рабочего дерева. Rollback должен ссылаться на предыдущий commit и immutable
artifact.

HTTP 200, health, `systemd active` и terminal `BOT_MESSAGE` сами по себе не
являются доказательством успешного поиска. Smoke обязан подтвердить
`response_model.status=valid`, `published=true`, отсутствие validation/fallback
ошибок и наличие проверяемого trusted provider envelope. Отсутствие producer-полей
`mcp_call_count`, `mcp_tool_name`, `mcp_result_projection` или
`effective_constraints` — release blocker; consumer нельзя ослаблять, чтобы
скрыть несовместимость producer → schema → consumer.

### Обязательный simplicity gate перед commit

Перед каждой правкой и перед commit проверить весь impact chain:

```text
user input → owner/prompt → provider/MCP → validator → state → publication → Jivo
```

1. Сначала использовать существующий owner; не добавлять новый classifier, router,
   prompt, adapter или fallback, если тот же контракт уже имеет владельца.
2. Если ошибка повторяется, не добавлять очередную локальную заплатку: остановиться,
   найти общий контракт и исправить границу ответственности.
3. Для каждого нового слоя письменно указать: почему нельзя использовать текущий,
   какую одну ответственность он получает, какие вызовы добавляет и как будет удалён.
4. Проверить producer → schema → consumer: каждый обязательный результат должен
   реально создаваться producer-ом и проходить тот же контрактный тест. Отсутствующий
   producer — release blocker, а не повод ослабить consumer.
5. Проверить, что решение не ломает соседние state, fallback, publication, rollback
   и runtime-version paths. Если более простой вариант сохраняет контракт — выбрать его.

Без положительного ответа на этот gate commit и release запрещены.

`scripts/nmbot_contour_recon.py --contour <id>` is a separately authorized SSH read-only receipt, never part of local preflight. Post-deploy read-only verify needs separately authorized VPS/Jivo/direct-API route evidence. If Jivo smoke is missing, the release state is `incomplete`, never green. Backup, deploy, restart and live Jivo smoke require explicit release owner stop/go.

Reference: `docs/NMBOT_OPERATIONS_MAP.md`; `scripts/nmbot_atomic_release.py`; `scripts/nmbot_contour_recon.py`; `scripts/nmbot_release_preflight.py`; `scripts/nmbot.py preflight`.

## 5. Rollback

First command (**VPS/read-only evidence before action**):

```bash
bash scripts/nmbot_diag.sh --logs
```

Expected evidence: fresh error/dialogue markers showing why rollback is considered. Rollback itself is a side-effect route and is not implemented by steps 0–6. Use the last release backup and the release owner’s explicit approval; do not change selector/config based only on stale docs.

Use `nmbot_dialogue_report.py` to identify both the affected `runtime_version`
and `release_id`; compare the matching release manifest hashes with its backup
before an approved rollback. Do not infer an old `release_id` for journal rows
where it is `UNKNOWN`.

Reference: `docs/NMBOT_RUNTIME_REGISTRY.md`; operations map rollback/evidence column.

## 6. Approved Jivo deployment procedure

This section is operational detail; the always-on safety gate stays in
`AGENTS.md`. Do not deploy without explicit user/release-owner approval.

1. Record Actual / Contract / Desired and the impact chain.
2. Read the relevant context pack (`jivo/release`, `runtime/fallback`, or the
   changed runtime/prompt pack) and run its local checks.
3. On VPS, confirm current hashes and create a timestamped backup of only the
   files/config that will change. Never reset or replace the dirty project tree.
4. Sync only the intended files, run remote `py_compile`, and verify hashes.
5. Restart only the affected unit: API for API/runtime/prompt changes; bridge
   only for bridge changes.
6. Check both health endpoints and fresh service journal output.
7. Send exactly one first Jivo request and immediately inspect its correlated,
   privacy-safe trace/error/dialogue evidence. Stop the batch on the first error.
8. Require one terminal delivery (`BOT_MESSAGE`, or `INVITE_AGENT` only for an
   explicit operator handoff). Run further cases only after the first route is
   proven.
9. Run `scripts/openrouter_balance` locally, never on VPS.

### First atomic migration of the test API contour

The first migration is baseline-first and is not a normal candidate deploy.
After a fresh TEST VPS source snapshot, reviewed baseline artifact and explicit
approval, use only `scripts/nmbot_atomic_release.py bootstrap-apply` with the
baseline release ID, archive, manifest, exact snapshot-manifest SHA256,
authorized host/port and `--confirm`. The command refuses a candidate artifact
and refuses to start if `current` already exists or any immutable migration path
would be overwritten.

The command owns the pre-write canonical env/mode/path guard, unit/env/identity
backup, immutable extraction, external-path links and remote preflight. It stops
the old API and proves it inactive before changing `.env`, the API unit,
`current` or release identity, then owns baseline cutover and health verification.
On failure after the mutation boundary it restores the old unit, env and identity
state and starts the old API before returning an error. Do not reproduce these
writes manually. Only after baseline health and identity are proven may the
reviewed candidate be deployed by the ordinary atomic `deploy` command. The
bridge and client-production contour are not part of this migration.

### Ordinary one-command TEST API candidate

After the TEST API contour has already been migrated to atomic releases, an
ordinary reviewed candidate can use one orchestrated command instead of running
the safe steps by hand:

```bash
python3 scripts/nmbot_atomic_release.py test-release \
  --release-id REL-ID \
  --overlay scripts/nmbot_api_server.py \
  --out-dir /tmp/opencode/nmbot-test-release-REL-ID \
  --confirm
```

Repeat `--overlay` for each reviewed runtime file. The command is TEST-only: it
uses the authorized TEST SSH target, fixed TEST remote root
`/home/neiro/novostroy-bot`, a fresh TEST source snapshot, an isolated prepared
worktree, exact overlay copy from the canonical project root, fail-closed exact
diff, immutable artifact build, local preflight, the existing guarded atomic
`deploy`, then fresh read-only recon/health/identity confirmation. It preserves
the existing snapshot, provenance, diff, preflight, deploy and rollback guards;
it does not weaken production, client-production or bridge routes and does not
replace the lower-level subcommands for reviewed/manual operation.

This command does **not** include Jivo smoke yet. The next explicit gate after a
successful TEST candidate is the existing smoke runner in strict mode:

```bash
python3 scripts/nmbot_v6_jivo_smoke.py --require-accepted
```

Strict mode reads only bounded, sanitized journal metadata and fails when the
turn is a fallback, validation failure, composer failure, or quality-blocked
result. A terminal `BOT_MESSAGE` with HTTP 200 is not sufficient. The first
failure stops the release; keep the previous immutable artifact for rollback.

### Narrow live API helper overlay

For the exceptional reviewed update of **only** `scripts/nmbot_env_secrets.py`,
use the narrow command below after taking a fresh source snapshot of the
physical VPS root `/home/neiro/novostroy-bot` and recording its manifest hash:

```bash
python3 scripts/nmbot_atomic_release.py live-api-helper-overlay \
  --release-id REL-ID \
  --snapshot-dir /tmp/opencode/nmbot-live-api-snapshot \
  --source-snapshot-manifest-sha256 SNAPSHOT_SHA256 \
  --host neiro@193.107.155.236 \
  --confirm
```

The snapshot schema may still label that root `test`; this command names the
physical root rather than presenting it as a TEST deployment. Before any remote
file write, it proves that `novostroy-bot-api.service` has an `ExecStart` that
contains `/home/neiro/novostroy-bot/current/scripts/nmbot_api_server.py`.
Failure stops before lock creation, staging or upload. The command accepts no
root, path or generic overlay option and writes only the fixed destination
`/home/neiro/novostroy-bot/scripts/nmbot_env_secrets.py`. Its publish transaction
uses the guarded helper backup, staged hash verification, atomic replacement,
post-write hash check, rollback and cleanup; it does not change `current`, a
service, a selector or an environment file. The retired
`client-production-helper-overlay` command is intentionally unavailable because
its former root is not the active API contour.

### Explicit contour identity preflight

#### Mandatory target identity preflight

No dialogue, provider, MCP or Jivo request may start until its target is bound
to a fresh identity receipt. This applies to the documented runner and to
isolated candidate probes.

For an unnamed target such as “new version”, “current version” or “TEST”, do
not choose a VPS root from the label. First ask for an explicit contour ID. The
registry currently records `primary` and `client-production`, but deliberately
does not claim which one receives public Jivo traffic without a correlated Jivo
trace. Then run the read-only command below.

```bash
python3 scripts/nmbot_contour_recon.py --contour <primary|client-production>
```

Before the first external/model call, record:

```text
requested_target: <exact user wording>
contour: <explicit registry ID>
target_kind: deployed_contour | local_candidate
active_release: <fresh recon release_id>
expected_release: <release id or n/a>
candidate_root: <absolute path or n/a>
candidate_commit: <full git SHA or n/a>
candidate_status: clean | dirty | n/a
decision: proceed | stop_mismatch
```

For `local_candidate`, obtain `candidate_commit` and `candidate_status` from
that exact root. Proceed only if the user named the commit/artifact or explicitly
confirmed the candidate after seeing this receipt. A different active release,
dirty candidate, missing identity, unselected contour or ambiguous request is
`stop_mismatch`. This stop happens before file transfer and before
provider/MCP/Jivo calls.

Use the documented runner below for an already deployed release. Do not assemble
temporary remote `dialogue.py` wrappers. An exceptional ad-hoc runner is allowed
only when the documented runner cannot exercise the explicitly confirmed local
candidate; its manifest must contain the same target receipt before execution.

For an already deployed TEST runtime, use the local helper instead of assembling
temporary Jivo wrappers. It creates a synthetic TEST session, stops on the first
failed stage, and prints only opaque event references, event kinds and boolean
checks — never a token, contact, query or bot text.

```bash
# Quick route: /start → search → phone → name
python3 scripts/nmbot_test_dialogue.py --runtime v3

# Full route: also wait for the private callback worker's Sheets receipt
python3 scripts/nmbot_test_dialogue.py --runtime v4 --check-delivery
```

`--check-delivery` succeeds only when the callback outbox reports
`sheet_delivered` with a bounded row reference. The runner is TEST-only and
always uses the local TEST Jivo endpoint; it is not a deployment or production
verification tool.

### Immutable TEST bridge release

Bridge changes use the separate bridge-prefixed scope in
`scripts/nmbot_atomic_release.py`; manual bridge file sync is prohibited. Take
a fresh `snapshot-vps-bridge-source`, prepare an isolated bridge worktree, build
and run `bridge-preflight`, review the exact change, then use `bridge-deploy`
with the exact snapshot-manifest SHA256 and `--confirm`. The bridge artifact is
limited to the bridge, dialogue journal helper, and egress policy. Deployment
switches `bridge-current`, updates/restarts only the bridge unit, checks
`:8093/health`, and restores the previous unit/symlink on failure. API
`current`, API unit/identity and client-production must remain untouched.

After a successful bridge release, send exactly one TEST Jivo turn and inspect
its correlated bridge/API/journal trace immediately. Stop on the first error.

VPS project: `/home/neiro/novostroy-bot`; units:
`novostroy-bot-api.service`, `novostroy-bot-n8n-bridge.service`. Use
`bash scripts/nmbot_diag.sh --vps --json` and the safe dialogue diagnoser rather
than copying raw payloads into reports.

## 7. Model/fallback and response changes

Before implementation, prove the payload stage and identify the owner layer:
`main_search`, `conversation_answer`, `chat`, `operator`, `transport`, or
`fallback`. Read the `runtime/fallback` context pack. A provider/model retry must
preserve the same stage contract; search and chat fallbacks are not
interchangeable. If the stage is absent from fresh evidence, stop at diagnostics.

### Local V0 model switching helper

V0 has two independently configurable model keys for the shared gateway route:

- `NMBOT_V0_SEARCH_MODEL` controls `nmbot_v0_scenario_search`.
- `NMBOT_V0_ANSWER_MODEL` controls `nmbot_v0_answer`.
- `NMBOT_V0_MODEL` remains the legacy shared fallback for both stages.

Resolution is backward-compatible: each stage first uses its stage-specific key,
then `NMBOT_V0_MODEL`, then the existing code default. To inspect or update the
keys without manual `.env` editing and without printing values, use the local
dotenv helper:

```bash
python3 scripts/nmbot_env_secrets.py v0-models --env .env status
python3 scripts/nmbot_env_secrets.py v0-models --env .env set-search '<model-id>'
python3 scripts/nmbot_env_secrets.py v0-models --env .env set-answer '<model-id>'
```

The helper updates only the requested allowlisted key through the same atomic
dotenv writer. This implementation only adds local tooling and runtime
resolution; it does not change any model value, deploy anything, restart
services, touch VPS, or alter V2/V3/prompt/search contracts.

### Client-production model admin portal

The isolated client-production contour has a protected model/runtime panel at
`http://193.107.155.236:8765/secrets-tokens-7f3a9c/nmbot-models`. It manages
only V0, V2, and V3. Search and answer models are separate settings; applying a
model change atomically rewrites `.env.client-production`, restarts only
`novostroy-bot-client-production-api.service`, waits for protected loopback HTTP
readiness on `127.0.0.1:8188`, and then applies/verifies the runtime selector.
Tokens and unrelated dotenv values must never be returned to the browser or
printed in diagnostics.

If Apply reports that changes were rolled back, stop after the first failed
request. Read the fresh portal and API journals before retrying:

```bash
ssh -p 1905 neiro@193.107.155.236 \
  "journalctl --user -u mpn-dashboard-web.service --since '5 minutes ago' --no-pager -n 80"
ssh -p 1905 neiro@193.107.155.236 \
  "journalctl --user -u novostroy-bot-client-production-api.service --since '5 minutes ago' --no-pager -n 80"
```

A same-second `start -> POST 502 -> restart` sequence means that systemd became
`active` before port 8188 accepted HTTP. The fixed transaction uses a bounded
readiness poll (about ten seconds) before runtime POST and repeats that wait in
the rollback path. Do not replace this with `systemctl is-active` alone.
Release `2026-07-29.client-production-admin-readiness.1` first proved the fix:
the next model-changing Apply returned HTTP 200, runtime readback remained V2,
API health returned 200, and no rollback ran. After any future change, verify
fresh POST/status evidence, API health, active runtime, and an empty UI diff.

Prompt or response-composer quality may be checked in an isolated answer-stage
probe when allowed. It is not generalized eval evidence and does not replace the
local contract checks or Jivo release gate. Do not run promptfoo/eval without the
user's explicit confirmation.

## 8. Legacy Telegram rollback/debug

Detailed historical inventory lives in `docs/legacy/TELEGRAM_LEGACY.md`.

`scripts/chat_tester_bot.py`, `novostroy-bot.service`,
`novostroy-bot-staging.service`, Telegram tokens and old `master/staging`
instructions describe the legacy Telegram contour. They may be used only for an
explicitly requested rollback/debug task and never as proof that Jivo production
works. Current Jivo work must not restart those services.

Source references: `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md:165-188,478-509,529-543,558-571`; `docs/NMBOT_OPERATIONS_MAP.md:3,23-27`; `docs/NMBOT_RUNTIME_REGISTRY.md`; `docs/BOT_ARCHITECTURE.md`; `scripts/nmbot_diag.sh:88-118`; `scripts/nmbot_atomic_release.py`; `scripts/nmbot_contour_recon.py`; `scripts/nmbot_release_preflight.py`; `scripts/nmbot_project_audit.py`; `scripts/nmbot.py`.
