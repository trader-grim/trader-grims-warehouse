Todo #1245 — DONE (the 3 CONFIRMED parts). 4 PLAUSIBLE findings deliberately
deferred per Dave's instruction ("do the 3 confirmed, collect the plausibles
and process toward the end of the process").

CONFIRMED, fixed:
1. items.strip_fields()'s catalog_verified-clearing side effect was
   undocumented before data_scrub_magento.py started routing through it.
   Documented in both strip_fields()'s docstring and process_item()'s;
   added test_execute_clears_catalog_verified_as_documented_side_effect to
   lock the behavior in.
2. The 8 hand-copied _on_terminal_failure overrides (todo #1234/#1242) were
   collapsed into a single generalized default in worker_base.QueueWorker:
   it introspects self._reschedule's signature and calls it automatically
   when no argument is required. Removed the 7 now-redundant overrides
   (token_refresh, velocity_stats, ebay_sync, ebay_dole, ebay_price_reducer,
   ebay_legacy_sync, sync_conflict). Kept ebay_sku_migrate's explicit
   override (its _reschedule needs interval_hours, which the base class
   can't supply). Rewrote tests/test_self_rescheduling_workers_have_terminal_hook.py:
   old version required every _reschedule-having class to also define
   _on_terminal_failure (would now false-positive on all 7 collapsed
   workers) — new version asserts the opposite for no-arg cases (a
   redundant hand-written override is now itself a violation) and adds
   direct unit tests of the base-class auto-dispatch mechanism (4 new
   tests, 8 total in that file).
3. (Filed separately as todo #1244, already closed) the missing
   todo/inbox note for the multi_intake.py collision redesign.

Deferred (PLAUSIBLE, to process at the end per Dave's instruction):
4. multi_intake.py's collision notify() fires once per colliding child in
   the extraction loop, no batching/dedup — could spam external channels
   on a large re-drop.
5. state_machine.mark_failed()'s UPDATE statements never check cur.rowcount
   — a lease-expiry race between the SELECT and UPDATE could report a state
   transition that never actually happened in the DB.
6. ebay_sku_migrate.py's _on_terminal_failure recomputes interval_h with
   logic duplicated from handle() — could drift if one changes without the
   other.
7. The multi_intake collision notify() text doesn't tell the operator the
   affected SKU now needs an operator-forced ebay_stage duplicate-check
   pass to ever get staged (since 'Item number' is no longer auto-stripped).

Evidence: 4 new/changed tests total this round (data_scrub_magento +1,
self_rescheduling_workers_have_terminal_hook rewritten net +4). Full suite:
1874 passed, same 10 pre-existing unrelated failures as every prior run
this session.
