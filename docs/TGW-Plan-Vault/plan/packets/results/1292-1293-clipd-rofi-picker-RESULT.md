# Result: 1292-1293 clipd-rofi-picker
Status: done
Todo: #1292, #1293   PP: PP-COHESION-001

Files touched:
- src/tgw/clipd.py (`launch_rofi_picker()` — two SELECT statements + double-fetchone)
- tests/test_clipd.py (3 new regression tests)

Live evidence:
- Confirmed live against real schema (`src/tgw/clip.py`): table is `clip_history`
  with columns `id, content, selection, is_sku, sku, captured_at` — no `clips`
  table and no `ts` column exist at all.
- End-to-end acceptance run against a real SQLite `clip_history` DB populated
  via the real `record_clip()` capture path (rofi/Popen interaction mocked
  since no `rofi` binary or X/Wayland picker session is available in this
  environment; SQL layer and control flow are unmocked/real):
  - Case 1 (truncated 120-char rofi selection of the most recent, 522-char
    entry) -> `launch_rofi_picker()` returned the full 522-char content
    exactly (`result == long_content`), not `None`, not truncated, not
    swallowed by the outer `except Exception`.
  - Case 2 (selection matching nothing in `clip_history`) -> fell back to
    the raw `selected` string, preserving the existing fallback path.
  - Reproduced pre-fix: with the original `clips`/`ts` query, both cases
    returned `None` (query raises `sqlite3.OperationalError: no such table:
    clips`, caught by the broad `except Exception`, function returns `None`)
    — confirms #1292 was masking #1293 as the packet stated.
- `pytest -q tests/test_clipd.py tests/test_clip.py` -> 47 passed (incl. 3
  new tests: `test_launch_rofi_picker_returns_full_content_not_truncated`,
  `test_launch_rofi_picker_falls_back_to_raw_selection_on_no_match`,
  `test_launch_rofi_picker_queries_clip_history_table`).
- Full offline suite `pytest -q` -> 2078 passed, 1 skipped (unrelated), 0
  failed.

Deviations from spec:
- Packet step 3 said "only the table name and the double-call are wrong" —
  live inspection of the real query found the ORDER BY clause also
  references a nonexistent `ts` column (`clip_history` only has `id` and
  `captured_at`). Fixing only the table name per the packet's literal
  wording would still leave the query broken (`ORDER BY ts` ->
  OperationalError, silently swallowed by the same `except Exception`),
  defeating the packet's own acceptance criteria. This matches todo #1292's
  own brief text verbatim ("'clips' table with 'ts' column that don't
  exist"), so it is treated as part of the same identified bug, not a new
  finding. Fixed by changing `ORDER BY ts DESC` to `ORDER BY id DESC`
  (monotonic primary key, same recency-ordering semantics used elsewhere in
  `clip.py`'s own retention-prune query) rather than `captured_at`, since
  `id` needs no extra index lookup and is already the ordering column used
  by `clip.py`'s internal queries.
- Added 3 regression tests to `tests/test_clipd.py`, per the todo brief's
  stated constraint ("new behavior gets tests") even though not explicitly
  named in the packet's own Spec section — this is bugfix-adjacent
  regression coverage for the exact function being fixed, not scope
  expansion.

Out-of-scope findings filed: none — no new findings encountered outside the
two already-tracked bugs.
