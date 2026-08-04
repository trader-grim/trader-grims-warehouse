# Packet: tools/photo_history_recovery.py's recover_item() copies atomically

Todo: #1383   PP: PP-COHESION-001   Track: mechanical/atomicity (continues
the graduated sequence — #1305/#1307/#1315 already merged clean this cycle;
no pairing gate needed, stitch immediately after this clears)

## Context budget (ALL the model may load)
This packet + `tools/photo_history_recovery.py` (the whole file) +
`src/tgw/workers/photo_history_recovery.py` lines 87-115 (`ensure_copy()` —
read-only, this is the reference fix pattern from #1307, already merged) +
this todo's existing test file if one exists. Nothing else.

## Verified live before this packet was written
- `tools/photo_history_recovery.py:180` — `recover_item()` calls
  `shutil.copy2(src, dest)` directly onto the live destination path. A
  reader (thumbnail_gen, ebay_upload, catalog_rebuild, or a human) could
  observe a partial/corrupt photo file if the process is interrupted
  mid-copy — same gap #1307 fixed in the sibling
  `src/tgw/workers/photo_history_recovery.py::ensure_copy()`.
- `src/tgw/workers/photo_history_recovery.py:105-112` (the already-merged
  #1307 fix, verify this is still the exact shape before copying it):
  ```python
  tmp_dst = dst.with_name(dst.name + f'.tmp{os.getpid()}')
  try:
      shutil.copy2(src, tmp_dst)
      os.replace(tmp_dst, dst)
  except BaseException:
      try:
          tmp_dst.unlink(missing_ok=True)
      except Exception:
          pass
      raise
  ```
- `tools/photo_history_recovery.py` already `import os` (line 23) and
  `import shutil` (line 25) — no new imports needed.
- This is a one-shot legacy recovery script (out of scope for #1307's
  single-file fix per that todo's own text), invoked manually by an
  operator, not a running worker — same fence/atomicity gap applies
  regardless of how it's invoked.

## Spec
In `tools/photo_history_recovery.py::recover_item()`, replace the direct
`shutil.copy2(src, dest)` (line 180, inside the `if write:` block) with the
same temp-file + `os.replace` pattern #1307 used, adapted to this
function's existing try/except structure (it already catches `Exception`
around the copy and appends an `'error'` row — keep that error-handling
shape, only change how the copy itself is performed):

```python
if write:
    tmp_dest = dest.with_name(dest.name + f'.tmp{os.getpid()}')
    try:
        shutil.copy2(src, tmp_dest)
        os.replace(tmp_dest, dest)
        action = 'copied'
    except Exception as e:
        try:
            tmp_dest.unlink(missing_ok=True)
        except Exception:
            pass
        rows.append({'sku': sku, 'ref': ref, 'action': 'error',
                     'source': str(src), 'dest': str(dest),
                     'error': str(e)})
        log.error('SKU %s: copy failed %s -> %s: %s', sku, src, dest, e)
        continue
else:
    action = 'would_copy'
```

## Out of scope
- `src/tgw/workers/photo_history_recovery.py` — already fixed by #1307,
  do not re-touch.
- Any other copy/write site in this file (e.g. report-writing) — only the
  photo-copy in `recover_item()`.
- Adding a dry-run gate, changing CLI flags, or any other behavior change —
  this packet is atomicity-only, matching #1307's scope exactly.

## Dataset
None — this only changes how the photo file is written to disk (atomic vs.
non-atomic); no new data captured or discarded, no format change.

## Acceptance (live)
1. Run the existing test suite for this tool if one exists
   (`tests/test_photo_history_recovery*.py` — check both possible names,
   worker and tools variants may be tested together or separately).
2. Manually verify: call `recover_item()` with `write=True` against a real
   temp directory + a small fixture photo file — confirm the final file at
   `dest` matches the source exactly (same bytes) and no `.tmp<pid>` file
   is left behind after a successful run.
3. Simulate a failure mid-copy (e.g. monkeypatch `shutil.copy2` to raise)
   — confirm no partial file is left at `dest`, the `.tmp<pid>` file is
   cleaned up, and an `'error'` row is still appended to `rows` (same
   contract as before this fix).
4. Run the full offline suite — zero regressions.

## Quota/risk
None — no new API calls, local filesystem atomicity fix only, applied to
a manually-invoked one-shot recovery script.
