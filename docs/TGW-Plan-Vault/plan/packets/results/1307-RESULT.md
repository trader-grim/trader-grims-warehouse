# Result: 1307 photo-history-atomic-copy
Status: done
Todo: #1307   PP: PP-COHESION-001

Files touched:
- `src/tgw/workers/photo_history_recovery.py` — `ensure_copy()`: photo
  copies now write to a temp file (`<dst>.tmp<pid>`) in the same
  destination directory, then `os.replace()` onto the final path, with
  temp-file cleanup on any exception during copy/replace.
- `tests/test_photo_history_recovery_dry_run.py` — added 3 tests:
  no leftover temp file on success, `os.replace` called with a fully-
  written temp path and a not-yet-existing destination (proves the write
  happens off to the side, not in-place), and temp-file cleanup on a
  simulated copy failure.

Live evidence:
- `PYTHONPATH=<worktree>/src` sanity check confirmed
  `tgw.workers.photo_history_recovery.__file__` resolves under the
  worktree, not the shared checkout, before any test run.
- `pytest -q tests/test_photo_history_recovery_dry_run.py` → 8 passed
  (5 pre-existing dry-run-gate tests unaffected + 3 new atomicity tests).
- Full offline suite: `pytest -q` (LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH,
  PYTHONPATH=<worktree>/src) → **2049 passed, 1 skipped in 166.53s**,
  exit code 0. No regressions from the change.
- Thermal status checked before/after the full-suite run: `NORMAL` both
  times (load ~59-62, well under any threshold).

Deviations from spec:
- The finding text describes the bug as "bypassing the tgw-api fence and
  the atomic-write (temp file + os.replace) pattern used elsewhere."
  I implemented the **atomic-write** half exactly as specified (mirrors
  `items.atomic_write_json()`/`context.py`'s `_write_state`/
  `_atomic_symlink` temp+`os.replace` pattern — invariants.md A1). The
  **"tgw-api fence"** half is not literally actionable as a code change:
  confirmed live (grep of `src/tgw/api.py` and `http_server.py`) that
  **no tgw-api fence function for writing photo binaries into
  `ItemData/<SKU>/` exists anywhere in the codebase today** — every other
  site that copies media into a SKU folder (`alt_text.py`,
  `bundle_intake.py`, `multi_intake.py`, `pm_intake.py`) also calls
  `shutil.copy2`/`copyfileobj` directly, not through any fence helper.
  `invariants.md` A8 explicitly documents this as a known, accepted gap:
  *"there is no general fence-level guard for media writes (A1-A7 cover
  JSON only)... a general guard would require routing media writes
  through a fence helper — suggestion, not made."* Inventing a new
  general media-write fence facility here would be new architecture, not
  a single-file mechanical fix, and is explicitly out of scope per the
  packet's own stop condition ("if scope is larger than a single-file
  mechanical fix, stop and report"). I did not invent one. Flagging this
  explicitly rather than silently treating "atomic write" as satisfying
  the whole finding.

Out-of-scope findings filed:
- #1383 (pp_ref=PP-COHESION-001): `tools/photo_history_recovery.py`'s
  `recover_item()` has the identical non-atomic `shutil.copy2(src, dest)`
  pattern (line ~180) as the now-fixed `ensure_copy()`. It's a separate
  one-shot legacy script (not imported by the fixed module), out of scope
  for this single-file packet, but shares the same atomicity gap.
