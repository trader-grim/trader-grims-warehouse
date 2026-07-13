# Result: 1275 catalog-location-tree-safety
Status: done
Todo: #1275   PP: PP-COHESION-001
Files touched:
- src/tgw/catalog.py (build_location_tree() link_dir construction now
  routes through config.location_dir())
- tests/test_catalog_location_tree_safety.py (new — 3 tests covering
  acceptance steps 1 and 2)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1275-catalog-location-tree-safety.md
  (breadcrumb)
- docs/TGW-Plan-Vault/plan/packets/results/1275-RESULT.md (this file)

Live evidence:
- `PYTHONPATH=/opt/TGW/var/worktrees/1275-catalog-location-tree-safety/src pytest -q`
  → `2058 passed, 1 skipped, 1 warning in 30.58s` (2055 passed pre-existing +
  3 new tests, zero regressions). Confirmed via
  `python3 -c "import tgw.catalog as c; print(c.__file__)"` that the
  worktree's own copy was under test (resolved to
  `/opt/TGW/var/worktrees/1275-catalog-location-tree-safety/src/tgw/catalog.py`),
  not the shared checkout.
- Acceptance step 1 (valid location, e.g. `SAT013`): new test
  `test_valid_location_builds_link_no_problems` — link built at
  `location_tree_root/SAT013/<sku>`, resolves to the correct itemdata
  target, `out['ok'] is True`, `problems == []`. Identical behavior to
  pre-fix code path (location_dir() is a pure pass-through allow-list
  check for well-formed segments — todo #1274's own live verification
  already confirmed this against real production location values).
- Acceptance step 2 (`location = "../../../tmp/evil"`): new test
  `test_malicious_location_rejected_not_escaped` — `out['ok'] is False`,
  `problems` contains an `"unsafe location for sku ..."` entry, no
  exception raised, `links_built == 0`, and the escape target
  (`tmp_path/tmp/evil`) confirmed NOT created.
- Acceptance step 2b (malicious row doesn't halt the batch): new test
  `test_malicious_row_does_not_block_remaining_rows` — a malicious row
  followed by a valid row (`SAT014`) still produces
  `links_built == 1` for the valid row; the malicious row is skipped via
  `continue`, not a crash.
- Acceptance step 3 (full offline suite, zero regressions): confirmed
  above, 2058/2059 passed (1 pre-existing unrelated skip).

Deviations from spec: none. Followed the packet's exact replacement
(try/except ValueError → problems.append + continue) verbatim.

**Merge-path note (flagged per task instructions):** todo #1274
(`config.location_dir()` hardening) was NOT yet merged into `main` at
worktree creation time — it existed only on branch
`todo/1274-config-path-safety-validation`. Rather than duplicating the
hardened logic or importing across an unmerged branch, this worktree
fast-forward-merged `todo/1274-config-path-safety-validation` into the
`todo/1275-catalog-location-tree-safety` branch before making any edits
(`git merge --no-edit todo/1274-config-path-safety-validation`, fast-forward,
no conflicts — this branch's base was identical to #1274's base). This
means the #1275 branch currently contains #1274's commit
(`ceb0f09 fix(#1274): sku_dir()/location_dir() reject path-traversal &
absolute-override input`) as an ancestor. Whoever stitches these two
branches to main should be aware #1274's commit will already be present
once #1275 merges — no double-apply expected (git will recognize the
common history), but flagging so the stitch step doesn't need to
re-derive this.

Out-of-scope findings filed: none — `sku_migration.py`'s `rename_sku()`
(todo #1284) and `location_dir()` itself were confirmed out of scope per
the packet and left untouched.
