# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-18-planning-session-actioned.md`; running
per-session narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Planning session closed out 2026-07-18** (post-sprint, post-break) —
master plan reconciled (2364→1235 lines, #1477 now genuinely closed),
Catio/Stripe-minions applicability matrix built + 2 shovel-ready todos
filed, secrets/DR bundle-distribution redesign promoted from FUTURE-IDEAS.
Full detail: `inbox/archive/DONE-2026-07-18-planning-session.md`.

**Open now, needs Dave:**
1. **R1 critical path resumes next session** — Dave's own words ending
   this session: "we will return to tweaking the web ui model when I
   return." Pick up R1.2 (operator test) / R1.5 (price confirm) / R1.6
   (end-to-end intake) — whichever Dave wants to drive first.
2. Google Drive OAuth client setup (Cloud Console) — Tigwa's access needs
   requested first, note in her inbox, waiting on her response before Dave
   builds it.
3. #1382 leg 3 (Tigwa tmux-notify into Claude's pane) — blocked by the
   permission classifier on a live authority confirmation, needs Dave to
   say so directly in a session, not via the todo tracker.
4. #1534 — one-line `setfacl` fix for the `tgw` user's pytest permission
   gap, ready whenever Dave wants it applied.
5. #1541 — Syncthing secrets-bundle leg (tgw-prod→a1131), ready to build;
   Dave is separately handling the phone/Tasker fob-refresh piece himself.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
