# V6 local and release runbook

## Local build

```bash
python3 scripts/nmbot_atomic_release.py \
  --release-id <exact-id> --out-dir <new-directory> --profile v6-only
```

The builder rejects a dirty worktree and records exact Git commit/tree identity plus a
clean-tree receipt inside the immutable manifest.

## Fast repeatable V6 version path

For a new V6 version, reuse the same small sequence instead of manually changing VPS files:

1. Build one immutable artifact from a clean Git commit.
2. Activate that exact artifact in the isolated API-only TEST and verify its health.
3. If a Jivo route check is required, run primary bridge preflight, then make the separately
   approved bridge switch.
4. Run one separately approved correlated Jivo smoke. If it fails, use the owner rollback.

The artifact manifest and release ID are the version passport for every step. A local test or
isolated TEST health receipt does not prove Jivo delivery; it only makes the next step safe to
attempt. Do not combine build, TEST deployment, bridge routing, external smoke, rollback, commit,
push, or production activation into one approval.

## Safe activation sequence

1. Name the explicit TEST or PROD contour and verify its current live identity separately.
2. Register the exact artifact and prepare the inactive A/B slot.
3. Require exact V6/profile/release health before switching.
4. Atomically switch the route; keep the previous slot warm.
5. Run the separately authorized post-check. If it fails, restore the previous route.

`sync` and `promote` copy exact bytes only. Neither activates PROD. Activation, rollback,
external smoke, commit, push and deploy are separate approvals.

This controller releases only the V6 API. Bridge and callback worker are separate
infrastructure components: this source tree deliberately provides no deploy command for them.
Do not deploy either component until its own immutable install and rollback procedure has been
implemented, reviewed and explicitly authorized.

## Read-only local diagnostics

`scripts/nmbot_diag.py` reads local identity, health snapshots and route files only. It does
not call HTTP, VPS, Jivo or a model.

## Read-only live contour recon

Select one exact deployed contour before any live conclusion:

```bash
python3 scripts/nmbot_live_recon.py --contour primary
python3 scripts/nmbot_live_recon.py --contour client-production
```

Each command performs one bounded read-only SSH receipt. It checks the API and bridge user
services, their loopback health endpoints, the current release identity and the V6
runtime/profile/release agreement when those fields are available. It never reads dotenv
values, restarts services, changes routes, calls a model/provider or sends a Jivo message.

`service_health=healthy` proves only that both services and health endpoints were available at
`observed_at_utc`. `source_root=verified` proves that both systemd processes resolve inside the
selected contour root. `v6_contract=verified` additionally requires matching V6 profile and
release identity evidence. `traffic_role` deliberately remains `unverified`: current client
routing or terminal delivery requires a separately authorized correlated Jivo trace.

## Isolated API-only TEST

The canonical release controller owns the current A/B TEST route at
`/home/neiro/.local/state/nmbot-v6-release/routes/test.json`. Slot selection and the
loopback port are controller state: operators and smoke commands must not choose
`18088` or `18089` manually. The profile journal remains fixed at
`/home/neiro/.local/state/nmbot-v6-release/profiles/test/data/dialogue/dialogue.jsonl`.

The standalone owner below is the legacy initial TEST fallback only. Do not use it for a
canonical release registered in the A/B controller.

When no independently identified TEST contour exists, do not reuse `primary` or
`client-production`. `scripts/nmbot_test_api_deploy.py` owns one fixed, loopback-only V6 TEST at
`/home/neiro/.local/state/nmbot-v6-clean-test`, transient unit
`nmbot-v6-clean-test.service`, and port `18088`.

The owner has no bridge, connected Jivo ingress, n8n or CRM delivery. The API still contains its
ordinary loopback Jivo handler, but no bridge or webhook routes traffic to it. The deployer
projects only the allowlisted gateway settings from the existing primary environment without
printing their values. Before and after start/stop it requires all four existing API/bridge
services and loopback health endpoints to stay healthy, with unchanged process identities. It
never restarts or stops those services.

Safe order:

1. Build the exact immutable API artifact from a clean Git commit.
2. Run `python3 scripts/nmbot_test_api_deploy.py preflight` (read-only).
3. Review the artifact SHA, target contract and rollback command.
4. Obtain a separate deploy approval, then run `deploy` with `--apply` and exact confirmation
   `DEPLOY-<release-id>-TO-ISOLATED-TEST`.
5. Health verification is automatic and does not call a model/provider. A chat/model probe is a
   separate paid/external approval.
6. The first rollback is an isolated stop, not a route change: after separate approval run
   `stop --release-id <release-id> --apply --confirm STOP-<release-id>-ISOLATED-TEST`.

This first TEST has no previous TEST release. Therefore adding a persistent A/B control plane
would not improve its initial rollback: stopping the isolated service removes its only effect
while both existing live contours continue unchanged. Add A/B only before a later TEST upgrade
that needs a stable TEST endpoint.

The deployer never overwrites an existing release ID or private upload staging directory. On a
failed install it stops the isolated unit if startup had begun and preserves partial evidence
under the isolated TEST root. Inspect that evidence before any separately approved cleanup or a
new release attempt.

## Primary Jivo bridge switch to isolated V6 TEST

`scripts/nmbot_primary_v6_jivo_switch.py` owns one narrow migration of the existing **primary**
bridge from its current local API (`127.0.0.1:8088`) to the active canonical V6 TEST slot. The
owner reads the fixed controller TEST route, requires its exact release ID, accepts only slot A/B
and loopback port 18088/18089, then health-checks that exact upstream. No caller supplies a port
or evidence path. It does not deploy bridge code, alter V_exp, change the primary API, or touch
either client-production service. The visible Jivo ingress may share this primary bridge; do not
claim client isolation without a separately authorized route receipt.

Before switching, provide the exact currently running isolated V6 release ID and run:

```bash
python3 scripts/nmbot_primary_v6_jivo_switch.py preflight \
  --expected-v6-release <release-id>
```

The preflight is read-only. It requires primary API/bridge and both client-production services to
be healthy, their protected process identities unchanged, V6 TEST health to match the requested
release, and the current primary bridge upstream to be exactly `127.0.0.1:8088`.

If preflight reports `switch_status_already_exists`, do not delete the private status or bridge
environment files manually. First run the read-only `recon`. When it proves the bridge route is
already `current`, the expected isolated V6 TEST release is healthy, and the saved backup is still
valid, the separately approved recovery is:

```bash
python3 scripts/nmbot_primary_v6_jivo_switch.py reconcile \
  --expected-v6-release <release-id> --apply \
  --confirm RECONCILE-PRIMARY-BRIDGE-<release-id>-TO-CURRENT
```

`reconcile` does not change the bridge route or restart services. It only marks a verified stale
active status as reconciled after validating the current route, protected health, status schema,
backup integrity, and backup hash. Run preflight again after its receipt; any other reconciliation
failure is fail-closed and needs diagnosis, not manual cleanup.

After separate switch approval, use the exact confirmation printed by the command. The owner saves
a private, restrictive backup of the primary bridge environment, atomically changes only
`NMBOT_BRIDGE_UPSTREAM`, and restarts only `novostroy-bot-n8n-bridge.service`. A failed technical
check restores the backup and restarts that bridge back to its former route. A correlated Jivo
`CLIENT_MESSAGE` -> terminal `BOT_MESSAGE` smoke remains a separate external approval.

The strict smoke owner reads the same fixed controller route and canonical TEST profile journal.
It rejects a release mismatch, unsafe route file, non-A/B slot, or non-loopback upstream.

Rollback is also explicit and restores the saved primary bridge environment:

```bash
python3 scripts/nmbot_primary_v6_jivo_switch.py rollback \
  --expected-v6-release <release-id> --apply \
  --confirm ROLLBACK-PRIMARY-BRIDGE-<release-id>-TO-CURRENT
```

Do not manually edit the primary `.env` or restart the bridge outside this owner: that loses the
verified backup and rollback receipt.
