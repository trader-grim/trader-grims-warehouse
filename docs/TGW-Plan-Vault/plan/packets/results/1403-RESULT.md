# Result: 1403 truncated-image-log-notify-defer
Status: done
Todo: #1403   PP: PP-DATAINTEGRITY-001

Files touched:
- src/tgw/workers/ebay_draft.py — `_encode_resized()` now catches `OSError`
  from `Image.open()`/`thumbnail()`/`.save()` and returns `None` instead of
  propagating; `_aspect_fill_photos()` gained an optional `sku=`/`config=`
  keyword pair and now pre-screens candidate photos with a cheap
  `Image.open().load()` readability check, filtering out unreadable/corrupt
  files and (when sku/config are supplied) recording a durable
  `pipeline_error` finding via the existing `fence_patch_item` mechanism
  (code `photo_files_readable`, source `ebay_draft`). The `handle()` call
  site now passes `sku=`/`config=self.config` and defensively skips any
  `None` result from `_encode_resized()` as a second line of defense.
- tests/test_ebay_draft_corrupt_photo.py (new) — unit tests for the catch,
  the mixed-good/corrupt filtering + finding persistence, the all-good
  no-op case, and the no-sku/config (pre-#1403 call site) fallback.
- tests/test_ebay_draft_aspect_photos.py — `_touch()` helper now writes a
  genuinely decodable 1x1 JPEG (was a bare header stub) since the new
  readability pre-screen would otherwise (correctly) filter the stub files
  out and break these filename-selection tests.
- tests/test_ebay_draft_nonjson_truncation.py — `_aspect_fill_photos` mock
  lambda signature updated to accept `**kw` (new sku/config kwargs).

Live evidence:
- Full offline suite: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
  PYTHONPATH=<worktree>/src pytest -q` → `2215 passed, 1 skipped, 1
  warning` (zero regressions; confirmed running against the worktree copy
  via `tgw.workers.ebay_draft.__file__` path check before the run).
- New/targeted tests: `pytest -q tests/test_ebay_draft_corrupt_photo.py
  tests/test_ebay_draft_aspect_photos.py` → `11 passed`, including a
  genuinely truncated JPEG (built by saving a real image then cutting the
  byte stream mid-way — confirmed to raise `OSError: Truncated File Read`
  from bare `PIL.Image.open().load()` before wiring it into the worker
  test, matching the live-confirmed dead-letter class exactly).
- Live discoverability check (acceptance item 5): called the real
  `tgw.api._verify_item()` (unmodified — no code change needed there,
  it already generically surfaces any `pipeline_error` dict) against a doc
  carrying a `pipeline_error` block shaped exactly like what
  `_aspect_fill_photos()` now writes. Result:
  `{'rule': 'pipeline_error:photo_files_readable', 'sku': 'tgw1403test',
  'severity': 'warning', 'detail': '1 photo(s) unreadable/corrupt, skipped
  for vision aspect-fill (other readable photos/fields still used):
  tgw1403test_2.jpg: Truncated File Read'}` — confirms the new finding is
  queryable through the same catalog-verify path (and, transitively,
  `ops_digest`'s `by_rule` summary, which reads the catalog-verify
  sidecar) as PP-DATAINTEGRITY-001 leg 1's `photo_files_readable` rule
  (#1154), without adding a second/competing tracking mechanism.

Deviations from spec:
- The rule name that ends up in catalog-verify output is
  `pipeline_error:photo_files_readable` (the generic `pipeline_error:`
  prefix is added by `_verify_item`'s existing generic handler), not a
  bare `photo_files_readable` string identical to leg 1's own
  catalog-verify rule name. This is a deliberate, flagged choice: the two
  findings mean different things (leg 1 = proactive project-wide decode
  scan; this = reactive "a specific pipeline run couldn't use this photo")
  and reusing the *mechanism* (item-addressable `pipeline_error`,
  surfaced generically by the same code path) rather than literally
  emitting the same rule string felt truer to "reuse the pattern, don't
  build a parallel one" than forcing rule-name identity. Both are visible
  in the same catalog-verify/ops_digest surface. Flagging in case the
  reviewer wants exact rule-name parity instead.
- Point 4 of the spec ("check whether `tgw health` or `ops_digest.py` is
  the right existing surface to extend") required no extension: because
  the new finding rides the existing generic `pipeline_error` →
  `_verify_item` → catalog-verify → `ops_digest`'s `by_rule` sidecar
  read path unmodified, `ops_digest.py` itself needed zero code changes —
  it already surfaces this class of finding by construction. Noting this
  as a (non-)deviation since the packet anticipated possibly needing to
  extend one of those two surfaces and neither needed touching.

Out-of-scope findings filed: none — no new adjacent issues surfaced
during this packet; photo repair and legs 2/3 remain intentionally
untouched per the packet's own scope boundary.
