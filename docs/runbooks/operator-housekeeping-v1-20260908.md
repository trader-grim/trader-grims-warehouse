# Operator housekeeping — v1 (2026-09-08)

For the operator. **When to run what, and when to do nothing.** The coding
lifecycle exists so agents + timers handle routine housekeeping; you step in
only for the cases below. If a command "reappears" and you're not sure — this
page is the answer.

## The one rule

> Run a `--repair` only when `sudo /usr/local/sbin/tgw-coding-bootstrap --commit
> <HEAD>` prints a **FAIL** whose `operator_action:` line tells you to. Copy that
> exact line. Otherwise, leave it alone.

`<HEAD>` = `git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse rev-parse HEAD`
(the full 40 characters).

## The two things that drift

| domain | what it is | who keeps it current |
|---|---|---|
| **source `main`** | the code. Agents land one squashed commit per accepted task. | agents land it; `tgw-publish.timer` mirrors it to GitHub |
| **Context projection** | a read-only "here is the current Plan / state" snapshot built *from* `main`, for sessions to orient on. Always lags `main` a little. | `tgw-doctor-auto-repair.timer` (every 5 min) rebuilds it |

## The tools, in plain language

| command | means | you run it when |
|---|---|---|
| `sudo tgw-coding-bootstrap --commit <HEAD>` | hash-verified `tgw doctor check`, as root | you want the full health picture (the plain `tgw doctor check` skips root-only checks) |
| `... --commit <HEAD> --repair context` | rebuild the Context snapshot from `main` | **currently broken** — see note below; leave `context.*` FAILs alone |
| `... --repair context-launcher` | re-point the Context MCP process at the new snapshot | as above |
| `... --repair database` | apply the coding-DB roles/grants SQL | `check_database` FAILs (an agent added a table/role) |
| `... --repair harness` | (re)create `tgw-harness` / `tgw-coder`, sudoers, run the canary | standing up a fresh host, or onboarding a new executor |
| `... --repair unix-git-access` | re-lease every worktree (slow, ~9 min) | **only** the quiescence deadlock — `access.unix-group` FAIL that won't clear. This one can make things worse; ask an agent first. |
| `sudo -u tgw-release tgw-source-git publish` / `tgw-plan-git publish` | push `main` to GitHub now | you don't want to wait for `tgw-publish.timer` |

The root-owned launcher **does not self-update**. After `bin/tgw-coding-bootstrap`
changes in the repo:
`sudo install -m 0555 -o root -g root
/opt/TGW/tgw-lib/src/trader-grims-warehouse/bin/tgw-coding-bootstrap
/usr/local/sbin/tgw-coding-bootstrap`

## The situations you actually act on

1. **A `tgw doctor` FAIL names a repair and it persists >15 min.** Run the exact
   `operator_action:` line. If it fails or the FAIL comes back, hand it to an
   agent — it's a bug, not your job to force.
2. **An agent asks you to** run one specific command (group/sudoers install, a
   `--repair`, a GitHub deploy-key step). Those are the operator-only effects
   the agent can't do.
3. **Onboarding** a new host or executor — `--repair harness` per
   `harness-onboarding-v1`.

## NOT an emergency — do nothing

- `TGW Context: SOURCE_AHEAD …` at session start — sessions read live source.
- `context.snapshot` / `context.launcher` FAIL — **the repair path is itself
  broken** as of 2026-09-08 (Todo 2011): `--repair context --commit <HEAD>`
  demands a materialized release tree that LEAF-11-1.DELETE-APPARATUS removed
  ("runtime selector lock is not initialized" — a misnamed FileNotFoundError),
  and the auto-repair timer runs stale code pinned at an old commit. **This does
  not block anything** — sessions read live source. Leave it for an agent.
- `context.clients: RESTART_REQUIRED` — just start a fresh session.
- GitHub mirror behind — `source.github-publish` WARN. The timer catches up.
- A `WARN` of any kind — informational. Only `FAIL` with an `operator_action`
  is a call to act, and even then only if it sticks.

## If a timer is stuck

`systemctl list-timers 'tgw-*'` — check `tgw-doctor-auto-repair.timer` and
`tgw-publish.timer` are `active` and fired recently. `journalctl -u
tgw-doctor-auto-repair -n 50` shows why a repair isn't landing. A stuck timer is
an agent fix — capture the journal and hand it over.

**The auto-repair timer is gated by `access.unix-group`.** If that check FAILs,
the timer refuses every repair, so `context.snapshot` / `context.launcher` stay
red until it's cleared. `access.unix-group` FAILs when any file in the worktree
or `.git` is not group-writable — usually a `sudo`-created file at `0644`. Find
it: `find . -path ./.git -prune -o -type f -perm -u+w ! -perm -g+w -print` (and
the same under `.git`), then `sudo chmod 664` it. That one command often clears
the whole context cascade.

## Do not touch

- `.git/hooks/reference-transaction` and `.git/tgw-main-ref-guard/` — the main
  ref guard. Truncating `override-events.log` breaks its integrity chain and
  turns a benign WARN into a "tampering" FAIL. The "19 out-of-band advancements"
  WARN is just this session's phase-0 bridge commits; leave it or let an agent
  rotate it properly (`python3 -m tgw.main_ref_guard install` re-seals the
  hook body but not the log).

## Why this is managed

Git and the release/Context lifecycle are fiddly and have been repeatedly
half-broken by agents removing one piece and leaving a caller pointing at it
(e.g. the 2026-09-08 `coding-bootstrap` op removal). The direction of travel:
timers + `tgw doctor` self-checks + the identity inventory make the housekeeping
legible and self-correcting, so the operator's list stays as short as this page.
