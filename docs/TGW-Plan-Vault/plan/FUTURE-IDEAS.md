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

**PP-ANNEX-001 and PP-SEARCH-001 promoted** — both now live under
`PP-KNOWLEDGE-001` in the master plan (the 5-layer knowledge hub umbrella,
extended 2026-07-11). PP-SEARCH-001 has been LIVE since s45 (441K docs
indexed). PP-ANNEX-001's full design lives in
`docs/ai-plans/recoll-annex-jetstream.md` (Track A, packets A0-A5) plus the
"archivist" reframe in `PP-KNOWLEDGE-001`'s master-plan entry. Original
research content (control-plane/data-plane grounding, tiered-remote design,
scale context) is preserved in that design doc and this session's plan
notes — not deleted, just relocated out of the future-ideas holding pen.
