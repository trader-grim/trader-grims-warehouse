# TGW Handoff

**Rule (corrected 2026-07-16, Dave): this is a handoff note, not a log.**
Once read and acted on, the whole file archives as a standard TGW
timestamped snapshot (`archive/handoff-YYYY-MM-DD-<reason>.md`, same
convention as `handoff-v5-2026-07-02-preredraw.md`) and gets replaced —
never appended to, never rotated piecemeal into `SESSION-LOG.md`. Keep it to
what's needed to pick up right now: the one open thread, not a running
history. Target: a few sentences, not pages. Prior full snapshot:
`archive/handoff-2026-07-20-radar-and-flake-fixes-actioned.md`; running
per-session narrative log (unaffected by this rule, still flat-append):
`archive/SESSION-LOG.md`.

---

**Session closed out 2026-07-20.** `PP-AGENTTRACE-001` Phases 1-3 built,
reviewed, merged (agent trace logging: Postgres table, `tgw trace` CLI,
Obsidian render, `/form/runs` UI — all live). Invariant E14 (agent-trace
evidence write-once, no exemptions) built and wired. Full detail:
`inbox/claude/INPROGRESS-2026-07-20-agenttrace-evidence-ebay-session.md`.

**Open now, needs Dave, in priority order:**
1. **PP-AGENTTRACE-001 Phase 4 + integrity-hardening packet #1586 paused**
   on Tigwa's authenticity/anti-cover-up review — needs Dave's sign-off on
   the hardening design (content hash-commitment lock, Syncthing folder
   spec) before either goes to an executor. Tigwa's own `PP-EVIDENCE-001`
   Stage 0 audit (broader — spans ItemData/Plan Vault/backups/git history
   too) is in progress on her side; todo #1589 tracks it.
2. **Three eBay Developer Support tickets drafted, none sent** — case
   `260605-000035` was closed after eBay answered only 1 of 3 bundled
   asks. `DRAFT-1591` (EPS increase), `DRAFT-1592` (alternative options
   for sold-price data), `DRAFT-1593` (new-keyset status, confirmed
   Growth-Check-gated) all sit in `docs/TGW-Plan-Vault/reference/` for
   Dave's review/edit/submission. Tigwa briefed and asked to help track
   follow-through.
3. **Unread Tigwa thread, never reached this session** — a "guided
   research acceptance gate" review request + two addenda, sitting in
   `inbox/claude/` since earlier today. Pick up next session.

No other standing risk carried forward — check `tgw plan status` / `tgw
health` fresh each session rather than trusting a stale note here.
