# IN PROGRESS — planner rubric + planning sweep, 2026-07-16

## Done this session (chronological)

1. **Todo #1414 CLOSED** — `reference/PP-HERMES-EA-001-planner-rubric.md`.
2. **Todo #1478 CLOSED** — PP-STORAGE-001, PP-VISION-001 planned;
   PP-WHISPER-001 reassigned to Tigwa.
3. **Second sweep** ("is that all unplanned?") — PP-INVENTORY-001
   rewritten around Dave's real workflow (todo #1482); PP-UIUX-001 opened,
   absorbing a 10-day orphaned Flutter-vs-web discussion (todo #1483);
   PP-RUNNERCOMMS-001 resolved as the "mailbox" design (todo #1484, old
   #1390 closed).
4. **Third sweep** ("where are they, nobody mentions the camera") —
   PP-INTAKE-004's own acceptance checklist had an unchecked box (seed
   build todos) that was never done despite a 388-line real design
   existing. Seeded Phase 1a-1e as todos **#1485-#1489**, ticked the
   checkbox, did NOT start building (per Dave: "we shouldn't be that far
   yet, but I would rather have it planned and ready").
5. **Fourth pass — Dave triaged 7 stale/quiet PPs directly:**
   - **PP-BULKLIST-001** (#1111) — backend already partially exists
     (`/api/bulk/*`, `/form/bulk` confirmed live) — queued as the pass
     right after the pipeline restart-in-earnest, not before.
   - **PP-PHOTO-001 Phase B** (#1065) — reassigned to Tigwa, sits on top
     of the gdrive-archive/git-annex layer.
   - **PP-RECOVERY-001** (#1039) — CLOSED. Checked whether it had already
     been triaged over, per Dave's ask: confirmed yes — the branch it was
     gating a merge on no longer exists, both todo batches it tracked
     (142 items total) are 100% done, predates 3 PPs that superseded it.
   - **PP-MACRO-001** (#15) — reassigned to Tigwa, works with Dave
     directly on the keyd config.
   - **PP-LOOKUP-001** (#7, IGDB) — reassigned to Tigwa for nag-duty.
     **Flagged, not resolved:** Dave said "2 credentials issues" but only
     one is tracked here — Keepa/upcitemdb are also unset in
     `secrets_root` but have no filed todo. Need to ask Dave directly
     which he meant.
   - **PP-EBAY-SNAPSHOT-001** (#1077) — status note only: still waiting
     on eBay Dev Support; the hostile rep from the earlier call got
     promoted into eBay's business-division decision leadership (bad
     sign, no action available).
   - **PP-MARKETING-001** (#1110, SerpApi) — deferred, "let's get the
     pipeline restarted in earnest first."
6. `tgw plan check` clean throughout all four passes.

## Open question for Dave (from step 5)

Which second item did you mean by "2 credentials issues" alongside IGDB
(#7)? Candidates found unset in `secrets_root` with no todo filed: Keepa,
upcitemdb/go-upc (all named in `reference/PP-LOOKUP-001-APIs.md`).

## Fifth pass — "recover lost PPs" sweep + process corrections

- **PP-ROUTER-001 opened** — recovered `docs/ai-plans/router-dlink-
  dir868l-ecosystem.md` (filed 2026-07-06, never had a PP or a master-plan
  mention). DD-WRT confirmed correct firmware; 6 candidate capabilities
  (todo #1491); live IP-conflict finding filed as todo #1490. Decision
  scope narrowed by Dave to just "flash or don't" — not a commitment to
  build all 6 at once; Entware (not Optware) lets services land one packet
  at a time once flashed. **Still just a proposal, no flash decision
  made.**
- **PP-DOCLIB-001 correction** — master plan's "no standalone design doc
  existed" claim was wrong; a real one does
  (`docs/ai-plans/pp-doclib-001.md`, todo #1044). Dave confirmed no action
  needed — recoll was the faster route already taken, doc stays as
  historical record.
- **Possible NATS-JetStream-for-alarm-system leg** — sent my router
  findings to Tigwa (`inbox/tigwa/CLAUDE-NOTE-2026-07-17-router-findings-
  for-nats-alarm-research.md`) since she's already researched this; not
  merged into one design yet.
- **Process correction, twice, same thread:** Dave clarified (1) lost-PP
  recovery is pull-based (search/reinstate on request, not continuous
  Claude-run audit sweeps), then (2) the search/recovery function itself
  belongs to Tigwa (the librarian), not Claude — extends her existing
  filing authority. She's already working this at night on Dave's direct
  assignment (thermal-driven schedule Dave briefed her on, not her own
  initiative — corrected an overstatement in my first draft of that
  memory). Saved as `feedback-pp-recovery-is-pull-based.md`. **Tonight's
  sweep was Claude acting outside its now-clarified lane — don't repeat
  the pattern next session; route "find X" requests to Tigwa instead.**
- Also reconfirmed the six-stage loop doctrine at the PP level (master
  planning pass = stage 2 "documented" for a whole PP, not just a packet)
  and encoded "parallel-track discipline" (R1 gets concentrated focus;
  background PPs keep nudging forward, not frozen) into the master plan
  near the R1 table — both updates to existing memory/plan sections, not
  new structures.
- `tgw plan check` clean throughout.

## Still open from earlier in the day (todo #1477, unchanged)

Master-plan reconciliation walkthrough (5-point cleanup) still paused, not
confirmed complete by Dave. Not touched this session.

## Next session should

1. **"We code in the morning" (Dave, closing line)** — next session is
   likely execution, not more planning. Pipeline-restart-in-earnest is the
   stated top priority (mentioned repeatedly this session).
2. Get Dave's answer on the "2 credentials issues" question above, file
   the second todo if there is one.
3. Pick up any of #1480-#1491 Dave prioritizes once the pipeline restart
   is underway — all planned, none started, per the parallel-track
   discipline.
4. #1458 (Aider MCP contract gaps) still open, assigned to Claude.
5. Circle back to #1477's confirm-with-Dave step if it hasn't happened
   organically.
6. Do NOT run another unprompted "recover lost PPs" sweep — that's
   Tigwa's job now (see fifth pass above). Only search on a specific
   Dave-named target, and prefer routing even that to Tigwa first.
