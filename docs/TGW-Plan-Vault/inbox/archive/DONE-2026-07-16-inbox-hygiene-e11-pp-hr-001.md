# DONE — inbox hygiene sweep, invariant E11, PP-HR-001 opened (2026-07-16)

Session opened with a bare "Howdy!" — ran the mandatory startup sequence in full this
time (the same-day recurrence that preceded this session was already fixed structurally
before this session started; this session is the first real test of that fix, prior to
the SessionStart hook also built today).

## What was done

1. **Inbox hygiene**: found ~90 files sitting in `docs/TGW-Plan-Vault/inbox/claude/`,
   most days old. Archived 51 already-incorporated `DONE-*.md` files to `inbox/queued/`
   (their content was already folded into `handoff.md`'s session narrative — nothing
   lost, just decluttered). Checked off the one stale unprocessed `SUGGESTIONS.md` item
   (2026-07-13 Tigwa reporting-channel request — confirmed already delivered via the
   2026-07-15 per-actor inbox split + the existing `tigwa` agent-tag). Logged as todo
   #1448, closed same session.
2. **Dave flagged the backlog itself as a symptom** ("It shouldn't exist... skipping
   your inbox at startup had other effects") and then asked directly: "can we make your
   invariants take affect before you do anything else?" — approved building a
   `SessionStart` hook instead of another CLAUDE.md wording patch (the wording fix had
   already been tried once, same day prior session, and failed again within hours).
3. **Built `.claude/hooks/session-start-briefing.py`**, wired into `.claude/settings.json`
   as a `SessionStart` hook alongside the existing `PreToolUse` flake-guard. Read-only;
   injects the `inbox/claude/` file list, unchecked-`SUGGESTIONS.md` count, `tgw plan
   check`, and a capped `tgw plan status` into context automatically before any reply.
   Pipe-tested clean; JSON schema validated via Python (`jq` unavailable on this host).
   **Live-fire not yet confirmed** — needs a `/hooks` reload or session restart, same
   caveat as the earlier flake-guard hook.
4. **Dave generalized the pattern**: "we should use a similar pattern when configuring
   any agent to lock them into their role." Added **invariant E11**
   (`reference/invariants.md`) and audited the two existing custom agent profiles —
   found the flake-guard hook only matches `Bash` (not raw `Edit`/`Write` on flake
   files, todo #1449) and `tgw-coder`'s entire worktree-isolation contract is still
   prose-only (todo #1450, with `settings.worktree.bgIsolation` flagged as a plausible
   existing harness mechanism not yet evaluated).
5. **Dave connected this to the ferals audit** (todo #1333) and asked for an "HR
   department" concept covering both. Per his explicit instruction, this was NOT
   designed by Claude — **PP-HR-001 opened** (master-plan placeholder section), a full
   considerations brief written to `inbox/tigwa/CLAUDE-REQUEST-2026-07-16-hr-department-
   design-brief.md`, and todo #1451 filed + delegated to Tigwa. Dave is guiding the
   actual design with her directly; it comes back through the normal review seam.
6. **Dave then reframed today's E11/hook work as PP-HR-001's first delivered
   component** ("today we designed the job descriptions portion of our design. This was
   not a waste") — updated the master-plan section and the design brief itself to
   record this explicitly, plus saved memory `feedback-infra-before-design-not-wasted`
   (concrete scoped work doesn't need to wait for its umbrella project to be named
   first).
7. **Checked the aider/tgw-coder busywork-tier thread** at Dave's mention of resuming
   it — found the 2026-07-15 INPROGRESS note was stale: its "uncommitted" config changes
   (`.aider.conf.yml`, `bin/tgw-aider`, `aider_mcp_server.py`) actually landed in commit
   `2d98364` already. Did not touch the thread further — Dave is getting Tigwa started
   on PP-HR-001 first, aider/tgw-coder training resumes after.

## Still open

- **SessionStart + PreToolUse hooks**: both need a `/hooks` reload or restart to confirm
  live-fire. Check this first next session.
- **Todo #1449**: extend flake-guard.py's matcher to cover Edit/Write on flake files.
- **Todo #1450**: evaluate `settings.worktree.bgIsolation` vs. tgw-coder's manual
  worktree contract.
- **Todo #1451 / PP-HR-001**: delegated to Tigwa, Dave guiding — not Claude's next step
  unless asked to review.
- **Aider/tgw-coder busywork tier** (separate thread, paused not abandoned): #1358
  (worktree wiring) is done/live-verified, just needs closing out; #1424 (aider `--yes`
  auto-add bug) open, low priority; #1365 (tgw can't run pytest, nix symlink permission)
  blocked on Dave's call — widen tgw-group access vs. re-point symlinks; #1361
  (tgw-owned `.pytest_cache` blocking worktree cleanup) confirmed live, not fixed. Dave's
  stated next step: "go back to training the aider tgw-coder" once Tigwa's started.
- Carried over, untouched this session: todo #1445 (`tgw202605040949058` listing-revision
  drift, deferred twice); the ~33 remaining non-DONE inbox/claude files (INPROGRESS/
  TIGWA-REQUEST/REPORT/REVIEW/NOTE — real open items, not mechanically archivable,
  still need an actual read-and-decide pass); the orphaned `PP-ADD-005` pp_ref warning
  from `tgw plan check` (todos #1411/#1412 reference a PP with no plan-section heading).
- A stray `result/` directory at the repo root (Nix build-output symlink for a1131,
  untracked, harmless) — never addressed, still there.

## Next step

Confirm the two hooks fire live (`/hooks` reload). Then either continue the inbox
backlog triage (the ~33 real files) or resume the aider/tgw-coder training thread,
per whichever Dave picks up first.
