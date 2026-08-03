# NMBOT command migration — 2026-07-22

Compact persistent entry map: `AGENTS.md`. Telegram-only legacy command history:
`docs/legacy/TELEGRAM_LEGACY.md`.

`nmbot check` is provided now as `python scripts/nmbot_check.py`: a thin local read-only dispatcher over the manifest in `tests/nmbot_check_manifest.yaml`. `python scripts/nmbot.py` is only a convenience wrapper over existing commands; it does not duplicate business logic or replace deploy, live smoke or release verification. The step-7 decision is to improve the existing `nmbot_diag.sh`, not add `nmbot doctor`.

| Existing command / habit | Standard route or future interface | Compatibility | Owner |
|---|---|---|---|
| Manual `python -m py_compile ...` for touched files | `python scripts/nmbot_check.py <scope>` uses manifest-listed compile commands | Existing manual command remains valid; manifest is preferred for repeatability | local check owner TBD |
| Manual targeted pytest for callback/API/runtime | `python scripts/nmbot_check.py contracts/v0/v2/runtime` | Existing tests remain the source; dispatcher does not duplicate pytest | test owner TBD |
| `python scripts/nmbot_architecture_preflight.py --repo . --json` | Included in `docs` and `runtime` scopes | Existing script remains canonical static preflight | architecture owner TBD |
| `bash scripts/nmbot_diag.sh --quick/--logs/--dev` | `nmbot_diag.sh --local` / `--vps` with optional `--json`; it remains the alarm route | `doctor` is not added: the existing script gained explicit read-only modes and structured evidence | operations owner TBD |
| `python3 scripts/nmbot.py release status [--host HOST] [--port PORT]` | Canonical atomic `recon` route after explicit VPS/network approval | Delegates direct argv to `scripts/nmbot_atomic_release.py recon`; read-only SSH recon covers unit/path/env-name/identity/health state, not exact source hashes | release owner TBD |
| Exact VPS source comparison | `python3 scripts/nmbot_atomic_release.py snapshot-vps-source`, then `compare-snapshot` | Snapshot-first atomic workflow; not called by `nmbot check` and requires explicit VPS/network approval | release owner TBD |
| Guarded atomic deploy | Approved `scripts/nmbot_atomic_release.py` deploy workflow after step 8 | Mutates remote and needs explicit stop/go; intentionally not exposed by `nmbot.py release` | release owner TBD |
| Release source attribution | `python3 scripts/nmbot_release_identity.py show`; approved deploy requires `--release-id ID` | Local baseline is not production proof; journal/report show `runtime_version` plus `release_id` per new turn | release owner TBD |
| Local release evidence preflight | `python scripts/nmbot_release_preflight.py` | Local-only Step 8 prep. It hashes target files, plans manifest scopes and stays `incomplete`; `--run-checks` invokes only `scripts/nmbot_check.py` with local scopes | release owner TBD |
| Static project mapping before simplification | `python scripts/nmbot_project_audit.py` or `python scripts/nmbot.py audit` | Local source-only audit. It scans scripts/prompts, reports duplicate SHA256 groups and `unreferenced_candidate` / `needs_review` coverage candidates only. It never proves production behavior and never says a file is unused | local check owner TBD |
| Local context-pack navigation | `python scripts/nmbot_context_pack.py --pack prompt/rental --human` or `python scripts/nmbot.py context --pack prompt/rental --human` | Reads `docs/NMBOT_CONTEXT_PACKS.md` and prints required docs/files/checks. It never runs checks, calls models/providers/VPS/API/Jivo, deploys, restarts, or proves production | local docs owner TBD |
| Recipe semantic-overlap review | `python3 scripts/nmbot.py recipes overlap --human` | Explicit manual local Ollama embedding call. Reports `needs_review` candidates with exact field intersections; not part of `nmbot check`/CI and never production evidence | recipe owner TBD |
| Convenience command entry point | `python scripts/nmbot.py check ...`, `audit`, `preflight`, `diag ...`, `context ...` | Thin direct-argv wrapper. `context` delegates only to `scripts/nmbot_context_pack.py`; `diag` delegates to `bash scripts/nmbot_diag.sh` only when invoked and may be VPS/network depending on user args | operations owner TBD |
| Full repository `pytest` | Not a fast gate; run only when task explicitly requires broad regression | Deferred outside `nmbot check`; manifest scopes target files only | test owner TBD |
| Live Jivo smoke/model/provider checks | Future smoke/release verify route | Deferred to step 8 or explicit release task | runtime owner TBD |
| Promptfoo eval | Not part of simplification steps 0–6 | Forbidden unless separately approved by user | eval owner TBD |

The following deferred commands and live gates remain outside the local preflight: it neither deploys nor supplies VPS, direct-API, or Jivo evidence.

Typical choices:
1. Docs-only simplification: `python scripts/nmbot_check.py docs --dry-run`, then `python scripts/nmbot_check.py docs`.
2. Callback contract change: `python scripts/nmbot_check.py contracts --dry-run`, then contracts scope after reviewing listed tests.
3. Runtime selector/API change: `python scripts/nmbot_check.py runtime --dry-run`, then runtime scope; production remains `needs_live_verification` until release route.
4. Simplification mapping: `python scripts/nmbot_check.py audit --dry-run`, then `python scripts/nmbot_check.py audit` or `python scripts/nmbot.py audit --human`. Treat results as candidates requiring review, not deletion instructions.
5. Release prep without external evidence: `python scripts/nmbot_release_preflight.py` for JSON evidence, or add `--run-checks` after accepting the local `docs/contracts` scope. This is not post-deploy verification and cannot make the release green.
6. Recurring prompt/runtime context: `python scripts/nmbot.py context --pack prompt/rental --human`. Treat it as a navigation aid only; read the listed files and run checks yourself if needed.
7. Recipe review: `python3 scripts/nmbot.py recipes overlap --human`. Review each pair against the registry; similarity is not a defect verdict.
8. Release attribution: create a new immutable ID only for an approved deploy; then use `nmbot_dialogue_report.py` to connect an answer to both runtime and release. See `docs/NMBOT_RELEASE_IDENTITY.md`.

CI uses only the local fast gate `python scripts/nmbot_check.py docs contracts quality` in `.github/workflows/nmbot-local-fast-gate.yml`. It has no secrets, SSH, deploy, release, provider/model/API/Jivo calls or external writes, and it does not replace manual/nightly integration, release verification or live Jivo evidence. The workflow currently uses Python 3.12 because the repository has no root `.python-version`, `runtime.txt`, `pyproject.toml` or `setup.cfg` Python-version declaration.

Post-deploy read-only verification still needs separately authorized VPS/Jivo/direct-API evidence. A missing Jivo smoke remains `incomplete`, not green.

Source references: `docs/NMBOT_PROJECT_SIMPLIFICATION_PLAN.md:207-221,493-509,529-543,558-571`; `docs/NMBOT_OPERATIONS_MAP.md:3,23-27`; `docs/NMBOT_CONTEXT_PACKS.md`; `docs/NMBOT_RECIPE_OVERLAP.md`; `scripts/nmbot_context_pack.py`; `scripts/nmbot_recipe_overlap.py`; `scripts/nmbot_architecture_preflight.py:161-172`; `scripts/nmbot_diag.sh:5-9`; `scripts/nmbot_atomic_release.py`; `scripts/nmbot_release_preflight.py`; `scripts/nmbot_project_audit.py`; `scripts/nmbot.py`.
