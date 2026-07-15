# In progress: todo #1403 (truncated/corrupt image log+notify+defer, ebay_draft)

Branch `todo/1403-truncated-image-log-notify-defer`, worktree
`/opt/TGW/var/worktrees/1403-truncated-image-log-notify-defer`.

Wrapped the uncaught `Image.open()`/`OSError` in `_encode_resized()` and
added a readability pre-screen in `_aspect_fill_photos()` (both in
`src/tgw/workers/ebay_draft.py`), so a truncated/corrupt photo is skipped
for the vision aspect-fill call instead of dead-lettering the whole
`ebay_draft` job. Skips are recorded via the existing generic
`pipeline_error` mechanism (`fence_patch_item`, code
`photo_files_readable`) so they surface through the same catalog-verify /
ops_digest path as PP-DATAINTEGRITY-001 leg 1's rule (#1154) — confirmed
live against `api._verify_item()`. No photo repair, no legs 2/3 work
(out of scope). Tests added in `tests/test_ebay_draft_corrupt_photo.py`;
existing `tests/test_ebay_draft_aspect_photos.py` and
`tests/test_ebay_draft_nonjson_truncation.py` fixture/mock signatures
updated to match. Full offline suite passes (2215 passed, 1 skipped).
Result manifest written, branch committed. Not merged/stitched — that's
the reviewer's/stitcher's call.
