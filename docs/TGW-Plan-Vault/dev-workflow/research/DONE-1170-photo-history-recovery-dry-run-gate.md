# DONE — todo #1170 (audit#1143)

`src/tgw/workers/photo_history_recovery.py` is a near-duplicate of
`tools/photo_history_recovery.py`, but had dropped the dry-run safety gate
the tools/ version has. `tools/`'s `main()` defaults to dry-run and requires
an explicit `--write` flag before `recover_item()` ever calls
`shutil.copy2()`; the `workers/` copy had no such flag at all —
`ensure_copy()` always executed `shutil.copy2()` unconditionally whenever a
match was found, writing straight into live `ItemData` with no review step.
(Note: this script is not in CLAUDE.md's registered systemd worker list —
it's a standalone script that happens to live under `workers/`, not an
active queue worker.)

## Fix
Mirrored `tools/`'s exact convention:
- `ensure_copy(src, dst, overwrite=False, write=False)` — now returns
  `'would_copy'` instead of actually copying when `write=False` (the
  default).
- `process_item(..., write=False)` threads the flag through to
  `ensure_copy()`.
- `main()` gained a `--write` flag (`action='store_true'`, help text
  matching `tools/`'s wording) and logs the mode (`DRY-RUN`/`WRITE`) at
  start, plus a "run with --write to copy N photos" hint at the end when
  running dry — same operator-facing pattern as `tools/`.

## Tests
New `tests/test_photo_history_recovery_dry_run.py` (this file had zero
prior test coverage):
- `ensure_copy()` dry-run (default) does not touch disk, returns
  `'would_copy'`
- `ensure_copy(write=True)` actually copies
- an existing destination is reported `'exists'` and left untouched
  regardless of the write flag
- `process_item()` dry-run (default) does not copy a real match
- `process_item(write=True)` actually copies a real match

`pytest -q tests/test_photo_history_recovery_dry_run.py`: 5/5 pass. Full
suite: 1979 passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only, no files touched)
Ran the fixed script for real against production config and a real item:

```
sudo -u tgw python3 -m tgw.workers.photo_history_recovery \
  --config /opt/TGW/config/queue-workers/photo_history_recovery.config.json \
  --itemdata /opt/TGW/data/ItemData/tgw20141218162434698/tgw20141218162434698.json \
  --report /tmp/photo_recovery_test_report.jsonl
```

Ran cleanly in default dry-run mode (no `--write` passed); the item's photo
files and JSON were confirmed byte-for-byte unchanged afterward (`find`
before/after identical). Note: the real `default_search_roots`
(`/opt/TGW/data/history/`) is currently empty (4 KB, no files) — confirmed
via `du` before running to avoid a heavy scan — so no `would_copy` action
was actually produced against live data; the dry-run/write behavioral split
itself is proven directly by the 5 new unit tests using synthetic matches,
and this live run additionally confirms the script executes cleanly and
safely against the real config/data path.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no photos copied into or modified in live ItemData.
