Todo #1243 — DONE. All remaining code-review findings resolved except #8
(deliberately skipped, see below).

2. scripts/data_scrub_magento.py — restored try/except around
   items.strip_fields(), catching any parse/read exception and reporting
   "WARNING: Could not process {sku}: {exc}" + returning -1, same as before.
3. state_machine.mark_failed() — row-None case now returns 'dead_letter'
   instead of 'retry_wait' (nothing will ever retry a job whose row is
   gone; this makes worker_base.py alert/reschedule instead of silently
   doing nothing).
4. items.atomic_write_json() gained a sort_keys param (default False,
   unchanged for existing callers); itemdata_scrub.py now passes
   sort_keys=True to restore its old deterministic/diffable output.
5. items.py: atomic_write_json/atomic_write_text refactored onto a shared
   _atomic_write(path, write_body, archive_root=) core — one tmp+rename+
   chmod+archive implementation instead of two copies.
6. get_access_token.py/refresh_access_token.py: extracted the identical
   inline tmp+rename+chmod(0o600) block into a new tiny dependency-free
   module tgw/apis/ebay/_token_io.py (atomic_write_token_json). NOT reusing
   items.atomic_write_text — that helper preserves/defaults 0o660
   group-writable mode, wrong for a secret file. Kept this module free of
   any tgw.* imports since these two scripts are the OAuth recovery path
   and must keep working even if something else in the package is broken.
7. scripts/data_scrub_magento.py — removed the hardcoded ITEM_DATA_ROOT
   constant entirely; main() now derives the enumeration root from
   cfg['itemdata_root'] (same cfg strip_fields() already resolves paths
   against), so the two can never drift apart again.
8. token_refresh.py's duplicate disk read (_on_terminal_failure ->
   _reschedule() re-reading the tiny token file handle() already read) —
   deliberately NOT fixed. Threading the already-read state through would
   add real coupling/complexity for a near-zero-cost duplicate read of a
   few-hundred-byte file on the rare dead-letter path only. Not worth it
   per "don't add complexity beyond what's needed."

Evidence:
- 6 new/updated tests: test_mark_failed_missing_row.py (1),
  test_items_atomic_write_text.py (+2 sort_keys cases),
  test_token_state_atomic_write.py (+1 0600-always case),
  test_data_scrub_magento.py (+2: corrupt-JSON isolation, ITEM_DATA_ROOT gone).
- Full offline suite: 1870 passed, same 10 pre-existing unrelated failures
  (google_direct/openrouter + pricing-invariant tests) as every other run
  this session.
- Restarted ALL 19 active tgw-worker@* services (items.py is shared by most
  workers; Dave's call given the shared-module scope) — all active,
  tgw health shows the same 3 pre-existing tracked warnings (backups, nats,
  ebay_sync_fallback/#1077), nothing new.

This closes out all 8 findings from today's /code-review pass on the
audit#1143 #1234/#1235 diff (finding #1 was todo #1242; #2-7 this todo; #8
explicitly skipped as not worth the complexity).
