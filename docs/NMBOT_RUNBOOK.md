# V6 local and release runbook

## Local build

```bash
python3 scripts/nmbot_atomic_release.py \
  --release-id <exact-id> --out-dir <new-directory> --profile v6-only
```

The builder rejects a dirty worktree and records exact Git commit/tree identity plus a
clean-tree receipt inside the immutable manifest.

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
