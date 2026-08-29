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
The manifest binds refs, PostgreSQL/WAL positions, filesystem/media object manifests,
tool versions, start/completion times, failure state, retention class and every tier.
`tgw-lib-recovery verify RECEIPT OBJECT_ROOT` performs cold hash readback. Missing,
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
