# Review: 1274 config-path-safety-validation
Status: cleared — NOT stitched yet (cadence rule: first run of the
SECURITY track, holding for a second clean run before stitching)
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec (exact `_safe_segment()` implementation, wired into both
`sku_dir()`/`location_dir()` as specced, `sku_json()`/`sku_exists()`
correctly left untouched), Out-of-scope (only config.py + its new test
file — #1273/#1275/#1284 correctly NOT touched), invariants.md (this IS
the invariant being hardened — path containment for the ItemData/location
tree root), Live evidence (module load confirmed from worktree, both
escape vectors confirmed blocked, 15 real production values round-tripped
with zero rejections, full suite green with 9 new tests). No deviations,
no out-of-control triggers fired.

This is the root fix for a 4-item cluster (#1274/#1273/#1275/#1284).
Next: verification pass against each dependent todo's own reported
scenario, using this worktree's fixed config.py — not automatic new
coding tasks for each, per the shared-root triage rule being proposed to
Dave this session.

Ready to stitch pending the cadence rule's second clean run.
