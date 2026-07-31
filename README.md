# nmbot — Novostroy AI Bot

Compact project entrypoint for navigation only. Detailed contracts, runbooks,
release gates and dated evidence live in the owner documents linked below.

## Runtime/version map

Runtime contracts are documented in
[`docs/NMBOT_RUNTIME_VERSIONS.md`](docs/NMBOT_RUNTIME_VERSIONS.md) and registered
in [`docs/NMBOT_RUNTIME_REGISTRY.md`](docs/NMBOT_RUNTIME_REGISTRY.md). Docs can
describe supported architecture, but they do **not** prove the current live
selector or production readiness.

| Version | Client name | Boundary |
|---|---|---|
| V0 | Валерия | Isolated two-prompt runtime under `nmbot_v0/*`. |
| V1 | Татьяна | Independent typed TEST runtime under `nmbot_v1/*`; guarded GPT final-text path is not publish proof. |
| V2 | Ирина | Typed runtime under `nmbot_v2/*` with deterministic renderer and optional validated composer. |
| V3 | Светлана | V2 runtime plus `IntentPlanV3` semantic contract; V3 evidence must be selector-specific. |
| V4 | Марина | Isolated one-prompt runtime under `nmbot_v4/*`; local implementation evidence is not live proof. |

Live runtime/version claims require fresh runtime marker/Jivo evidence through the
runbook route, not README text.

## Current transport map

- Client-facing transport: Jivo widget → n8n bridge → private API → selected
  runtime → terminal `BOT_MESSAGE`.
- Main VPS source path used by operations docs: `/home/neiro/novostroy-bot`.
- Main local development path in this checkout: repository root.
- Telegram runtime (`scripts/chat_tester_bot.py`, historical systemd units) is
  legacy rollback/debug only and is not a Jivo release gate.

Do not infer current production health, active selector, composer mode, delivery
success or client-visible UX from this map. Use the runbook/diagnostics owner and
fresh evidence when network/VPS checks are explicitly allowed.

## Documentation routes

- [`docs/README.md`](docs/README.md) — primary docs registry by lifecycle/status.
- [`docs/CURRENT_ARCHITECTURE.md`](docs/CURRENT_ARCHITECTURE.md) — compact system map.
- [`docs/NMBOT_RUNTIME_VERSIONS.md`](docs/NMBOT_RUNTIME_VERSIONS.md) — V0/V1/V2/V3/V4 separation passport.
- [`docs/NMBOT_RUNTIME_REGISTRY.md`](docs/NMBOT_RUNTIME_REGISTRY.md) — runtime selector and ownership registry.
- [`docs/NMBOT_EXTERNAL_CONTRACTS.md`](docs/NMBOT_EXTERNAL_CONTRACTS.md) — Jivo/callback contract owner.
- [`docs/NOVOSTROYM_MCP_SCHEMA.md`](docs/NOVOSTROYM_MCP_SCHEMA.md) — canonical `novostroym` MCP/database schema reference.
- [`docs/NMBOT_RUNBOOK.md`](docs/NMBOT_RUNBOOK.md) — operational commands, local gates, deploy/rollback boundaries.
- [`docs/JIVO_DIAGNOSTICS.md`](docs/JIVO_DIAGNOSTICS.md) — Jivo trace, terminal delivery and diagnostics.
- [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) and [`docs/RESPONSE_MODEL_EVAL.md`](docs/RESPONSE_MODEL_EVAL.md) — prompt/model experiments and response-model evidence.
- [`docs/NMBOT_ENGINEERING_LESSONS.md`](docs/NMBOT_ENGINEERING_LESSONS.md) — reusable lessons and prevention checklists.
- [`docs/ARCHIVE_INDEX.md`](docs/ARCHIVE_INDEX.md) — historical records and old evidence.

## Local safe checks

```bash
# Documentation/static docs gate; no VPS/network/model/deploy route.
python3 scripts/nmbot_check.py docs

# See available local gates before choosing a narrower scope.
python3 scripts/nmbot_check.py docs --dry-run
```

Broader tests, live diagnostics, deploy, promptfoo/eval and provider/model calls
require a separate explicit decision.
