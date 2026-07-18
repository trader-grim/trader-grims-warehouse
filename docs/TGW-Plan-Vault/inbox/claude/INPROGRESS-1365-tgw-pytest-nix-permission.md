# In progress: todo #1365 — tgw pytest / nix-symlink permission

Working in worktree `/opt/TGW/var/worktrees/1365-tgw-pytest-nix-permission`
(branch `todo/1365-tgw-pytest-nix-permission`, off `catio-nix-0.0.1-alpha`).

Reproduced the `PermissionError` live as `tgw` and root-caused it precisely:
pytest's `Session.collect()` does `os.scandir(rootdir)` + `entry.is_file()`
on every direntry in the repo root — including the `nix`, `flake.nix`, and
`flake.lock` symlinks that point into `/home/db/tgw-flake/...` — BEFORE any
`norecursedirs`/`collect_ignore`/`--ignore` filtering is consulted. Since
`/home/db` is `700 db:users` with no ACL granting `tgw` traversal, `stat()`
through it raises `PermissionError` and kills the whole collection phase.
No pytest-level config change can prevent this (tried `norecursedirs`,
`collect_ignore` in conftest.py, explicit `--ignore`, `--rootdir` override,
invoking from `tests/` directly — all hit the same wall, confirmed via
live testing, not just code-reading).

This is a filesystem-permission architecture issue, not a pytest-config
issue — out of tgw-coder's authority per this packet's own instructions.
Writing up the exact minimal-scope fix in the result manifest
(`docs/TGW-Plan-Vault/plan/packets/results/1365-RESULT.md`) rather than
applying it. Not marking #1365 done — leaving status `blocked` for
Dave/Tigwa/nix-flake-maintainer to action the ACL change.
