Status: cleared
Reviewer: Claude (runner-review)
Todo: #1303   PP: PP-COHESION-001
Checked: diff (`git diff 714de85 todo/1303-ebay-upload-no-photos-finding`)
against the todo brief's stated bug, scope (ebay_upload.py + api.py + 2
test files), result manifest completeness. api.py touch is in-scope —
explicitly requested (catalog-verify detector rule). Confirmed
`fence_patch_item` was already imported/used elsewhere in ebay_upload.py,
not a new fence dependency.
Summary: `ebay_upload_blocked` persisted on the no-photos guard hit
(reason + detected_at), self-healing clear (`None`) on the next full
upload success — correctly mirrors `legacy_listing_blocked`/
`legacy_listing_resolved`'s clear-on-repair pattern. Job status
deliberately left SUCCEEDED, matching `ebay_stage.py`'s equivalent guard
convention rather than inventing a new status — reasonable, avoids
retry/backoff churn for a state that's now durably tracked and
operator-visible instead. New `ebay_upload_no_photos_unrepaired`
catalog-verify rule (critical severity — reasonable, an item that can
never list without operator action is more severe than the warning-level
sku_collision rule) mirrors legacy_listing_unrepaired exactly. 4 new tests
cover persistence, clear-on-success, and both catalog-verify states.
Also filed todo #1374 (PP-NIXOS-001) for a real operational-friction
finding (psycopg2/libz.so.1 LD_LIBRARY_PATH gap in worktree pytest runs) —
correctly tagged with pp_ref per the standing rule, not left untagged.
Full suite green modulo the known #1370 flake. No triggers fired. Cleared
for stitch.
