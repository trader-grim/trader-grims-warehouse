# PLAN: NixOS production migration (PP-NIXOS-001)

**Status:** DRAFT for Dave's review — no code edited yet, no step executed.
**Decision record:** `ADR/ADR-nixos-migration.md` (NixOS is committed; this plan is *how*).
**Written:** 2026-06-10, verified against the live system (uid, PostgreSQL version, unit
layout checked on the host, not assumed from docs).

Executor notes: every step lists **Do / Test coverage / Done when**. Steps inside a phase
are ordered; phases must complete in order except where marked parallel-safe. All repo work
follows house rules: no commits until Dave asks, `tgw health` after config/worker changes,
restart affected `tgw-worker@` units after source changes.

---

## 1. Current state (verified 2026-06-10)

- **Host:** single MX Linux (Debian 13) machine; everything runs as user `tgw`
  (**uid 1001**, also in `sudo`, `keyd`, `tgwadmin` groups).
- **PostgreSQL 17.10** (Debian package), db `state_machine`, peer auth. Schema applied
  historically by hand from `src/tgw/queue/schema.sql` (+ `sku_history.sql`, todo table) —
  **no automated schema bootstrap exists anywhere**.
- **Services:** 18 worker queues as systemd *template* instances `tgw-worker@<queue>.service`
  (template only in `/etc/systemd/system/`, not in repo), `tgw-http.service`
  (`etc/systemd/tgw-http.service`, ExecStart from venv `/opt/TGW/.venvironments/tgw/bin/tgw
  serve`), `queue-workers.target` + boot timer, `trader-grims-backup.service` (ExecStart from
  the same venv — **the backup binary comes from a separate package, not this repo**),
  `ollama.service`.
- **App install:** editable pip install into venv `/opt/TGW/.venvironments/tgw/`.
- **Data:** `/opt/TGW/data/ItemData` (~55k SKU dirs, large), derived catalogs, secrets
  (0700/0600), rclone remote `dbukove:/TGW/` carries data backups.
- **Nix artifacts:** `flake.nix` + `nix/tgw.nix` + `nix/README.md` authored (session 17),
  pinned to `nixos-24.11`, **never built or VM-validated**. Known divergences from the live
  system found while drafting this plan (each becomes a Phase-0 fix):
  - module intends a **system uid (<1000)** but the live user is **uid 1001**; uid 999 is
    already taken on the MX host (dnsmasq/systemd-journal) — **900 verified free** (both
    uid and gid) on 2026-06-10. Decision: migrate the live user below 1000 (step 0.6),
    do not pin the module to 1001;
  - NixOS 24.11 `services.postgresql` defaults to **PostgreSQL 16**, live cluster is **17.10**
    (a v17 `pg_dump --format=custom` will not restore into a v16 server);
  - workers renamed `tgw-worker-<queue>` (module) vs `tgw-worker@<queue>` (live + all
    tooling: `tgw restart-workers`, `tgwlogs` MC extfs, CLAUDE.md, runbooks).
    **DECIDED (Dave, 2026-06-10): keep the template form** — the module is reworked, not
    the tooling (step 0.3);
  - backup unit ExecStart references `${cfg.package}/bin/trader-grims-backup`, which this
    package does not build (off by default, so VM testing won't catch it);
  - `ensureDatabases` creates an **empty** `state_machine` — ledger schema never applied;
  - `python3Packages.mcp` availability on nixos-24.11 unconfirmed (flake watch-item);
  - `pyproject.toml` carries Pillow only in extras while the flake treats it as a base dep.
- **In-flight constraint:** `ebay_sku_migrate` is mid-migration (~8,350 listings, ~10/h,
  months remaining). It pauses cleanly via config `ebay_sku_migrate.enabled: false`.
- **Test suite:** 475 passing as of 2026-06-11 (346 at the original 2026-06-10 verification),
  ruff clean. eBay token live (scopes locked — never change).
- **Spare hardware:** one former intake-support machine, already in use as the NixOS
  learning/testing target. **Severe hardware limitation: it cannot run most Ollama
  models** — it validates NixOS structure, configuration, services, and restore mechanics,
  but NOT AI inference. Inference validation waits for production hardware (cutover step
  5.6) or a hardware path Dave resolves separately. Early read from this machine: NixOS is
  significantly different but appears to be a good fit for the platform.

## 2. Desired state

- Production host runs NixOS, fully described by the repo flake: `services.tgw.enable = true`
  brings up the tgw user (system uid <1000), PostgreSQL 17 with the ledger schema, the
  18-worker fleet (template-unit form), tgw-http, and (eventually) Syncthing headless
  (GUI/REST :8385, sync :22001).
- **Two host tiers, not one** (from Dave's 2026-06-09 suggestion + 2026-06-10 direction):
  - **Master** (`bases/master.nix`) — the complete architecture above. Only the master and
    possible **future online failover servers** carry it.
  - **Clients** (`bases/portable.nix`) — portable catalog only: satellite SQLite
    `tgwcatalog.db` subset + thumbnail cache (same path layout as master, per the settled
    satellite-catalog design), Syncthing for catalog delivery, no PostgreSQL, no worker
    fleet, no eBay secrets. NixOS is **certain for the server, probable for the clients**;
    the client profile is a follow-on track that must never block the server migration.
- `/opt/TGW` is a self-contained imageable entity (venv/nvm/npm/HOME under the tree, no
  `~tgw` state).
- Recovery equation holds and is **drilled, not assumed**:
  `NixOS flake + site-config GitHub repo + secrets restore + ItemData restore = running system, tgw health green`.
- Non-secret site config (`tgw-api-config.json`, category-groups, policy IDs) in a private
  GitHub repo; Google Drive holds the DR kit (ISO pointer, repo URL, rclone restore script).
- Operator tooling (`tgw restart-workers`, `tgwlogs`, docs) works identically on the new
  host.
- MX restore ISO retained until ≥2 weeks clean shakedown, then retired by recorded decision.

## 3. Alternatives considered

Recorded in full in the ADR. Summary: stay-on-MX (rejected: DR ceiling, drift), MX+Nix
overlay (rejected: solves devShells, not OS state), Guix (rejected: ecosystem), Silverblue
(rejected: containers ≠ declarative services), poetry2nix packaging (deferred: no lockfile,
six deps). One *plan-level* alternative was considered: **big-bang cutover without the
spare-machine phase** — rejected because the spare machine gives a free full-restore drill
and NixOS familiarity with zero production risk, at the cost of only calendar time.

## 4. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | PG 17 dump won't restore into NixOS default PG 16 | Certain if unpinned | Cutover blocked mid-window | Phase 0.2 pins `postgresql_17`; Phase 2 includes a real `pg_restore` drill |
| R2 | uid mismatch (module default vs live 1001) breaks every file permission after restore | Certain if unfixed | Whole platform red | Phase 0.6 migrates the live user to a system uid (<1000, candidate **900**, verified free) *before* the ISO bake; module default set to the same value; permissions audit in every gate |
| R2b | uid-migration chown pass misses files (other mounts, paths outside scanned trees) | Possible | Scattered permission failures | Pipeline stopped during the change; full-disk `find` audit must return empty before restart; `tgw health` ownership check |
| R3 | Unit rename breaks `tgw restart-workers`, `tgwlogs`, muscle memory, docs | ~~Certain if unhandled~~ CLOSED | — | **Decided: keep `tgw-worker@<queue>` template form** — module reworked to declare a template unit + per-queue instances (step 0.3); tooling unchanged |
| R4 | Ledger schema never applied on fresh NixOS | Certain | Workers crash-loop | Phase 0.2 adds declarative schema bootstrap; VM test asserts tables |
| R5 | `python3Packages.mcp` absent/old on 24.11 | Possible | Package build fails | Checked first thing in Phase 2; fallback: nixos-unstable input or overlay (flake already documents both) |
| R6 | WAL-recovery `ExecStartPost` gotcha kills postgres during restore-mode start | Possible | Restore drill fails confusingly | Phase 0.2 adds recovery-mode guard per Perplexity finding; drill in Phase 2.4 |
| R7 | Two hosts both running eBay-writing workers (shadow + prod) | Possible during Phase 4 | Double reprice/migrate/stage against live marketplace; token churn | Hard rule: eBay workers **masked** on any non-production host; checklist + health check |
| R8 | Sold events missed during cutover downtime | Certain (window-sized) | Items sell while pipeline down | Acceptable: webhook isn't deployed anyway; `ebay_legacy_sync` GetOrders polling catches up after start (365-day lookback ceiling, window is hours) |
| R9 | Backup watcher ExecStart broken on NixOS (binary not in package) | Certain if enabled | No continuous backup post-cutover | Phase 0.2 fixes unit definition; PP-BACKUP-001 separation noted |
| R10 | ISO/backup unverified = no real rollback | — | Catastrophic if cutover fails | Phase 1 *is* the PP-DEPLOY-001 runbook including boot-verify; cutover gate requires it |
| R11 | Secrets leak via Nix store or git | Low | Credential compromise | Rule: secrets only via out-of-band restore into `/opt/TGW/secrets`; reviews check no `builtins.readFile` of secrets, site-config repo is non-secret only |
| R12 | Ollama models/perf differ on NixOS | Possible | ai_identify/pm_intake stall | **Cannot be rehearsed on the spare machine (hardware can't run most models).** Validated only at cutover step 5.6 on production hardware, *before* intake reopens: pull/carry models, run one real identification, then unmask the AI-dependent queues. If a GPU/hardware upgrade lands first, rehearse there instead |

## 5. Dependencies

- **Operator (Dave):** runs everything Nix-related (this dev box has no Nix toolchain);
  bakes/verifies the MX ISO; owns the cutover window; provides spare machine + USB/VM.
- **Repo state:** suite fully green before Phase 0 starts (record the count); uncommitted
  session work committed or stashed at Dave's direction first.
- **External:** GitHub private repo (site config), Google Drive/rclone remote current,
  eBay token alive (verify before cutover; do **not** request new scopes).
- **Sequencing:** Phase 1 (MX ISO) before any production change; Phase 5 cutover requires
  every prior gate green. Phases 2 and 3 are parallel-safe with each other.

---

## 6. Step-by-step implementation plan

### Python deployment decision (session 37)

**Option B — current (server migration):** NixOS module manages OS/systemd/PostgreSQL/tree.
Python app installed out-of-band into `/opt/TGW/.venvironments/tgw` via pip, same as MX.
`services.tgw.venvPath` (default: `${dataDir}/.venvironments/tgw`) drives all ExecStart.
No `src = ./.` Nix build at install time — eliminates the "codebase bundled in install"
pain from the A1131 session. Post-install step: `pip install -e /opt/TGW/src/trader-grims-warehouse`.

**Option A — future (tgw-test hardening after production cutover):** Replace `venvPath`
with `services.tgw.package` pointing at a Nix-built package fetched from GitHub.
`nixos-rebuild switch` then updates OS + Python app atomically. `flake.nix` retains the
`tgwPackage` / `packages.tgw` output as the skeleton for this path.

### Distribution infrastructure (session 37 → revised session 38)

**Flake distribution: `nixos-rebuild --target-host` (not Syncthing)**

Configs are pushed FROM MX — the flake is evaluated locally and the Nix store closure
is transferred to the remote host. No copy of the flake source on remote hosts. No
`tgw-flake` Syncthing folder.

```bash
# Push a config update to any NixOS host (run on MX, from the git repo):
bash scripts/tgw-push-config.sh tgw-test 100.x.y.z    # Tailscale IP
bash scripts/tgw-push-config.sh tgw-prod 100.x.y.z
```

**Initial provisioning: `nixos-anywhere`**

For machines with SSH access (including existing Linux installs), `nixos-anywhere`
provisions NixOS remotely without physical access after the first USB boot:

```bash
nix run github:nix-community/nixos-anywhere -- \
  --flake path:.#tgw-prod \
  --extra-files /tmp/secrets \   # injects Tailscale auth key etc.
  root@<IP>
```

Requires Disko partition config in the host's flake entry (planned — see Phase 3.3).
The A1131 was installed manually; nixos-anywhere will be used for production cutover.

**Syncthing folders (Syncthing is NOT used for the flake):**

| Folder | Syncthing folder | Notes |
|--------|-----------------|-------|
| NixOS ISO | `tgw-install-bundle` | still distributed for USB boot-stick creation |
| Site config | via USB or git clone | not Syncthing |
| plan-vault, ItemData, ItemCatalog | their own folders | unchanged |

Operator username never hardcoded — all Syncthing paths derived from
`config.services.syncthing.user` in the Nix module.

**Emergency offline:** `scripts/tgw-nix-sync.sh` copies flake source to a local dir
(`$HOME/tgw-flake` by default, or any path via `TGW_NIX_FLAKE_DIR`) for USB kits.

Reference: `nix/CLAUDE-NIX.md` (session guide), `reference/TGW-NixOS-Reference.md` (bootstrap + topology).

### Phase 0 — Pre-flight repo work (on MX, normal dev flow; no infra change)

**0.1 Unify Python dependency source of truth.** ✅ **DONE (session 38, 2026-06-22)**
Pillow>=10.0 promoted to `[project.dependencies]` in pyproject.toml. `thumbnails` extra
retained as alias. Flake `dependencies` list already had Pillow — now consistent.

**0.2 Fix the Nix module against verified reality** — partial ✅, remainder open:

✅ Done (session 37):
- `services.tgw.uid` default → **900** (set in `nix/tgw/users.nix`; assertion guard in `bases/master.nix`)
- `services.postgresql.package = pkgs.postgresql_17` pinned in `nix/tgw.nix`
- Backup unit decoupled: `enableBackup` uses `cfg.venvPath` (not a package reference); PP-BACKUP-001 owns the binary
- Python deployment decoupled: **Option B** (session 37) — `cfg.package` replaced by `cfg.venvPath`; ExecStart points at `/opt/TGW/.venvironments/tgw`; Nix-built package not required at install time (see §Python deployment below)

Still open:
- ~~Declarative ledger schema bootstrap~~ ✅ **DONE (session 38)** — `tgw-db-init` now applies
  `schema.sql`, `sku_history.sql`, `image_hashes.sql` after DB creation. Idempotent (safe on
  pg_restore'd DB). WAL-recovery guard (`pg_is_in_recovery()`) exits 0 on standbys. `ON_ERROR_STOP=1`.
- *Done when:* `nix flake check` passes ✅ (all 4 configs pass clean)

**0.3 Implement the template-unit form in the Nix module** ✅ **DONE (session 37)**

`nix/tgw.nix` now declares concrete units named `tgw-worker@<queue>.service` (at-sign,
not dash). `workerScripts` map drives the queue→script mapping (not a clean transform,
hence explicit). `tgw-workers.target` added, mirroring `queue-workers.target` on MX.
`tgw restart-workers`, `tgwlogs`, all runbooks, CLAUDE.md — unchanged, work as-is.
*Verification gate (Phase 3):* `systemctl status 'tgw-worker@echo'` on A1131 after `tgw-rebuild`.

**0.4 Config normalization for the site-config repo** (closes ISS-003 + ISS-004 while we're
guaranteed to touch config):
*Do:* align `full_catalog_path` in the live JSON to the code default (`tgwcatalog.json`) or
delete the key; surface `ebay_sku_migrate` through `load_config()` proper; delete the nine
documented dead keys; back up config first
(`cp tgw-api-config.json tgw-api-config.json.bak-nixos-p0`).
*Test coverage:* unit tests asserting `load_config()` returns `ebay_sku_migrate` defaults
when absent and JSON values when present; existing config tests still green; `tgw health`
green on the live host after the JSON edit; `ebay_sku_migrate` worker restarted and observed
claiming on its next hourly run.
*Done when:* `TGW-Config-Reference.md` updated; ISSUES.md entries closed with dates.

**0.5 Create the private site-config GitHub repo.** ✅ DONE 2026-06-19
`trader-grim/tgw-site-config` (private) — contains `config/` (tgw-api-config, category-groups,
ebay-config, queue-config, tgw-models, trader-grims-backup.yaml, nginx/, queue-workers/, www/,
local/) and `systemd/` (all live tgw-* and queue-workers* units). Cloned onto both USB kit
partitions. `hosts/` dir confirmed obsolete and excluded. CI secret-grep pre-commit hook:
still pending (nice-to-have, not blocking).
*Test coverage:* manual credential audit passed (no secrets in any included file); cloned
successfully to /media/tgw/TGW-SECRETS/site-config and TGW-SECRETS1/site-config.

**0.6 Migrate the live `tgw` user to a system uid below 1000** (operator, on MX, **before
the Phase-1 ISO bake** so the rollback image already carries the final uid):
*Target value:* **uid/gid 900** — verified free on the MX host 2026-06-10 (`getent` shows
900–909 fully free; 999 is taken by dnsmasq/systemd-journal). Re-verify at execution time
on MX **and** confirm 900 is free on the NixOS target (`config.ids` / fresh-install
`getent`); whatever value is chosen becomes the single number used in `usermod`, the Nix
module default, and the restore docs.
*Why:* a system service account belongs below `SYS_UID_MAX` (999); the module and
PP-DEPLOY-001 always intended this, and `check_ownership()` in `health.py` already flags
uid≥1000 informationally. Doing it on MX first means the ISO, the NixOS host, and every
future restore agree — no chown pass ever needed at restore time.
*Procedure (pipeline must be stopped — fold into the Phase-1 §1 downtime window to avoid a
second outage):*
1. `sudo systemctl stop 'tgw-worker@*' tgw-http trader-grims-backup` (already done at this
   point in the Phase-1 runbook).
2. `sudo usermod -u 900 tgw && sudo groupmod -g 900 tgw`
   (memberships in `sudo`/`keyd`/`tgwadmin` are name-based and survive; PostgreSQL peer
   auth and `User=tgw` in units are name-based and survive).
3. Chown everything the automatic `usermod` home-dir pass misses:
   `sudo chown -R --from=1001 tgw /opt/TGW /home/tgw` then the same with `--from=:1001 :tgw`
   for group-only matches; repeat per additional filesystem if `/opt/TGW` spans mounts
   (`findmnt -T /opt/TGW` from the runbook tells you).
4. **Audit before restarting anything:**
   `sudo find / -xdev \( -uid 1001 -o -gid 1001 \) -print 2>/dev/null | head` → must be
   empty (run once per mounted filesystem that matters: `/`, `/opt` if separate, `/home`).
5. `sudo bash scripts/tgw-permissions-reset.sh --check` → clean; secrets still 0700/0600.
6. Restart the pipeline; proceed with the Phase-1 runbook from its pg_dump step.
*Test coverage:* the step-4 find-audit (empty = pass); `tgw health` fully green including
the ownership check now reporting a system uid; one full worker cycle observed
(`echo` round-trip + `journalctl -u 'tgw-worker@token_refresh'` clean start); reboot test
before the ISO bake. Nix module change: `services.tgw.uid` default → 900 in the same
session (covered by 0.2's `nix flake check`).
*Rollback for this step alone:* re-run `usermod -u 1001` / `groupmod -g 1001` + the same
chown/audit in reverse — symmetric and low-risk while the pipeline is stopped.

### Phase 1 — Bake the rollback (operator; PP-DEPLOY-001 runbook, unchanged)

**1.1 Execute `reference/PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` end-to-end:** drain + stop
pipeline, `pg_dump --format=custom` into the tree, permissions `--check`, MX Snapshot with
ItemData excluded, sha256, **boot-verify in QEMU**, loop-mount spot-check, manifest.
*Test coverage:* the runbook's own §3 verification is the test; additionally record
`tgw health` green + queue-depth snapshot before stopping, and re-run both after restart.
*Done when:* manifest line "ISO verified <date> — bootable, key roots + DB dump present"
exists and the live pipeline is running again (this phase pauses production ~1–2 h).

### Phase 2 — VM validation of the full stack (no production risk; parallel-safe with 3)

**2.1 Flake evaluation + package build** (operator, any Nix machine):
`nix flake check && nix build .#tgw`. First checkpoint for R5 (mcp availability) — if it
fails here, apply the documented fallback (unstable input or overlay) as a Phase-0.2
amendment before proceeding.
*Test coverage:* `pythonImportsCheck` (in flake) passes; `./result/bin/tgw --help` and
`./result/bin/tgw-echo-worker --help` execute.

**2.2 Boot the VM:** `nixos-rebuild build-vm --flake .#vm && ./result/bin/run-*-vm`.
*Test coverage / assertions inside the VM:*
- `systemctl status postgresql tgw-http` active; all worker units active (correct names per 0.3);
- `psql -U tgw state_machine -c '\dt'` shows `queue_jobs`, `queue_job_history`,
  `queue_workers`, `sku_history`, `todo_items` (proves 0.2.3 schema init);
- reboot the VM; re-assert — schema init idempotent, services return.

**2.3 Minimal-data smoke:** copy a stub config + 3 sandbox SKU folders + a dummy
`tgw-api-key.json` into the VM's `/opt/TGW`.
*Test coverage:* `tgw health` runs (eBay checks red = expected, all path/postgres/catalog
checks green); enqueue an `echo` job → `succeeded`; `tgw build-all` over the 3 items;
`curl -H "Authorization: Bearer ..." :7373/api/items` returns them; `POST .../action`
enqueues `thumbnail_gen` and it succeeds.

**2.4 Restore drill (the R1/R6 test):** copy the Phase-1 `db-backup-PRE-SNAPSHOT-*.dump`
into the VM; `pg_restore --clean --if-exists -d state_machine` against the NixOS PG 17.
*Test coverage:* restore exits 0; row counts of `queue_jobs` match source
(`select count(*)` recorded both sides); workers restart cleanly against restored ledger;
`recover_expired_jobs()` path observed (any stale leases from the dump get requeued, not
crash).
*Done when (phase gate):* all four steps recorded in a dated note in the plan vault.

### Phase 2.5 — USB Boot Media Preparation (prerequisite for Phase 3; weekend 2026-06-21/22)

**Purpose:** A single USB drive serves as both the NixOS installer and the carrier for the
TGW flake config. It is also the permanent DR artifact — bare-metal restore starts here.

**Status (2026-06-20):** Both drives prepared. Kit contents moved to a dedicated partition
labeled `TGW-SECRETS` (see updated layout below). NixOS ISO on Ventoy partition. A1131
(iMac12,1 — 64-bit Intel, Apple EFI quirks) boot tested: Ventoy menu loads, but EFI
chainload hangs and GRUB2 can't find the ISO. Resolved by booting NixOS directly from a
dd'd ISO stick. One drive stays here (dev/active kit), one goes to satellite warehouse
(offsite DR). Dedicated secrets drives (3+) rotate separately.

**Test rig:** iMac12,1 (2011, 64-bit Intel, Apple EFI). Ventoy EFI chainload unreliable on
this hardware — direct dd of NixOS ISO to USB is the validated boot path for this machine.
Production hardware (standard UEFI) should work with Ventoy normally.

**Hardware:** 2 × 16 GB USB drives, prepared identically. One active kit, one offsite DR spare.

**Drive layout:**
```
|-- Ventoy partition 1 (exFAT, ~15.2 GB) --|-- TGW-SECRETS (ext4, 400 MB) --|-- Ventoy EFI (~32 MB) --|
   ISOs live here                              flake, site-config,                 Ventoy boot files
                                               schema SQL, enc. secrets
```

Ventoy's `-r 400` reserves 400 MB unallocated between its two partitions. The TGW kit
(formerly called `tgw-kit`) lives there, now labeled `TGW-SECRETS`. Mount by label —
no UUID bookkeeping needed, works regardless of device node assignment:

```bash
mount /dev/disk/by-label/TGW-SECRETS /mnt/tgw-secrets
```

**Preparation (run on MX host; repeat for each drive; substitute real device for `/dev/sdX`):**

```bash
# 1. Confirm target device — verify before any write
lsblk -o NAME,SIZE,MODEL,TRAN | grep -i usb

# 2. Install Ventoy with 400 MB reserved and legacy BIOS support
ventoy -i -g -r 400 /dev/sdX
#    -g       = GPT partition table (Ventoy adds hybrid MBR for legacy boot)
#    -r 400   = reserve 400 MB unallocated (between Ventoy partitions 1 and 2)

# 3. Unplug and replug the drive — kernel won't see the new partition table until
#    the device is cycled (ventoy -i rewrites the MBR/GPT in place)
#    Verify it re-appears: lsblk | grep sd

# 4. Check exact partition boundaries
fdisk -l /dev/sdX

# 5. Create TGW kit partition in the reserved space
parted /dev/sdX mkpart primary ext4 -- -432MiB -32MiB
#    adjust -432MiB if Ventoy EFI partition is a different size per fdisk output

# 6. Format and label
mkfs.ext4 -L TGW-SECRETS /dev/sdX3

# 7. Populate the kit partition
mount /dev/disk/by-label/TGW-SECRETS /mnt/tgw-secrets
mkdir -p /mnt/tgw-secrets/{flake,site-config,schema,secrets}
git clone <tgw-site-config-repo> /mnt/tgw-secrets/site-config
cp flake.nix flake.lock          /mnt/tgw-secrets/flake/
cp -r nix/                       /mnt/tgw-secrets/flake/nix/
cp src/tgw/queue/schema.sql      /mnt/tgw-secrets/schema/
cp src/tgw/queue/sku_history.sql /mnt/tgw-secrets/schema/
# Secrets: age-encrypted blobs only — raw secrets never on the USB
# cp /path/to/tgw-secrets.age  /mnt/tgw-secrets/secrets/
umount /mnt/tgw-secrets

# 7. Drop ISOs onto the Ventoy partition
mount /dev/sdX1 /mnt/ventoy
cp nixos-minimal-*.iso           /mnt/ventoy/
cp mx-linux-restore-*.iso        /mnt/ventoy/   # MX rollback image (Phase 1 artifact)
umount /mnt/ventoy
```

**Keeping the kit current:**
```bash
mount /dev/disk/by-label/TGW-SECRETS /mnt/tgw-secrets
cd /mnt/tgw-secrets/flake       && git pull   # if tracking a remote
cd /mnt/tgw-secrets/site-config && git pull
umount /mnt/tgw-secrets
```

**A1131 boot:** Ventoy with `-g` includes hybrid MBR, so legacy BIOS chainloading
works. If GRUB loads but cannot find the ISO, add `ventoy.json` to the Ventoy partition:
```json
{ "control": [{ "VTOY_DEFAULT_SEARCH_ROOT": "/ventoy" }] }
```

**Install invocation (from the booted NixOS live environment):**
```bash
mount /dev/disk/by-label/TGW-SECRETS /mnt/tgw-secrets
nixos-install --flake /mnt/tgw-secrets/flake#tgw-test
```

**✅ COMPLETED 2026-06-20 (session 37):**
1. Both drives prepared on MX host ✅
2. Ventoy EFI chainload unreliable on A1131 — resolved by dd'ing NixOS ISO directly to USB ✅
3. NixOS 25.05 installed on A1131 from the kit partition (nixos-26.05 live ISO, nixos-25.05 flake) ✅
4. Hardware config committed as `nix/hardware/tgw-test-hardware.nix` (Btrfs subvols: /, /home, /nix) ✅
5. A1131 quirks captured in `nix/hosts/tgw-test.nix` (mbpfan, Apple EFI notes) ✅
6. Bootstrapping pain documented in `docs/TGW-Plan-Vault/reference/TGW-NixOS-Reference.md` ✅

**✅ UPDATED 2026-06-22 (session 38) — TGW-VAULT replaces TGW-SECRETS:**
- New 16 GB stick: Ventoy (ISOs) + 10 GB btrfs partition labelled `TGW-VAULT`
- btrfs subvolumes: `secrets/`, `dumps/`, `flake/` — resilient to mid-write disconnects
- `scripts/tgw-usb-stamp.sh` — populates all three subvolumes on demand (--dry-run safe)
- `nix/tgw/usb-vault.nix` — udev rule auto-stamps on insertion (production only)
- First stamp complete: 1% full

**Phase 2.5 fully complete.** Syncthing pairing between MX and tgw-test not needed (push model).

**Manual GRUB fallback (A1131 only, if Ventoy definitively fails):**
```bash
grub-install --target=i386-pc --boot-directory=/mnt/sdX1/boot /dev/sdX
# write grub.cfg pointing to extracted NixOS kernel + initrd
```
NixOS x86_64 boots fine on the A1131 in BIOS/legacy mode — CPU is 64-bit, only the
EFI firmware is 32-bit. `--target=i386-pc` sidesteps the EFI entirely.

---

### Phase 3 — Spare machine, client mode (familiarity; parallel-safe with 2)

**Scope reality (Dave, 2026-06-10):** this machine is hardware-limited — it cannot run
most Ollama models. Its job is NixOS *structure and configuration* familiarity plus
restore mechanics, **not** pipeline or inference rehearsal. It is already serving this
purpose. Its target profile is the **client tier**: portable catalog (satellite SQLite
subset + thumbnails via Syncthing), not the master architecture — which is exactly what a
hardware-limited box can carry.

**3.1 Install NixOS on the spare intake machine** ✅ **DONE 2026-06-20** — iMac12,1 (A1131)
running NixOS 25.05 via `bases/portable.nix` (client tier: no workers, no HTTP, no PostgreSQL).
Hardware config committed. mbpfan for fan control. Apple EFI notes + Ventoy dd workaround
documented in `reference/TGW-NixOS-Reference.md`.

**3.2 Config push + first validation** — ✅ **DONE (session 38, 2026-06-22)**

Session 37 deliverables:
- NixOS ISO moved to `~/tgw-install-bundle/iso/` (out of git repo, distributed via install-bundle)
- Operator username no longer hardcoded — all paths derived from `config.services.syncthing.user`
- Syncthing `tgw-install-bundle` folder wired into `platform.nix`

Session 38 revision (nixos-anywhere replaces Syncthing-for-flake):
- `tgw-flake` Syncthing folder **removed** from `platform.nix` — not needed
- `tgw-rebuild` alias removed — configs pushed FROM MX via `tgw-push-config.sh`
- `scripts/tgw-push-config.sh` added: `nixos-rebuild switch --flake path:.#<host> --target-host db@<ip> --use-remote-sudo`
- `scripts/tgw-nix-sync.sh` repurposed as emergency offline-kit utility only

Completed (session 38):
- `tgw-push-config.sh` validated end-to-end (normal mode + --bootstrap mode)
- `trusted-users = root @wheel` in base.nix enables normal push from MX permanently
- fish shell + Home Manager (PP-HM-001 Phase 1) deployed to tgw-test via push
- `nix flake check` passes all 4 configs (vm, tgw-test, tgw-test-rehearsal, tgw-prod)
- Syncthing disabled on tgw-test (lib.mkForce false) — not configured, not needed
- One remaining step: `nixos-rebuild switch --rollback` practice (low priority)

**3.3 Disko partition config** — **PLANNED (prerequisite for nixos-anywhere on production)**

Disko provides declarative disk partitioning — required by nixos-anywhere for automated
full-disk provisioning. Add to flake.nix as an input; write host disko configs.

```nix
# In flake.nix inputs:
disko.url = "github:nix-community/disko";
disko.inputs.nixpkgs.follows = "nixpkgs";
```

- Write `nix/hosts/tgw-test-disko.nix` (Btrfs matching the manually-installed layout) — validates the approach on A1131
- Write `nix/hosts/tgw-prod-disko.nix` at production cutover time (hardware-specific)
- Wire `disko.nixosModules.disko` into each host config in `flake.nix`
- Gate: nixos-anywhere can fully reprovision tgw-test from MX before production cutover

### Phase 4 — Dress rehearsal: shadow server on the spare machine

**Code-side READY (session 38, 2026-06-22):**
- `nix/hosts/tgw-test-rehearsal.nix` — master.nix server profile; inference + Syncthing
  disabled; R7 eBay-worker mask commands documented inline
- `nixosConfigurations.tgw-test-rehearsal` in flake.nix (passes `nix flake check`)
- Push: `bash scripts/tgw-push-config.sh tgw-test-rehearsal 192.168.60.101`

**Operator gate (Dave):** uid migration (0.6) → MX ISO bake (Phase 1) → USB stamp → rehearsal.


**4.1 Full restore onto the spare machine as if DR:** flake (server profile) + site-config
clone + secrets copy (sneakernet, 0700/0600) + fresh `pg_dump` restore + rclone ItemData
restore (or local rsync — faster; record which).
**Hard rule (R7): before any worker starts**, mask every eBay-writing queue:
`systemctl mask` `ebay_draft? no — mask: token_refresh? NO` — precise list to mask:
`ebay_upload, ebay_price (Browse-read ok but mask anyway), ebay_stage, ebay_publish,
ebay_price_reducer, ebay_sync, ebay_legacy_sync, ebay_sku_migrate, token_refresh`.
Token_refresh masked so the shadow host never consumes the refresh token. Allowed live:
`echo, pm_intake (vault is synced — keep masked too, simpler), bundle_intake, multi_intake,
ai_identify, catalog_rebuild, thumbnail_gen, velocity_stats`. Recommended: start with only
`echo, catalog_rebuild, thumbnail_gen, ai_identify` unmasked.
*Test coverage:*
- `tgw health` — everything green except deliberately-masked eBay token checks;
- `tgw-permissions-reset.sh --check` clean (proves the uid-900 migration from step 0.6
  held through the restore — by Phase 4 nothing should be uid 1001 anymore);
- end-to-end local pipeline **minus inference** (R12: this hardware cannot run the
  models): drop a test photo bundle → intake → catalog_rebuild/thumbnail_gen succeed;
  ai_identify is exercised only as far as job claim + a clean transient requeue on the
  unavailable model (proves the queue path, not the model). Real inference validation
  moves to cutover step 5.6 on production hardware — or to upgraded hardware if Dave's
  hardware path lands first;
- timing recorded: restore wall-clock per layer (ledger / secrets / ItemData) — this number
  *is* the DR RTO and feeds the cutover window estimate.
*Done when:* a written rehearsal report (restore timings, deviations, fixes fed back into
Phase 0 files) lives in the plan vault; any module fix discovered is applied + re-verified.

### Phase 5 — Production cutover (operator window; length = Phase 4 timing + margin)

Precondition gate (all must hold): Phases 0–4 complete; suite green; ISO verified ≤30 days
old or re-baked; rclone backup fresh (`rclone lsd` spot-check); Dave has uninterrupted time.

1. **Pause intake** (stop dropping photos); set `ebay_sku_migrate.enabled: false`; let
   queues drain to zero: `psql ... GROUP BY queue_name,state` shows nothing
   queued/leased/running (dead_letter/retry_wait noted and accepted).
2. **Stop pipeline:** `sudo systemctl stop 'tgw-worker@*' tgw-http trader-grims-backup`.
3. **Final captures:** fresh `pg_dump --format=custom` into `/opt/TGW/var/`; fresh rclone
   sync of ItemData delta; `apt list --installed`/unit-list snapshots (runbook §1.5);
   permissions `--check`.
4. **Install NixOS on the production machine** (disk wipe is fine — the ISO + backups are
   the rollback) from `hosts/production-server` config; or, if hardware-swap is preferred,
   promote the rehearsed spare machine to production and demote the MX box to standby —
   **decision for Dave; the spare-promotion path has strictly less risk** (the rehearsal
   machine is already proven; MX box stays bootable untouched = instant rollback).
5. **Restore** exactly as rehearsed (site-config, secrets, ledger dump, ItemData).
6. **Unmask/start in order:** postgresql → schema-init → tgw-http → `token_refresh` first
   (watch one successful `ebay_token_refreshed` event) → **Ollama inference validation**
   (first time on NixOS — R12: models pulled/carried, one real `ai_identify` run on a test
   SKU completes before intake reopens) → non-eBay workers → eBay workers → re-enable
   `ebay_sku_migrate` last, after 24 h clean.
7. **Decommission check on the old host (or masked state if promoted-spare path):** ensure
   the MX box's workers are stopped/disabled so R7 cannot occur — physically verify
   `systemctl is-enabled 'tgw-worker@*'` is not enabled there before the new host's eBay
   workers start.
*Test coverage:* the §7 verification battery below, executed in full and recorded.

### Phase 6 — Shakedown and retirement

- ≥2 weeks normal operation: `tgw health` green daily, items flowing end-to-end including
  ≥1 operator-gated publish, `velocity_stats` nightly runs, `ebay_sku_migrate` progressing,
  zero unexplained dead_letters.
- Then: record MX-ISO retirement decision in the master plan; open follow-ups as todos —
  multi-tier flake split (`bases/`, `interfaces/`, `graphical/`, `ai/`), personal operator
  flake, PP-BACKUP-001 DR suite, Google Drive rebuild kit upload.
- **Post-NixOS track — cryptographic chain of trust (revisit once stable on NixOS):**
  NixOS + `systemd-cryptenroll` makes LUKS2 disk encryption + TPM2 auto-unlock a
  one-command enrollment: the decryption key is sealed against PCR values (measured boot
  state), so the disk auto-unlocks on every unattended reboot *but* becomes a brick if
  anyone boots a different kernel or swaps the drive. Custom Secure Boot keys (your own
  PK/KEK/db, not Microsoft's) close the chain — only your signed kernel can produce the
  PCR fingerprint the TPM2 expects. The YubiKey touch requirement from Sécurix is
  dropped entirely; it was the human-auth node and is wrong for a headless server.
  TGW's age-encrypted USB secrets (PP-BACKUP-001) are complementary — TPM2 protects
  the running disk, age protects backup material that leaves the machine. Research file:
  `docs/TGW-Plan-Vault/securix-borgbackup.md`.
*Test coverage:* the daily checks are the test; retirement requires the shakedown log.

---

## 7. Verification steps (cutover battery — run at Phase 5.6, record output)

```bash
tgw health                                   # every check green (incl. ownership, postgres)
sudo bash scripts/tgw-permissions-reset.sh --check
psql -U tgw state_machine -c "select count(*) from queue_jobs"   # matches pre-cutover dump count
tgw todo claude                              # ledger CLI path
tgw list --limit 5 && tgw resolve --location <known-bin>          # catalog reads
curl -s -H "Authorization: Bearer $KEY" localhost:7373/api/queue/status | jq .ok
# echo round-trip:
tgw enqueue-sku <any-sku> echo && sleep 10 && tgw dead-letter      # expect empty
# pipeline end-to-end on ONE test item:
#   photo bundle drop → intake → identify → draft → upload → price → stage
#   then operator: tgw staged / tgw publish — confirm live listing URL opens
journalctl -u 'tgw-worker@token_refresh' --since -1h | grep -i refreshed
# sold-sync catch-up: confirm ebay_legacy_sync ran and GetOrders window covered the outage
```

Pass criterion: all green and one real item staged + published. Anything red → consult
rollback tiers before debugging live.

## 8. Rollback plan (tiered — smallest hammer first)

- **T0 — Nix generation rollback** (config-level mistake on the new host):
  `nixos-rebuild switch --rollback`. Data untouched. Seconds.
- **T1 — Service isolation** (one subsystem misbehaving): stop/mask the affected worker
  queue(s); pipeline is stage-partitioned and idempotent, so a paused stage backs up
  harmlessly in the ledger. Fix forward.
- **T2 — Ledger restore** (queue DB corrupted/wrong): stop workers,
  `pg_restore --clean --if-exists` from the cutover dump, restart. Item JSON is unaffected
  (it is the canonical store); at worst, already-done jobs re-run — handlers are idempotent
  by invariant.
- **T3 — Full retreat to MX** (NixOS host unusable): per
  `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` §4 — boot verified ISO, install, pg_restore,
  rclone ItemData, permissions reset, restart. If the promoted-spare path was taken, T3 is
  simply "power the untouched MX box back on and re-enable its units" (minutes, not hours)
  — strongest argument for that path.
- **Irreversibles to respect:** eBay-side state (anything published/repriced during partial
  operation stays done — fine, those are real business actions); sold events during the
  window arrive via polling either way. There is no rollback that loses ItemData if the
  pre-cutover rclone sync completed — gate 5.3 exists precisely for this.

## 9. Monitoring / observability updates

Changes to make (Phase 0.7, small code, normal tests; or noted as post-cutover follow-ups):

1. **`tgw health` additions** (unit-tested like `check_ownership` was):
   - `check_postgres` also asserts server major version == 17 and that the five ledger
     tables exist (catches R1/R4 classes forever, including future restores);
   - worker-fleet check: expected unit list (from `WORKER_QUEUES` + naming pattern of 0.3)
     vs `systemctl list-units` — flags a queue with no live unit (would have caught the
     template/instance rename and catches future drift);
   - eBay-workers-masked indicator surfaced explicitly, so the R7 rule is observable;
   - **backup-freshness check**: age of the newest `pg_dump` artifact + last rclone sync
     timestamp, with a staleness threshold — today *nothing* watches backup age, so a
     silently-dead backup path is invisible until a restore is needed;
   - **queue-aging check**: oldest active job per queue + a completions-flatline signal
     (succeeded-per-hour = 0 while the queue is non-empty) — the generic surfacing of the
     invariant-D7 zero-work-stall class, currently detectable only by the manual query in
     `runbooks/pipeline-stall.md`.
2. **NixOS journald**: ensure persistent journal in the host config
   (`services.journald.extraConfig = "Storage=persistent"`) — worker logs are the primary
   forensic record and must survive reboot.
3. **Notify**: keep `log,file` backends through the migration (behavior-neutral); enable
   the desktop backend on the new host once the operator session exists. The
   `notifications.jsonl` path lives under `/opt/TGW/var/log/` and migrates with the tree.
4. **Cutover metrics to record by hand** (in the rehearsal + cutover reports): queue-depth
   snapshot before/after, restore wall-clock per layer, time-to-first
   `ebay_token_refreshed`, dead_letter count at +24 h, `ebay_sku_migrate` items/day before
   vs after re-enable.
5. **Standing watch items, first 2 weeks**: daily `tgw health`; `tgw dead-letter` daily
   (expect empty); `ebay_legacy_sync` sold matches present; nightly `velocity_stats`
   timestamp advancing; Syncthing conflict files in the vault (none expected).

## 10. Test coverage summary (requirement, restated per area)

| Area | Required coverage |
|---|---|
| pyproject/Pillow (0.1) | Full suite in clean venv without extras; import test for `tgw.fingerprint` |
| Nix module (0.2) | `nix flake check`; new `enableBackup` assertion; VM double-boot schema idempotency |
| Unit naming (0.3) | Unit tests for restart-workers name construction (both schemes); tgwlogs allowlist test; doc grep-audit |
| Config normalization (0.4) | `load_config()` unit tests for `ebay_sku_migrate` surfacing + removed keys; live `tgw health` |
| Site-config repo (0.5) | Secret-pattern CI grep; clone-and-load smoke |
| uid migration (0.6) | Full-disk find-audit for uid/gid 1001 empty; permissions `--check` clean; `tgw health` ownership green (system uid); echo round-trip; reboot test before ISO bake |
| Health additions (9.1) | Unit tests per new check, mocked psql/systemctl, mirroring `check_ownership` tests |
| VM phase (2.x) | Scripted assertion list per step above; restore drill row-count equality |
| Rehearsal/cutover (4–5) | The §7 battery is the acceptance test; one real published item is the end-to-end proof |

## 11. Related migrations register (tracked here, executed elsewhere)

NixOS is **the** migration — approached carefully, gates before execution, per this plan.
But per Dave's direction (2026-06-10), any **new feature that overrides existing behavior,
plus its integrations**, is also a migration and gets the same treatment in miniature
(current state → cutover → verification → rollback), not a hot swap. Currently in view:

| Migration | Replaces / overrides | Status & home |
|---|---|---|
| PP-BACKUP-001 DR suite | `trader-grims-backup` watcher (separate repo split first, then replacement) | design pending; the old watcher stays frozen until the suite proves itself |
| Satellite catalog write-back (PP-ADD-001 P6 / PP-PORTABLE-CATALOG-001) | one-way catalog derivation becomes two-way sync — touches the canonical-store invariant | design open (dirty-flag/merge); must not ship before conflict-resolution worker exists |
| Sync-conflict resolution worker | manual Syncthing conflict handling | designed, not built; prerequisite for trusting the catalog back-channel |
| `ebay_sku_migrate` completion | legacy Trading-API SKU scheme | running (~months); its private fulfillment-policy copy reconciles with the main resolver **after** it finishes — that reconciliation is itself a small override-migration |
| psycopg2 → psycopg3 (PERPLEXITY-005 audit item) | DB driver under every worker | research only; if adopted, it rides a normal test-gated change, ideally post-NixOS |
| Webhook sold-events go-live (PP-SOLD-001 T4 infra) | polling-only sold detection | code done; signature verification (dev_id) must be completed **before** public exposure |

Rule of thumb encoded here: *override-style changes keep the old path intact and switchable
(config flag, parallel unit, frozen fallback) until the new path has survived its own
shakedown* — exactly the pattern this NixOS plan uses at full scale.

No production code is edited under this plan until Dave approves it; Phase 0 items become
`tgw todo claude` entries on approval.
