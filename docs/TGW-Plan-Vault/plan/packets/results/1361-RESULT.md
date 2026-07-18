# Result: 1361 pytest-cache-ownership
Status: done
Todo: #1361   PP: PP-HERMES-EA-001

Files touched:
- pyproject.toml (added `cache_dir = "$HOME/.cache/tgw-pytest"` under `[tool.pytest.ini_options]`)

Live evidence:
- Pre-fix reproduction (before the edit, in this worktree): ran
  `sudo -u tgw ... pytest tests/ -k nonexistent_test_xyz --collect-only -q`
  → created `.pytest_cache/` inside the worktree, owned by `tgw:tgw`
  (`drwxr-xr-x+ 1 tgw tgw ... .pytest_cache`), confirming the ownership
  mismatch the todo describes.
- Post-fix, same tgw invocation: `.pytest_cache` is NOT created in the
  worktree; instead `/opt/TGW/.cache/tgw-pytest/` appears (tgw's own
  `$HOME`), confirmed via `ls -la .pytest_cache` → "No such file or
  directory" in the worktree, and `sudo -u tgw ls -la /opt/TGW/.cache/tgw-pytest`
  showing the cache contents (CACHEDIR.TAG, v/, etc.) owned by tgw.
- Post-fix, full suite run as `db` (no sudo, no env override) in the same
  worktree: `pytest tests/ -q -x` → "2470 passed, 1 skipped, 1 warning in
  41.14s". No `.pytest_cache` appeared in the worktree; cache landed in
  `/home/db/.cache/tgw-pytest/` instead (owned by db).
- Round-trip cleanup proof: copied the worktree to a scratch dir, ran
  pytest as `tgw` there (still produced no in-tree `.pytest_cache`), then
  ran `rm -rf` on the whole scratch copy as plain `db` (no sudo) →
  succeeded ("rm -rf succeeded without sudo").
- Mechanism: pytest's cache-dir resolution
  (`_pytest.pathlib.resolve_from_str`) calls `os.path.expandvars()` on the
  configured `cache_dir` before use, so `$HOME` expands to the invoking
  user's actual home at pytest-run time — tgw and db each land in their
  own, separate, already-writable-by-them cache directory, never inside
  the worktree.

Deviations from spec: none — used fix option (a) as the packet
recommended ("prefer (a) if it doesn't conflict with existing pytest
config"); confirmed no prior `cache_dir` setting existed before adding
one.

Out-of-scope findings filed: none new. Note: while reproducing this live,
`pytest tests/` (run as `tgw`, without narrowing to the `tests/` dir via
an interactive shell path — i.e. the collection root itself) still hits
the PRE-EXISTING, already-tracked, already-blocked todo #1365 issue
(`PermissionError` scanning `flake.lock`/`flake.nix`/`nix` symlinks that
point into `/home/db/tgw-flake`, unreadable by `tgw`) when pytest scans
the repo root for conftest discovery before applying `testpaths`. That is
a separate, already-filed, Dave-gated issue (permission change to Dave's
home dir needed) — not touched here, and it does not block or interact
with this fix: the cache-ownership fix works regardless of whether that
separate collection error occurs (verified — the tgw-owned `.pytest_cache`
was created and confirmed empty of any in-worktree footprint even on
runs that hit the #1365 error).
