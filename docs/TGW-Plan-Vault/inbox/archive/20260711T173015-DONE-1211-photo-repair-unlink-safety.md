# DONE — todo #1211 (audit#1143)

`scripts/photo_repair_iss013.py`'s cleanup step (removes a wrongly-created
`<SKU>.jpg` leftover from a prior repair attempt) unlinked the file based
only on a byte-size match against the found original, with no content-hash
check and no archive-before-delete. A coincidental same-size,
different-content file would have been permanently destroyed (violates E5),
unlike the alt-rename step right above it in the same function, which
already does both a content-hash check and archive-before-manipulation.

## Fix
- Added a content-hash confirmation (same `first_n_bytes`/`CONTENT_CHECK_BYTES`
  comparison already used for the alt-rename) before deleting the
  wrongly-created `<SKU>.jpg` — a size match alone no longer authorizes
  deletion.
- Added archive-before-delete: copies the file to `history/ItemData/<SKU>/`
  (same `copy2`-to-history convention as the alt-rename step, skipped if
  already archived) before unlinking.
- If either check fails, the file is left in place with a warning logged
  for manual review, rather than silently deleted.
- Updated the module docstring's SAFETY DESIGN section to describe the new
  guard.

## Tests
New file `tests/test_photo_repair_iss013.py` (script had no prior test
coverage):
- wrong-primary removed + archived when size AND content match
- wrong-primary **left in place** when size matches but content differs
  (the exact failure scenario in the bug report)
- wrong-primary left in place when size differs (existing behavior, still covered)
- no-op when no wrongly-created file is present

`pytest -q tests/test_photo_repair_iss013.py`: 4/4 pass. Full suite: 1954
passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only, no files touched)
Ran `find_affected_skus()` + `find_original_photo()` + the same size/content
checks directly against real `/opt/TGW/data/ItemData` as the `tgw` user:
- 30 SKUs currently have the misnamed `<SKU>-alt.jpg` this script targets.
- None of those 30 currently have a leftover wrongly-created `<SKU>.jpg` —
  this cleanup branch is a dormant safety net for a rare leftover-attempt
  scenario, not something with active corrective effect on today's data (no
  live risk was sitting unaddressed).
- A broader scan found 103 unrelated items whose real, legitimate primary
  photo happens to be named `<SKU>.jpg` — confirmed these are outside the
  30 affected SKUs and untouched by this code path, so the fix's blast
  radius is correctly scoped to only the repair-cleanup branch.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no live files modified (script not run with `--execute`).
