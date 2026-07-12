# DONE — todo #1154: photo-integrity-mitigation leg 1

This todo bundled 4 sub-packets (per `ai-plans/photo-integrity-mitigation.md`).
Per the tgw-packet skill's "one packet per invocation, split rather than
stretch" rule, executed the highest-leverage one now and filed the rest.

## Shipped: `photo_files_readable` catalog-verify rule (leg 1 — DETECT)

- `_check_photo_readable()` — decodes a photo via PIL, cheap incremental
  via a (size, mtime) sidecar cache keyed by path (a rename/move is
  treated as fresh, never inherits a stale verdict).
- `_load_photo_decode_cache()` / `_save_photo_decode_cache()` — sidecar at
  `catalog_root/photo-decode-cache.json`, same fail-open-to-empty pattern
  as this codebase's other disk caches.
- Wired into `_verify_item()` as a new `photo_files_readable` critical
  violation — **opt-in** via `photo_decode_cache` param (`None` = skip
  entirely). This was a deliberate deviation from "always on": existing
  test fixtures use empty-byte `.jpg` files, which would legitimately fail
  a real decode and break dozens of unrelated existing tests. Opt-in
  keeps 100% backward compatibility while making the rule fully active
  for real runs.
- `cmd_catalog_verify(..., check_photos=True)` + new `tgw catalog-verify
  --check-photos` CLI flag turn it on for real scans.

## NOT wired into any automatic schedule (flagged, not decided)

The plan doc is explicit that a full-fleet decode is heavy and belongs on
a1131 over the ro NFS mount, not tgw-prod's nightly timer. Didn't make
that call unilaterally — filed as **todo #1268**.

## Filed as separate todos (not attempted this pass)

- **#1266** — leg 2: verify-after-copy sha256 helper for bulk-copy paths.
- **#1267** — leg 3: intake decode-verify (open question on full-load vs
  header-only verify still needs a call).
- Leg 4 (recovery) needs physical archive-drive access — already tracked
  via `PP-DRIVE-INDEX-plan.md` Phase 1 in the master plan, no new todo
  needed.

## Live evidence

- New tests in `tests/test_catalog_verify.py`: off-by-default (no
  regression), flags a genuinely corrupt file, passes a real PIL-generated
  JPEG, cache skips PIL entirely on an unchanged file, cache re-decodes
  when the file changes, full `cmd_catalog_verify(check_photos=True)`
  end-to-end (writes the cache file, reports the violation).
- `pytest -q` — 2065 passed, 1 skipped (was 2059 — 6 new tests).
- `ruff check` — clean.
