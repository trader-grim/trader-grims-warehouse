# DONE — nix-flake-maintainer guardrails (todo #1444, PP-AGENT-DISCIPLINE-001)

Follow-up to `INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`. Dave signed off
on three concrete pieces after walking through the incident:

1. **Invariant + detector** in `reference/invariants.md`: flake checkouts across hosts must
   not silently diverge from `origin/master` — a standing, system-level check, not a memory
   note only I might apply.
2. **`nix-flake-maintainer` subagent** (`.claude/agents/`) — general sysadmin capability
   (wide read: logs, systemd, process state, SSH between hosts, D-Bus) but narrow/gated
   mutation (git commit/push, `nixos-rebuild switch`, service restarts). Bakes the
   `commit-nix-flake` procedure in as mandatory steps rather than a skill file that has to
   be remembered and correctly generalized (today's actual failure: "for tgw-prod
   specifically" wording not generalized to a1131).
3. **PreToolUse hook** gating `git push`/`git commit`/`nixos-rebuild switch` when cwd is
   `~/tgw-flake`, so the boundary doesn't depend on which agent happens to be invoked.

Scope note from Dave: the agent is explicitly "general sysadmin also for interfacing
outside nix" — not narrowly flake-file-only. Split is READ (wide, standing, no gate) vs
WRITE/MUTATE (narrow, hooked).

## Status — all three built, 2026-07-16

- [x] invariants.md E10 — added, ⚠️ status (standing periodic detector still not built,
  explicitly flagged as the remaining gap before this could go ✅).
- [x] `.claude/agents/nix-flake-maintainer.md` — general sysadmin agent, wide read / narrow
  gated-write split, bakes in Step 1 (drift check both hosts before any mutation) and
  Step 2 (commit-nix-flake procedure, host-generalized, not tgw-prod-only wording).
- [x] PreToolUse hook — `.claude/hooks/flake-guard.py` + `.claude/settings.json`, gates
  `git commit`/`push` on `~/tgw-flake` and `nixos-rebuild switch`/`test` anywhere, requires
  explicit confirmation (`permissionDecision: ask`). Pipe-tested against 9 positive/negative
  cases, all correct. **Live-fire proof did not intercept** — expected per the settings-watcher
  caveat (it only watches `.claude/` dirs that existed when the session started; this file was
  created mid-session). **Needs Dave to open `/hooks` once (or restart) to activate** — not
  yet confirmed live-fire working as of session end.

Also, separately as part of the same reconciliation work this session: merged and pushed
a1131's 15-commit local drift (`ae13f50`, `61e9a3f`, todo #1427) back into `origin/master`
and fast-forwarded a1131 to match — both hosts + origin now at `5c729ff`, even.

## Next session

- Confirm the PreToolUse hook actually fires (needs the `/hooks` reload first).
- The standing periodic drift detector (invariant E10's remaining gap) is not filed as its
  own todo yet — file one before considering E10 fully ✅.
- See also `TIGWA-NOTE-listing-revision-drift-tgw202605040949058.md` (todo #1445,
  PP-LISTEDITOR-001) — separate, unrelated item Dave asked to be picked up at next startup,
  not addressed this session.
