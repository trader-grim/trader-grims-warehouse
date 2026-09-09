# tgw-lib independent backup and restore (Todo 1918)

Status: implementation candidate only. Review, admission, tgw-lib installation,
restore-drill evidence, deployment, live verification, and operator acceptance are
separate states. This runbook authorizes no provider write or destructive action.

## Recovery contract and threat model

PP-BACKUP-001's production jobs are useful inputs, but are not evidence that the
tgw-lib authority domain is protected. GitHub, GDrive, tgw-prod, a cache, and any one
disk are neither sole recovery copies nor runtime authorities. tgw-prod backup and
tgw-lib backup must use distinct schedules, credentials, receipts, and failure paths.

Threats include host/disk loss, silent corruption, ransomware or credential loss,
operator error, partial generations, provider/network outage, PostgreSQL/Git/filesystem
skew, and loss of Unix ownership. A generation is successful only when the single
canonical manifest is atomically sealed after every required tier verifies.

## Surface classification

| Surface | Classification | Required evidence/mechanism |
|---|---|---|
| Standalone Plan Git, approved/evidence refs | authoritative | `git bundle --all`, refs, bundle verify/fsck, independent encrypted off-host readback |
| Canonical source Git, Todo branches/worktrees | authoritative/durable | all-ref bundle plus worktree/dirty-patch preservation manifest; remote ref readback is additional evidence |
| Todo/queue/item/history PostgreSQL and migrations | authoritative | consistent custom dumps plus globals/schema/migration identity and WAL/LSN barrier; physical base+WAL is the fast tier |
| `/opt/TGW/library` Plan/materializations/runbooks/archive | authoritative/durable | filesystem snapshot when supported, otherwise content-addressed file manifest and copy |
| `/opt/TGW/tgw-lib` config/context inputs/Doctor/coding receipts/queue evidence | durable | content-addressed copy preserving mode/uid/gid/xattrs; exclude secrets into their tier |
| master ItemData/media/history, annex/GDrive/archive manifests | authoritative originals/durable | object hashes, annex fsck/whereis, hydration sampling; provider is a replica only |
| Unix users/groups/ownership/ACL/xattrs | durable recovery metadata | numeric identity/group export and filesystem metadata manifest, restored before data |
| secrets/credentials | authoritative protected | separately age-encrypted bundle; operator-held offline keys; never put plaintext or private keys in a generation |
| build/hydrated caches, thumbnails, catalogs, `/tmp` | regenerable only | omit only with named source, command, version, expected output/hash, and degraded behavior contract |

## Inventory and immediate protection proposal

Run `tgw-lib-recovery inventory` as the non-destructive first leaf. Record mount/device,
filesystem type (from `findmnt`), capacity/free space, database size, Git object sizes,
annex availability, backup tool versions, existing replica age, and whether snapshot
support actually exists. Do not assume Btrfs, ZFS, Nix, containers, or a cloud vendor.

Immediate protection is additive: stage all-ref Plan/source bundles; consistent
PostgreSQL dumps with `pg_backup_start`/`pg_backup_stop` or `pg_dump` snapshot and LSN;
filesystem/media manifests; identity metadata; and a separately encrypted secrets
bundle. Verify locally, seal one generation, copy it to a local fast tier and an
encrypted off-host repository in an independent failure domain, then perform clean
readback. Network or credentials failing leaves the local recovery copy usable but
marks off-host protection degraded; it never reports full success.

## Generation protocol

Collectors use bounded per-store barriers, not global quiescence. Capture Git refs;
start the PostgreSQL snapshot/base backup and record start/stop LSN and timeline;
snapshot or walk each filesystem at a recorded barrier; hash every staged object.
The v2 manifest binds exact Git commits/trees/ref maps and capture times; PostgreSQL
start/stop LSN, timeline, WAL continuity, schema hash and migration identity;
filesystem barrier IDs/times/methods; filesystem/media object manifests including
uid/gid/mode, ACLs, xattrs and hard-link groups; tool versions, start/completion
times, failure state, retention class and every tier. Success also requires verified
readback evidence for a local-fast replica and an encrypted off-host replica in a
different named failure domain. Secrets must name the encryption mechanism, assert
plaintext exclusion, and bind operator-held offline key custody.

Receipts are Ed25519-signed with an operator-controlled key and full success requires
verified WORM/object-lock/append-only receipt storage; local chmod is not treated as
immutability. `tgw-lib-recovery verify RECEIPT OBJECT_ROOT TRUSTED_PUBLIC_KEY KEY_ID`
performs trusted-signature and cold metadata/hash readback. Missing,
failed, empty, duplicated, escaping, or mutated objects make the generation incomplete.

Baseline objectives: local snapshots RPO 1 hour/RTO 2 hours; off-host RPO 24 hours/RTO
24 hours. Retain 48 hourly, 35 daily, 12 monthly and 7 yearly generations; prune only
after a separately reviewed policy, capacity headroom check, verified successor and
operator authorization. Alert on failed/incomplete generation, local age >2 hours,
off-host age >26 hours, receipt/hash failure, WAL gap, capacity <20% or less than two
projected generations, and monthly restore drill age >35 days. Receipts are append-only
and copied to both tiers.

## Clean isolated restore drill order

On a simple replacement host with tgw-prod routes blocked: (1) recreate numeric
users/groups and ownership, then provide protected secrets through the operator-held
key; (2) restore and fsck Plan/source bundles and exact refs; (3) initialize the
recorded PostgreSQL major version, restore globals/schema/data/WAL, validate constraints,
history counts and migration identity; (4) restore library/master data/media; (5)
restore context inputs and Doctor/coding/queue evidence; (6) cold-read CLI and MCP;
(7) run a fixture-only coding probe whose provider-writing transports are disabled.

The drill passes only with receipt/object hashes, `git fsck`, exact Plan/source ref and
tree identity, database constraint/history checks, sampled annex/media hydration, no
tgw-prod reachability, and truthful degraded results when local, off-host, secrets,
media, or WAL tiers are individually hidden. Record each drill as separate immutable
evidence; do not label this implementation candidate as an executed restore drill.

Luet may package the CLI, units, and configuration after review. It is not the backup,
Plan, operator, effect authority, generation receipt, or recovery proof.

## Operator directive — the config bundle (2026-09-08)

The DR config bundle previously carried by `trader-grim/tgw-site-config` (private,
2026-06-19; `config/` + `systemd/` on the TGW-SECRETS USB kit) **stays part of
this backup**, as-is or refreshed — it is not folded away. It is the config half
of the "restore from bare metal" contract, alongside the source/plan bundles,
the PostgreSQL dump, and the operator-held secrets tier.

`tgw-site-config` itself lapsed (no push since the Nix-migration work wound down;
its content partly migrated into the source repo's `config/environment/` +
`config/tgw-coding-*.json` + `systemd/`). Retire the standalone repo or refresh
it — operator's call — but the *captured surface* must include the deployed live
copies that are not git-tracked: `/opt/TGW/config/`, `/opt/TGW/tgw-lib/config/`,
`/etc/sudoers.d/tgw-*`, `/etc/systemd/system/tgw-*`, `pg_ident.conf`/`pg_hba.conf`
as installed, and the numeric identity/ownership export.

**Increment it in parallel with development — do not defer to a catch-up push.**
A config change (a sudoers edit, a new unit, a role SQL change, a pg_ident map
entry) must feed the bundle on the same cadence it lands, the way a `main` commit
should imply a GitHub publish (see `plan-vault-shared-access-v1-20260908.md` and
the manual-push gap). Concretely: a small recurring capture of the surface above
→ content-addressed manifest → into the backup generation (local fast tier +
encrypted off-host) → optionally a git-committed projection (the `tgw-site-config`
successor). This is the concept registry's library-substrate pattern
(`concepts/CATIO-LIBRARY-SUBSTRATE.md`): deployed config is the authority, the
bundle is its versioned projection, the backup tiers are its replicas.

Placement in the concept layering: the platform's own config + DR is `catio`; the
business config (api-config, category-groups, item-data adjacency) is a `tgw`
specialisation. Bind as an incrementable sub-thread of Todo 1918 rather than
gating on the full generation protocol.

**The surface is moving — design for it.** The current shape (`systemd/` units,
`sudoers.d`, `pg_ident`/`pg_hba`, JSON config) is being replaced as TGW
containerises: `PLAN-nixos-migration.md` turned toward a compose runtime, and
LEAF-11's direction is workers and agents in containers, with `keeper`
(LEAF-11.4) materialising containers / stacks / CatioOS from declared specs. The
config-bundle sub-thread must be structured to absorb Containerfiles / compose
definitions / image digests / Luet materialisation specs / CatioOS image config
as they land — not hardcode `/etc/systemd/system/tgw-*`. Those specs are `keeper`
*input* (the recipe, not the built image): they belong in the versioned library
projection. The built images are bound by `generation_microchip` (LEAF-11.5) and
are regenerable-from-recipe, so the backup captures recipe + digest, and an image
on the fast tier only as an optimisation. LEAF-11.4 / 11.5 intersection.
