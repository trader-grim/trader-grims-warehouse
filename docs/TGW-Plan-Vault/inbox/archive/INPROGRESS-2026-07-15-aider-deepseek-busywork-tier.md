# In progress: Aider + deepseek-v4-flash busywork execution tier

**Session:** 2026-07-15, Claude Code (main session, catio-nix-0.0.1-alpha)
**Trigger:** Dave: "let's apply our tgw-coder skill to aider and change its
model to deepseek-v4-flash... put it to work. Coding, monitoring,
schlepping files, merging, all the busy work."

## What was done

1. `.aider.conf.yml`: single model `deepseek/deepseek-v4-flash` (DIRECT
   API, funded key — not OpenRouter), no editor/weak-model split,
   `map-tokens` raised 16000→65536 to use the 1M context window.
2. `DEEPSEEK_API_KEY` added to `/home/db/.env` (copied from
   `secrets_root/tgw.env` without ever printing the value) and to
   `aider_mcp_server.py`'s `_load_api_keys()` for the MCP path.
3. Fixed todo #1358's real gap: both `bin/tgw-aider` and
   `aider_run_task`'s new `task_slug` param now create/reattach an
   isolated worktree at `/opt/TGW/var/worktrees/<id>-<slug>` on branch
   `task/<id>-<slug>`, base branch verified live — matches tgw-coder's
   mandatory contract. **Live-verified working, twice**, via
   `bin/tgw-aider`.
4. `.claude/settings.local.json`: added a scoped Bash permission rule for
   `bin/tgw-aider --yes *` (was blocked by the auto-mode classifier as an
   unattended-agent-execution risk; Dave authorized).
5. Filed `FUTURE-IDEAS.md` entry: conditional "Gemini-brain" second Aider
   profile for Google-ecosystem tasks — not built, needs a concrete
   triggering task first.

## Live smoke test — todo #1365, real result, not a toy

Picked a real todo (tgw user can't run pytest — nix symlink permission
error) instead of a hello-world, per Dave's steer. Result:

- Aider/deepseek-v4-flash correctly diagnosed the root cause and proposed
  a plausible fix, cheaply (~$0.01–0.05/exchange).
- **But it never committed anything** — hit a real, reproduced-3x aider
  harness bug: under `--yes`, aider auto-adds any repo-root path the
  model's reply mentions as a chat file; #1365's fix is about a path
  literally named `nix` (a directory), so every correct explanation
  re-triggers a doomed auto-add attempt, burning all 3 reflections.
  **Filed as todo #1424.**
- Finished #1365 by hand instead. Turns out **no pytest-config option can
  fix it** — the PermissionError fires during pytest's initial directory
  scan (`entry.is_file()` on every top-level entry), before
  `collect_ignore`/`norecursedirs`/`--ignore` are ever consulted.
  Confirmed against `nix`, `flake.nix`, AND `flake.lock` (all three
  symlink into `/home/db/tgw-flake/`, unreadable by `tgw`). Real fix
  needs a filesystem permission change on Dave's home directory tree —
  not done unilaterally. **#1365 marked blocked, findings on the todo,
  needs Dave's call on the approach** (widen tgw-group access vs.
  re-point the symlinks).
- Cleanup hit **todo #1361** live (tgw-owned `.pytest_cache` blocking
  `git worktree remove`) — confirmed the bug firsthand, noted on the
  todo, worked around with `sudo -u tgw rm -rf .pytest_cache` first.

Dave's read (2026-07-15): process validated end-to-end; the one failure
was aider's own quirk, not the tier being unreliable — "if aider had been
entirely unsuccessful it would just cost your management tokens and
$0.000002." Bias going forward: default to routing XS/S busywork through
this tier. See memory `project-aider-deepseek-tier-validated.md`.

## State — nothing committed

All of #1, #2 (env line), #3, #4 above are **uncommitted in the shared
checkout** at `/opt/TGW/src/trader-grims-warehouse` — per "commit only
when Dave asks." No worktrees left open (the #1365 smoke-test worktree/
branch was cleaned up after use, nothing to stitch).

## Next step

Dave is stepping away ("on a budget for a bit... call you later"). When
resumed:
1. Dave reviews/commits the uncommitted aider-tier changes (or asks
   Claude to commit).
2. Decide #1365's real fix (permission change vs. symlink re-point) —
   Dave's call, not yet made.
3. #1424 (aider auto-add-on-mention bug) is open, not investigated
   further — low priority, narrow trigger condition.
4. Separately, unrelated to this thread: the field-set fix
   (#1415/#1418/#1416/#1417) is still awaiting Dave's own review pass —
   see `project-fieldset-review-sequence.md` — do not conflate the two
   threads on resume.
