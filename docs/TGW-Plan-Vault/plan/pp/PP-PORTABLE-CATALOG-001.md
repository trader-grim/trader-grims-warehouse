# PP-PORTABLE-CATALOG-001 — offline/portable catalog sync (Flutter app)

**Status:** First real design doc for this PP — never had one before (session-18
archive note: "PP-PLASMA-001 + PP-PORTABLE-CATALOG-001 never got formal plan
sections"). Written 2026-07-11 after Dave asked for a cohesive assessment:
"I don't know the cohesive status of the portable catalog... I really want to
see the final output for the current design and see where it lacks or shines."

## Problem / intent

A satellite/offline catalog for the Flutter mobile app (`apps/tgw_app/`),
meant to run on a1131 (and handheld devices generally) for browsing/editing
inventory without a live connection to tgw-prod — snapshot-sync when
connected, queue edits when not, flush on reconnect.

## Ground truth as of 2026-07-11 (do not re-derive — this was a full investigation)

**Marked "done" in the master plan (2026-06-20) and in the tracker (todos
#150-152). Neither claim survives verification.**

- **Never installed on a1131** — the device it was designed for. Zero
  mentions in any a1131 setup doc.
- **Never live-verified by anyone.** The acceptance spec required a
  screenshot of the offline→edit→reconnect→sync flow actually working —
  never produced, searched the whole vault.
- **Dart sync logic has zero test coverage** — `test/widget_test.dart` is
  still the unmodified Flutter template.
- **Documented precedent for exactly this failure, on this exact feature**
  (`SUGGESTIONS.md:209-210`, 2026-06-15): todo **#151 self-marked done while
  the Flutter build was actively failing** (missing `libsecret-1-dev`,
  missing pubspec deps). Caught and patched via #869, but the pattern —
  an agent marking "done" without the thing actually working — is on
  record for this feature specifically, from the same batch-execution
  sprint that produced #150-152.
- The compiled Linux build artifact on disk **predates the final commit by
  3 hours** — doesn't reflect current code.
- The Python sync-conflict worker (todo #152, `src/tgw/sync_conflict.py`)
  has 47 passing unit tests but **no systemd unit, no config key set, never
  run against a real production conflict file.**
- **A planning doc separately overstated this feature's state**:
  `reference/PP-EVENTD-001-design.md` claimed "Flutter app connects via
  HTTP listener (already implemented)" — false, corrected 2026-07-11 (see
  that doc). No listener of any kind exists.

**Verdict: this is a real, substantive multi-week prototype effort — not
abandoned, not fake — but it has never crossed the line from "compiles" to
"verified working, on the target hardware, by a human."** #150-152 status
left as-is in the tracker for now (marked done) — this doc is the honest
record; Dave has not yet decided whether to formally reopen them.

## Architecture assessment (deep code review, 2026-07-11)

Scope reviewed: `lib/api/api_client.dart`, `lib/db/offline_db.dart`,
`lib/db/outbox_db.dart`, `lib/services/catalog_sync_service.dart`,
`lib/repository/repository.dart`, `lib/providers/providers.dart`.

### Shines — genuinely solid, not just "compiles"
- **Snapshot download → atomic rename → reopen-readonly.** Rename-in-place
  on the same filesystem is genuinely atomic on Linux; this part of the
  design is sound (`catalog_sync_service.dart`).
- **Outbox schema + enqueue/flush mechanics** (`outbox_db.dart`) — a flat
  `pending_mutations` mutation log, structurally clean.
- **Per-call online-then-offline-fallback on reads** (`repository.dart`) —
  arguably more robust than the coarse connection flag, since it re-checks
  reachability on every call rather than trusting stale state.

### Lacks — architectural gaps, not "needs live verification"
1. **Connectivity detection is 100% manual.** `connectivity_plus` and
   `workmanager` are in `pubspec.yaml` — added specifically for this
   feature (commit `bc640a0`, stated purpose "offline flush scheduling,
   connectivity detection") — but **never imported or referenced anywhere
   in `lib/`.** They fixed a build error and were abandoned. No
   `Timer.periodic`, no lifecycle hook. A real network drop mid-session
   shows a stale "ONLINE" badge indefinitely.
2. **No conflict resolution at all.** Silent last-write-wins ordered by
   local flush sequence (insertion order on *this device*) — two devices
   editing the same SKU offline clobber each other with zero detection.
3. **Offline reads don't see the device's own queued outbox mutations.**
   Edit a SKU offline, view it again, you see the stale pre-edit snapshot
   data — your own change looks like it didn't take.
4. **No retry cap or backoff.** `attempts` is incremented on the
   `pending_mutations` row but never read anywhere — a permanently-failing
   mutation (e.g. server-side validation error, deleted SKU) retries
   forever, silently, on every offline→online transition.
5. **Dead UI plumbing.** `pendingMutationsProvider`, `FlushResult`,
   `SyncResult`, `hasLocalCopy()` all exist in code and are computed, but
   **never rendered/consumed anywhere** — zero visibility into sync state
   for the user.
6. **Misleading success signal.** `patchItem` returns `true` identically
   whether the write hit the server or only got locally queued;
   `EditItemScreen` shows the same "Item updated" snackbar either way.
7. **No integrity check on downloaded snapshots** — success is inferred
   purely from "Dio didn't throw," no checksum, no size sanity check, no
   explicit status-code check.
8. **No error handling around opening a possibly-corrupt local DB** —
   `offline_db.dart`'s `ensureInitialized()` has no try/catch; a corrupted
   snapshot surfaces as silent "no items," not a diagnosable error.
9. **The action surface beyond field-patch has no offline path at all** —
   `performAction`, `bulkAction`, `deleteItem`, `setItemTemplate`,
   `uploadToInbox` all call the API directly with no queuing; offline they
   just fail.
10. **No server-initiated communication of any kind — the backchannel.**
    See below.

## The backchannel (Dave, 2026-07-11: "we still need to build [this]")

**Confirmed missing, not just unbuilt-but-planned.** Today the whole design
is "app pulls when a human taps refresh, pushes its outbox when a human
taps refresh" — zero server-push, zero background sync. This is real,
scoped, unblocked work, not new scope invented today:

- **It's PP-EVENTD-001's own Phase 5** ("Flutter HUD WebSocket subscriber"),
  already spec'd there — `payload_type: sku` / `clipboard_image` /
  `pipeline_event` event routing. PP-EVENTD-001 is now unfrozen (this
  session, #1086 gate cleared) so this is buildable once its Phase 1-4
  land.
- **Same bidirectional-participant pattern already specified for
  PP-INTAKE-004's camera app** (Concept 6, this session): the app should be
  both a consumer (receive server-pushed events) AND fully standalone
  (works with zero event-server dependency, offline-capable) — not an
  either/or.
- Building the backchannel would also directly fix gap #1 above (real
  server-push obsoletes the need for client-side polling/`workmanager`
  entirely for the "know when to sync" problem) and could carry gap #5's
  sync-state visibility (server can push flush-result/conflict events back).

## Remediation plan (proposed phasing, not yet started, needs Dave's go)

*Phase A — make the existing manual model trustworthy (small, standalone):*
- Wire `connectivity_plus` (already a dependency) to actually drive
  `ConnectionStatusNotifier` instead of only checking at startup/on-tap.
- Overlay pending outbox mutations onto offline reads (fixes gap #3).
- Add retry cap + backoff + dead-letter surfacing for outbox entries
  (fixes gap #4).
- Render `pendingMutationsProvider`/`FlushResult`/`SyncResult` somewhere in
  the UI — even a simple badge (fixes gap #5).
- Distinguish "saved to server" vs. "queued for later" in the save
  confirmation (fixes gap #6).
- Checksum/size-check downloaded snapshots; wrap `ensureInitialized()` in
  try/catch with a real error state (fixes gaps #7-8).

*Phase B — the backchannel (depends on PP-EVENTD-001 Phase 1-4 landing):*
- Build the WebSocket/event subscriber the Flutter side has never had.
- Server-pushed sync triggers replace/augment manual polling.
- Route conflict/flush-result events back through it.

*Phase C — conflict resolution (real design work, not yet scoped at all):*
- Needs an actual decision: last-write-wins-with-warning, field-level merge,
  or operator-review-queue for conflicting edits. Not decided — flag for a
  dedicated design pass, don't improvise this into Phase A or B.

*Explicitly out of scope for this doc:* extending offline support to the
full action surface (`performAction`, `bulkAction`, etc.) — gap #9 is noted
but not phased; revisit after Phase A proves the core model out.

## Open questions
- Live install on a1131 — hasn't happened yet, needed before any of this
  can be genuinely verified rather than just code-reviewed.
- #150-152's tracker status — left "done" for now; Dave hasn't decided
  whether to reopen.
- Conflict-resolution model (Phase C) — undecided, needs its own pass.
- Whether Phase A is worth doing before Phase B, or whether the backchannel
  should come first and obsolete some of Phase A's polling-focused fixes —
  not decided, flag at next touch.

## Cross-links
- `reference/PP-EVENTD-001-design.md` — the backchannel's actual design
  (Phase 5, Flutter HUD WebSocket), and the corrected false-claim note.
- `plan/pp/PP-INTAKE-004.md` — same bidirectional-event-participant pattern,
  built the same way, for the camera app.
- `SUGGESTIONS.md:209-210` — the documented precedent for this feature's
  "marked done without verification" failure.
