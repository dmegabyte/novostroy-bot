# V6 local and release runbook

## Local build

```bash
python3 scripts/nmbot_atomic_release.py \
  --release-id <exact-id> --out-dir <new-directory> --profile v6-only
```

Build only from a clean committed SHA. The resulting archive/manifest is immutable.

## Safe activation sequence

1. Name the explicit TEST or PROD contour and verify its current live identity separately.
2. Register the exact artifact and prepare the inactive A/B slot.
3. Require exact V6/profile/release health before switching.
4. Atomically switch the route; keep the previous slot warm.
5. Run the separately authorized post-check. If it fails, restore the previous route.

`sync` and `promote` copy exact bytes only. Neither activates PROD. Activation, rollback,
external smoke, commit, push and deploy are separate approvals.

## Read-only local diagnostics

`scripts/nmbot_diag.py` reads local identity, health snapshots and route files only. It does
not call HTTP, VPS, Jivo or a model.
