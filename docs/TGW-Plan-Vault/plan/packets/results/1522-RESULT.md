# Result: 1522 padlock-clear-revert-fix
Status: done
Todo: #1522   PP: PP-LISTEDITOR-001

Files touched:
- `src/tgw/http_server.py` — `_apply_patch`: after a top-level field write
  (`doc.update(fields)`), mirror any direct `title`/`description` edit
  (including a clear to `""`) into `draft_listing[<key>]` so the two never
  diverge.
- `tests/test_http_server.py` — removed the `xfail` marker from
  `test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save`.
- `tests/test_invariant_c12_field_set_accessors.py` — refreshed 6
  hardcoded line-number allowlist entries in `_ALLOWLIST` that shifted by
  +15 (the size of the inserted fix block) in `http_server.py`; no new
  C12 violation, purely a line-number-pinning refresh (same known
  fragility already documented in that file's own comments from prior
  packets today).

Root cause: `_apply_patch`'s "Padlock auto-sync (Dave, 2026-07-18)" block
resyncs an unlocked top-level `title`/`description` FROM
`draft_listing[<key>]` on every `draft_listing` save. When an operator
clears the top-level field directly via the item-detail editor (a plain
top-level PATCH, not a `draft_listing` PATCH), nothing updated
`draft_listing`'s own copy of that field — so the very next unrelated
`draft_listing` save (e.g. a price edit) found a stale, pre-clear
`draft_listing[<key>]` value and silently pushed it back into the
now-cleared top-level field, with no error.

Fix: whenever a top-level `title`/`description` field is written directly
(`_base_key in fields`), mirror the same value into
`doc["draft_listing"][_base_key]` immediately, in the same `_apply_patch`
call. This keeps the base field and the draft's copy in permanent
agreement for these two keys, so the auto-sync block downstream never
finds a stale draft value to resurrect — the draft is authoritative for
the unlocked case exactly as designed, it's just never allowed to go
stale relative to a direct base-field edit. The lock mechanism
(`inventory_record.is_locked`) still governs the opposite draft→base
direction unchanged; this fix only adds base→draft mirroring, so the
control test (locked field survives) is unaffected by construction.

Live evidence:
- `test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save`
  (`tests/test_http_server.py`) now PASSES (xfail marker removed, no
  longer needed) — ran isolated:
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH python -m pytest tests/test_http_server.py -k "c14 or padlock or lock" -q`
  → `11 passed, 312 deselected` (includes
  `test_c14_locked_top_level_field_clear_survives_unrelated_draft_save`,
  the control test, still passing).
- Confirmed testing the worktree's own copy, not the shared checkout:
  `python -c "import tgw.http_server as h; print(h.__file__)"` resolved to
  `/opt/TGW/var/worktrees/1522-padlock-clear-revert-fix/src/tgw/http_server.py`.
- Full offline suite, run to completion in the foreground (not
  backgrounded) after the C12 allowlist refresh:
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH python -m pytest -q`
  → `2545 passed, 1 skipped, 1 xfailed, 1 warning in 180.75s` — the one
  remaining xfail is `tests/test_revision.py:798`, pre-existing and
  unrelated to this fix. No regressions anywhere else in the suite,
  including the other padlock/inventory-record sync tests from earlier
  sessions today.

Deviations from spec: none. The fix follows the packet's own suggested
direction ("does the draft ever get updated to reflect a base-field
clear... so they stay in agreement and the auto-sync has nothing stale
to overwrite") directly — implemented base→draft mirroring rather than a
timestamp/version staleness comparison, since it fully satisfies the
acceptance test without adding new state to track. One incidental
mechanical fix bundled in (not a scope deviation, a same-file
consequence of the code-shift): refreshed 6 stale hardcoded line numbers
in `test_invariant_c12_field_set_accessors.py`'s `_ALLOWLIST` that the
new code's insertion shifted by +15 lines — same known-fragile
line-number-pinning pattern already flagged/refreshed by several other
packets today per that file's own comments.

Out-of-scope findings filed: none. No new adjacent issues discovered
during this fix.
