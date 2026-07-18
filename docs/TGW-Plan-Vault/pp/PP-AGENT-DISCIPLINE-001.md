# PP-AGENT-DISCIPLINE-001 — agent role/procedure guardrails (full detail)

## PP-AGENT-DISCIPLINE-001 — agent role/procedure guardrails made mechanical, not prose — NEW 2026-07-16, orphaned pp_ref backfilled same day
Born from `INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`: a session
skipped the CLAUDE.md startup sequence because the user's first message read as a
quick question — proof that a written "always run this" instruction still depends
on the model choosing to comply, and it had already failed. Same-day recurrence
happened a second time (new session, bare greeting, ran only the thermal check,
skipped inbox processing) before the fix below existed.

Four pieces built, todo #1444 closed:
1. **Invariant E10** (`reference/invariants.md`) — flake checkouts across hosts must
   not silently diverge from `origin/master`. Status ⚠️ not ✅ — a standing periodic
   detector (cron/systemd-timer, independent of any agent) is the flagged remaining
   gap, not yet filed as its own todo.
2. **`.claude/agents/nix-flake-maintainer.md`** — general sysadmin agent for
   tgw-prod/a1131. Wide standing READ (logs, systemd, process state, SSH, D-Bus),
   narrow procedure-gated WRITE (git commit/push on the flake, `nixos-rebuild
   switch`, service restarts). Bakes in mandatory drift-check-both-hosts-first and
   the `commit-nix-flake` skill's procedure, host-generalized.
3. **PreToolUse hook** — `.claude/hooks/flake-guard.py` + `.claude/settings.json`.
   Gates `git commit`/`push` when `tgw-flake` appears in the command, and
   `nixos-rebuild switch`/`test` unconditionally (`ask`, not a hard block).
4. **SessionStart hook** — `.claude/hooks/session-start-briefing.py`. Runs
   automatically before any reply; read-only. Injects the `inbox/claude/` file
   list, unchecked `SUGGESTIONS.md` count, `tgw plan check`, and capped `tgw plan
   status`. Removes the judgment call from CLAUDE.md Steps 1/3 entirely — Steps 2/4
   (actually reading the plan, registering the todo/breadcrumb) still require the
   model to act on what's surfaced.

**Live-fire not yet confirmed for either hook** — this repo's settings watcher only
picks up a hooks config that existed when the session started; needs a `/hooks`
reload or session restart once to prove firing for real.

**Tigwa's Claude-contract cross-verification, 2026-07-16 (read-only, no
mutation):** confirmed the `nix-flake-maintainer.md` contract and
`SessionStart` hook wiring/firing are real (hashes recorded in the review).
Flagged `sudo -u tgw tgw plan check`/`tgw plan status` returning
`sudo: tgw: command not found` in her test environment — **re-verified
live in this session, 2026-07-16: both commands run clean** (`tgw plan
check` → "all clear"; `tgw plan status` → 56 PP-* items), so this
particular gap does not reproduce here; treat Tigwa's finding as
environment-specific (PATH/sudoers difference on her host) rather than a
standing defect, worth a note back to her rather than new work. The other
finding — flake-guard's PreToolUse matcher covers `Bash` only, not raw
`Edit`/`Write` on flake files — reconfirmed still open; already tracked as
#1449/#1450, no new todo needed.

Two open follow-up todos:
- **#1449** (p50) — extend `flake-guard.py`'s PreToolUse matcher (currently `Bash`
  only) to also catch raw `Edit`/`Write` on flake files.
- **#1450** (p50) — evaluate whether Claude Code's `settings.worktree.bgIsolation`
  harness feature can replace `tgw-coder`'s current 100%-prose worktree isolation.

