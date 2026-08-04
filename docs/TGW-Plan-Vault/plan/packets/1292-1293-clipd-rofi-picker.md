# Packet: rofi clipboard picker returns full content end-to-end
Todo: #1292, #1293   PP: PP-COHESION-001   Track: framework pilot (PP-HERMES-EA-001)

**Scoping note (flagged, not silent):** two separate todo ids, one packet.
#1292 (query targets nonexistent `clips` table) and #1293 (double
`cursor.fetchone()` call) are the same function, stacked — #1292's bug
fires first and masks #1293 entirely (caught by the broad
`except Exception`). Fixing either alone leaves the picker still broken;
there is no live-acceptance evidence possible without fixing both.

## Context budget (ALL the model may load)
This packet + `src/tgw/clipd.py` (`launch_rofi_picker()` only) +
`src/tgw/clip.py` (schema reference, read-only, do not modify) + the two
todo briefs (`tgw todo brief 1292`, `tgw todo brief 1293`). Nothing else —
no need for PP-CLIP-001's full history doc, invariants.md (this is a local
desktop utility, not ItemData/eBay/pipeline), or the master plan.

## Spec
Fix `launch_rofi_picker()` in `src/tgw/clipd.py`:
1. Query `clip_history` (the real table, per `clip.py`'s
   `CREATE TABLE clip_history`), not `clips` (doesn't exist).
2. Replace the double `cursor.fetchone()` call (current line ~193) with a
   single call, result stored, then branched on.
3. No other behavior change. The `content` column selected is already
   correct — only the table name and the double-call are wrong.

## Dataset
None — local clipboard-picker utility (PP-CLIP-001), not ItemData/eBay/
pipeline. Nothing to persist beyond the code fix.

## Out of scope
- `clip.py`'s schema or any other function in it — read-only reference.
- Any other function in `clipd.py`.
- Any other PP-COHESION-001 finding noticed in passing — file a todo, don't fix.
- Refactoring the surrounding `except Exception` handling beyond what's needed.

## Acceptance (live)
1. Insert (or capture via the existing clip-capture path) a test row in
   `clip_history` with `content` longer than rofi's display truncation.
2. Invoke `launch_rofi_picker()` (or the keybinding/CLI path that calls
   it) and select that entry.
3. Observed result: the FULL `content` value is returned — not `None`,
   not a `TypeError`, not silently caught by the outer `except`.
4. Second case: a selection that matches nothing in `clip_history` still
   falls back to the raw `selected` string (fallback path preserved).

## Quota/risk
None — local SQLite only, no API calls.
