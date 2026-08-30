# NMBot atomic API releases

This helper builds deterministic, API-only release archives for the NMBot API service.

## Identity timestamp contract

`release_identity/nmbot_release_identity.json` includes `generated_at`.

For deterministic builds, the value is derived from `SOURCE_DATE_EPOCH` when that
environment variable is set. If it is not set, the value is the documented
deterministic sentinel string `deterministic-build-clock-not-recorded`.

## Runtime identity file contract

The deploy guard requires every name listed in `required_secret_names`,
`required_setting_names`, and `required_mode_names` to be present in the
canonical API `.env` and to parse to a non-empty value. Required/API-owned fields
are owned by the API `.env` only.

`NMBOT_RELEASE_IDENTITY_FILE` must resolve to the fixed external file:

`<remote_root>/data/nmbot_release_identity.json`

The health verifier and the API share this same external identity-file contract.
The verifier does not use a release-local identity override.

`systemctl --user show novostroy-bot-api.service --no-pager` is parsed as a full
unit contract before any remote write. The API unit must use exactly one
`EnvironmentFiles` entry: either `<remote_root>/current/.env` or the canonical
external `<remote_root>/.env`. Additional environment files are refused.
`ExecStartPre` must be empty. `ExecStart` must be exactly the approved Python
interpreter plus `<remote_root>/current/scripts/nmbot_api_server.py`; wrappers
such as `/usr/bin/env`, shell commands, inline assignments, extra executables,
and extra arguments are refused.

The systemd `Environment` field must not override identity, state, runtime,
bind, contour, or `PYTHONPATH` settings. Guard errors print field names only,
never values.

The canonical remote API env values are `NMBOT_API_HOST=127.0.0.1` and
`NMBOT_API_PORT=8088` in `.env`. The required mode values are exactly
`NMBOT_V2_MANAGER_REWRITER_MODE=off` and
`NMBOT_V3_MANAGER_REWRITER_MODE=publish`. `NMBOT_CONTOUR_PROFILE` must not be
`client_production` for this API release contract.

The TEST callback outbox remains external and durable at
`data/private/callback-outbox`; releases link the parent `data` directory and
must not relocate or recreate this queue during cutover.

The TEST API artifact requires only its real root `.env`; it does not create or
require a separate `.env.client-production`. If that optional file already
exists, guards still require a safe regular file and refuse any API-owned field
inside it. It can never satisfy, duplicate, or override an API requirement.
Guard errors print field names only, never values.

## Artifact and preflight immutability contract

Release archives use a narrowed script allowlist for the API runtime. Arbitrary
`scripts/*.py` files are not accepted. The current allowed script files are the
API entrypoint, release identity reader, runtime adapter, gateway/client runtime
helpers, CRM outbox helper, dialogue journal, egress policy, planner context,
card reformatter, and Bluesminds runtime interceptors/rewriter helpers required
by current API imports. Bridge, worker, deploy, release, test, diagnostic,
publish, and Telegram-only utilities are excluded unless they are demonstrably
part of the API import graph and added explicitly. `planner_trace.py` is included
only because the API planner-context helper imports it directly.

Remote preflight compiles an explicit fixed list of mandatory runtime Python
files with `py_compile` while writing `.pyc` output into a temporary directory
outside the release. It imports the exact release `IMPORT_MODULES` with
`PYTHONDONTWRITEBYTECODE=1` and a clean `PYTHONPATH` containing only the release
root and release `scripts` directory. It snapshots regular release-owned files
before and after compile/import, skips external symlinks, and refuses if the
release file set or file hashes changed. The generated capture command is also
tested against a synthetic root with a full no-write inventory of files,
directories, symlinks, modes, mtimes, symlink targets, and file hashes. Atime is
intentionally not part of the contract because reads can update it. This proves
preflight/capture do not create `__pycache__`, `.pyc`, or other release-tree
mutations inside the immutable release.

## Embedded release identity contract

`release_identity/nmbot_release_identity.json` is validated locally and remotely
before cutover. It must have exactly this schema: `schema`, `release_id`,
`generated_at`, and `tracked_files`. The `release_id` must equal the manifest and
release directory. `generated_at` must be a safe deterministic string. The
tracked file list must exactly match the manifest release-owned files except the
identity file itself, with unique safe relative paths and valid sha256 hashes.

## Deployment lock contract

Remote deploy runs static read-only systemd/config guards first, then acquires
`<remote_root>/.release_lock` with atomic `mkdir`. Only after the lock is held it
probes `current`, validates the complete previous release identity and tracked
files, and checks that the new release id does not already exist. Staging/upload
mutation starts after those locked checks.

The lock is removed with `rmdir` in `finally` only after this process acquired
it. If the deploy otherwise succeeds but lock cleanup fails, the helper returns a
failure rather than `deploy=ok`; the API state is preserved, and the operational
follow-up is to verify the host state and remove the stale lock manually.

If deployment or rollback fails and lock cleanup also fails, both errors are
reported together so the original deploy/rollback failure is not hidden by the
cleanup failure.

## Cutover consistency contract

Deployment intentionally uses a brief API stop to remove the code↔identity false
attribution window. After the new release preflight passes, deploy stops only the
API service, verifies it is inactive with a bounded `systemctl is-active` check,
atomically switches `current`, publishes the matching external identity file,
starts the API, and verifies health plus release identity. Health is accepted
only when the JSON response has `ok is true`, `jivo_token_configured is true`,
and `api_token_configured is true`; the verifier then checks that the canonical
external release identity reports the expected release id. It does not print
token values or other secrets.

If stop/switch/identity/start/health fails, rollback first ensures the API is
stopped, then atomically restores the previous `current` symlink and previous
external identity, starts the API, and verifies previous health plus identity.
Bridge and worker services remain excluded from the API scope.

Bridge releases use a separate immutable scope in the same tool; they never
weaken or extend the API artifact allowlist. The bridge artifact contains only
`scripts/nmbot_n8n_bridge_server.py`, `scripts/dialogue_journal.py`, and
`scripts/nmbot_egress_policy.py`. Its snapshot, manifest, release directory,
`bridge-current` symlink, unit backup, health check, and rollback are separate
from API `current`, API identity, API unit, and worker service.

The manifest separates candidate hashes in `files` from immutable snapshot
hashes in `source_provenance.baseline_files`. Before any write,
`bridge-deploy` proves that live source still matches the snapshot baseline;
after extraction it proves the release matches candidate hashes. It also
requires the exact known unit path, environment file, working directory,
command line and active process state, rejecting drift before upload or lock.
The bridge release scope owns a fixed unit body with
`WorkingDirectory=/home/neiro/novostroy-bot/bridge-current`, the canonical
external `.env`, the exact bridge command, `Restart=always`, and
`RestartSec=5`, while preserving the exact non-secret inline setting
`PYTHONUNBUFFERED=1`; it does not perform generic unit-text rewriting. Any
missing, changed or additional inline environment entry is rejected before
deployment and rechecked after rollback.

For the first migration only, source provenance may be mixed but remains
explicit and hash-bound: the bridge server and dialogue journal come from the
canonical bridge root; egress policy comes from that root when present, or
from a safe API `current -> releases/<safe-id>` target when the canonical file
is absent. Each baseline row records `source_scope`, and provenance records the
API release ID when used. Deploy re-resolves the same origins and release ID
before any write. After migration, all three files come only from the safe
`bridge-current` release.

```bash
python3 scripts/nmbot_atomic_release.py snapshot-vps-bridge-source --host neiro@193.107.155.236 --port 1905 --out-dir /tmp/opencode/nmbot-bridge-snapshots
python3 scripts/nmbot_atomic_release.py prepare-bridge-worktree --snapshot-dir <snapshot> --out-dir /tmp/opencode/nmbot-bridge-worktrees
python3 scripts/nmbot_atomic_release.py build-bridge-from-worktree --worktree-dir <worktree> --release-id <id> --out-dir /tmp/opencode/nmbot-bridge-artifacts
python3 scripts/nmbot_atomic_release.py bridge-preflight --archive <archive> --manifest <manifest>
python3 scripts/nmbot_atomic_release.py bridge-recon --host neiro@193.107.155.236 --port 1905
python3 scripts/nmbot_atomic_release.py bridge-deploy --release-id <id> --archive <archive> --manifest <manifest> --host neiro@193.107.155.236 --port 1905 --source-snapshot-manifest-sha256 <sha256> --confirm
```

`bridge-deploy` restarts only `novostroy-bot-n8n-bridge.service`. Any failure
after the mutation boundary restores the previous unit and previous
`bridge-current` state, starts the old bridge, and verifies old health. Rollback
passes only after the exact previous unit/current state and healthy active
process are re-proven. It must never stop or restart API/worker services.
`bridge-recon` reports `ok: false` for unhealthy or drifted state; its output is
not an unconditional success marker.

The previous-release probe validates the previous embedded release identity
before upload, including a `generated_at` string that matches the same safe
timestamp/sentinel regex used for new release identities.

Release destination and temporary extraction existence checks use symlink-aware
`lexists` semantics. A broken symlink at the target release id is still treated
as an existing immutable release destination and is refused.

This document is still local-only operational documentation; production migration
is not claimed here.

## First-migration bootstrap tooling

`recon` and `capture-baseline` are read-only preparation tools for a first
migration. They are not deploy or cutover commands.

## Permanent snapshot-first production workflow

Before any future production code change, take a fresh read-only source snapshot
from the pinned VPS host/root first. The command is:

```bash
python3 -m scripts.nmbot_atomic_release snapshot-vps-source \
  --out-dir /tmp/opencode/nmbot-vps-source-snapshots
```

The module form is the preferred invocation. Direct invocation remains supported
for existing owner commands: the script bootstraps its project root before it
imports the `scripts` package.

The command is intentionally source-only and read-only. It connects only to
`neiro@193.107.155.236:1905` and `/home/neiro/novostroy-bot`; callers cannot pass
arbitrary remote paths. The generated remote code walks only fixed source roots
`nmbot_v0`, `nmbot_v2`, `scripts`, `prompts`, and `schemas`, plus the explicit
safe root runtime files from the release policy. It excludes `.env*`, hidden
paths, symlinks, non-regular files, data/logs/backups/cache/tests/results/reports,
release bundles, `.log`, `.jsonl`, key/credential/database-like files, secret-like
filenames/content, and deploy/control/release scripts unless they are explicitly
allowlisted API runtime source.

The remote output is a tar stream containing `snapshot-manifest.json` and
`source/<relative-path>` members. The manifest schema is strict:
`schema_version`, `snapshot_id`, `created_at_utc`, `source_host`, `remote_root`,
`policy`, `files`, and `tar_members`. Every file row has only `path`, `sha256`,
`size`, and `mode`. Local extraction rejects duplicate, missing, extra,
traversal, link, device, symlink, secret-like, hash, size, mode, policy, and
manifest↔tar mismatch cases before publishing. Successful publication is one
immutable no-clobber directory:

```text
<out>/<snapshot-id>/
  snapshot-manifest.json
  snapshot.tar              # optional original envelope
  source/...
```

This snapshot is Actual VPS source. It is not a runnable baseline and not a
deployable artifact unless a separate entrypoint/import preflight proves that.

After capture, compare the snapshot to the local checkout without printing file
bodies:

```bash
python3 scripts/nmbot_atomic_release.py compare-snapshot \
  --snapshot-dir /tmp/opencode/nmbot-vps-source-snapshots/<snapshot-id> \
  --project-root /home/ser/ai/projects/nmbot
```

`compare-snapshot` is read-only and only accepts the real NMBot project root. It
prints added, missing, and changed relative paths with hashes only.

To work safely, prepare an isolated copy instead of editing the project checkout
or VPS in place:

```bash
python3 scripts/nmbot_atomic_release.py prepare-worktree \
  --snapshot-dir /tmp/opencode/nmbot-vps-source-snapshots/<snapshot-id> \
  --out-dir /tmp/opencode/nmbot-worktrees
```

The prepared tree is published with the same no-clobber primitive and contains
`source/...` plus `snapshot-provenance.json` with only snapshot id, manifest hash,
source host, and remote root. It does not copy env/data/logs.

Future deploy commands must carry explicit source snapshot provenance. The CLI
requires `--source-snapshot-manifest-sha256 <sha256>` before real SSH deploy can
start. This is only a guard and does not authorize migration, cutover, restart,
or production writes.

`recon` connects only to the pinned production SSH host/root used by this helper
and returns a strict JSON schema made of fixed paths, fixed field names, booleans,
and shape checks. The accepted top-level keys are exactly `ok`, `remote_root`,
`paths`, `systemd`, `env_names`, `canonical_api`, `modes`, `current`, `identity`,
and `health`. Extra fields such as `token`, `prompt_body`, or arbitrary remote
strings are refused. `paths` must equal the canonical current, external `.env`,
and external identity paths. `env_names` must contain exactly the required env-name
set, each with boolean `present` and `nonempty`. `canonical_api`, `modes`,
`systemd`, `current`, `identity`, and `health` each have exact key sets and
primitive types. Recon errors use a fixed sanitized message and never echo remote
stdout or secret-shaped stderr values.

`capture-baseline` requests only the exact sorted `_contract_capture_paths(ROOT)`
path list derived from the same build contract as normal artifacts. Any other
local path set, order, duplicate, missing path, extra path, or unsafe path such as
`.env` is refused before a command string is created. The generated remote code
also verifies that the payload paths exactly match the embedded contract list,
are unique, sorted, relative, non-hidden, and safe; it does not accept arbitrary
paths. Each requested path must be a regular non-symlink file under the fixed
remote root, and the remote side streams tar bytes to stdout without creating
remote files.

The local extractor requires the tar member set to equal the expected contract
paths exactly, with no missing, extra, or duplicate members. Links, device nodes,
traversal paths, non-regular members, secret-like filenames, and secret-like file
contents are rejected. Binary command errors never include stdout bytes, because
stdout is reserved for tar payloads. Failures report only a fixed exit-code
message plus bounded sanitized stderr. Lines assigning names containing `TOKEN`,
`API_KEY`, `SECRET`, or `PASSWORD`, with either `=` or `:`, are redacted as whole
lines, including quoted and multi-word values. Other unsafe stderr lines are also
redacted rather than echoed verbatim.

Baseline capture builds the archive and manifest inside one private staging
directory under the requested output parent and runs exact local preflight there.
Only after capture extraction and preflight both pass is the complete release
directory published as `<output>/<release-id>/` by one Linux
`renameat2(RENAME_NOREPLACE)` operation. The final directory therefore appears
with both files together or does not appear at all. A pre-existing empty or
non-empty directory, regular file, symlink, or broken symlink is never replaced.
There is deliberately no fallback to plain `rename`, because plain directory
rename may replace an empty destination; unsupported kernels, filesystems, or
cross-filesystem publication fail closed. After successful publication the final
release directory is immutable and is never removed as error cleanup. Before
publication, cleanup is limited to the invocation's private staging directory;
it never unlinks a shared final path. Returned archive and manifest paths both
point inside the published release directory.

`bootstrap-plan` is fully local and does not contact the VPS. It verifies both a
baseline artifact and a candidate artifact, then writes only under lexical
descendants of `/tmp/opencode` or `<project>/release_bundles/bootstrap`: a
machine-readable plan JSON, a candidate API systemd unit, and a non-secret
env-additions file. Parent traversal segments (`..`) are refused before path
resolution, for both absolute and relative aliases. The two allowed roots may be
resolved canonically, but the requested output must still be lexically under one
of those roots. The output directory is refused if the original output path is a
symlink or if any existing component from the filesystem root through the output
path is a symlink. The generated unit uses
`WorkingDirectory=/home/neiro/novostroy-bot/current`, exactly one
`EnvironmentFile=/home/neiro/novostroy-bot/.env`, no `Environment`, no
`PYTHONPATH`, no `ExecStartPre`, and `ExecStart=/usr/bin/python3
/home/neiro/novostroy-bot/current/scripts/nmbot_api_server.py`. The env-additions
file contains only `NMBOT_CONTOUR_PROFILE=api_production`,
`NMBOT_RELEASE_IDENTITY_FILE=/home/neiro/novostroy-bot/data/nmbot_release_identity.json`,
and `NMBOT_RUNTIME_VERSION_FILE=/home/neiro/novostroy-bot/data/nmbot_runtime_version.json`.

The bootstrap plan always records `remote_writes_performed=false` and
`cutover_authorized=false`; it does not itself perform or authorize writes.

After a separate explicit approval, the first migration is executed only by the
baseline-only `bootstrap-apply` command:

```bash
python3 scripts/nmbot_atomic_release.py bootstrap-apply \
  --release-id <BASELINE_RELEASE_ID> \
  --baseline-archive <BASELINE_ARCHIVE> \
  --baseline-manifest <BASELINE_MANIFEST> \
  --host 193.107.155.236 --port 1905 \
  --source-snapshot-manifest-sha256 <SNAPSHOT_MANIFEST_SHA256> \
  --confirm
```

The command accepts no candidate artifact. It verifies immutable artifact and
snapshot provenance before remote writes, requires `current` to be absent, checks
the live API and the exact user-unit path, and refuses any existing lock,
release, staging or backup path. Before the lock, backup or upload it also checks
the canonical API bind, manager-rewriter modes, required non-empty API settings,
fixed external paths and optional bridge-env non-ownership. Only the three canonical
bootstrap settings may still be absent; conflicting existing values are refused.

It then creates a private no-clobber backup of the API unit, root `.env` and prior
external identity, extracts the baseline, links only canonical external runtime
paths and runs remote preflight. The two preflight layers are intentionally
asymmetric: local artifact preflight compiles every Python file in the immutable
artifact, while remote preflight rechecks the full file/hash set, compiles the
fixed API runtime subset from `REMOTE_PREFLIGHT_PY_FILES`, and runs the declared
module import smoke. The fixed remote subset must be present in the artifact and
is therefore a tested subset of the stricter local compile closure. The old API
is stopped and proven inactive
before `.env`, the API unit, `current` or release identity can be changed. Only
then are absent bootstrap settings appended, the unit migrated, `current` cut
over to the baseline and health plus matching release identity required.

If a post-backup step fails, rollback first stops the API and proves it inactive,
then restores the old unit, `.env`, prior identity state and absence of `current`,
reloads systemd, starts the old API and requires the old health/config proof.
The immutable extracted baseline is retained for investigation. A candidate may
be deployed only later through ordinary `deploy`, after baseline health,
identity and migrated-unit checks are green.

Baseline capture has important limitations: it proves only the captured
allowlisted API runtime tree, local artifact preflight, no-write generated capture
behavior, sanitized local error reporting, and local no-clobber publication. It
does not authorize production writes, systemd edits, restarts, deploy, or cutover.
Any real migration still needs separate approval, a live gate, and a full audit
of the exact baseline, candidate and release-tool fingerprints.

`capture-baseline` remains the legacy first-migration helper and still uses the
current local release contract path set. For current live VPS source-of-truth
capture, use `snapshot-vps-source`; it derives its remote path set from the fixed
remote safety policy instead of from the local checkout.

## Known medium issue intentionally left documented

`scripts/nmbot_release_identity.py` still has a local-writer hardening concern in
this package. The second audit allowed that medium issue to remain documented;
this fix set does not change that script.
