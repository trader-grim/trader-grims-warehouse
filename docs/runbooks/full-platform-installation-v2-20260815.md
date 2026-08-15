# TGW full platform installation and recovery — v2 (2026-08-15)

This version succeeds
`full-platform-installation-v1-20260815.md`; the v1 file remains unchanged as
installation evidence.

## Repository and runtime boundaries

- Application source: `tgw-lib:/opt/TGW/tgw-lib/src/trader-grims-warehouse`
- Standalone Plan: `tgw-lib:/opt/TGW/library/plans`
- Production flake: `tgw-prod:/home/db/tgw-flake`
- Immutable releases: `tgw-prod:/opt/TGW/releases/<generation>`
- Active application selector: `tgw-prod:/opt/TGW/current`

Application code is not operated from `/tmp` or from a production checkout.
Flake-owned operational services execute scripts under `/opt/TGW/current/bin`
or `/opt/TGW/current/scripts`; `/opt/TGW/src/trader-grims-warehouse` is a
retirement sentinel, not a source or service path.

## Application release

1. Verify the standalone approved Plan root and exact application commit/tree.
2. Run focused, static, and full tests from the canonical tgw-lib source.
3. Create and verify a deterministic Git archive before transfer.
4. Install it as a new immutable generation with the exact expected current
   generation and operation ID.
5. Verify the materialized manifest, atomically select the generation, and
   restart the declared application services.
6. Verify logged generation/commit paths, HTTP, MCP, workers, PostgreSQL, NATS,
   tokens, queues, catalog, and operator workflows.
7. Retain the prior generation for rollback and remove only transfer archives
   after the installed generation verifies.

## Flake maintenance

Build and switch only from `tgw-prod:/home/db/tgw-flake`. Before switching,
verify that every flake-owned application script path resolves through
`/opt/TGW/current`; never reintroduce the retired production source checkout.
Run `nix flake check --no-build`, then `nixos-rebuild dry-build`, before
`nixos-rebuild switch`.

The flake owns Syncthing service enablement, users, GUI/listen/discovery ports,
and Tailnet GUI binding. It does not own GUI-managed devices or folders.
Retain both independent production instances:

- `db`: `syncthing.service`, GUI `100.107.99.66:8384`, sync `22000/21027`
- `tgw`: `syncthing-tgw.service`, GUI `100.107.99.66:8385`, sync `22001/21028`

Save both `config.xml` files before and after every switch and require exact
byte equality unless the switch explicitly changes Syncthing topology.

## Standalone Plan Syncthing share

The production `tgw` instance owns folder ID `tgw-project-plan`:

- path: `/opt/TGW/library/plans`
- type: `sendonly`
- peer: `a9`

`/opt/TGW/var/backups/plan-vault` was the obsolete pre-consolidation source and
must not be recreated as the live folder. Its 2026-08-15 contents were retained
under a root-protected `retired-plan-vault-*` backup for recovery.

After a path or topology change:

1. protect the before/after Syncthing configuration;
2. require the standalone Plan Git commit/tree and clean status to remain exact;
3. rescan `tgw-project-plan` and require zero folder errors and
   `needTotalItems=0` locally;
4. inspect peer completion separately—local scan completion is not proof that
   `a9` has pulled the data;
5. preserve remote-only files before applying a send-only override.

## Backup verification

Verify the snapshot, ItemData sync, cloud sync, database backup, secrets backup,
thermal watchdog, and USB stamp units all reference `/opt/TGW/current`. A
long-lived process whose parent unit no longer exists is stale and must not be
counted as a healthy backup job. The full cloud sync and continuous ItemData sync
share `/run/lock/tgw-rclone-gdrive.lock`; each rclone execution must be under the
release's bounded `timeout` wrapper.

## Rollback

- Application: repoint `/opt/TGW/current` to the prior verified generation,
  restart declared services, and repeat live checks.
- NixOS: select the prior system generation, then compare both Syncthing configs
  to the protected pre-switch copies before accepting rollback.
- Plan share: restore the protected Syncthing config and retained retired tree;
  never guess at peer content or silently discard remote-only files.
- Source: use canonical refs and protected recovery bundles, not a live release.
