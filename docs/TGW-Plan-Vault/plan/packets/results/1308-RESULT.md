# Result: 1308 photo-history-announce
Status: done
Todo: #1308   PP: PP-COHESION-001 (invariant E9)

Files touched:
- src/tgw/workers/photo_history_recovery.py
- tests/test_photo_history_recovery_dry_run.py

What was wrong: `photo_history_recovery.py`'s `main()` never called
`tgw.logging.announce_script_run()` before loading config, building the
photo index, copying files into live ItemData, or enqueueing a
catalog_rebuild job — violating invariant E9 (established after the
2026-07-04/05 requeue storm: any one-off script touching ItemData/the
queue must announce itself before doing anything, so a burst of activity
has an attributable cause in the logs).

Exact fix: added `from tgw.logging import announce_script_run` and, at
the top of `main()` (right after `config_path = Path(args.config)`,
before `load_config()` is called or anything else happens), added:
```python
announce_script_run(
    'photo_history_recovery.py',
    'recover missing item photos from history archives into ItemData',
    write=args.write, config=str(config_path), itemdata=args.itemdata,
)
```
Signature/call convention matched exactly to `tgw/logging.py`'s
`announce_script_run(script_name, purpose, **fields)` and its own
docstring usage example (script name as first positional arg, a short
purpose string as second, then relevant run-specific fields as kwargs).

Test added: `test_main_announces_script_run_before_touching_anything` in
`tests/test_photo_history_recovery_dry_run.py` — monkeypatches
`phr.announce_script_run` and `phr.load_config` to record call order,
runs `main()` end-to-end against a tmp_path config/itemdata_root, and
asserts the announce call happens strictly before `load_config` (i.e.
before any config/ItemData work begins).

Live evidence: full offline test suite, PYTHONPATH pinned to this
worktree's src/ (confirmed via `phr.__file__` resolving under
`/opt/TGW/var/worktrees/1308-photo-history-announce/...`, not the shared
checkout):
```
PYTHONPATH=/opt/TGW/var/worktrees/1308-photo-history-announce/src python3 -m pytest -q
...
2138 passed, 1 skipped, 1 warning in 50.99s
```
Module-specific test file also run in isolation: 6 passed.

Deviations from spec: the packet's pre-flight assumption ("this pattern
is established, not new... there should be several [existing callers]")
did not hold live — a full grep of `src/tgw/` for `announce_script_run`
found exactly one hit outside its own definition, and that hit is the
*docstring usage example inside `tgw/logging.py` itself* (for a
hypothetical `requeue_ebay_draft_402_dead_letters.py`), not a real caller
anywhere in the current tree (`tools/photo_history_recovery.py`, the
near-duplicate this worker was copied from, also does not call it). No
other one-off script in this codebase snapshot currently calls
`announce_script_run()`. Per Prime Directive 3 this is flagged rather
than silently adapted: I proceeded anyway because the function's own
docstring gives an unambiguous, already-specified calling convention
(positional script_name + purpose, then **fields), so there was no real
ambiguity to resolve — but the packet's premise that several other
scripts already do this is not currently true. This is otherwise the
same class of gap the invariant exists to prevent; worth a follow-up pass
across `src/tgw/workers/*` and `tools/*` one-off scripts to see how many
are still missing this call.

Out-of-scope findings filed: #1369 (PP-COHESION-001) — audit all
src/tgw/workers/*.py and tools/*.py one-off scripts for the same missing
`announce_script_run()` gap; #1308 found zero other scripts in the
current tree actually call it.
