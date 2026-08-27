# NMBot version build plan

**Status:** proposed operating contract; no runtime, deploy or production state is
changed by this document.

**Purpose:** make a small NMBot change move through one predictable route instead
of mixing product code, release tooling, configuration drift and incident repair
inside one recovery branch.

## 1. Non-negotiable rules

1. **One change, one owner layer.** Product behavior, deployment tooling,
   topology/configuration and an incident repair are separate changes.
2. **Build once.** An API or bridge artifact is built once from a clean Git SHA;
   TEST and primary receive the same archive SHA, never a rebuild.
3. **Configuration is not code.** Secrets and per-contour values stay outside an
   artifact. A tracked, secret-free topology says which service, ports and
   upstreams are expected for every contour.
4. **Stop after the first failed gate.** Do not patch the already-built artifact
   or add a repair command to the same release. Record the failure, identify its
   owner, then create a new commit and release ID.
5. **Health is necessary but insufficient.** A behavior release needs health,
   a correlated bridge -> intended API -> terminal Jivo receipt, and one terminal
   outcome before a broader scenario batch.
6. **Release IDs are immutable.** Changed hashes always get a new ID.
7. **Documents have one owner and lifecycle.** Temporary incident history does
   not accumulate inside permanent product or release contracts.

These rules preserve the existing snapshot, provenance, rollback and Jivo safety
boundaries; they do not introduce a new runtime or deployment framework.

## 2. Branch and commit policy

`master` is the canonical integration branch. Keep it releasable by using short
topic branches:

| Branch prefix | Contains | Must not contain |
|---|---|---|
| `feat/` or `fix/` | one product behavior or contract change and its tests | systemd/config repair, artifact tooling rewrite |
| `ops/` | release tooling, topology validator, CI or documentation | bot behavior/prompt changes |
| `incident/` | one bounded emergency repair with rollback receipt | unrelated feature or refactor |
| `docs/` | documentation-only change | runtime/deploy mutation |

Use small independently testable commits. A topic branch is merged only at a
defined point; do not periodically merge recovery history into new work.
Recovery branches and detached worktrees are evidence, not a source for blind
deploys.

## 3. Version identities

Keep three different identifiers:

| Identifier | Meaning | Example form |
|---|---|---|
| `runtime_version` | engine selected for one dialogue | `V6` |
| `source_sha` | exact reviewed Git commit | full Git SHA |
| `release_id` | immutable deploy bundle | `v6-YYYYMMDD-<short-sha>-<seq>` |

An artifact manifest records the release ID, archive SHA256, source SHA,
dependency-lock SHA, source snapshot manifest SHA, profile, build receipt and
test receipt. API and bridge may remain separate archives, but the release
envelope records both digests and the same source/config contract version.

Do not introduce SemVer for internal deploy IDs yet. Add SemVer only after the
public API/contract compatibility policy is explicitly defined.

## 4. Secret-free contour topology

The tracked contour registry is the single topology contract. It must describe,
without secrets:

```text
primary:
  api:    127.0.0.1:8088
  bridge: 127.0.0.1:8093
  bridge_upstream: http://127.0.0.1:8088

client-production:
  api:    127.0.0.1:8188
  bridge: 127.0.0.1:8193
  bridge_upstream: http://127.0.0.1:8188
```

Secrets remain in the canonical environment file or secret store. Before any
deploy, a read-only topology preflight compares safe values and shapes only:
unit path, working directory, EnvironmentFile path, expected ports, release
identity, bridge upstream host/port, and required key presence. It must fail
before upload/restart if configuration drifts.

A topology migration is an `ops/` or `incident/` change with a backup, an exact
precondition, rollback and a post-change receipt. It is never silently folded
into a product release.

## 5. Standard release pipeline

### A. Define the change

Before mutation, record this compact Change Brief in the task or PR:

```text
Final outcome:
Product scenario:
Actual:
Desired:
Owner layer:
Impact chain:
Non-goals:
Allowed paths:
Checks:
Definition of Done:
```

The brief must describe the end-to-end outcome, not only the first failing
function or log line. One brief has one scenario and one owner layer.

1. Name the affected scenario in `NMBOT_PRODUCT_CONTRACT.md`, then complete the
   Change Brief above.
2. Create one short branch from current `origin/master`.
3. Declare the exact tests and the one expected user scenario.
4. Stop if the desired behavior is ambiguous or the supported test baseline is
   already red.
5. If read-only impact mapping proves that the task needs another owner layer,
   split it before mutation; do not broaden the current branch.

### B. Local verification

1. Run `py_compile` and targeted unit/contract tests for the owner layer.
2. Run the supported CI baseline from `.github/workflows/nmbot-local-fast-gate.yml`.
3. If the broader suite contains historical failures, maintain an explicit
   quarantined list with an owner, reason and removal date. Do not call a broad
   red suite green.
4. Commit only the independently passing topic.

### C. Build and attest

1. Record clean source SHA and status.
2. Build immutable API/bridge artifacts once.
3. Store archive SHA256, manifest, provenance and preflight receipt together.
4. Verify import/startup from the archive, not only from the checkout.
5. Do not deploy an uncommitted artifact or rebuild for another contour.

### D. TEST promotion

1. Run read-only contour/topology preflight before any write.
2. Deploy the already-built artifact SHA(s) only after a separate approval.
3. Check release identity and both health endpoints.
4. Run exactly one strict Jivo smoke.
5. Require a correlated receipt: bridge trace -> selected contour API journal ->
   terminal Jivo outcome.
6. Only after that route passes, run the agreed scenario set and stop at the
   first failure.

### E. Primary promotion

1. Confirm the exact TEST-tested archive SHA(s), source SHA and release envelope.
2. Run a fresh read-only primary topology receipt.
3. Promote the same artifact SHA(s), never a rebuilt archive.
4. Run one strict primary smoke and preserve its correlation receipt.
5. Roll back using the prior immutable release if any post-write gate fails.

## 6. Stop/go matrix

| Gate failure | Action | What must not happen |
|---|---|---|
| dirty source or unknown base | stop before build | snapshot/deploy |
| targeted or supported baseline test fails | stop in code owner | unrelated patch or deploy |
| topology/config drift | stop in config owner | artifact upload or restart |
| artifact/preflight failure | stop in build owner | deploy or rebuild in place |
| TEST smoke lacks correlation/terminal result | reject release | primary promotion or scenario batch |
| primary smoke fails | rollback or preserve current state | next scenario or second repair in same release |

An incident can use a dedicated `incident/` branch only when it declares the
exact changed value/path, precondition, backup, rollback and terminal receipt.
After it is resolved, document the durable topology guard separately; do not
keep growing the ordinary release path with incident-specific commands.

## 7. Document ownership and precedence

| Document | Owner role | Update trigger |
|---|---|---|
| `NMBOT_PRODUCT_CONTRACT.md` | product owner | user outcome, scenario, consent or fact-safety changes |
| `CURRENT_ARCHITECTURE.md` | runtime owner | package, route, state or component boundary changes |
| `NMBOT_EXTERNAL_CONTRACTS.md` | integration owner | Jivo, gateway, CRM or journal contract changes |
| `NMBOT_VERSION_BUILD_PLAN.md` | release owner | build, promotion, identity or gate changes |
| `NMBOT_RUNBOOK.md` | operations owner | operator command, contour or rollback changes |
| `DECISIONS.md` | affected owner | important choice is accepted, superseded or retired |

Within this V6-only root, nearest `AGENTS.md` defines repository scope and safety.
Source/tests establish **Actual** behavior; Product Contract establishes desired
product outcomes; architecture/external-contract docs explain boundaries; Build
Plan and Runbook define delivery and operations. A conflict stops work and gets a
decision entry; it is not resolved by copying text between documents.

`DECISIONS.md` is append-only. One entry records one important decision, context,
chosen option, rejected alternative, consequences, evidence and status
`proposed|accepted|superseded|retired`. Accepted entries are superseded by a new
entry rather than rewritten. No ADR framework or validator is required.

## 8. Source basis

Project contracts:

- `docs/NMBOT_RUNBOOK.md` — deploy/Jivo stop-go procedure.
- `docs/NMBOT_RELEASE_IDENTITY.md` — runtime versus immutable release identity.
- `docs/NMBOT_PRODUCT_CONTRACT.md` — product scenarios and acceptance boundary.
- `docs/CURRENT_ARCHITECTURE.md` — current V6-only runtime boundary.
- `AGENTS.md` — repository safety and verification contract.

External principles, retrieved 2026-08-27:

- [Git workflows](https://git-scm.com/docs/gitworkflows): small logical commits
  and topic branches.
- [SLSA build requirements](https://slsa.dev/spec/v1.2/build-requirements):
  provenance binding artifact digest, source and build.
- [Twelve-Factor Config](https://12factor.net/config): strict separation of
  deploy-specific configuration from code.
- [Google DORA capabilities](https://cloud.google.com/architecture/devops/devops-tech-trunk-based-development):
  trunk-based development, small batches, CI/CD and observability as delivery
  capabilities.
- [C4 model](https://c4model.com/) and [arc42](https://arc42.org/overview/):
  architecture at appropriate levels instead of one giant document.
- [Architecture Decision Records](https://github.com/architecture-decision-record/architecture-decision-record):
  explicit decision context, consequences and superseding lifecycle.
