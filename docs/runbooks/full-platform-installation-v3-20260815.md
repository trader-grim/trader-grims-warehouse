# TGW full platform installation and recovery — v3 (2026-08-15)

This version supersedes
`full-platform-installation-v2-20260815.md`. The v2 file remains unchanged as
historical evidence of the briefly deployed, incorrect production Plan
topology.

## Canonical boundaries

- Application source: `tgw-lib:/opt/TGW/tgw-lib/src/trader-grims-warehouse`
- Standalone Plan: `tgw-lib:/opt/TGW/library/plans`
- Production flake: `tgw-prod:/home/db/tgw-flake`
- Immutable releases: `tgw-prod:/opt/TGW/releases/<generation>`
- Active application selector: `tgw-prod:/opt/TGW/current`

There is no application source checkout and no Plan checkout on tgw-prod.
`/opt/TGW/src` and `/opt/TGW/library` must remain absent. Do not create a
sentinel, compatibility symlink, detached Plan worktree, or coding worktree in
either namespace. Production application processes consume only the immutable
release selected by `/opt/TGW/current`.

## Application release

1. Resolve and verify the approved Plan on tgw-lib.
2. Work from the canonical tgw-lib application repository using an isolated
   task worktree. Reconcile and commit the candidate there.
3. Run focused, static, and full tests against the exact candidate.
4. Produce a deterministic archive and verify its commit, tree, size, hash,
   paths, and types before transfer.
5. Install the archive as a new immutable `/opt/TGW/releases/<generation>` on
   tgw-prod, using an exact expected-current generation and operation ID.
6. Verify the installed manifest, atomically update `/opt/TGW/current`, restart
   declared application services, and verify HTTP, MCP, workers, PostgreSQL,
   NATS, tokens, queues, catalog, and operator workflows.
7. Remove transfer files after verification. Retain the prior immutable release
   as the rollback target; never retain a production source checkout.

Production configuration must use `/opt/TGW/current` for release-owned scripts
and modules. Coding workers, repository mutation, Plan rendering, and Plan
approval execute on tgw-lib, not tgw-prod.

## Production flake

Build and switch only from `tgw-prod:/home/db/tgw-flake`. The flake is its own
Git repository and must never contain application or Plan history. Before a
switch, run `nix flake check --no-build` and an exact dry-build. After the
switch, verify the selected system generation, application services, backup
services, and both Syncthing configurations.

The production worker set must not include `plan_render`. Flake-owned
application script paths resolve through `/opt/TGW/current`; none may resolve
through `/opt/TGW/src`.

## Syncthing

tgw-prod retains two independent Syncthing instances for production data and
backup replication:

- `db`: `syncthing.service`, GUI `100.107.99.66:8384`, sync `22000/21027`
- `tgw`: `syncthing-tgw.service`, GUI `100.107.99.66:8385`, sync `22001/21028`

The standalone Plan folder `tgw-project-plan` is configured on tgw-lib as a
send-only folder rooted at `/opt/TGW/library/plans`. It is not configured on
either production Syncthing instance. Peer acceptance and peer completion are
checked from tgw-lib and the peer independently; local scan completion alone is
not proof of delivery.

Protect each affected Syncthing `config.xml` before and after a topology or
NixOS change. A NixOS switch must not overwrite GUI-managed devices or folders.

## Retired production material

The former production Plan repository and approved worktree were removed from
`/opt/TGW/library` on 2026-08-15. Root-only recovery tarballs and self-hashed
metadata are under `/opt/TGW/var/retired/plan-copies/`. These are recovery
artifacts, not Plan authority, search roots, Syncthing folders, or runtime
inputs. The canonical tgw-lib Plan must be used for every normal operation.

The former production application checkout is preserved only in the protected
tgw-lib recovery area. `/opt/TGW/src` on tgw-prod is absent.

## Backup verification

Verify snapshot, ItemData sync, cloud sync, database backup, secrets backup,
thermal watchdog, and USB stamp units against the selected immutable release.
The full cloud sync and continuous ItemData sync share
`/run/lock/tgw-rclone-gdrive.lock`; each rclone execution is bounded by the
release wrapper. A detached or orphaned process does not count as a healthy
backup job.

## Rollback

- Application: select the prior verified immutable generation and rerun the
  live service checks. Do not recreate `/opt/TGW/src`.
- NixOS: switch to the prior system generation and compare protected Syncthing
  configurations before accepting rollback.
- Plan: recover or reconcile on tgw-lib. Never restore a live Plan tree on
  tgw-prod; a production recovery tarball must first be reconciled into the
  canonical tgw-lib repository.
