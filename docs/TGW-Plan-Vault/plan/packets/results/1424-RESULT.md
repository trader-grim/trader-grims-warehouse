# Result: 1424 aider-directory-autoadd
Status: done
Todo: #1424   PP: PP-HERMES-EA-001

## Files touched
- `.aiderignore` (new, tracked in git — 59 lines, adds `/nix`, `/flake.nix`, `/flake.lock` plus general repo-map noise filters)
- `.gitignore` (add `!.aiderignore` exception so it's no longer accidentally excluded by the `.aider*` glob, and is present in every fresh `git worktree add`)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1424-aider-directory-autoadd.md` (breadcrumb, prior session)
- `docs/TGW-Plan-Vault/plan/packets/results/1424-RESULT.md` (this file)

(All three code/config changes were already committed as `51ca875` by a prior
session that hit an account session-limit mid-run before writing the result
manifest. This session resumed the existing worktree/branch, independently
re-verified the fix live against the actually-installed aider binary — the
prior session's evidence was against a `pip download`'d 0.83.1 source tree,
since no live `aider` binary was reachable in that sandbox — and wrote this
manifest. No further code changes were needed.)

## Root cause
`aider`'s `--yes` mention-auto-add scan
(`Coder.check_for_file_mentions` → `Coder.get_file_mentions` →
`Coder.get_addable_relative_files` → `GitRepo.get_tracked_files()`) treats
any git blob-type tree entry as an addable candidate file — and git stores
symlinks as blobs (mode 120000), identically to regular files. `nix`,
`flake.nix`, and `flake.lock` at the repo root are git-tracked symlinks into
`/home/db/tgw-flake/*` (todo #1365's design), unreadable by `tgw` and, for
`nix` specifically, resolving to a directory. Any `--yes` reply that
mentions the word "nix" (unavoidable for any task whose fix subject is that
path, e.g. todo #1365 itself) gets it silently auto-added; the subsequent
read fails (`IsADirectoryError` for `nix`, `PermissionError` for the other
two under `tgw`), the edit can't apply, and aider's reflection-retry loop
burns its hardcoded 3-attempt cap without ever landing the fix.

`GitRepo.get_tracked_files()` already filters candidates through
`ignored_file()`, which consults `.aiderignore` (aider's own real,
documented mechanism — `--aiderignore`, default `.aiderignore` at git root,
wired automatically by `aider/main.py` with no `aider_mcp_server.py` code
change needed). The applied fix is option (a) from the packet: use the real
existing config mechanism, not a bridge-code pre-filter.

The second half of the bug: `.aiderignore` existed on disk in the shared
checkout already (from earlier Aider-tuning work, todo referenced inline in
the file) but was **untracked** in git (excluded by `.gitignore`'s `.aider*`
glob with no exception carved for it). `git worktree add` — the isolation
path every `aider_run_task` call with a `task_slug` uses (mandatory per
PP-HERMES-EA-001) — only materializes tracked files, so every fresh worktree
silently got a checkout with NO `.aiderignore` at all, i.e. the protection
only existed by accident in whichever one checkout a human had hand-edited
it in. Fixed by tracking `.aiderignore` and carving the `!.aiderignore`
exception in `.gitignore`.

## Pre-flight (invariant C11)
Confirmed no aider flag exists to globally disable mention-triggered
auto-add — checked live `aider --help` (installed `aider-chat==0.86.2`,
`/home/db/.local/bin/aider`) for every add/mention/detect-related flag;
the only such toggle is `--detect-urls`/`--no-detect-urls` (URL detection,
unrelated). `--aiderignore` (a real, documented flag, default
`.aiderignore` at git root) is the correct, narrower mechanism and is what
was used.

## Live evidence (this session's independent re-verification)
All commands run against the actually-installed `aider-chat==0.86.2`
(`/home/db/.local/share/pipx/venvs/aider-chat/`), not a downloaded source
tree, from this worktree
(`/opt/TGW/var/worktrees/1424-aider-directory-autoadd`):

1. **`GitRepo.get_tracked_files()` live against this repo:**
   - Without `aider_ignore_file` wired: `nix`, `flake.nix`, `flake.lock` all
     `True` (addable) out of 1477 tracked files.
   - With `aider_ignore_file=".aiderignore"` (matches `aider/main.py`'s
     actual default resolution — `os.path.join(git_root, ".aiderignore")`):
     all three `False` (excluded), 1462 tracked files remain.

2. **Full `Coder.check_for_file_mentions()` reproduction** in a scratch repo
   (`/tmp/.../aider-repro`) built to mirror the exact structure (`nix` →
   symlink to a real directory, tracked in git, alongside a real file
   `app.py` already in chat), driven through real `aider.coders.base_coder.Coder`
   + `aider.repo.GitRepo` + `aider.io.InputOutput(yes=True)` objects (no
   live LLM call needed — the bug is in the local file-mention scan, not the
   model call):
   - **Before fix** (no `.aiderignore`): `check_for_file_mentions("Let's
     look at the nix directory contents to fix this.")` →
     `"I added these files to the chat: nix"`; `nix` symlink resolved and
     added, `get_inchat_relative_files()` → `['app.py', 'target_dir']`.
     Confirmed follow-on failure: `open(abs_path)` on the added path raises
     `IsADirectoryError: [Errno 21] Is a directory` — the exact error class
     named in the todo.
   - **After fix** (`.aiderignore` containing `/nix`): same call →
     `check_for_file_mentions()` returns `None` (nothing added),
     `get_inchat_relative_files()` → `['app.py']` only. No directory ever
     reaches the read path; no reflection-loop trigger.

3. **Worktree materialization check:** `git worktree add` from a fresh
   temp path off this branch's `HEAD` produces a checkout with
   `.aiderignore` present (1901 bytes, matches tracked blob) — confirming
   the fix actually reaches the isolation path `aider_run_task` uses for
   every `task_slug` run, not just the one hand-edited checkout.

## Deviations from spec
- The packet's pre-flight step asked to run `aider --help | grep -i add`
  against "the current installed version" — the prior session (interrupted
  by account session-limit) had no live `aider` binary reachable and
  substituted a `pip download`'d 0.83.1 source tree, noting this as a
  deviation in its breadcrumb. This session found `aider` IS live-reachable
  here (`aider-chat==0.86.2`, differs from the 0.83.1 assumed) and reran
  every verification step from scratch against the real binary/library,
  superseding the earlier substitute evidence. No behavioral difference
  found between the two versions for this bug (`get_tracked_files()` →
  `ignored_file()` → `.aiderignore` chain is unchanged).
- No `aider_mcp_server.py` code change was needed (matches packet's
  preference for option (a) over (b)); flagged here only because it's
  worth being explicit that the "fix" is entirely a `.aiderignore` +
  `.gitignore` change, not a Python change, in case that's unexpected.

## Out-of-scope findings filed
none — the untracked-`.aiderignore` / worktree-isolation gap was the direct
mechanism keeping this exact bug alive across attempts, so fixing it was
in-scope, not adjacent.
