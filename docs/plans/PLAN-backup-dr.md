# PLAN: Backup, archiving, and disaster recovery (PP-BACKUP-001)

**Status:** DRAFT for Dave's review — designed 2026-06-11 (session 24), verified against
the live host (disks, services, rclone state, dump inventory checked — not assumed from
docs). Companion to `PLAN-nixos-migration.md`; **Phase C of this plan is a hard
requirement of that one** (the completed strategy must be declarative in the Nix flake).

Executor notes: house rules apply — no commits until Dave asks, `tgw health` after
config/worker changes, operator applies all systemd/root-level changes.

---

## 1. Current state (verified 2026-06-11)

| Layer | State | Verdict |
|---|---|---|
| **Local continuous snapshots** | `trader-grims-backup.service` (inotify + rsync hardlink), enabled+active, writes to a **dedicated 699 GB disk** (`/dev/sde1` → `/opt/TGW/var/local/backups/trader_grims_warehouse/`), 201 G used / 490 G free, ~31 timestamped snapshots per subtree, current to within minutes. Covers `bin config data docs src`. Resident cost: **6.2 G RAM** (peak 12.6 G) watching ~1 M+ files. | ✅ healthy (RAM cost noted for Phase B) |
| **Cloud copy** | rclone remote `dbukove:TGW/` (Google Drive) mirrors the same trees + logs. Last sync **2026-05-14/16 — 27 days stale**. No timer, no cron: manual-only. | 🔴 stale, unscheduled |
| **PostgreSQL ledger** | `state_machine` = 23 MB. **Zero dumps exist anywhere.** File snapshots cannot give a consistent copy (live WAL). `todo_items` (the canonical task queue) is not re-derivable. | 🔴 unprotected |
| **Secrets** | `/opt/TGW/secrets/` is in **no backup tier at all** — not snapshotted, not on Drive (correct that it's not there *unencrypted*, but there's no encrypted copy either). Disk loss ⇒ eBay OAuth re-consent + re-provision every API key. | 🔴 unprotected |
| **Cold archive** | MasterArchive (`/dev/sdc5`, ext4, repaired 2026-06-11): ItemData history 584 G, ItemArchive 163 G (54 K zips, 40 % indexed), job_archive 371 G, etc. (GEMINI-007 inventory). GDrive holds old `ItemArchive` (2024) and `Photo Archive` (2020) copies. | 🟡 exists, partially indexed |
| **System image** | PP-DEPLOY-001 MX restore ISO: runbook written, **not yet baked** (NixOS plan Phase 1). | 🟡 pending |
| **Plan vault** | Syncthing-replicated to other devices + git-tracked. | ✅ multi-copy |
| **Source repo** | git + GitHub private remote. | ✅ off-machine |
| **Tools available** | rsync, rclone, gpg, systemd timers. restic/borg **not installed** (apt-available). | — |

**Live data sizes:** ItemData 180 G, ItemCatalog 1.8 G (derived — regenerable),
ledger 23 MB, `/opt/TGW` partition 187 G used / 295 G.

### What this means in RPO terms today

| Failure | Recovery point today |
|---|---|
| Fat-fingered file/folder | minutes (snapshot disk) |
| `/opt/TGW` nvme dies | minutes for files — **but ledger: total loss; secrets: total loss** |
| Host/site loss (both disks) | **27 days and growing** (stale Drive copy); ledger+secrets: total loss |
| Ledger corruption | total loss (no dump to restore) |

## 2. Protection targets

| Store | Canonical? | Tiers required | RPO target (Phase A) |
|---|---|---|---|
| ItemData (180 G incl. media) | YES | snapshot + cloud | minutes local / ≤24 h cloud |
| PostgreSQL `state_machine` | YES (work state, todos, sku_history) | daily dump → snapshot disk + cloud | ≤24 h |
| Secrets | YES (operator-provisioned) | **encrypted** copy → snapshot disk + cloud | ≤ next change |
| Config (`tgw-api-config.json`, category-groups) | YES | snapshot + cloud + site-config repo (NixOS Phase 0.5) | minutes |
| Repo / plan vault | YES | git+GitHub / Syncthing+git | already met |
| Derived (catalogs, thumbnails, velocity) | no | none — regenerable by invariant | n/a |
| Cold history (MasterArchive) | archive | index + selective cloud | Phase B |

## 3. Phase A — MX now (tools at hand; no new software)

Ordered by risk closed per effort. A1–A3 are systemd template work (repo files, operator
installs); A4 is a normal code task; A5–A6 are operator drills/policy.

**A1 — Daily ledger dump (closes the worst gap; handoff §6 step 3).**
*Do:* `tgw-db-backup.service` (oneshot, `User=tgw`) + `.timer` (daily 03:30):
```
pg_dump -U tgw --format=custom state_machine \
  -f /opt/TGW/var/backups/trader_grims_warehouse/db/state_machine-$(date +%%Y%%m%%d).dump
rsync -a /opt/TGW/var/backups/trader_grims_warehouse/db/ \
  /opt/TGW/var/local/backups/trader_grims_warehouse/db/
find .../db/ -name '*.dump' -mtime +35 -delete   # both locations
```
~5 MB/day compressed — retention is a non-issue. Dump lands on the nvme tree **and** the
snapshot disk; A2 carries it to Drive.
*Done when:* two consecutive timer runs logged; `pg_restore --list` reads the newest dump.

**A2 — Scheduled cloud sync (un-stales the off-machine tier).**
*Do:* `tgw-cloud-sync.service` + `.timer` (daily 02:30, before A1 — or after; order is
cosmetic): `rclone sync` of the existing scope (`bin config data docs logs src`) plus the
new `var/backups/.../db/`, with
`--backup-dir dbukove:TGW-trash/$(date +%%Y%%m%%d)` so deletions/overwrites land in a
dated trash rather than propagating destructively (cheap ransomware/oops insurance), and
`--log-file /opt/TGW/var/log/rclone-sync.log`. First run after 27 days will be large;
subsequent nightly deltas small.
*Done when:* `rclone lsd dbukove:TGW/data` shows a fresh date two mornings running.

**A3 — Encrypted secrets backup (closes the silent total-loss).**
*Do:* small script `tgw-secrets-backup`:
`tar -C /opt/TGW -cz secrets | gpg --symmetric --cipher-algo AES256 -o
secrets-$(date +%%Y%%m%%d).tar.gz.gpg`, written to the snapshot disk + rclone'd to
`dbukove:TGW-secrets/`. Timer monthly **+ run manually after any credential change**.
**Operator decision:** passphrase custody — written down off-machine (safe/wallet);
without it the backup is useless, with it on this disk the encryption is theater.
*Done when:* a test round-trip (`gpg -d | tar -tz`) lists the expected files, and the
passphrase exists somewhere that survives the house burning down with the server.

**A4 — Backup-freshness health check (the watcher for the watchers).**
*Do:* `check_backups(cfg)` in `health.py` (pattern: `check_ownership`): age of newest
`db/*.dump` (>26 h ⇒ red), age of newest rclone log success line (>26 h ⇒ red), newest
file under the snapshot tree (>1 h ⇒ yellow), newest secrets bundle (>40 days ⇒ yellow).
Unit-tested with mocked filesystem. This is the same check already specified for the Nix
module in `PLAN-nixos-migration.md` §9.1 — build it once here, it migrates with the tree.
*Done when:* `tgw health` shows the four ages; goes red when a timer is deliberately
stopped for a day (test by date-faking files, not by actually skipping a night).

**A5 — Restore drills (a backup is a rumor until restored).**
- Ledger: `createdb scratch && pg_restore -d scratch <newest.dump>` → row counts match.
- ItemData: pick 3 random SKUs; diff snapshot copy and Drive copy against live.
- Secrets: the A3 round-trip.
- Record wall-clock times in the vault — these are the RTO numbers the NixOS cutover
  window estimate needs (Phase 4 rehearsal reuses them).

**A6 — Archive policy (the "archiving" leg).**
- MasterArchive is the **cold tier**; GEMINI-007's cleanup order stands: consolidate
  zips → index loose CSVs → complete ItemArchive index to 100 % → offload cold data.
  `tgw history-index` (unblocked since the drive repair) is the tool to build.
- Policy line to adopt: *nothing is deleted from a hot tier until it is indexed in a
  cold tier* — the index is what makes an archive an archive instead of a junk drawer.
- GDrive `ItemArchive`/`Photo Archive` copies (2024/2020) are cold-tier replicas; leave.

### Phase A result

| Failure | RPO after Phase A |
|---|---|
| File oops | minutes |
| nvme loss | minutes (files) / ≤24 h (ledger) / ≤last-change (secrets) |
| Host/site loss | ≤24 h everything |
| Ledger corruption | ≤24 h |

## 4. Phase B — upgrade path (PP-BACKUP-001 proper)

Adopt when Phase A has run clean for a few weeks and the suite design session happens.

1. **Repo split first** (already decided, session 16): `trader-grims-backup` to its own
   repository; the watcher stays frozen and running.
2. **Engine: restic** (recommendation). One tool replaces the rsync+rclone+gpg layering:
   encrypted at rest, content-deduplicated, snapshot-versioned, **native rclone backend**
   (`restic -r rclone:dbukove:TGW-restic`) so Google Drive becomes a versioned encrypted
   repository, `forget --prune` retention policies, `check --read-data` integrity scrubs.
   Borg was considered and rejected: no native cloud backend (needs ssh target). restic
   is in Debian/nixpkgs — installs cleanly on both sides of the migration.
3. **`tgw backup` CLI** wrapping it: `status` (the A4 ages + restic snapshots),
   `run [tier]`, `verify` (scrub + restore-sample), `restore` (guided). Health stays wired.
4. **Watcher decision** (design question for the Phase B session): keep inotify watcher
   for the instant-local tier alongside restic timers, or retire it — 6.2 G resident RAM
   for 1 M-file watches is real money on a 32 G box that also runs Ollama. A restic run
   every 30–60 min from the snapshot disk may be good enough; the snapshot disk itself
   provides the instant tier.
5. **Cold-tier integration:** `tgw history-index` output (A6) becomes the manifest that
   lets MasterArchive contents be selectively pushed to the restic cloud repo.

## 5. Phase C — Nix flake integration (required end state)

The completed strategy must be **declarative in the flake** — this also resolves the
NixOS plan's **R9** (the module's backup unit references a binary the package doesn't
build) properly instead of by deletion:

1. `services.tgw.backup` module options: `pgDump.{enable,schedule,retentionDays}`,
   `cloudSync.{enable,schedule,remote,backupDir}`, `secretsBackup.{enable,schedule}`,
   and (Phase B) `restic.{enable,repos,keep*}` — each emitting the systemd
   service+timer pairs from Phase A, byte-equivalent behavior.
2. Secrets rule unchanged (NixOS plan R11): nothing secret in the Nix store or git;
   the gpg passphrase and restic repo password arrive only via out-of-band restore.
3. `check_backups` ships with the package — `tgw health` is identical on both OSes.
4. **Recovery equation extended and drilled** (NixOS plan Phase 4 rehearsal adds one
   line): `NixOS flake + site-config repo + secrets restore + ItemData restore +
   newest ledger dump = running system, tgw health green including backup freshness`.
5. **Google Drive rebuild kit** (one folder): pointer to the ISO, flake/site-config repo
   URLs, the secrets bundle, this plan, and the restore runbook. The kit is what a
   bare-metal stranger needs to rebuild the warehouse.

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| B1 | First A2 run uploads ~weeks of churn over residential bandwidth | run manually once in an off-hours window before enabling the timer |
| B2 | gpg passphrase lost ⇒ secrets bundle worthless | A3 custody decision is a gate, not a footnote; verify annually |
| B3 | Drive quota (180 G ItemData + history already there) | `rclone about dbukove:` before first sync; prune `TGW-trash/` dated dirs >90 d |
| B4 | Snapshot disk (`sde1`) dies silently | A4 yellow on snapshot-age catches write-stops; disk SMART is operator routine; cloud tier is the redundancy |
| B5 | Dump runs while a migration batch writes sku_history | `--format=custom` is a consistent snapshot by construction (single transaction) — no action needed, noted for confidence |
| B6 | rclone sync propagates a local catastrophe upward | `--backup-dir` dated trash (A2) is the rewind; restic versioning (Phase B) is the real fix |

## 7. Verification (standing, after Phase A)

Daily (automatic): `tgw health` includes the four backup ages.
Weekly (operator, 2 min): newest dump readable (`pg_restore --list`), rclone log tail clean.
Monthly: one random-SKU restore diff; quarterly: full A5 drill + record times.

---

No production code or system units are changed under this plan until Dave approves it.
On approval: A1–A3 unit/script files land in `etc/systemd/` + `bin/` as a normal
reviewed change (todo seeded), A4 is a normal code task (todo seeded), A5/A6 are
operator items, Phase C rides PLAN-nixos-migration Phase 0.2/0.7.
