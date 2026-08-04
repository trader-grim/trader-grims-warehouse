# Result: #1303 ebay-upload-no-photos-finding

Status: done
Todo: #1303   PP: PP-COHESION-001 (invariant C11)

## What was wrong

`src/tgw/workers/ebay_upload.py::EbayUploadWorker.handle` — when `ordered_photos()`
found no photos on disk for a SKU, the code logged a warning + a `tgw_logging.log_event`
and then `return`ed, which the queue framework records as job SUCCEEDED. There was no
durable record anywhere on the item that anything was wrong: no field on the item JSON,
no catalog-verify detector. The item silently stalled in the pipeline forever, findable
only by grepping journald (which rots) — exactly the C11 anti-pattern already fixed once
for `ebay_stage.py`'s legacy-listing guard.

## Fix

**File:** `src/tgw/workers/ebay_upload.py`
- In the `if not photos:` guard, after the existing log/log_event calls, the worker now
  calls `fence_patch_item(self.config, sku, {'ebay_upload_blocked': {...}})` via the
  existing fenced write path (same helper already used elsewhere in this file), persisting:
  ```
  {"reason": "no_photos_on_disk", "detected_at": "<ISO8601 UTC>"}
  ```
  Field name chosen: **`ebay_upload_blocked`** — mirrors the naming convention of
  `ebay_stage.py`'s `legacy_listing_blocked` (`<worker>_<condition>_blocked`, a dict with
  `reason`/`detected_at`).
- Self-healing (matches the `legacy_listing_resolved` pattern that suppresses
  `legacy_listing_unrepaired`): on a subsequent FULL success (all photos uploaded, no
  errors, no quota block), the worker now clears the field back to `None` via
  `fence_patch_item` if it was previously set, so catalog-verify stops flagging an item
  once photos have actually been added back and uploaded.
- **Job status decision:** left as SUCCEEDED (plain `return`, no exception). Checked
  `ebay_stage.py`'s equivalent legacy-listing guard for consistency — it also persists
  the durable finding and then `return`s (SUCCEEDED) rather than raising, treating a
  recognized/recorded stall as a normal terminal outcome, not a retry-worthy transient
  failure. Matched that convention rather than inventing a new one (e.g. `HardFailure`
  would dead-letter and spam retries for a condition that's operator-repairable, not
  code-repairable, by adding photos to disk).

**File:** `src/tgw/api.py` (`_verify_item`, catalog-verify)
- Added a new detector rule immediately after the existing `legacy_listing_unrepaired`
  block, following the exact same shape:
  ```python
  upload_blocked = doc.get("ebay_upload_blocked")
  if upload_blocked:
      v("ebay_upload_no_photos_unrepaired", "critical", ...)
  ```
  Rule name: `ebay_upload_no_photos_unrepaired` (mirrors `legacy_listing_unrepaired`
  naming). Fires only while `ebay_upload_blocked` is truthy; suppressed once the worker
  clears it to `None` on a subsequent successful upload.

## Tests added

- `tests/test_ebay_upload_integrity.py`:
  - `test_no_photos_on_disk_persists_durable_finding` — item with no photos on disk;
    asserts `ebay_upload_blocked` is set with `reason='no_photos_on_disk'` and a
    `detected_at` timestamp after `handle()`.
  - `test_full_success_clears_prior_no_photos_finding` — item with a pre-existing
    `ebay_upload_blocked` finding; photos are added back and upload succeeds; asserts
    the field is cleared.
- `tests/test_catalog_verify.py`:
  - `test_ebay_upload_no_photos_blocked_is_critical` — item with `ebay_upload_blocked`
    set; asserts `ebay_upload_no_photos_unrepaired` appears in `_verify_item`'s
    violations.
  - `test_ebay_upload_no_photos_cleared_suppresses_rule` — item with
    `ebay_upload_blocked: None`; asserts the rule does NOT fire.

## Live evidence

Full offline `pytest -q` run, PYTHONPATH pinned to this worktree's `src/` (confirmed via
`tgw.workers.ebay_upload.__file__` resolving under
`/opt/TGW/var/worktrees/1303-ebay-upload-no-photos-finding/src/...` before running, per
the profile's mandatory PYTHONPATH-override check — the shared checkout's editable
install would otherwise silently shadow it). Also required `LD_LIBRARY_PATH` pointed at
a built zlib output from the Nix store (`.../zlib-1.3.2/lib`) because the venv's
`psycopg2` couldn't locate `libz.so.1` from a plain shell — flagged below as an
operational-friction finding.

```
2154 passed, 1 skipped, 1 warning in 51.79s
1 failed: tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter
```

The one failure is the pre-known, pre-existing full-suite-only flake tracked as
todo #1370 (shared quota-state pollution; passes in isolation, unrelated to this
change) — confirmed it is the ONLY failure, not touched.

Also ran just the two directly-relevant test files in isolation:
```
tests/test_ebay_upload_integrity.py tests/test_catalog_verify.py
90 passed in 26.25s
```

## Deviations from spec

None. Field name `ebay_upload_blocked` was a naming choice within the packet's
explicit "pick a name consistent with that convention, note your choice" allowance —
noted above.

## Out-of-scope findings filed

- todo #1374 (pp_ref PP-NIXOS-001): running pytest/python directly in this worktree's
  shell fails with `ImportError: libz.so.1: cannot open shared object file` from
  `psycopg2` unless `LD_LIBRARY_PATH` is manually pointed at a Nix-store zlib output
  (worked around here with
  `LD_LIBRARY_PATH=/nix/store/dbz6pb9g67kpgpl95k8d85kzpxm1c32p-zlib-1.3.2/lib`) — no
  wrapper script or devshell in this repo/worktree sets this up automatically, so any
  future coder in a fresh worktree will hit it.
