# Packet: reports.py _pct() distinguishes "no data" from "genuine zero"
Todo: #1295   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/reports.py` (`_pct()` function and its existing
test file, if one exists) + the todo brief (`tgw todo brief 1295`).
Nothing else.

## Spec
`_pct(n, total)` at `src/tgw/reports.py:109` currently returns `"—"`
(em-dash, meaning "no data") whenever EITHER `total` is falsy OR `n` is
falsy. This conflates two different states: `total == 0` genuinely means
no data to compute a percentage from (em-dash is correct), but `n == 0`
with `total > 0` means "zero occurrences out of a real total" — a real,
computable 0.0%, not missing data.

Fix: return `"—"` only when `total` is falsy. When `total` is truthy and
`n == 0`, fall through to the normal computation (which correctly
produces `"0.0%"`).

## Dataset
None — this is a display/reporting function, not a data write.

## Out of scope
- Any other function in `reports.py`.
- Any caller of `_pct()` — their behavior with the corrected output is
  expected to already be correct (a real "0.0%" instead of a misleading
  em-dash is what callers want), not something to also change.

## Acceptance (live)
1. `_pct(0, 10)` → `"0.0%"` (was `"—"` before the fix).
2. `_pct(0, 0)` → `"—"` (unchanged — genuinely no data).
3. `_pct(5, 10)` → `"50.0%"` (unchanged — normal case still works).
4. Run against real report data if a live report-generation path is easy
   to invoke; otherwise the three cases above, verified directly, are
   sufficient live evidence for a pure function with no external state.

## Quota/risk
None.
