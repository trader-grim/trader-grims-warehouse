Todo #1242 — DONE. Closes code-review finding #1 from today's audit#1143
#1234/#1235 follow-up.

Added _on_terminal_failure override (log warning + call self._reschedule())
to all 6 remaining self-rescheduling workers:
- ebay_sync.py, ebay_dole.py, ebay_price_reducer.py, ebay_legacy_sync.py,
  sync_conflict.py — all take no args, same pattern as token_refresh/velocity_stats.
- ebay_sku_migrate.py — _reschedule() takes interval_hours; the hook
  recomputes it the same way handle() does
  (self.config.get('ebay_sku_migrate', {}).get('interval_hours', 1)) since a
  terminal failure means handle() never reached its own reschedule call.

Structural guard added: tests/test_self_rescheduling_workers_have_terminal_hook.py
AST-scans every class in src/tgw/workers/*.py — any class defining
_reschedule must also define _on_terminal_failure, or the test fails. This
closes the altitude gap the code review flagged (relying on every future
worker author remembering the override) at low cost, without a larger
worker_base.py redesign.

Evidence:
- New test passes; full suite: 1864 passed, same 10 pre-existing unrelated
  failures (google_direct/openrouter + pricing-invariant tests) as before
  this session's other work.
- Restarted 4 live workers (ebay_sync, ebay_price_reducer, ebay_legacy_sync,
  ebay_sku_migrate) — all active after restart.
- ebay_dole.service and sync_conflict.service have no systemd unit currently
  (not in `systemctl list-units 'tgw-worker@*'`) — code fix is in place and
  will apply whenever/if those workers are deployed; no live process to
  restart today.
- tgw health: same 3 pre-existing failed checks (backups, nats,
  ebay_sync_fallback — all already tracked, ebay_sync_fallback references
  todo #1077), nothing new.
