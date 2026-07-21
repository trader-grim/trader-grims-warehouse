# In progress: 2026-07-20 session — PP-AGENTTRACE-001, evidence-integrity review, eBay tickets

## What was done this session

- **PP-AGENTTRACE-001 fully built and merged** (Phases 1-3): `agent_runs`
  Postgres table + `tgw trace start`/`end` CLI + `archive_transcript()`
  (Phase 1, #1580); Obsidian render `TGW-Agent-Runs.md` + coalesced
  `agent_run_render` worker (Phase 2, #1581); `/form/runs` HTTP UI page
  (Phase 3, #1582). All three runner-reviewed, independently re-tested,
  merged into `catio-nix-0.0.1-alpha`. Full suite clean at each step
  (final: 2719 passed, 1 skipped).
- **Invariant E14** (agent-trace evidence write-once/append-only, no
  exemptions) built and wired: `.claude/hooks/trace-immutability-guard.py`
  (hard deny, no exempt agent), CLAUDE.md, `invariants.md`,
  `tgw-coder.md`/`nix-flake-maintainer.md` contracts all updated. Interim
  mechanism pending `PP-CATIONIX-001`'s crypto-lock.
- **Phase 4 (Claude Code hooks) PAUSED** — Tigwa filed a real authenticity/
  anti-cover-up review before it went live; multi-round exchange followed
  (gap analysis, hash-commitment + Syncthing-versioning design, Tigwa's
  same-Unix-identity trust-boundary refinement, a broader archive/library
  integrity sinkhole review, Stage 0 audit plan review). Packet **#1586**
  (Phase 1b hardening: Leg A hash-commitment, Leg B Syncthing folder spec,
  Leg C Tigwa monitoring) is **DESIGN ONLY, not authorized for build**.
- **Kate MIME registration** (todo #1579) — landed and verified live on
  both hosts via nix-flake-maintainer.
- **Flake approval-prompt consolidation** (todo #1584) — `autoMode.allow`
  entries + `nix-flake-maintainer.md` batching instructions, live.
- **eBay Developer Support**: case `260605-000035` closed by eBay after
  answering only 1 of 3 bundled asks (Marketplace Insights denied; EPS
  increase and a 2026-06-05 new-keyset request both went unanswered).
  Live-reverified EPS limit is genuinely still 5,000/day. Three separate
  clean draft tickets written per Dave's "play ball" direction:
  `DRAFT-1591` (EPS), `DRAFT-1592` (alternative options), `DRAFT-1593`
  (keyset status, confirmed Growth-Check-gated) — **none sent yet**,
  Dave's to submit. Tigwa briefed and asked to help track follow-through.

## What's still open / next session's starting points

1. **#1586 (integrity hardening) needs Dave's sign-off** before Leg A
   (Postgres hash-commitment) or Leg B (Syncthing folder, nix-flake-
   maintainer's to execute) go to any executor. Tigwa's Stage 0 audit
   (`PP-EVIDENCE-001`, read-only asset/trust register) is in progress on
   her side — todo **#1589** tracks waiting on her proposal back.
2. **Phase 4 (Claude Code trace-capture hooks) stays paused** until #1586
   Leg A lands — do not wire `.claude/hooks/agent-trace-start.py`
   (currently an inert, uncommitted-to-live draft) into `settings.json`
   without that.
3. **Three eBay drafts unsent** — `DRAFT-1591`/`1592`/`1593` in
   `docs/TGW-Plan-Vault/reference/`. Dave reviews/edits/submits himself.
4. **Unread inbox items from Tigwa, never reached this session** — a
   separate thread, unrelated to the above: `TIGWA-REQUEST-guided-
   research-acceptance-gate-review-2026-07-20.md` + two addenda
   (`TIGWA-ADDENDUM-all-research-submissions-acceptance-gate-2026-07-20.md`,
   `TIGWA-ADDENDUM-syncthing-transport-library-gate-2026-07-20.md`), all
   still sitting in `inbox/claude/`. Next session should pick these up
   first per the "active loop, keep it moving" standing instruction.

## If interrupted mid-read of this note

Check `tgw todo --by-pp` for `PP-AGENTTRACE-001` open items (#1583, #1586,
#1589 as of session end) and `EXTERNAL-SUPPORT-TICKET-REGISTER.md` for the
`EBAY-DS-1591`/`1592`/`1593` rows' current state (still `prepared / not yet
submitted` as of session end — if that's changed, the tickets were sent).
