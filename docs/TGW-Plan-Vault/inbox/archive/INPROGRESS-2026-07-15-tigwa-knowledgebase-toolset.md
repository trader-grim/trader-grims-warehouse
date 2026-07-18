# In progress — Tigwa's knowledgebase toolset (PP-KNOWLEDGE-001, todo #1150/#1149/#1392)

Dave asked to "setup knowledgebase tool set for tigwa." Confirmed via Q&A this means
PP-KNOWLEDGE-001's already-authorized starting point (Dave, 2026-07-14): git-annex
(Storage) + Recoll (Search) on a1131, NOT the separately-gated #1427 MasterArchive
maintenance toolset (that one explicitly waits for a future "GO").

**Found already done (prior session):** `git-annex` + `recoll` packages declared in
`nix/hosts/a1131.nix` `environment.systemPackages` and confirmed live-deployed
(`git-annex version` / `recoll -h` both run on a1131).

**Still missing, this session's scope:**
1. Tigwa has no git identity, no git-annex repo, no recoll config at all yet
   (`/home/tigwa` checked live — clean).
2. A1 pilot (todo #1150): dedicated git-annex repo, numcopies=2, bounded sample,
   add/tag/whereis/drop/get round-trip proof.
3. Recoll index for Tigwa targeting PP-DATAINTEGRITY-001 use cases (photo-integrity
   legs 2/3, status/#STATUS reconciliation) — plan's R3 idea: a1131-local index over
   the existing ro NFS view of tgw-prod data+log, zero risk to the primary recoll index.

**Not doing this pass:** A0's full Syncthing folder inventory (separate decision
packet), A2 (rclone/GDrive special remote), any ItemData-adjacent action (A4 explicitly
excludes live data). Scope stays to what's needed for Tigwa to start using the tools.

## Done this session (2026-07-15)

1. Confirmed `git-annex`/`recoll` already declared+deployed in `nix/hosts/a1131.nix`
   (prior session, commit b801a74).
2. Set tigwa's global git identity on a1131, `git annex init "tigwa-a1131-pilot"` in
   `~/knowledgebase-pilot` with `numcopies=2` — A1 pilot repo exists, empty, ready for a
   bounded sample import (not done yet — that's the next A1 step).
3. Wrote `~/.recoll/recoll.conf` for tigwa: indexes the ro NFS view
   (`/opt/TGW/mnt/tgw-prod/{data,log}`) into a **separate local** xapiandb on a1131 — R3
   pattern from the design doc, zero risk to tgw-prod's own live PP-SEARCH-001 index.
   Kicked off `recollindex` in background (pid 117742) — still running, ~125K docs
   indexed after 17min, full corpus is 241G so expect it to run for a while longer.
4. Separately, todo #1427 (PP-CATIONIX-001, MasterArchive reconstruction toolset —
   p7zip/sqlite/mariadb/duckdb/exiftool/etc.) confirmed GO by Dave in this same
   conversation. Added to `nix/hosts/a1131.nix` alongside the existing git-annex/recoll
   block, `nix flake check` passed, committed `ae13f50`,
   `nixos-rebuild switch --flake path:~/tgw-flake#a1131` running in background.

## Update — later same session (2026-07-15)

User clarified mid-session: todo #1427 (MasterArchive maintenance toolset) is a
**separate, already-scoped** request Dave+Tigwa worked out together — not the same
thing as the PP-KNOWLEDGE-001 git-annex/Recoll work above, but also wanted this
session, alongside it. Confirmed explicitly by Dave after I flagged the ambiguity.

- **#1427 DONE and closed.** Added the confirmed package list (p7zip, unzip, zip,
  libarchive, file, android-tools, appimage-run, sqlite, sqlitebrowser, mariadb,
  postgresql, duckdb, jq, yq-go, csvkit, exiftool, poppler_utils, tesseract,
  ocrmypdf, imagemagick, mediainfo) to `nix/hosts/a1131.nix`, `nix flake check`
  passed, committed, `nixos-rebuild switch` applied and verified live (all binaries
  resolve: psql, 7z, duckdb, sqlite3, exiftool, tesseract, mariadb, jq, yq, csvcut,
  mediainfo, pdftotext, ocrmypdf, convert).
- **`glabels` dropped from this install, NOT abandoned.** nixpkgs 25.05's `glabels`
  fails to build (deprecated GTK2 API breakage); nixpkgs-unstable has removed it
  entirely upstream in favor of the maintained `glabels-qt` fork. Drafted a
  lan-mouse-style overlay pinning `glabels-qt` from nixpkgs-unstable — evaluated
  clean — but **reverted it, not applied**, per Dave: he wants Tigwa to go over the
  fork decision (different toolkit, Qt not GTK) with him directly before it lands.
  Filed as **todo #1430**, delegated to tigwa, linked to #1427, priority bumped to
  p25 — Dave flagged inventory label printing is coming up soon, not urgent today
  but don't let it sit.
- User also asked about routing around the glabels build failure via Flatpak;
  advised against it (same class of untracked-install risk that broke `hermes
  update` twice, would also need `services.flatpak`/portal as a new standing OS
  capability) — recommended the overlay path instead, which is what #1430 covers.
- Two background processes were still running when this note was finalized and
  will continue past session end:
  - `nixos-rebuild switch` (a1131, #1427) — **completed**, verified live.
  - `recollindex` (tigwa's a1131-local index of the ro NFS view) — **still running**
    at session end, pid 117742, ~261K docs / 2.1G indexed so far, full corpus is
    241G so expect it to keep running for a while. Check with:
    `ssh a1131 "sudo -u tigwa -i sh -c 'ps -p 117742; tail ~/.recoll/recollindex.log'"`

## Still open / next

- Wait for recollindex to finish its first full pass; verify a live query works
  (`recoll -t -q ...` as tigwa) once done.
- A1's actual pilot import (bounded 5-10GB sample from masterarchive/history, add/tag/
  whereis/drop/get round-trip) — repo (`~/knowledgebase-pilot`) is initialized but
  still empty.
- A0's Syncthing folder inventory is a separate decision packet, not started.
- Todo #1430 (glabels-qt fork decision) needs Dave+Tigwa's actual conversation —
  not blocking, but real and soon per Dave.
