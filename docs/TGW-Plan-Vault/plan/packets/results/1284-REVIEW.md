# Review: 1284 sku-migration-location-safety
Status: cleared — stitched in `73daae2` ("SECURITY batch, closes
path-safety cluster"). RECONSTRUCTED RETROACTIVELY 2026-07-13 from
`1284-RESULT.md` evidence + the merge commit; no contemporaneous
REVIEW.md was written (compliance gap — same concurrent-batch pattern as
#1280/#1282/#1288/#1291/#1297).
Reviewer: Claude (retroactive reconstruction)

Checked (from RESULT.md's own reported evidence): `rename_sku()`'s
location-symlink construction now routes through the hardened
`config.location_dir()` from #1274/#1275. New test file
`test_sku_migration_location_safety.py` covers both acceptance scenarios
directly: a normal location value ("SAT013") updates the symlink with no
warning; a malicious value (`"../../../tmp/evil"`) is rejected — no
escape outside `location_tree_root`, an "unsafe location"/"rename_sku"
warning is logged, and the already-completed SKU rename does NOT roll
back (matches spec — bad location degrades to a recorded warning, not an
abort). Full offline suite: 2110 passed, 1 skipped. Confirmed testing
against the worktree's own module before trusting results.

Deviation reported and accepted (Prime Directive 3): the packet's
invocation said to branch from `main` and that #1274 was already merged
there; pre-flight found this false on literal `main` (HEAD unhardened) —
#1274/#1275 only exist on `catio-nix-0.0.1-alpha`, the actual active
trunk. Worktree/branch rebuilt from `catio-nix-0.0.1-alpha` instead,
flagged rather than silently substituted. No out-of-scope findings filed.

Stitched.
