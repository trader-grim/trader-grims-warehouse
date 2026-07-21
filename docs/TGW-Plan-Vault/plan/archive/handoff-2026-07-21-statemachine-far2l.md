# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-20-agenttrace-actioned.md`; running per-session
narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Session closed out 2026-07-20 (statemachine/hooks incident chain).**
Full detail: `inbox/claude/INPROGRESS-2026-07-20-ai-identify-batch-and-statemachine.md`.
Memory: `project-2026-07-20-statemachine-and-hooks-incident`.

**Open now, top priority — explicit next action from Dave:**
Run the ai_identify reidentify batch on 427 items (2026-added, not sold,
genuinely unlisted on eBay — see inbox note for the exact query and the
`tgw hint --force` mechanism needed since most already have
`ai_identified: true`). Was mid-batch, couple-at-a-time validation, when
session ended — pick up there.

**Other open threads, lower priority:**
1. `ebay_legacy_sync` worker stays deliberately stopped — the lease-race
   mitigation (lease_seconds bump) is live, but the structural fix
   (heartbeat renewal / mark_succeeded rowcount check, #1607's remaining
   scope) isn't built yet.
2. `PP-STATEMACHINE-001` follow-ups not built: #1602 (detective control
   for the broken hooks), #1609 (run-once job semantics + gate-passing
   observability).
3. `PP-LISTEDITOR-001` #1611 (reidentify-as-full-redraft) — real feature,
   needs its own design session.
4. Aider long-queue eval never actually got dispatched — got derailed by
   the live incident chain. Still an open goal.
5. Several quick-decision asks from earlier in the session never got
   Dave's answer (tracker-hygiene batch-close, #1509 backfill approval,
   #1564 rofi/wofi choice, #1368 moot-or-not) — worth a quick pass.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
