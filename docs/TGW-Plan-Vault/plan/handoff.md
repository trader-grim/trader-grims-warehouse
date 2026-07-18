# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-18-sprint-actioned.md`; running per-session
narrative log (unaffected by this rule, still flat-append): `archive/
SESSION-LOG.md`.

---

**Sprint (waves 8-15, ~30 todos) closed out 2026-07-18** — backlog caught
up, CI green, nothing unmerged. Full detail:
`inbox/archive/DONE-2026-07-18-overnight-sprint.md`.

**Open now, needs Dave:**
1. Google Drive OAuth client setup (Cloud Console) — Tigwa's access needs
   requested first, note in her inbox, waiting on her response before Dave
   builds it.
2. #1382 leg 3 (Tigwa tmux-notify into Claude's pane) — blocked by the
   permission classifier on a live authority confirmation, needs Dave to
   say so directly in a session, not via the todo tracker.
3. #1534 — one-line `setfacl` fix for the `tgw` user's pytest permission
   gap, ready whenever Dave wants it applied.
4. #1477 (master-plan reconciliation) still not fully closed per Dave's
   own words — don't treat as done.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
