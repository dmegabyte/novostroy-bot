# nmbot — persistent agent map

Root file is only the compact, always-on instruction map. Do not duplicate
runbook commands here; open the linked owner document for details.

## Current source of truth

- Fresh read-only evidence confirms two live VPS contours:
  - `primary`: `/home/neiro/novostroy-bot`,
    `novostroy-bot-api.service` and `novostroy-bot-n8n-bridge.service`;
  - `client-production`: `/home/neiro/novostroy-bot-client-production`,
    `novostroy-bot-client-production-api.service` and
    `novostroy-bot-client-production-n8n-bridge.service`.
- A deliberately marked Jivo dialogue was correlated with `primary`: inbound
  request, upstream `200`, egress guard and terminal Jivo send `200` occurred
  in one trace; `client-production` had no matching new trace. Therefore the
  tested Jivo route was `primary` on 2026-08-21. This is a dated live receipt,
  not a permanent routing guarantee: verify again before a release.
- `config/nmbot_deployment_contours.json` deliberately keeps both
  `traffic_role: unverified`; it is a safe target registry, not a live routing
  status store. Never infer the public target from a root name, health check or
  remembered documentation.
- Inbound: Jivo `CLIENT_MESSAGE` → bridge → private API. Outbound: terminal
  `BOT_MESSAGE`; `INVITE_AGENT` only for live operator handoff.
- Runtime selector/version docs cover V0/V1/V2/V3/V4 as separate contracts:
  V0 Валерия, V1 Татьяна, V2 Ирина, V3 Светлана and V4 Марина. V2/V3 composer
  modes are `off|shadow|publish`; V1/V4 have their own separate publication
  boundaries. Live values are proven only by fresh VPS/runtime markers, never by
  docs, memory, local files or stale logs.
- **Jivo live-diagnostics rule:** statements about the current Jivo bot
  (availability, runtime version, active release, errors, delivery or search
  behavior) require a read-only SSH/VPS check first. Local logs and local
  diagnostic output are historical/development evidence only; they may guide
  investigation but cannot establish current status.
- Telegram (`scripts/chat_tester_bot.py`, `novostroy-bot.service`,
  `novostroy-bot-staging.service`) is historical rollback/debug only and never
  a Jivo release gate. Details: `docs/legacy/TELEGRAM_LEGACY.md`.
- Do not use model memory as a project fact. Order: project docs/NotebookLM →
  source/tests → fresh VPS/Jivo evidence when explicitly authorized.
- Do not print secrets or `.env` values. Key names and shape/existence checks
  are allowed.

## Documentation routes

| Need | Open first |
|---|---|
| Primary human docs registry by lifecycle/status | `docs/README.md` |
| Current high-level system map | `docs/CURRENT_ARCHITECTURE.md` |
| Project context retrieval, NotebookLM isolation, STOP-2 route contract | `docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md` |
| Multi-project memory/context integration plan | `docs/MULTI_PROJECT_MEMORY_HARNESS_INTEGRATION_PLAN.md` |
| Local deterministic navigate / FTS cards before grep/read | `docs/NMBOT_RETRIEVAL.md` |
| Operational first command, deploy/rollback boundaries | `docs/NMBOT_RUNBOOK.md` |
| Local context packs by task | `docs/NMBOT_CONTEXT_PACKS.md` |
| Documentation update gate, queue, owner routing | `docs/DOCUMENTATION_GATE.md` |
| V0/V1/V2/V3/V4 selector and version ownership | `docs/NMBOT_RUNTIME_VERSIONS.md`, `docs/NMBOT_RUNTIME_REGISTRY.md` |
| Jivo trace, terminal delivery, diagnostics | `docs/JIVO_DIAGNOSTICS.md`, context pack `diagnostics/trace` |
| Release identity/source attribution | `docs/NMBOT_RELEASE_IDENTITY.md` |
| Experiment/prompt/model change log | `docs/EXPERIMENTS.md` |
| External callback/Jivo contracts | `docs/NMBOT_EXTERNAL_CONTRACTS.md` |
| Owners, stop/go, lifecycle map | `docs/NMBOT_OPERATIONS_MAP.md` |
| Historical planning records and old evidence | `docs/ARCHIVE_INDEX.md` |
| Legacy Telegram/local dev history | `docs/legacy/TELEGRAM_LEGACY.md` |

## Memory and session policy

Before project-specific conclusions, check project memory/docs. After completed
work: store important project facts in NotebookLM, personal summary in MemPalace
diary, and if a new fact is absent from docs ask: «Обновить доку?».

## UX North Star

For answer, prompt, routing, state, MCP/search parsing, visible-options or
operator-flow changes, read `docs/IDEAL_IRINA_UX.md` first. Do not fix a single
example with a private regex/`if` until the owner layer and neighbouring
scenario impact are known.

## Mandatory safeguards

### Test target identity gate

Before any model/provider/MCP/Jivo test, including an isolated temporary probe,
identify the target **before the first paid or external call**:

1. For “current”, “new”, “TEST” or otherwise unnamed versions, do not infer a
   VPS root from the label. First name an explicit contour from
   `config/nmbot_deployment_contours.json`, then run its fresh read-only receipt:
   `python3 scripts/nmbot_contour_recon.py --contour <id>`. If the user did not
   name the contour, stop before any external call. `TEST` is not an alias for a
   remembered root, old commit, notebook note or local path.
2. Record a compact target receipt: user-request wording, explicit contour ID,
   target kind (`deployed_contour` or `local_candidate`), active release ID, and
   either the expected release ID or the candidate root plus exact Git commit
   and clean status.
3. A local candidate may be tested only when the user named its commit/artifact
   or confirmed it after seeing both the active TEST release and candidate
   identity. If identities differ or intent is ambiguous, stop before copying
   files or calling a model/MCP/Jivo route.
4. Use the documented TEST runner. An ad-hoc VPS `/tmp` runner is forbidden
   unless the documented runner cannot exercise the required candidate and the
   user explicitly approves the shown candidate identity and exceptional route.

Do not spend model tokens to discover that the wrong version was selected. The
full receipt and command order live in `docs/NMBOT_RUNBOOK.md`.

### Production-affecting work

Before any NMBot production code change, first run a fresh read-only VPS source
snapshot with `scripts/nmbot_atomic_release.py snapshot-vps-source`, compare it
with `compare-snapshot`, modify only an isolated `prepare-worktree`/source copy,
then build/test a full immutable artifact and deploy the whole package. Do not
manually edit VPS files and do not deploy partial files. This rule is for
production changes only; simple questions and local experiments do not need it.

Local checks never prove production. For any approved production write: build
the impact chain, back up touched files/config, sync only intended files,
restart only the affected Jivo unit, then inspect the first correlated live
trace/log immediately. Stop on the first failure. Final proof requires fresh
health plus correlated Jivo trace/smoke and one terminal outcome. Full procedure
lives in `docs/NMBOT_RUNBOOK.md`.

### Model / fallback

Before changing model, fallback, retry, reasoning, stage routing or response
contract: read `docs/BOT_ARCHITECTURE.md`, `docs/RESPONSE_MODEL_EVAL.md`,
`docs/EXPERIMENTS.md` and fresh project notes; record Actual / Contract /
Desired and prove `payload_stage` from evidence. Search fallback is not chat
fallback. Without a proven stage, only diagnostics are allowed. Context pack:
`runtime/fallback`.

### Production status

`systemctl active` alone does not prove a user answer. Production status = fresh
VPS timestamp + health + error/bridge log + Jivo evidence for behavior changes.
Old local logs are historical snapshots.

## Local entry commands

```bash
# 1. Список локальных context packs
python3 scripts/nmbot.py context --list

# 2. План безопасной локальной проверки
python3 scripts/nmbot_check.py docs --dry-run

# 2a. FTS-карточки перед grep/read; текущая сессия выбирает 0..4
python3 scripts/nmbot.py retrieve "короткий вопрос" --json

# 2b. Узкий локальный маршрут: stage_id/path_id, Python symbol, docs anchors
python3 scripts/nmbot.py navigate "v2.search" --json

# 2c. STOP-2 strict executor: сначала выбрать точный target через navigate/session,
# затем gate читает только этот target в жёстком budget. Intent cards — legacy pilot.
python3 scripts/nmbot.py context-gate "ignored by strict executor" --project-id nmbot --evidence-type stage --target-kind stage --target v2.search --definition-of-done "owner source and focused test" --json

# 2d. Passive project memory registry route/denial; no source read or memory writes
python3 scripts/nmbot.py memory-registry --project-id nmbot --json

# 2e. Passive privacy-safe outcome metadata; no behavior hints or adaptive use
python3 scripts/nmbot.py memory-outcomes --validate --json
python3 scripts/nmbot.py memory-outcomes --hints --project-id nmbot --policy-version nmbot-passive-v1 --route docs --evidence-type docs --json

# 2f. Local documentation update queue; validates/routes only, never edits docs
python3 scripts/nmbot.py docs-gate --validate --json
python3 scripts/nmbot.py docs-gate --capture --input tmp/verify_receipt.json --json
python3 scripts/nmbot.py docs-gate --plan --update-id update-001 --json

# 3. Локальный gate нужного слоя
python3 scripts/nmbot_check.py <docs|contracts|v0|v2|runtime|audit>

# 4. Локальная диагностика без SSH
bash scripts/nmbot_diag.sh --local --json

# 5. Read-only production-диагностика
bash scripts/nmbot_diag.sh --vps --json

# 6. Первый маршрут при alarm
bash scripts/nmbot_diag.sh --logs

# 7. Release-preflight без deploy
python3 scripts/nmbot_release_preflight.py
```

Do not run eval/promptfoo without the user's personal confirmation. Context
packs and local checks do not call model/provider/VPS/Jivo and are not
production proof. Owner fields marked `TBD` must not be filled by guessing.
`memory-outcomes` is append-only local metadata; its default hints response is
`hints_disabled_by_policy` and must not be used for behavior.
For broad project discovery: FTS cards first, then semantic selection by the
current session. If no card is suitable, use docs/stage map → grep → read and
abstain from naming unverified files. Ollama is not a retrieval fallback.
