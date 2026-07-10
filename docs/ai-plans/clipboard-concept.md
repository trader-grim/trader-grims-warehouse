# clipboard-concept: unified clipboard architecture — validating the PP-CLIP-001 → PP-EVENTD-001 staircase

**Status:** Draft — 2026-07-04
**PP ref:** PP-CLIP-001 (todo #1086 — this pass), PP-EVENTD-001 (design-complete, not implemented)

## Problem / motivation

Dave's gate (session 40, todo #1086): before building PP-CLIP-001 Phase 2 (rofi
history picker, todo #1055) or any further clipboard tooling, validate that the
whole staircase — **clipd → rofi picker → hook sync → event server** — is one
coherent architecture, not four things built in isolation that need rework
against each other later.

Reviewed three inputs per the task brief:
1. `pp/PP-CLIP-001.md` — Phase 1 daemon (DONE), Phase 2 rofi picker (next),
   Phase 3 "Unix socket endpoint in tgw-clipd + lan-mouse hook scripts",
   Phases 4-6 (Tasker, app-tagging, eBay URL detection).
2. `reference/PP-EVENTD-001-design.md` — a separate, design-complete Go daemon
   (`clip-route`) for cross-machine clipboard/event routing via PostgreSQL,
   lan-mouse hooks, KDE/Android delivery, git-annex + Google Drive data plane.
3. Inbox research drop (`inbox/linux universal lan clipboard manager - Google
   Search.html`/`.pdf`, 2026-06-28) — **checked and found empty.** This is a
   saved Google "AI Mode" (`udm=50`) search results page; that mode renders
   results client-side via JavaScript and the static HTML capture contains
   only page chrome (analytics scripts, no result titles/snippets/links —
   confirmed via `<h3>`/`<cite>`/external-`href` extraction, all zero). There
   is no prior-art research to incorporate from this file. Flagging for Dave:
   if external prior art on LAN clipboard managers is wanted, the search needs
   to be redone and actually saved (e.g. via a reader-mode capture, or with
   `udm` unset so results render server-side).

## Constraints (from settled architecture)

- Wayland is the primary target platform (X11/XFixes is XWayland fallback
  only, per PP-CLIP-001's session-33 reversal).
- No cloud VM: control plane is GitHub (NixOS flake), data plane is
  git-annex + Google Drive — "near-serverless" per PP-EVENTD-001.
- PostgreSQL `state_machine` is the one Postgres instance in the stack;
  PP-EVENTD-001 correctly proposes reusing it, not standing up a second DB.
- tgw-api fence / worker-thinness rules don't apply here — this is desktop
  tooling (Sway/lan-mouse/systemd --user), not the ItemData pipeline.

## The actual finding: PP-CLIP-001 Phase 3 and PP-EVENTD-001 describe the same job twice

PP-CLIP-001's own Phase 3 line reads: *"Unix socket endpoint in tgw-clipd +
lan-mouse hook scripts for cross-machine sync"* — implying the **existing
Python `tgw-clipd` daemon** grows a socket, receives the lan-mouse hook's
payload, and fans it out to KDE/Android itself.

PP-EVENTD-001 describes a **wholly separate Go binary** (`clip-route`) doing
the identical job: lan-mouse `enter_hook` → Unix socket → daemon → PostgreSQL
`clipboard_states` table → HTTP fan-out to KDE (a1131) and Android/Tasker,
plus git-annex/Google Drive for large payloads.

These are not complementary phases of one plan — they're **two competing
implementations of the same cross-machine-sync job**, one imagined as an
extension of the local Python daemon, one as a new Go daemon. Building
Phase 2 (rofi picker) without resolving which of these actually happens would
risk the picker's assumptions (where clipboard history lives, what process
owns it) getting invalidated the moment Phase 3 work starts — exactly the
rework Dave's gate exists to prevent.

## Proposed approach — the staircase, revised

**Split cleanly by machine-scope, not by build-phase order:**

1. **tgw-clipd (Python, DONE, local-only forever)** — owns *local* clipboard
   history, SKU/location detection, and the SQLite store on tgw-prod (and
   a1131 independently, per-machine). Its job ends at the local machine's
   boundary. It never grows a cross-machine socket.

2. **rofi picker (todo #1055, next, local-only)** — reads exclusively through
   the existing `tgw clip {list,get,last-sku}` CLI contract, never touches
   tgw-clipd's SQLite schema directly. This is **already correctly designed**
   in PP-CLIP-001's Phase 2 section — no revision needed here. Because the
   picker only depends on a stable CLI surface, it survives Phase 3+
   regardless of what daemon ends up owning cross-machine sync.

3. **hook sync (retarget to PP-EVENTD-001, not PP-CLIP-001 Phase 3)** — the
   lan-mouse `enter_hook` invokes `clip-route --target <machine>` directly.
   **Recommendation: retire PP-CLIP-001's own Phase 3 line entirely** ("Unix
   socket endpoint in tgw-clipd...") — it's superseded by `clip-route`, a
   purpose-built Go binary that doesn't need to route through tgw-clipd at
   all. `clip-route --target kde` reads the clipboard itself (`wl-paste -n`)
   at the moment the hook fires; it does not need tgw-clipd's history or
   SQLite store to do its job. Two clipboard-watching entry points (tgw-clipd's
   continuous watcher for local history, `clip-route`'s one-shot read on hook
   fire) is fine — they serve different purposes (history vs. sync) and never
   race, since `clip-route`'s read is synchronous with the hook event, not a
   background watcher.

4. **event server (PP-EVENTD-001, Go `clip-route --daemon`, cross-machine
   only)** — owns PostgreSQL `clipboard_states`, KDE/Android delivery,
   git-annex + Google Drive data plane, barcode-reader fan-out, Flutter HUD
   WebSocket, pm_intake event subscription. This is the "event server" tier;
   it is the single place cross-machine and cross-device routing lives.
   Nothing here depends on tgw-clipd.

**Net effect:** the staircase becomes two independent, non-blocking tracks
instead of one linear dependency chain:

```
Local track (tgw-prod, DONE→NEXT):     tgw-clipd  →  rofi picker (#1055)
Cross-machine track (design-complete): lan-mouse hook → clip-route --target → clip-route --daemon → {Postgres, KDE, Android, git-annex/GDrive, Flutter HUD, pm_intake}
```

Phase 2 (#1055) is now **unblocked from the cross-machine work entirely** —
it only ever depended on tgw-clipd's already-stable CLI contract, which this
pass confirms will not change regardless of how PP-EVENTD-001 is built.

## Optional future unification (not required, noted for later)

If Dave later wants the rofi picker to *also* show clips received from other
machines (i.e. cross-machine history, not just cross-machine sync), the
clean seam is: `clip-route --daemon` writes an entry into a small append-only
file or a second SQLite table that `tgw clip list` also reads, merged by
timestamp. This is additive — it does not require re-architecting either
side, and is explicitly deferred (no todo filed) until cross-machine sync
itself is built and proven.

## Files to change

None yet — this is a planning pass only, per PP-CLIP-001's gate. When
unblocked:

| File | Change |
|------|--------|
| `pp/PP-CLIP-001.md` | Retire the Phase 3 line ("Unix socket endpoint in tgw-clipd..."); point to PP-EVENTD-001 for all cross-machine work instead. Remove the now-stale "Phase 3 comes first and informs design" note under "Future: tgw-eventd". |
| (Phase 2, #1055) new rofi script | `etc/interfaces/shell/` or a small dedicated script — invoked by a Sway/macroboard keybind, shells to `tgw clip list` + `rofi -dmenu`, on select calls `tgw clip get --id N --copy`. |
| (PP-EVENTD-001 Phase 1) new Go module | `cmd/clip-route/` per the design doc's own file layout — net-new, no existing code touched. |

## Acceptance criteria

- [x] This doc exists at `docs/ai-plans/clipboard-concept.md`.
- [ ] Dave reviews and either confirms the two-track split or redirects it.
- [ ] On confirmation: `pp/PP-CLIP-001.md` updated to retire its own Phase 3
      line (small doc edit, not code) and todo #1055 (rofi picker) is
      unblocked to start.
- [ ] `pytest -q` unaffected — no source code changed by this pass.

## Open questions

- **Confirm the two-track split** (local tgw-clipd/rofi vs. cross-machine
  clip-route) is the right call, or should tgw-clipd's Python daemon be
  extended instead of standing up a new Go binary? The Go choice in
  PP-EVENTD-001 was justified by cold-start speed for the lan-mouse hook path
  (`< 2ms` requirement) — a Python `clip-route --target` equivalent would add
  interpreter startup latency on every screen-hop, which is the reason Go was
  chosen. This pass assumes that reasoning still holds; flag if not.
- **Google Drive account:** same as ItemData photo sync, or a separate vault
  account? (Carried over from PP-EVENTD-001's own open questions — not
  resolved by this pass.)
- **Go module path:** `github.com/DaveBuko/clip-route` or internal to the
  `trader-grims-warehouse` repo? (Also carried over, unresolved.)
- **Inbox research file:** redo the "linux universal lan clipboard manager"
  search capture (this pass found it empty), or drop the research step
  entirely since PP-EVENTD-001's design already stands on its own reasoning?
