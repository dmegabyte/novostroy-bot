# Documentation V1 Lite — executable specification

Дата: 2026-07-28  
Статус: implemented V1 Lite  
Область: documentation registry, local navigation, deterministic offline checks  
Не входит: runtime, prompts, production, deploy, archives, legacy content,
external/product contracts, new dependencies.

## Purpose and boundaries

Documentation V1 Lite is a small, executable contract over the existing NMBot
documentation. It does not create a portal, move files, rewrite owner contracts,
or prove live production state. Its job is to make the first documentation route
obvious, keep planning/history visibly non-current, and catch broken local
navigation before humans or agents rely on it.

Target route:

```text
question
→ one of four routes in docs/README.md
→ one selected owner document
→ the owner command/check/evidence when that owner defines one
→ first failure route from the owner/runbook when needed
```

Local docs checks are read-only and offline. They never call a model, network,
VPS, Jivo, secret store or external URL.

## Information architecture

`docs/README.md` is the primary human registry for `docs/`. It has exactly four
top-level task-route headings:

1. `## 1. Start and understand`
2. `## 2. Build and verify`
3. `## 3. Operate and release`
4. `## 4. Decisions and history`

Each entry is compactly labelled by lifecycle:

- `current` — active owner/current source for local project work;
- `reference` — exact contract/schema/map used when that layer is touched;
- `advanced` — optional navigation, retrieval or specialist workflow;
- `planning` — proposal, roadmap, hypothesis or implementation plan;
- `historical` — dated evidence, legacy or archive record.

Planning and historical materials must stay reachable, but must not look like
current runtime, release or product contracts.

## Owner principle

Detailed rules live in the smallest owner document for the topic. Overview files
link to owners instead of copying commands or normative text.

Core owner map:

| Topic | Owner |
|---|---|
| Documentation registry | [`docs/README.md`](README.md) |
| Compact current architecture | [`docs/CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md) |
| Operations and first commands | [`docs/NMBOT_RUNBOOK.md`](NMBOT_RUNBOOK.md) |
| Jivo trace/evidence interpretation | [`docs/JIVO_DIAGNOSTICS.md`](JIVO_DIAGNOSTICS.md) |
| Owners, contour boundaries, stop/go | [`docs/NMBOT_OPERATIONS_MAP.md`](NMBOT_OPERATIONS_MAP.md) |
| Runtime versions and selector ownership | [`docs/NMBOT_RUNTIME_VERSIONS.md`](NMBOT_RUNTIME_VERSIONS.md), [`docs/NMBOT_RUNTIME_REGISTRY.md`](NMBOT_RUNTIME_REGISTRY.md) |
| External callback/Jivo contracts | [`docs/NMBOT_EXTERNAL_CONTRACTS.md`](NMBOT_EXTERNAL_CONTRACTS.md) |
| Release/source identity | [`docs/NMBOT_RELEASE_IDENTITY.md`](NMBOT_RELEASE_IDENTITY.md) |
| Context retrieval and STOP-2 | [`docs/PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md`](PROJECT_CONTEXT_RETRIEVAL_PROTOCOL.md) |
| Experiments/prompt/model log | [`docs/EXPERIMENTS.md`](EXPERIMENTS.md) |
| Archive and legacy safety | [`docs/ARCHIVE_INDEX.md`](ARCHIVE_INDEX.md), [`docs/legacy/TELEGRAM_LEGACY.md`](legacy/TELEGRAM_LEGACY.md) |

Machine owner targets are also registered in
[`config/project_documentation_owners.json`](../config/project_documentation_owners.json).

## Structural gate

V1 Lite adds one deterministic gate inside the existing docs scope:

```bash
python3 scripts/nmbot_check.py docs
```

The gate preserves the existing static marker checks and additionally validates:

1. `docs/README.md` contains each of the four exact route headings once.
2. No other numbered `## N.` task-route heading is added to `docs/README.md`.
3. Markdown links in `docs/README.md` resolve locally when they are local links.
   External URLs, `mailto:` and pure anchors are ignored; optional query and
   fragment parts are stripped for local existence checks.
4. Local links from `docs/README.md` cannot escape the project root.
5. `config/project_documentation_owners.json` parses as JSON, has a non-empty
   `projects.nmbot` object, and every owner target is a project-relative existing
   file.
6. Absolute owner paths and escaping owner paths are rejected.

Failure output starts with `FAIL docs:` and includes the offending path/rule. The
gate makes no semantic production claims and does not inspect external URLs.

## Implementation stages

### Stage 1 — Registry and route cleanup

- Rewrite `docs/README.md` to the four-route IA.
- Keep all useful documents reachable without moving or deleting them.
- Mark entries with lifecycle labels so `planning` and `historical` cannot be
  mistaken for `current`.
- Reduce the root `README.md` documentation section to route/owner links.
- Keep `AGENTS.md` as compact agent safeguards and owner pointers, with
  `docs/README.md` named the primary human registry.
- Replace the long documentation hierarchy in `docs/CURRENT_ARCHITECTURE.md`
  with a compact routing rule.

### Stage 2 — Offline structural verification

- Add the structural gate to `verify_docs()` in `scripts/nmbot_check.py`.
- Use Python standard library only.
- Keep the existing manifest interface; do not add a new docs manifest command.
- Add focused dispatcher tests for pass and intentional failures.
- Run the targeted test first, then the docs scope, then docs-gate validation.

## Acceptance criteria

V1 Lite is accepted when all of these are true:

1. `docs/README.md` has exactly the four approved top-level task routes.
2. Navigation roles are unambiguous: registry, safeguards, owner docs and full
   architecture each have distinct jobs.
3. `DOCUMENTATION_V1_TZ.md` is this concise executable V1 Lite spec, not the old
   design memo.
4. `python3 scripts/nmbot_check.py docs` includes the structural gate while
   preserving existing static marker checks.
5. Focused tests cover pass plus broken registry link, missing/escaping owner
   target, and malformed/duplicate route layout.
6. `python3 scripts/nmbot.py docs-gate --validate --json` still passes.
7. Runtime, prompts, production, deploy, archives, legacy content and external
   contracts are untouched.

## Conditional ADR rule

No ADR is required for this V1 Lite navigation/check change. Create
`docs/decisions/` only when a future accepted decision crosses components,
changes an external contract, has high rollback cost, or supersedes an earlier
architecture path. For ordinary documentation routing and local gate maintenance,
the owner docs and this executable spec are enough.

## Sources

Project sources:

- [`README.md`](../README.md)
- [`docs/README.md`](README.md)
- [`docs/CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md)
- [`docs/NMBOT_RUNBOOK.md`](NMBOT_RUNBOOK.md)
- [`docs/DOCUMENTATION_GATE.md`](DOCUMENTATION_GATE.md)
- [`docs/ARCHIVE_INDEX.md`](ARCHIVE_INDEX.md)
- [`scripts/nmbot_check.py`](../scripts/nmbot_check.py)
- [`config/project_documentation_owners.json`](../config/project_documentation_owners.json)

External practices used as background only:

- [Diátaxis](https://diataxis.fr/how-to-use-diataxis/)
- [arc42](https://arc42.org/overview)
- [MADR](https://adr.github.io/madr/)
- [Google SRE Workbook — Incident Response](https://sre.google/workbook/incident-response/)
