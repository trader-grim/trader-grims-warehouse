# DONE (root-caused) — todo #1531, PP-AGENT-DISCIPLINE-001

## Finding

`.claude/hooks/worktree-guard.py` and `app-code-guard.py` do not fire on real
Edit/Write tool calls, confirmed reproducing twice (before and after an
explicit `/hooks` reload, which correctly showed all 4 hooks registered).
Hook script logic itself is correct (verified via manual stdin piping).

This is **not local** — it matches 3 confirmed, currently-open upstream
Claude Code bugs (installed version 2.1.205):
- anthropics/claude-code#74942 — Edit|Write-matched PreToolUse hooks never
  invoked under bypassPermissions/auto-mode; Bash-matched hooks in the same
  session fire normally.
- anthropics/claude-code#69260 — PreToolUse hooks don't fire for Agent-tool
  subagents at all, regardless of matcher.
- anthropics/claude-code#77212 — `ask` decisions silently auto-approved
  under bypassPermissions even when a hook does run.

No local fix exists. `invariants.md` E11/E12/E14 updated from ✅ to ⚠️ with
full detail — they were incorrectly claiming mechanically-enforced status.

## Todos
- #1531 closed (root-caused, documented).
- #1602 opened (p20): build a detective/post-hoc compensating control
  (branch/commit scan) since preventive enforcement is dead until Anthropic
  ships a fix.
- #1601 opened (p45, Dave's own ask): broader invariants-vs-contract
  redundancy/gap audit, folds in awareness of this finding.

## Next session
Read `invariants.md` E11's "CRITICAL UPDATE" block first. #1602 is the
concrete next build (detective control) if picked up before Anthropic fixes
the upstream bugs — check `gh issue view 74942/69260/77212 --repo
anthropics/claude-code` for status first, since a fix would make #1602 a
backstop rather than the primary control.
