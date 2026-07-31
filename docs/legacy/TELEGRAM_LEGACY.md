# Telegram legacy contour — historical only

This document preserves migrated Telegram/local-dev history so it does not look
like the current Jivo release route. Nothing here is a Jivo gate, production
health proof, deploy instruction, or permission to restart Telegram services.

Current replacement path for client-facing work:

- Jivo bridge: `scripts/nmbot_n8n_bridge_server.py`, unit
  `novostroy-bot-n8n-bridge.service`.
- Private API/runtime selector: `scripts/nmbot_api_server.py`, unit
  `novostroy-bot-api.service`.
- Production project path: `/home/neiro/novostroy-bot`.
- Diagnostics/runbook: `docs/JIVO_DIAGNOSTICS.md`, `docs/NMBOT_RUNBOOK.md`,
  context pack `diagnostics/trace`.

## Historical boundary

- `scripts/chat_tester_bot.py` was the Telegram bot runtime and local rollback
  reference. It is not the current Jivo production flow.
- `novostroy-bot.service` and `novostroy-bot-staging.service` were Telegram
  systemd units. They must not be used as proof that Jivo/API/bridge works.
- Telegram staging is retained only as historical reference or explicitly
  requested rollback/debug. Do not run a legacy staging poller on the production
  Telegram token: two pollers on one token can interfere with each other.
- Old local logs, Telegram journals, and service status are historical snapshots
  unless the task is explicitly about this legacy contour.

## Historical services, paths and logs

Known historical locations and files:

- local development path: `/home/ser/ai/projects/nmbot`;
- old VPS production path used by this project name: `/home/neiro/novostroy-bot`;
- old Telegram staging path: `/home/neiro/novostroy-bot-staging`;
- Telegram runtime entrypoint: `scripts/chat_tester_bot.py`;
- local CLI helper: `scripts/chat_cli.py`;
- log finder: `scripts/find_dialog.py` for `logs/dialogs-*.jsonl`;
- operational journals used by the legacy bot:
  - `logs/dialogs-YYYY-MM-DD.jsonl`;
  - `logs/dialogs-YYYY-MM-DD.md`;
  - `logs/sim_journal-YYYY-MM-DD.md`;
  - `logs/stateful_dialog_reviews.md`.

These files may still be useful for archaeology, regression context or local
debug, but they do not prove current Jivo delivery.

## Historical local tester and script inventory

Legacy/local tools mentioned in older docs and changelog entries:

- `scripts/chat_tester_bot.py` — Telegram runtime / rollback reference;
- `scripts/chat_cli.py` — local CLI for a two-step search/chat flow;
- `scripts/nmbot_test_agent.py` — historical CLI test agent and scenario suites;
- `scripts/nmbot_deploy_smoke.py` — originally checked the live Telegram process;
- `scripts/nmbot_quality.py` — operational checks over dialogue logs;
- `scripts/find_dialog.py` — dialogue-log search helper;
- `scripts/run_bot.sh` — old local runner that sourced `.env`.

Historical Telegram bot commands documented for dev use:

- `/start` — greeting/settings reset;
- `/model` — choose search model;
- `/mcp` — toggle novostroym MCP usage;
- `/reset` — reset settings;
- `/status` — print current settings.

## Historical model/pipeline notes

The legacy Telegram pipeline was described as:

1. Search through MCP `novostroym`, collecting facts and links.
2. Final chat answer from the collected facts, without direct MCP access.

Older docs also mention search fallback/race behavior and broad-search shortlist
rules in `scripts/chat_tester_bot.py`. Treat those as historical Telegram-runtime
notes unless a current Jivo/runtime document explicitly owns the same contract.

## Historical environment key names

Names only; no values belong in docs or reports:

- `TELEGRAM_BOT_TOKEN`;
- `OVERMIND_URL`;
- `OVERMIND_TOKEN`;
- `OPENROUTER_API_KEY`.

Current Jivo/API/bridge env key names are documented in the active Jivo docs and
examples, especially `docs/JIVO_DIAGNOSTICS.md` and
`deploy/systemd/novostroy-bot-n8n-bridge.env.example`.

## Source anchors

- `README.md:8-18,64-68,153-207,230-236`;
- `docs/JIVO_DIAGNOSTICS.md:13`;
- `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md:165-188,478-509`;
- `docs/NMBOT_RUNBOOK.md` legacy section.
