# NMBOT runtime registry — source map and dated evidence

This registry separates source-supported routes from dated live configuration snapshots. A snapshot proves configuration/service state only; user-visible readiness still requires a fresh correlated Jivo request, terminal delivery trace and error-journal check.

## Last safe live configuration snapshot

Read-only VPS check on **2026-07-23** (no deploy, restart or secret values): global runtime file selected `V2`; `NMBOT_V2_RESPONSE_COMPOSER_MODE=publish`; `NMBOT_V3_RESPONSE_COMPOSER_MODE=shadow`; repeating bridge statuses were not configured and therefore remained at the disabled default; API and bridge units were `active/running`. `data/nmbot_release_identity.json` was absent, so release identity is `MISSING`. This snapshot is not fresh Jivo E2E evidence.

> ⚠️ Этот live-снимок устарел. Он не описывает текущий VPS-статус. Для выводов
> о работе Jivo сначала выполните свежую read-only проверку по
> `docs/NMBOT_RUNBOOK.md`.

### Fresh read-only VPS observation — 2026-07-30

- `novostroy-bot-api.service` и `novostroy-bot-n8n-bridge.service` были
  `active` с main PID.
- API health был доступен для read-only проверки.
- `/api/runtime-version` вернул malformed/unsupported runtime marker, поэтому
  активную runtime-версию по этому ответу установить нельзя.
- Error-event log существовал, но был stale на момент проверки.

Это не Jivo E2E-проверка и не доказательство клиентского UX. Для такого вывода
нужен отдельный свежий correlated Jivo request с terminal `BOT_MESSAGE` и
журналом ошибок.

## Последнее TEST-доказательство V3 availability

- Release: `nmbot-v3-lot-availability-test-20260730-1015`.
- Scope: TEST only; client-production, bridge и глобальный selector не менялись.
- Smoke: `/start_3` → поиск → выбор ЖК → проверка наличия; все четыре ответа
  были terminal `BOT_MESSAGE`.
- Safe trace: `selected_enrichment=1`,
  `availability_evidence.confirmation=confirmed`, source `gateway`.
- Это подтверждает только TEST-путь и каталожное lot evidence, но не текущий
  production status и не гарантию брони/сделки.

| Runtime / flag | Source-demonstrable selector | Default / allowed values | Service / entry point | External inputs | Status | Required verification | Rollback / review |
|---|---|---|---|---|---|---|---|
| V0 | `runtime_version_override` may force `V0`; active runtime store can select V0 | Allowed by `SUPPORTED_RUNTIME_VERSIONS`; adapter falls back to V2 for invalid values | Jivo API entry point `scripts/nmbot_api_server.py`; engine in `nmbot_v0/` | Jivo/API turn normalized by API service | source-supported rollback; not global selection in 2026-07-23 snapshot | `nmbot check v0`; fresh Jivo `BOT_MESSAGE` evidence after deploy only | V0/runtime role; review on selector change |
| V2 | Active/default source route when no supported override applies | `_normalize_runtime_version()` returns `V2` for empty/invalid values; adapter initializes `version = "V2"` | `scripts/nmbot_api_server.py` → `scripts/nmbot_runtime_adapter.py` → `nmbot_v2/` | Jivo/API turn, search/evidence contracts | global live selector `V2` in 2026-07-23 config snapshot; fresh Jivo evidence still required for readiness | `nmbot check v2`; runtime marker and correlated dialogue/terminal delivery check | V2/runtime role; review after runtime or composer change |
| V3 public mode | Selector can return `V3`; adapter decorates V2 engine result with `runtime=v3`, `engine=v2` | Allowed value `V3`; source shows V3 uses V2 authoritative path with decoration | Same API/adapter entry point plus `nmbot_v2/transition.py` and semantic planner | IntentPlanV3/planner structures | opt-in source route; not global selection in 2026-07-23 snapshot; V3 composer config was `shadow` | Runtime adapter/V3 targeted tests; live marker required before production claim | V3/runtime role; review on selector change |
| V4 local route | Session override `/start_4` may select V4 outside client-production; no global switch was made | Allowed value `V4`; invalid values still fall back to V2 | `scripts/nmbot_api_server.py` → `scripts/nmbot_runtime_adapter.py` → `nmbot_v4/` | Jivo/API turn; one gateway request to `google/gemini-3.6-flash` with MCP server `novostroym` | locally implemented and reviewed 2026-07-30; no provider/MCP/VPS/Jivo proof | Focused offline suite, then separately approved first diagnostic request; production claim additionally requires normal live marker and correlated Jivo terminal evidence | Isolated `nmbot_v4` state; fail closed; no model retry/fallback |
| Intent plan flag | `NMBOT_INTENT_PLAN_VERSION` is documented as defaulting to v2 and v3 opt-in in task context; exact code owner/value not asserted here | Production value unknown without live env/config check; do not expose env values | Planner/transition layer under `nmbot_v2/` | Planner model/semantic plan boundary | configurable; production value `needs_live_verification` | Targeted planner tests and safe runtime marker, never secret dump | owner TBD; review date TBD |

Known current Jivo service names from source/docs context: `novostroy-bot-api.service` and `novostroy-bot-n8n-bridge.service`. Legacy Telegram references exist in project docs, but they are not the current Jivo release gate unless a separate live decision says otherwise.

Concurrency note: the current API serializes turns by session through `SessionLockRegistry` and writes the JSON state file through an atomic temporary-file replacement. There is no state-version compare-and-swap contract; architecture preflight intentionally keeps that item as a warning rather than claiming unsupported CAS safety.

Source references: `scripts/nmbot_runtime_adapter.py:52-83,86-119`; `scripts/nmbot_api_server.py:755-808,846-876,2283-2316,2832-2845,3163-3179`; `scripts/nmbot_diag.sh:12-18`; `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md:73-94,427-443`. Live snapshot source: read-only VPS runtime/env-key/service-state check on 2026-07-23.
