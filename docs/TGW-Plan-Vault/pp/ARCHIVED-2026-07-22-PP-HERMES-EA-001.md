# PP-HERMES-EA-001 — Tigwa & Leotha personas (full detail)

## PP-HERMES-EA-001 — Tigwa & Leotha personas (the "dev team" upgrade)
**New 2026-07-11.** Two personas on one Hermes instance — Tigwa
(business-facing executor, new direction for the stopped `pm_intake`
worker) and Leotha (Dave-facing translator, curates PP-KNOWLEDGE-001's
data long-term). **Both explicitly IN TRAINING** — Tigwa learns to operate
by using `tgw` itself, supervised, before any autonomous authority unlocks
(gated behind the crypto-lock, PP-CATIONIX-001). First concrete
apprenticeship task: justshoutit (PP-INTAKE-004). Execution/isolation
substrate is PP-AIOPS-001, not re-designed here. Full design:
`pp/PP-HERMES-EA-001.md`.

**Claude's cross-check of Tigwa's own contract, 2026-07-16 (read-only, no
mutation), returning the same review she ran on Claude's contract same
day:** verified live — `AGENTS.md` redirect, the MCP read-only gate
(traced the actual invocation chain, imported `mcp_server` under the real
env, confirmed `_READONLY == True`), `pm_intake` stopped,
`tgw-coder.md`'s pilot-derived rules, `hermes-gateway.service` active.
**One real finding, filed as todo #1459:** the contract's explicit,
twice-stated "notify/interrupt only, never pause/kill/shutdown" thermal
authority boundary (written specifically to prevent a repeat of the
2026-07-13 unauthorized-poweroff incident) is prose only — the standing
credential underneath it (`tigwa@a1131`'s SSH key into `db@tgw-prod`,
verified live: no `command=` restriction, full shell) combined with `db`'s
verified-live `NOPASSWD: ALL` sudo grant on tgw-prod gives Tigwa the exact
capability the boundary forbids. The contract itself already flagged this
as an open scoping question in the 2026-07-12 SSH-key section and it's
still unresolved. Same class of gap as invariant E11 (written rule vs.
mechanical enforcement), not yet named as its own invariant for Tigwa's
side. Full writeup: `inbox/tigwa/CLAUDE-REVIEW-tigwa-contract-cross-
verification-2026-07-16.md`. Two smaller confirmed-still-open gaps (no
code gate on the branch-review "out-of-control" triggers/fix-attempt cap;
no tracked counter for the 2026-07-14 independent-reviewer trigger) noted
in the same doc, no new todos — already acknowledged as open in the
contract's own text.

**Redirected to PP-HR-001's job-contract-review process, 2026-07-16
(Dave):** "Intent is an hr department, this is job contract review. Tigwa
scoped, you check and approve or comment." Todo #1459 delegated to Tigwa
— she proposes the actual credential-scoping fix for her own role (Claude
doesn't design it for her), Claude reviews and approves/comments, same
review shape as the rest of PP-HR-001. Request:
`inbox/tigwa/CLAUDE-REQUEST-credential-scoping-2026-07-16.md`.

**Tigwa's Aider contract cross-verification, 2026-07-16 (read-only, no
mutation):** confirms `bin/tgw-aider`'s intended shell path (spec →
`task/<id>-<slug>` worktree at `/opt/TGW/var/worktrees/<id>-<slug>`, live
base-branch lookup) and the MCP bridge's path-traversal/slug-syntax
validation both check out (`bash -n` / `python -m py_compile` clean).
`.aider.conf.yml` + `.aiderignore` scope DeepSeek V4 Flash correctly to
XS/S busywork and exclude secrets. Two real gaps, not yet covered by
existing todos, confirmed live against `src/tgw/aider_mcp_server.py`
2026-07-16 (re-verified same session: `task_slug: str = ''` at line 191,
worktree creation still gated behind `if task_slug:` at line 223 — an
empty slug silently falls through to the shared checkout, and
`auto-commits: true` in `.aider.conf.yml` means that shared-checkout path
can commit outside any task branch):
1. No Aider preflight seam — unlike Claude's `SessionStart` hook, nothing
   injects current Plan Vault inbox/plan state into an Aider task, and
   there's no auditable `skip startup` exception.
2. `aider_run_task(..., task_slug='')` defaults to the shared checkout
   instead of requiring a slug or separately approval-gating shared-mode
   with auto-commit disabled — prose says omitting the slug is only for
   trivial one-offs, but nothing enforces that.
Live Claude→Aider MCP discovery (`claude mcp list`) remains unverified —
Tigwa's first attempt used the wrong process identity (worker subprocess
runs as `tgw`; Claude client is `db`) and was corrected same day; the
retry under `db` hit the live Anthropic 529-overloaded outage instead, so
this is still an open verification, not a resolved one. Tracked as new
todo (see below) rather than folding into #1358, which covers the worktree
*wiring* already done, not this preflight/enforcement gap.

