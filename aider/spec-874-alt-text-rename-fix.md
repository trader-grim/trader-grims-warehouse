# Task 874 — ISS-013 alt-text photo naming fix

## Problem

`alt_text.py` currently RENAMES the original photo to `<sku>-alt.jpg`. This is wrong.
The `-alt.jpg` suffix must be a COMPANION DERIVATIVE only — the original file must keep
its original name (e.g. `<sku>.jpg` or whatever it was uploaded as).

## Required changes

### 1. Fix `alt_text.py` rename logic
- When generating alt-text, write the companion file as `<sku>-alt.jpg` (or `<sku>-alt.png`
  matching the original extension) WITHOUT renaming the original.
- If the code currently does `os.rename(original, alt_path)` or similar, replace it with
  logic that either copies/symlinks or just tags the alt text alongside the original.
- The alt-text derivative is a new file; the original is untouched.

### 2. Scan ItemData for already-renamed originals and restore
- Add a `repair_renamed_originals(item_data_root: Path) -> list[str]` function in
  `alt_text.py` that:
  - Walks `ItemData/<SKU>/` directories.
  - Detects any folder where the ONLY photo is `<sku>-alt.*` (i.e. the original was
    renamed and the bare `<sku>.*` is missing).
  - Renames `<sku>-alt.*` back to `<sku>.*` for those folders (restoring the original).
  - Returns a list of SKUs that were repaired.
- This is a one-time repair utility, not run automatically on every item.

### 3. Gallery sort — mtime-based, SKU-named files first
- In any listing/gallery function that returns photos for an item, sort so that:
  - Files named exactly `<sku>.<ext>` (the canonical originals) come first.
  - Within that group, sort by mtime ascending (oldest first = first uploaded).
  - `-alt.*` companions sort after the originals.
  - Other files sort by mtime last.

### 4. Update `tests/test_alt_text.py`
- Add / update tests covering:
  - That the rename logic no longer renames the original.
  - That `repair_renamed_originals` correctly identifies and renames back a folder
    where only `<sku>-alt.jpg` exists.
  - That gallery sort puts `<sku>.jpg` before `<sku>-alt.jpg`.

## Files to edit
- `src/tgw/alt_text.py`
- `tests/test_alt_text.py`

Do NOT touch `src/tgw/workers/alt_text.py` unless the rename logic is only there.
Check both files and fix whichever one contains the actual rename call.

## Constraints
- `pytest -q` must pass after changes.
- Do not change any ItemData file on disk — only the logic for future runs, plus the
  repair function.
- The repair function must be importable and callable from a script; it does not need
  a CLI entry point in this task.
