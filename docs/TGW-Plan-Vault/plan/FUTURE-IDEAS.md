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

## PP-CATIONIX-001 — CatioNIX: TGW Platform as Standalone AI Operational Safety Platform

**Also referred to as:** Catio  
**Filed:** 2026-06-20  
**Deferral trigger:** Revisit after PP-AIOPS-001 Phase 4 (litterbox worker) is complete and
the pattern is proven end-to-end on TGW. Phase 4 is the proof-of-concept for the core
differentiator.

### Concept

Extract the TGW base platform into a standalone, general-purpose AI operational safety
platform called **CatioNIX** (short: Catio).

**The key distinction from Sécurix:** Sécurix confines human government employees.
CatioNIX confines AI agents. The "users" of the platform are AI processes, not people.
Any system where AI agents take real-world actions (file writes, API calls, order
placement, code commits) benefits from this safety envelope.

Components that would form the extractable platform:
- **CatioNIX OS layer** — NixOS service topology: declarative, reproducible, immutable base.
  Already being built in `nix/os/`. TGW-agnostic. Would be the same for any application.
- **Agent user pattern** — service accounts as confined AI agents: `isSystemUser=true`,
  home under `/opt/<agent>`, no login shell, specific UID range, `createHome=false` (tmpfiles
  owns tree). Currently in `nix/tgw/users.nix`. Future: `catio.agents` module option.
- **PostgreSQL work ledger** — `state_machine` DB, `QueueWorker` base class, job lifecycle
- **NATS JetStream audit stream** — `ITEMDATA_MUTATIONS` + `QUEUE_TRANSITIONS` (PP-AIOPS-001)
- **QueueWorker base class** — thin worker pattern, queue-in / queue-out / dead-letter
- **Litterbox pattern** — auto-fix for INFO/WARN anomalies; queue CRITICAL for operator ack
  with human-in-the-loop gating (PP-AIOPS-001 Phase 4)
- **Anomaly detection layer** — rule library over audit stream (PP-AIOPS-001 Phase 3)
- **Session isolation** — Btrfs CoW snapshot per agent session; bad sessions roll back in one
  command (PP-AIOPS-001 Phase 5)

### Differentiator

The crowded "AI safety" space focuses on model alignment and output filtering. CatioNIX
targets **operational safety**: the environment in which AI agents run, not the models
themselves. Key properties:
- Audit trail: every data change timestamped + attributed, observable after the fact
- Anomaly detection: bad patterns surface within seconds, not by operator discovery
- Human-in-the-loop gating: CRITICAL anomalies require operator ack before proceeding
- Automated remediation with escalation: litterbox auto-fixes known-safe patterns;
  unknown patterns escalate rather than guess
- Session isolation: bad agent sessions roll back in one command

TGW is already building all of this for itself. CatioNIX is what it looks like when
the TGW-specific parts are extracted and the platform is offered generically.

### Current module structure (layer separation progress)

The `nix/` tree is already structured with the CatioNIX/TGW boundary in mind:

```
nix/os/          ← CatioNIX layer (TGW-agnostic)
  base.nix         OS config any CatioNIX host would have (SSH, tailscale, syncthing, admin tools)
  users.nix        Human operator account (db, uid 1000) — NOT TGW-specific
  desktop.nix      Opt-in GUI layer (X11+Qtile, KDE Connect, bluetooth, desktop apps)

nix/tgw/         ← TGW application layer (CatioNIX implementation)
  users.nix        tgw service account (uid 900, isSystemUser) — the first CatioNIX "agent user"
  platform.nix     TGW tools + syncthing folders + tgw-rebuild alias
  desktop.nix      TGW Qtile config (extraPackages, config.py symlinks)
  usb-sync.nix     TGW install bundle → USB via Syncthing markerName
```

**Separation test applied to `nix/os/base.nix`:** As of 2026-06-21, cleaned out TGW-specific
packages that had leaked in (`ffmpeg`, `imagemagick`, `exiftool`, `chafa`, `gh`, `ydotool`,
`thefuck`) and moved them to `nix/tgw/platform.nix`. CatioNIX base now passes the test: it
would work identically on a host running a different application.

**Future abstraction (`catio.agents` option):** When CatioNIX is separated as its own project,
`nix/tgw/users.nix` becomes the model for how any application declares its agent users:
```nix
# Future CatioNIX module option (not yet built)
catio.agents.tgw = {
  uid  = 900;
  home = "/opt/TGW";
  description = "Trader Grim's Warehouse service account";
};
```
The current manual declaration in `nix/tgw/users.nix` is already the right shape; the
abstraction is added without restructuring when separation happens.

### Related research

**Sécurix (DINUM / French government):** A NixOS-based hardened OS for confining users.
Directly relevant as architecture reference — adapt for AI agents as the confined entities.
- Open source: `github.com/cloud-gouv/securix`
- Key properties: declarative immutability (state defined in Nix → no config drift),
  TPM2 + LUKS FIDO2 hardware interlocking, Secure Boot with custom-keyed authority,
  instant reinstantiation when state diverges from baseline
- **Bureautix** shows how to fork and re-key for an alternate authoritative entity —
  same pattern CatioNIX would use to let other operators key their own deployments
- Architecture for AI agent confinement Dave noted:
  ```
  [ AI Agent Action ] → Modifies Files / Runs Malware → [ Local Ephemeral State ]
                                                              │
                                                 (Reboot / Agent Reset)
                                                              ▼
  [ Pure NixOS Baseline ] ◄═══ Cryptographic Lock ═══ [ Hardware TPM2 / Key ]
  ```
- Full research: `docs/TGW-Plan-Vault/inbox/archive/20260620T092933-securix-borgbackup.md`

### Relationship to current PP items

- **PP-NIXOS-001**: Builds the CatioNIX OS layer (`nix/os/`). Every session on this is
  progress toward a clean CatioNIX separation.
- **PP-AIOPS-001**: Builds the audit stream + litterbox — the platform's core safety
  components. Phase 4 (litterbox) is the concrete proof that the pattern is extractable.
- **TGW = first CatioNIX application**: `nix/tgw/` declares TGW as one implementation.

### Promotion criteria

Ready to promote to active PP item when:
- [ ] PP-AIOPS-001 Phase 4 (litterbox) is complete and proven on TGW
- [ ] PP-NIXOS-001 migration is stable on production
- [ ] Dave decides to pursue CatioNIX as a separate product/project

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

## PP-ANNEX-001: git-annex photo store with tiered GDrive remotes

**Architectural grounding (2026-06-28):** git-annex is a concrete application of the
**control-plane / data-plane separation** principle settled in the master plan architecture
section. git is the control plane — it tracks what files exist, their SHA keys, and where they
are stored, without holding the bytes. The annex special remote system is the data plane —
it transfers actual content on demand. The same principle governs lan-mouse (focus signals vs.
input events), Wayland clipboard (ownership notification vs. content transfer), and the TGW
event server (NATS notification vs. PostgreSQL payload fetch). Where these planes are decoupled,
failures are isolated; where they are coupled (old Input Leap, X11 clipboard in Qtile), a
data-plane failure hangs the control channel.

**Concept:** Replace direct filesystem photo copy in intake workers with git-annex
content-addressed object store. Photos become annex objects (SHA256-keyed), tracked by
symlinks in git. `git-annex-remote-googledrive` (Lykos153) handles GDrive sync via
native Drive API — faster than rclone's abstraction layer, truly resumable uploads.

**Tiered remotes:**
- `gdrive-active` — items listed on eBay, last 12 months
- `gdrive-archive-YYYY` — sold/delisted items, date-partitioned (one remote per year or
  per N items to stay under GDrive's 500k-items-per-folder limit)
- `nas-local` — full local copy (directory special remote)
- Cold backup tier (B2 or second GDrive account)

`git annex move --to gdrive-archive-YYYY --metadata status=sold` handles migration.
`numcopies = 2` enforces no single-copy objects.

**Design constraints to carry forward:**
- Archive remotes must be date/count-partitioned from the start — resharding later is
  painful. New remote = one config line.
- Fence API is already SKU-addressed, not path-addressed — scales cleanly regardless of
  storage tier changes.
- Intake must be queue-parallelisable: the current one-item ZIP drop model is the
  throughput ceiling; the fence + annex design should batch from day one.

**Scale context:** Current 55k catalog / 19k active listings is complete stagnation —
floor, not ceiling. When the pipeline is fully automated, item and photo volume will be
significantly higher. Every design decision here must hold at 10x current scale.

**Promotion criteria:**
- PP-FENCE-001 complete (workers can't write ItemData directly)
- Intake worker redesign underway
- Dave ready to invest in git-annex learning curve and NixOS annex packaging

---

## PP-SEARCH-001: recoll universal index — all TGW data searchable

**Design principle (2026-06-28):** ALL data in the TGW ecosystem shall be included in
the index. This is not just a convenience feature — it is a recovery and audit tool.
Today's investigation (49 missing item JSONs recovered from ItemArchive) took hours of
manual searching across zip files, CSVs, and catalogs. With recoll it would have been
one query in seconds.

**Scope — everything goes in:**

- **ItemData/** — item JSONs (title, description, aspects, AI draft, raw LLM
  prompt/response once PP-FENCE-001 captures them, price history, ebay blocks)
- **ItemData/ photos** — via Tesseract OCR plugin: serial numbers, labels, model
  numbers, barcodes visible in photos become searchable
- **ItemArchive/** — zip contents indexed including historical JSON versions and
  source-change records; makes archive recovery instant instead of manual
- **masterarchive/history/** — eBay download CSVs, all_skus_locations.csv,
  draft-listing-import CSVs, active-inventory reports; cross-reference in one query
- **ItemCatalog/** — historical-master-catalog.json, historical-tgwcatalog.json,
  by-location index
- **docs/TGW-Plan-Vault/** — plan, reference, inbox, suggestions; searchable alongside
  item data so "what does the plan say about X" and "which items are in location X"
  are the same search
- **git-annex metadata tags** — status, category, size_class, listed_at

**Queries this enables that took hours manually today:**
- "find any record containing SKU tgw202105091454567" → instant across all sources
- "find all items at location PB1061 across current and archive"
- "find items where AI identified 'Atari' in raw response"
- "find items with a visible serial number in any photo"
- "find all eBay download reports mentioning listing ID 326340608480"
- "find items where description contains a specific model number or ISBN"

**Integration points:**
- recoll daemon watches all index roots for changes (inotify)
- `tgw search` CLI gets a `--full-text` flag hitting recoll's REST/Python API
- Web UI search bar gains full-text capability alongside existing SKU/title lookup
- ItemArchive zip contents: recoll has a zip/archive filter that indexes inside zips
  without extracting — archive stays compressed, content is searchable
- NixOS: recoll + tesseract in nixpkgs; add to tgw-prod config with index paths

**Designed alongside PP-ANNEX-001** — git-annex is the prerequisite (photos accessible
as files for OCR; annex metadata becomes recoll field tags). But the index scope is
independent of git-annex — ItemArchive and masterarchive can be indexed immediately.

**Promotion criteria:**
- PP-FENCE-001 complete (raw LLM responses captured — makes index much richer)
- PP-ANNEX-001 underway (git-annex managing photos locally)
- Dave ready to configure recoll index paths and OCR pipeline on tgw-prod
- Phase 0 (no git-annex required): index ItemArchive + masterarchive/history + catalogs
  alone — already useful for recovery and audit without any other PP complete
