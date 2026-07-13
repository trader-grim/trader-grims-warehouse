# TGW Future Ideas Catalog

**Purpose:** Deferred and long-horizon concepts that have been evaluated and preserved but
are NOT active work items. Research, code samples, and full context are kept here so that
ideas survive context compaction and session boundaries.

**When to read:** Only at dedicated planning sessions, or when Dave explicitly asks to
review future ideas. Do NOT scan or process this file at routine session start.

**Process:**
1. A suggestion or idea arrives (SUGGESTIONS.md or inbox)
2. Evaluated: actionable now → master plan PP item; not yet → this file
3. SUGGESTIONS.md item is checked off with "→ FUTURE-IDEAS.md" annotation
4. At planning time: scan this file for ideas ready to promote to active PP items
5. When promoted: add PP item to master plan, remove entry here

---

## Generic wake-a-helper-node skill (Wake-on-LAN, reusable)

**Filed:** 2026-07-13
**Source:** Tigwa's `#1347` wake-path request (`TIGWA-REQUEST-1347-a1131-wake-path.md`,
archived) — superseded before being built when Dave decided a1131 stays
always-on (`reference-desktop-setup-rationale` memory, "this adds too much
value" to keep sleeping it).

### Why deferred, not built now
Two blockers, both circumstantial rather than a rejection of the idea:
1. a1131 no longer sleeps, so there's no everyday wake cycle to build
   against — the one concrete use case that prompted this request is gone.
2. Even setting that aside, **a1131 isn't a good testbed for WoL** (Dave,
   2026-07-13) — single always-on-adjacent desktop machine, no fleet to
   validate wake/readiness/idempotence patterns against.

### The actual future shape
Dave's framing: once there's more than one GPU-capable machine processing
jobs, a generic "wake a helper node" skill becomes genuinely useful — a
job scheduler or operator wakes a sleeping compute node on demand rather
than keeping every machine powered all the time. This is a fleet-scale
job-dispatch pattern, not a single-desktop convenience.

### What to reuse when this is picked back up
Tigwa's original request (archived `inbox/archive/`) already scoped the
hard parts worth not re-deriving:
- Readiness staging (host responds on LAN → SSH available → target
  service/session ready → handoff)
- Idempotence when the target is already awake
- Retry/timeout/backoff behavior
- Authority boundary: wake-only, never suspend/sleep/shutdown a target
  node remotely (matches the standing a1131 rule: waking is the agent's
  job, sleeping is the owning host's power management, never reversed)
- Audit logging: trigger, command, retries, readiness, outcome, elapsed time

### Promotion criteria
Promote to an active PP (e.g. a generic `wake-helper` skill or `tgw`
subcommand) once TGW has **two or more GPU/compute-capable machines** in
rotation for job processing — at that point the wake/readiness contract
above is worth building generically rather than re-deriving per-host.

---

## PP-NIXSTORE-001 — Move /nix to HDD + LVM Cache (lvmcache/dm-cache)

**Filed:** 2026-06-26  
**Source:** tgw suggest + Google AI Mode research (archived: inbox/archive/20260626-lvm-nix-cache-research.md)  
**Trigger:** OOM event 2026-06-26 — claude.exe exhausted all 30 GB RAM + 8 GB swap. vg_tgw (NVMe) is full (0.09 GB free). Growing NVMe swap requires moving /nix off the NVMe.

### Current disk state

**vg_tgw (NVMe /dev/nvme0n1p2, 200 GB — 0.09 GB free):**
| LV | Size | Notes |
|----|------|-------|
| nix | 71.9 GB | 29 GB used, 39 GB free — candidate to move |
| root | 50 GB | NixOS root |
| postgres | 50 GB | state_machine DB |
| home | 20 GB | /home |
| swap | 8 GB | NVMe swap (priority -2) |

**sda (1 TB HDD):**
| Partition | Size | Label | Status |
|-----------|------|-------|--------|
| sda1 | 260 MB | SYSTEM (FAT32) | Windows EFI — removable |
| sda2 | 16 MB | — | MS Reserved — removable |
| sda3 | 100 GB | Windows NTFS | Windows OS — removable |
| sda4 | 557 MB | NTFS | Recovery — removable |
| sda5 | 80 GB | tgw-catio-nix (btrfs) | Old CatioNIX install — decision needed |
| sda6 | 32 GB | swap | Active HDD swap (fallback, priority -3) |
| sda7 | 717 GB | TGW-SNAPSHOT-0 (btrfs) | **DO NOT TOUCH** — hourly backup target |

### Architecture decision (from Gemini/community research)

Do NOT merge HDD into vg_tgw. Two valid approaches:

**Option A — Separate VGs (simpler):** Keep /nix on an HDD LV in a new `vg_nix_hdd`. No caching. Slower cold reads but /nix is largely sequential and packages are warm after first use. Freed NVMe space → grow swap.

**Option B — lvmcache (recommended):** Move /nix to HDD LV, then use freed NVMe space as an `lvmcache` pool in front of it. Frequently accessed Nix store paths stay on SSD automatically. Best of both worlds.

### Proposed execution plan

**Phase 1 — Remove sda5 and sda6 only (prerequisite)**
- sda1–sda4 (Windows): **leave untouched** — only Windows license; future VM use via virt-manager
- sda5 (tgw-catio-nix, 80 GB btrfs): remove — old CatioNIX install, superseded by NVMe boot
- sda6 (32 GB swap): `swapoff /dev/sda6`, remove from NixOS config, then delete partition
- Create one new partition in the freed ~112 GB contiguous space (sda5+sda6 were adjacent)
- Create LVM PV on the new partition

**Phase 2 — Create HDD LVM**
```bash
pvcreate /dev/sdaX          # new partition(s) from freed space
vgcreate vg_nix_hdd /dev/sdaX
lvcreate -L 80G -n nix vg_nix_hdd   # size to match /nix usage + headroom
```

**Phase 3 — Migrate /nix**
```bash
# While booted — nix store migration
mkfs.xfs /dev/vg_nix_hdd/nix
mount /dev/vg_nix_hdd/nix /mnt/nix-new
rsync -aHX /nix/ /mnt/nix-new/
# Update NixOS config: fileSystems."/nix".device = "/dev/vg_nix_hdd/nix"
# nixos-rebuild switch → reboot → verify → remove old NVMe /nix LV
```

**Phase 4 — Set up lvmcache (Option B)**
```bash
# Freed NVMe space (~72 GB) after removing lv_nix
lvcreate -L 60G -n nix_cache vg_tgw        # cache data LV on NVMe
lvcreate -L 1G  -n nix_cache_meta vg_tgw   # cache metadata LV
lvconvert --type cache-pool   --poolmetadata vg_tgw/nix_cache_meta   vg_tgw/nix_cache
lvconvert --type cache   --cachepool vg_tgw/nix_cache   vg_nix_hdd/nix
# Result: /dev/vg_nix_hdd/nix is HDD-backed with NVMe cache
```

**Phase 5 — Grow NVMe swap**
```bash
# Remaining freed NVMe (~10 GB after cache pool)
lvresize -L 18G vg_tgw/swap
swapoff /dev/vg_tgw/swap && mkswap /dev/vg_tgw/swap && swapon /dev/vg_tgw/swap
# Result: 18 GB NVMe swap (primary) + sda6 can be retired or kept as emergency
```

### After completion
- vg_tgw free: ~0 GB (cache pool uses freed /nix space)
- NVMe swap: 18 GB (up from 8 GB)
- sda6: removed (folded into vg_nix_hdd LVM PV along with sda5 space)
- sda1–sda4 (Windows): preserved — only available Windows license; future VM candidate
- Total swap: 18 GB NVMe (primary); HDD swap gone (space repurposed as LVM)
- /nix: HDD-backed with 60 GB NVMe cache — warm paths hit SSD speeds

### Constraints and risks
- sda7 (TGW-SNAPSHOT-0) is the only DR backup — must remain intact throughout
- /nix migration must be done carefully: system is unbootable if /nix is broken mid-migration
- Consider doing Phase 3 from a live USB or tgw-test VM for safety
- lvmcache adds kernel complexity — test on tgw-test first if possible
- sda5 (tgw-catio-nix): **remove** — decision made 2026-06-26; old install, superseded by NVMe

### Promotion criteria
Ready to promote when: Dave explicitly approves the plan, sda5 decision is made, and a maintenance window is available (requires reboot). Reference: `inbox/archive/20260626-lvm-nix-cache-research.md`.

---

**PP-CATIONIX-001 promoted to active PP 2026-07-11** — full content moved to
`plan/pp/PP-CATIONIX-001.md`. Dave's direct decision, ahead of its own
originally-stated promotion criteria (litterbox complete + NixOS stable) —
see that doc's "Promotion — advanced ahead of schedule" section for why this
isn't a silent contradiction of the criteria below.

---

## Alt-text on all item photos

**Filed:** 2026-06-17  
**Suggestion text:** "We should add an option to add alt-text to additional photos, or
maybe even just put it on all of them. Books benefit a lot from back cover, table of
contents, copyright page."

**Context:** Current `ai_identify` worker generates alt-text/vision enrichment for the
primary photo only. Secondary photos (back cover, detail shots, copyright page for books)
carry significant product information that could improve listings.

**Deferral reason:** Vision pipeline is in flux (Google Vision confirmed fast+good
2026-06-13; Anthropic direct key pending). Design the multi-photo pass after the single-
photo pipeline is stable and the model routing is settled (PP-MULTIMODEL-001).

**Promotion criteria:**
- [ ] PP-MULTIMODEL-001 model router settled
- [ ] Single-photo pipeline stable on production
- [ ] Cost-per-item data available to estimate multi-photo pass cost

---

## MC-SYNCTHING-VFS — Midnight Commander Syncthing Virtual File System Plugin

**Origin:** SUGGESTIONS.md 2026-06-21T23:01; research in `perplexity/RESEARCH-syncthing-mc-plugin.md`
**Related PP:** PP-PYIPC-001

### Concept
A Midnight Commander `extfs` plugin that exposes a Syncthing share as a browseable
virtual file system pane — on-demand, no full local sync required. Based on the
Syncthing-Lite "sync browser" model: fetch directory listings from Syncthing's REST API
(`/rest/db/browse`) without downloading anything, then stream individual files only when
the operator copies them out (F5).

### Architecture
- **Backend**: local Syncthing daemon (Go binary, already installed), REST API on `localhost:8384`
- **Plugin**: Python `extfs` script at `/usr/share/mc/extfs.d/syncthinglite` (chmod +x)
- **MC hooks required**: `list` (directory listing), `copyout` (on-demand download), `copyin` (upload)
- **Folder type**: "Receive Only" + Watch disabled → prevents auto-sync; REST API reads global state

### Why it's interesting for TGW
MC is already part of the operator workflow. A Syncthing VFS pane would let `db` browse
any remote Syncthing share (phone photo drops, install bundles, USB kit contents) directly
in MC without mounting or full sync. Complements the existing Syncthing topology rather
than replacing it.

### Promotion criteria
- [ ] PP-PYIPC-001 stable and confirmed in production
- [ ] Operator workstation (tgw-prod) running NixOS with MC installed
- [ ] Clear use case identified (phone photos? remote install-bundle browsing?)

---

## PP-MASTERDB-001 — Master ItemData in Postgres, JSON as export/backup (not primary)

**Filed:** 2026-07-12
**Source:** Dave, in conversation ("that is our horizon... a database would be better when it
gets busy")
**Status:** Deferred pending discussion, but Dave raised its priority 2026-07-12 given the
drive-space rationale below overlaps a currently-live pressure (PP-NIXSTORE-001: NVMe was down
to 0.09GB free during the 2026-06-26 OOM event). Still not a PP item — promotion still needs
the dedicated discussion — but this should surface earlier at planning time than a typical
future-idea, not wait for routine "someday" review.

### The idea

This is TGW's own long-term direction, not a reference to an external system — Dave hadn't
raised it before "to avoid confusion." The idea: master dataset lives in the database as the
source of truth, with flat-file exports used for backup/portability rather than the reverse.
Originally framed as gated purely on business volume ("would be better when it gets busy");
the drive-space rationale below means part of the case is relevant now, not only at scale.

### Dave's stated rationale

Explicitly not "replace JSON, dislike it" — "I still like my json data." The case for DB-primary
is technical, at scale:

- **Filesystem is slow, database is fast** — at higher item/write volume, the current
  read-JSON/atomic-write-JSON-per-SKU path doesn't scale the way DB reads/writes do.
- **Multi-user locking "itself without our help"** — Postgres has real concurrency control
  built in; the current fence (`atomic_write_json`, per-item file locking) has to hand-build
  what a DB gives for free. As more concurrent writers show up (more workers, Tigwa, a1131,
  eventually more human operators), that hand-built locking is the part most likely to need
  ongoing engineering attention if it stays file-based.
- **Less SSD wear/heat** — Dave's observation, concretely evidenced by the same-day incident
  above this entry (catalog_rebuild loop): a single resurrected worker wrote 60.8G to disk in
  ~9 hours doing full-file JSON/SQLite rewrites for a change set that, as DB row
  updates + WAL, would have been a small fraction of that I/O. Full-file rewrite (whole
  catalog, or even a whole item JSON for a one-field change) is inherently more
  write-amplifying than in-place row updates — this compounds with tgw-prod's existing
  thermal sensitivity (see CLAUDE.md thermal-watchdog notes).
- **Lighter-weight backup** — a `pg_dump` is one coherent, consistent point-in-time export in
  a single operation. The current file-based approach needs a btrfs snapshot (and the
  coordination dance around it) to get the same all-or-nothing consistency guarantee across
  tens of thousands of individual per-SKU JSON files. Relevant given the `backups` health
  check is currently one of the standing failed checks (rclone sync incomplete, snapshot tree
  stale — see PP-BACKUP-001).
- **Frees drive space** — Dave's addition, and the reason this entry's priority was raised:
  the master ItemData JSON tree + full-rebuild catalog artifacts (master-catalog.json,
  search-catalog.json, tgwcatalog.db, location-tree symlinks) all live on the same NVMe
  volume that ran to 0.09GB free during the 2026-06-26 OOM event (PP-NIXSTORE-001, this same
  file). A DB-primary model with on-demand/periodic JSON export, rather than permanently
  keeping both the full JSON tree and every derived full-rebuild artifact on disk at once,
  directly relieves that pressure — this is the one rationale point that isn't gated on
  "when it gets busy."

This reframes the promotion question: it's not "is DB nicer in the abstract," it's "at what
write-concurrency/volume does file-based locking start costing more engineering time than a
DB migration would."

Dave also notes: JSON-as-primary was his own deliberate original call, not an imposed
constraint or a gap someone else should route around — and he already expects the eventual
switch to a purpose-built database to be the right call at some point. This isn't a case that
needs to be made to him later; it's already agreed in principle, just gated on timing/scale
rather than on convincing him DB is better.

### Why this is a bigger decision than it sounds

This is NOT the same as todo #1351 (moving the *derived catalog* — search index, location
tree, portable SQLite export — into incremental Postgres rows for cheaper rebuilds). That one
leaves ItemData JSON as the permanent raw record and only changes how *derived* artifacts are
built. This idea is different in kind: it proposes flipping which side is authoritative —
Postgres becomes primary, JSON becomes the derived/exported copy.

That's a direct hit on Prime Directive 1 and the settled architecture documented in
`reference/TGW-Data-Charter.md` and CLAUDE.md ("One folder per SKU —
`ItemData/<SKU>/<SKU>.json`... raw is permanent, derived is recomputable"). Before this can be
adopted, at minimum needs an explicit answer to:

- What "raw" means once the primary store isn't a flat file per item anymore — the Data
  Charter's asset-preservation guarantees were written assuming JSON-on-disk is the
  permanent record (Syncthing-replicated, git-history-style recoverability, human-readable,
  directly grep/recoll-able without a running DB).
  A DB-primary model needs its own answer to "how do we never lose data" that's at least as
  strong — schema migrations, backup verification, and point-in-time recovery all get harder
  to reason about than "the file is still there" once Postgres is the source of truth.
- How live JSON export from the DB would actually work for backup — is it still one-file-
  per-SKU (preserving recoll indexing, MC browsing, Syncthing replication of individual
  items) or a different shape entirely?
- Relationship to #1351 (derived catalog into Postgres) — likely the same underlying Postgres
  investment could serve both, but the *master* dataset move is the one that needs Dave's
  explicit sign-off given Prime Directive 1's weight, not something to bundle in silently.

### Promotion criteria

- [ ] Dedicated discussion with Dave about what "raw is permanent" means under a DB-primary
      model, and whether it still holds
- [ ] #1351 (derived catalog → Postgres) implemented and living, as a smaller proof of the
      same underlying pattern
- [ ] Concrete backup/export design reviewed (JSON-from-DB shape, recovery drill)

---

**PP-ANNEX-001 and PP-SEARCH-001 promoted** — both now live under
`PP-KNOWLEDGE-001` in the master plan (the 5-layer knowledge hub umbrella,
extended 2026-07-11). PP-SEARCH-001 has been LIVE since s45 (441K docs
indexed). PP-ANNEX-001's full design lives in
`docs/ai-plans/recoll-annex-jetstream.md` (Track A, packets A0-A5) plus the
"archivist" reframe in `PP-KNOWLEDGE-001`'s master-plan entry. Original
research content (control-plane/data-plane grounding, tiered-remote design,
scale context) is preserved in that design doc and this session's plan
notes — not deleted, just relocated out of the future-ideas holding pen.
