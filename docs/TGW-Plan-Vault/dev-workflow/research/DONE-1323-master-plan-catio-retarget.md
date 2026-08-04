# DONE: Master plan retarget — catio development framework (2026-07-11)

Todo #1323. Long design session with Dave folding 6 new/evolved concepts into
the master plan, framed by Dave as "the structural kickoff of PP-CATIONIX-001"
— "a catio, dev team, and Dave upgrade." Full working notes + rationale for
every decision remain at `/home/db/.claude/plans/shimmying-snacking-koala.md`
(plan-mode artifact, approved by Dave) — read that for full verbatim quotes
and reasoning behind each call; this note summarizes what actually landed.

## The six concepts — what was written into the real vault

1. **Hermes personas (Tigwa/Leotha)** — new `plan/pp/PP-HERMES-EA-001.md`.
   Scope kept narrow (personas + apprenticeship model only) — the execution/
   isolation substrate turned out to ALREADY exist in far more depth than
   expected: `plan/PP-AIOPS-001-cat-herding-platform.md` (6 phases: JetStream
   audit stream, anomaly detector, litterbox auto-fix, Btrfs/nspawn session
   isolation, MCP audit/rollback tools). Cross-referenced, not re-derived.
   Crypto-lock endgame noted as a Phase 5/6 addendum to PP-AIOPS-001, not a
   separate PP — avoids a redundant competing doc.
2. **Knowledge & translation hub** — `PP-KNOWLEDGE-001`'s master-plan entry
   rewritten as the 5-layer umbrella (storage/search/core-spine/memory/
   knowledge/graph). `PP-ANNEX-001` promoted out of `FUTURE-IDEAS.md`
   (archivist reframe, A3=GDrive, adapter kept open for empirical A2 pilot).
   `PP-SEARCH-001` folded in as the live Search layer.
3. **Event server / "Radar"** — `reference/PP-EVENTD-001-design.md` extended
   with the ratified #1086 two-track split (tgw-clipd/rofi local-only
   forever; PP-CLIP-001's old Phase 3 line formally retired) + full Radar/
   active-context spec (CurrentLocation regression flagged for fix, trigger
   scope, ActionConsole/tgw-http surface). PP-EVENTD-001 moved OUT of the
   master plan's Frozen list — its blocking gate is cleared.
4. **justshoutit** — folded into `PP-INTAKE-004` (same PP as the camera app,
   not a separate doc) as its "2026-07-11 expansion" section.
5. **Plan/invariant correctness doctrine** — new CLAUDE.md section, right
   after the Prime Directives. Framed as Dave's own pre-existing
   team-management principle formalized, not a borrowed framework.
6. **Camera app** — `docs/ai-plans/tgw-intake-app.md` promoted wholesale to
   `plan/pp/PP-INTAKE-004.md`, original content preserved + 2026-07-11
   expansion layered on (bidirectional event-bus requirement, 3 absorbed
   Tasker capabilities, hybrid barcode scanner, 3-phase build).

## Corrections made mid-execution (worth knowing for next session)
- **NATS is NOT fully dead** — my first-pass synthesis wrongly concluded
  Postgres LISTEN/NOTIFY superseded NATS everywhere. Reading
  `PP-AIOPS-001-cat-herding-platform.md` in full revealed NATS/JetStream has
  a separate, still-valid role: durable mutation-audit/CDC logging (Dave
  confirmed mid-session: "we want the transactional logging"). Postgres
  wins ONLY for the clip-route/knowledge-hub real-time event bus. The
  failing `nats` health check is a real gap for PP-AIOPS-001 Phase 1
  whenever picked up, not a moot nuisance.
- **Web UI vs Flutter is NOT resolved** — an earlier pass wrongly conflated
  PP-INTAKE-004's new Kotlin camera app with TGW's existing separate Flutter
  app. Caught and corrected before landing in the real vault. The actual
  open-discussion-item entry now carries the real nuanced answer (web UI
  primary/pragmatic, Flutter not abandoned, division deferred, hard
  constraint: Flutter must reuse web backend functions).
- Track naming: NOT a generic "R4" — Dave corrected this to "the catio
  development framework," which is PP-CATIONIX-001's actual Phase 1, not a
  sibling track beside R1-R3.

## Deferred, not done this session
- Full master-plan diet pass to ≤500 lines (todo #1331) — light-touch only,
  rushing a full mechanical rewrite risked destabilizing dense existing
  history. Real diet pass needs its own dedicated session.
- PP-AIOPS-001 itself was NOT unfrozen or kicked off — only linked as
  substrate. Its own "Open Questions for Dave Before Phase 1 Starts" remain
  unanswered.

## Verification
`tgw plan check` clean before AND after all edits. `tgw plan status` confirms
new pp_refs (PP-CATIONIX-001, PP-EVENTD-001, PP-INTAKE-004) correctly link
seeded todos #1323-#1330 to their new headings. Todos #1323-#1331 seeded and
tagged.
