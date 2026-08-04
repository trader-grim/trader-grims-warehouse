# In progress: todo #1361 pytest cache ownership fix

Reproduced live: running pytest as `tgw` in a worktree creates a
tgw-owned `.pytest_cache/` inside the worktree tree; a later `db`-owned
`rm -rf` on that worktree then fails needing `sudo -u tgw rm -rf` first.
Fixed by pointing `cache_dir` (pyproject.toml `[tool.pytest.ini_options]`)
at `$HOME/.cache/tgw-pytest` — pytest expands `$HOME` per invoking user
(confirmed via `_pytest.pathlib.resolve_from_str`), so tgw and db each
get their own cache directory entirely outside any worktree. Verified
live both directions (tgw run, db run, no `.pytest_cache` in worktree
either way; db `rm -rf` succeeds without sudo). Result manifest written
to `docs/TGW-Plan-Vault/plan/packets/results/1361-RESULT.md`. Done, ready
for stitch/review.
