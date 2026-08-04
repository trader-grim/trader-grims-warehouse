# Review: 1275 catalog-location-tree-safety
Status: cleared
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec (exact try/except ValueError -> problems.append + continue,
routes through the real #1274-hardened location_dir(), not a duplicate),
Out-of-scope (only catalog.py + its new test file, location_dir() and
sku_migration.py correctly left untouched), invariants.md (this closes a
genuine path-traversal gap — the fix correctly degrades to a recorded
problem rather than a crash or silent escape), Live evidence (module load
confirmed from worktree, all 3 acceptance cases + a bonus
doesn't-block-the-batch case verified, full suite green). Merge-path
handling (fast-forwarding #1274's branch in before editing, since #1274
wasn't merged to main yet) was correct and clearly flagged, not a
deviation. No out-of-control triggers fired.

Run 2 of 2 for the SECURITY track — second consecutive clean run (after
#1274). Per the cadence rule: stitch both together now, SECURITY track
graduates to concurrent execution starting with #1284 (same fix shape,
already queued).

Ready to stitch.
