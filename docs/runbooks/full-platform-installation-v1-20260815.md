# TGW full platform installation and recovery — v1 (2026-08-15)

This is a new operational runbook. It records the installation shape established
during the August 2026 reconciliation without replacing older incident records.

## Installed authority layout

- Application source: `tgw-lib:/opt/TGW/tgw-lib/src/trader-grims-warehouse`
- Plan: `tgw-lib:/opt/TGW/library/plans`
- Production flake: `tgw-prod:/home/db/tgw-flake`
- Immutable releases: `tgw-prod:/opt/TGW/releases/<generation>`
- Active application selector: `tgw-prod:/opt/TGW/current`
- Production configuration: `/opt/TGW/var/coding-worker/config.json` and the
  service-specific configuration selected by the flake/release

Never use `/tmp` for source or large build/test artifacts, and never restore the
retired production source checkout as an operational shortcut.

## Release procedure

1. Verify the standalone approved Plan commit with `verify_plan_root.py`.
2. Verify the candidate commit, tree, parents, clean status, and test/review receipts.
3. Create a deterministic Git archive and verify its SHA-256, size, PAX commit, paths,
   and member types before transfer.
4. Transfer to a bounded intake directory and invoke the release installer with the
   exact expected current generation. The installer must refuse a changed selector.
5. Verify the materialized content manifest before changing `/opt/TGW/current`.
6. Atomically select the new generation, restart only the declared application
   services, and verify their logged generation/commit/path.
7. Run application health, HTTP, MCP, worker, queue, catalog, PostgreSQL, NATS, token,
   and operator-interface checks. Keep the prior generation for rollback.
8. Remove transfer-only archives and staging directories after the immutable installed
   generation is verified.

## Flake maintenance and Syncthing preservation

The production flake is built and switched only from `/home/db/tgw-flake`. It must
retain both independent Syncthing services:

- `db`: `syncthing.service`, GUI `100.107.99.66:8384`, sync `22000/21027`
- `tgw`: `syncthing-tgw.service`, GUI `100.107.99.66:8385`, sync `22001/21028`

The flake owns service enablement, users, ports, and safe GUI bind addresses. It does
not own GUI-managed device/folder topology. Do not add declarative `devices` or
`folders` with `overrideDevices=false`/`overrideFolders=false`: NixOS initialization
can still PUT whole folder objects and erase peer membership. Before and after every
switch, save and compare each instance's config hash, device IDs, folder peers, GUI
address, and listen/discovery ports. A mismatch requires rollback or repair before
calling the switch complete.

## Rollback

- Application: atomically repoint `/opt/TGW/current` to the prior verified generation,
  restart declared services, and rerun health checks.
- NixOS: select the prior system generation, then verify both Syncthing instances and
  application dependencies.
- Source: use canonical Git refs and protected recovery bundles; do not reconstruct
  from a live release or production sentinel path.
