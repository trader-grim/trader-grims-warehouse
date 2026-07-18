# Result: 1365 tgw-pytest-nix-permission
Status: blocked
Todo: #1365   PP: PP-NIXOS-001

## Root cause (confirmed live, not just code-read)

The repo root contains three symlinks that point into `db`'s home directory
(deliberate, settled architecture — commit `79c152f`, "symlink nix/
flake.nix flake.lock → ~/tgw-flake", 2026-06-25):

```
nix        -> /home/db/tgw-flake/nix
flake.nix  -> /home/db/tgw-flake/flake.nix
flake.lock -> /home/db/tgw-flake/flake.lock
```

`/home/db` is `700 db:users` with no ACL. `tgw` (uid 900) is not in group
`users` and has no ACL entry, so `tgw` cannot traverse (`stat()` through)
`/home/db` at all.

Reproduced as `tgw` in this worktree:

```
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/python3.12 -m pytest -q
...
E   PermissionError: [Errno 13] Permission denied: '.../nix'
1 error in 0.10s
```

**This is not a fixable-via-pytest-config problem.** pytest's `Session.collect()`
(`_pytest/main.py`) does `os.scandir(rootdir)` and calls `entry.is_file()` on
*every* direntry in the collection root — including `nix`, `flake.nix`,
`flake.lock` — to classify each entry, **before** `norecursedirs`,
`collect_ignore`/`collect_ignore_glob` (conftest.py), or `--ignore` are ever
consulted. `entry.is_file()` on the `nix` symlink requires `stat()`-ing
through `/home/db`, which raises `PermissionError` immediately and aborts
the entire collection phase (not just that one entry).

Live-tested and ruled out, all producing the identical traceback:
- `norecursedirs = ["nix", ...]` (already present in `pyproject.toml`) — does not help; confirmed present but insufficient.
- `conftest.py` with `collect_ignore = ["nix"]` — found a stray uncommitted attempt at this exact fix already sitting in `/opt/TGW/var/worktrees/1365-tgw-pytest-nix-permission/` (dated 2026-07-15, pre-dating this todo) — confirms someone already tried this path and it didn't work either.
- Explicit `--ignore=nix` CLI flag — same crash (happens before ignore-filtering).
- `--rootdir=tests` override — same crash (Session still scans repo root because `pyproject.toml`'s `[tool.pytest.ini_options]` lives there).
- Invoking `pytest .` from inside `tests/` directly — same crash (pytest walks up to find `pyproject.toml`, uses repo root as the scan root regardless of cwd).
- Additionally: `flake.nix`/`flake.lock` are *file* symlinks, not directories — `norecursedirs`/`collect_ignore` for directories wouldn't even apply to them if the scandir crash weren't already fatal first.

**The only real fixes are filesystem-permission changes, out of tgw-coder's
authority** (this packet explicitly calls this out as a stop condition):

- Minimal-scope recommended fix: `setfacl -m u:tgw:--x /home/db` — grants
  `tgw` *execute-only* (traverse, no read/list) on `/home/db`. Verified this
  is sufficient and nothing further: `/home/db/tgw-flake` is already
  `775` (`o+rx`), so once traversal past `/home/db` itself is granted,
  `stat()` on `flake.nix`/`flake.lock`/`nix` succeeds (stat only needs `x`
  on parent directories, not permission on the target file/dir itself).
  `norecursedirs=["nix"]` already correctly prevents recursion *into*
  `nix/` once classification succeeds, so no further ACL is needed inside
  `tgw-flake/nix` (which is `2750`, no `other` access, and doesn't need
  to change).
- Alternative (rejected as out-of-scope for this packet, and contradicts
  the deliberate 2026-06-25 design decision to keep the canonical flake at
  `~/tgw-flake`): relocate/remove the symlinks so they don't point into
  another user's home directory. Not attempted — this is a settled
  architecture decision (`nix/CLAUDE-NIX.md` / commit `79c152f`), not a
  bug, and relitigating it is outside a coder packet's authority per
  CLAUDE.md's "Settled architecture (do not relitigate)" section.

Filed the ACL recommendation as todo **#1534** (`--pp PP-NIXOS-001`) for a
sysadmin/nix-flake-maintainer-authority actor to review and apply — not
applied here.

## Files touched
None (repo-scoped code/config changes were deliberately not made — the
fix is outside repo scope). Only the result manifest and inbox breadcrumb
were added on this branch.

## Live evidence
- `tgw` user, current state, full suite: `PermissionError: [Errno 13]
  Permission denied: '.../nix'`, collection aborts, `1 error in 0.10s`
  (reproduced both from `/opt/TGW/src/trader-grims-warehouse` and fresh
  in this worktree, with/without explicit `tests/` arg, with/without
  `--rootdir` override).
- `db` user, same worktree, full suite: `2570 passed, 1 skipped, 1
  warning in 184.80s (0:03:04)` — confirms no regression, `db` unaffected
  (control).
- `getfacl`/`stat` confirm `/home/db` is `700 db:users` with zero ACL
  entries; `tgw`'s groups are `tgw,video` only (`id tgw` — not in
  `users`).

## Deviations from spec
None — the packet itself anticipated this outcome ("If the real fix
requires a filesystem permission change outside this repo... that's a
system-administration action — flag it clearly rather than attempting it
yourself in a worktree-scoped packet"). Followed that instruction exactly:
investigated to confirmed root cause, ruled out all pytest-config-level
options with live evidence, did not apply the ACL fix myself, filed it as
a separate todo instead of silently doing it or silently leaving it
unaddressed.

## Out-of-scope findings filed
- Todo **#1534** (PP-NIXOS-001): `setfacl -m u:tgw:--x /home/db` — the
  precise, minimal-scope fix, for a sysadmin/root-authority actor to
  review and apply.
