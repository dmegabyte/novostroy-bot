# NMBot decisions

Append-only record of important product, architecture and delivery choices. An
accepted entry is never edited to change its meaning; a later entry supersedes
or retires it.

## DEC-001 — Separate product scope from technical mechanics

- **Status:** proposed
- **Context:** historical product, UX, runtime and release details had accumulated
  in overlapping documents and made small changes expand into multiple layers.
- **Decision:** `NMBOT_PRODUCT_CONTRACT.md` owns outcomes, five scenarios,
  non-goals and measurable acceptance. Architecture, external contracts and the
  Build Plan own implementation and delivery details.
- **Rejected:** one giant product/architecture/runbook document; a full C4 or
  arc42 document set for this small project.
- **Consequences:** every product change names one scenario and owner layer;
  JSON, prompts, systemd, shell and incident repair stay out of Product Contract.
- **Evidence:** `NMBOT_PRODUCT_CONTRACT.md`, `NMBOT_VERSION_BUILD_PLAN.md`,
  `AGENTS.md`.

## DEC-002 — Build clean V6 work from canonical master

- **Status:** proposed
- **Context:** a detached recovery chain mixed product, source capture, release
  tooling, systemd and bridge incident work and was not a descendant of the
  current remote master.
- **Decision:** V6 product topics start from current `origin/master`. Recovery
  worktrees are evidence; only a re-reviewed minimal product diff and its focused
  tests may move to a new topic.
- **Rejected:** deploying the recovery chain or copying an old whole prompt or
  production snapshot into product source.
- **Consequences:** finance consultation remains an independent two-file product
  candidate; topology, smoke and incident repairs stay in separate topics.
- **Evidence:** remote master `ca631db6909103cc3118f26881ed63c75e1608df`
  inspected 2026-08-27; recovery evidence remains outside this docs branch.
