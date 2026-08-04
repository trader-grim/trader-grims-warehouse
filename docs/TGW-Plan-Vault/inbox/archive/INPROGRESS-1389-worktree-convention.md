# In progress: todo #1389 (PP-HERMES-EA-001)

Implementing #1450's recommendation: set `worktree.bgIsolation: "none"` in
`.claude/settings.json` and build `.claude/hooks/worktree-guard.py` (sibling
to `flake-guard.py`) that blocks Edit/Write outside the
`/opt/TGW/var/worktrees/<id>-<slug>/` (or `/home/db/tgw-worktrees/<id>-<slug>/`)
convention when `agent_type` is `tgw-coder`. Confirmed via reverse-engineering
the installed Claude Code binary that PreToolUse hook payloads include an
`agent_type` field (from `ff()` in the CLI bundle), so the hook can gate on
agent identity, not just path. nix-flake-maintainer does NOT use the worktree
convention (works directly on `~/tgw-flake`), so it is out of scope for this
hook's agent-type match despite the packet naming it as "if relevant."

Working in worktree `/opt/TGW/var/worktrees/1389-worktree-convention` on
branch `todo/1389-worktree-convention`. NOT touching the orphaned
`.claude/worktrees/agent-a271e21fa52fe73ad` worktree.
