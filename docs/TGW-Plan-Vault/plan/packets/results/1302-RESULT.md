# Result: 1302 ebay-pull-orphan-finding
Status: done
Todo: #1302   PP: PP-COHESION-001 (invariant C11)

Files touched:
- src/tgw/ebay/pull.py
- tests/test_ebay_pull_orphan_registry.py (new)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1302-ebay-pull-orphan-finding.md

What was wrong:
`sync_active_listings()` in `src/tgw/ebay/pull.py` reconciles active eBay
listings (Trading API `GetMyeBaySelling`) against local ItemData. Any
listing with no `custom_label` (SKU), or a `custom_label` with no matching
`ItemData/<SKU>/<SKU>.json`, was appended to an in-memory `stats['orphans']`
list and counted in `stats['orphaned']`. Both callers of this function
(`api.py`'s `ebay-pull` CLI command, `workers/ebay_legacy_sync.py`'s
scheduled worker) only logged/printed each orphan once — the worker even
explicitly did `combined.pop('orphans', None)` before writing its summary
`log_event`. This is exactly the C11 anti-pattern: a real, recurring
condition (an eBay listing this system can't reconcile) reduced to a count
that was true at one point in time, with no durable way for an operator to
find and act on the specific listings later.

Exact fix:
Added to `src/tgw/ebay/pull.py`:
- `ORPHAN_REGISTRY = Path('/opt/TGW/var/ebay-orphan-listings.json')` — a flat
  JSON registry file keyed by `listing_id`, following the exact
  load/merge/atomic-write pattern already used by
  `workers/ebay_sku_migrate.py`'s `_update_blocked_registry()` /
  `migrate-blocked.json` (referenced in invariants.md's C11 entry as the
  sibling durable-registry example). Each entry carries `listing_id`,
  `custom_label`, `title`, `status`, `live_price`, `listing_url`,
  `first_seen`, `last_seen`, `seen_count`.
- `_load_orphan_registry()` / `_save_orphan_registry()` — read/atomic-write
  helpers (write to `.tmp`, then `Path.replace()`).
- `record_orphan_listings(orphans, synced_at, full_scan)` — merges this
  run's orphans into the registry (new entries get `first_seen`, existing
  ones get `last_seen`/`seen_count` bumped). When `full_scan=True` (no
  `sku_filter`, i.e. every active listing was actually examined this run),
  entries no longer reported as orphans are pruned — they were resolved
  (matched, delisted, or SKU corrected). A `sku_filter`'d partial run never
  prunes, since it only looked at a subset of listings.
- Wired into `sync_active_listings()` itself (not the callers) — one call
  right before `return stats`, passing `full_scan=sku_filter is None` — so
  both existing call sites (`api.py` CLI, `ebay_legacy_sync` worker) get
  durable persistence automatically without duplicating logic, and any
  future caller does too.

Persistence-mechanism choice (deviation flag, per packet instruction): the
packet left the exact target open. Chose a standalone JSON registry file
(`/opt/TGW/var/ebay-orphan-listings.json`), matching `migrate-blocked.json`'s
existing convention, over a `state_machine`/Postgres table. Reasoning: (1)
orphans have no ItemData record to attach a field to — ruled out by the
packet itself; (2) this codebase already has exactly one precedent for
"durable, queryable, cross-cutting finding with no single item to live on"
(`migrate-blocked.json`), and reusing its shape keeps the pattern consistent
rather than introducing a second storage mechanism for the same class of
problem; (3) it's the simpler of the two reasonable options the packet
named, per the packet's own tiebreaker instruction. No catalog-verify rule
was added — catalog-verify operates over ItemData docs and orphans by
definition have none; the registry file itself is the queryable store here,
consistent with "don't over-engineer... persist the finding, don't build a
full remediation workflow."

Test added:
`tests/test_ebay_pull_orphan_registry.py` (5 tests, offline, no eBay/DB):
- `test_orphans_persisted_not_just_counted` — 2 orphaned listings (one with
  no local ItemData, one with no custom_label at all) end up as separate
  entries in the registry file read back fresh from disk, not just in the
  returned stats dict.
- `test_orphan_seen_again_updates_last_seen_and_count` — recurrence tracking
  across two runs.
- `test_resolved_orphan_pruned_on_full_scan` — once a local item appears
  (listing no longer orphaned), a full unfiltered run removes the stale
  entry.
- `test_filtered_run_does_not_prune_unseen_entries` — a `sku_filter`'d run
  never deletes orphans outside its scope.
- `test_registry_is_queryable_json_keyed_by_listing_id` — registry shape/
  field content assertion.

Live evidence:
Offline pytest run, `PYTHONPATH` pinned to this worktree's `src/`
(confirmed `tgw.ebay.pull.__file__` resolves under
`/opt/TGW/var/worktrees/1302-ebay-pull-orphan-finding/src/tgw/ebay/pull.py`
before running):
```
PYTHONPATH=/opt/TGW/var/worktrees/1302-ebay-pull-orphan-finding/src:$PYTHONPATH \
  python3 -m pytest -q tests/test_ebay_pull_orphan_registry.py tests/test_ebay_pull_filters.py
17 passed in 0.76s
```
Full offline suite:
```
PYTHONPATH=/opt/TGW/var/worktrees/1302-ebay-pull-orphan-finding/src:$PYTHONPATH \
  python3 -m pytest -q
1 failed, 2155 passed, 1 skipped, 1 warning in 53.95s
```
The one failure is `tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter`
— matches the packet's named known pre-existing flake (todo #1370, shared
quota-state pollution across the full suite, unrelated to this change,
passes in isolation). No other failures.

No live eBay/production write was made or required by this packet — the fix
is offline reconciliation-logic + local file persistence; no
Acceptance(live) step was specified beyond the offline test suite.

Deviations from spec:
- Persistence target (JSON registry file vs. an alternative store) — chosen
  as described above; packet explicitly authorized this judgment call with
  a stated tiebreaker (simpler option), followed.
- No other deviations. Cadence/TTL/limits: none specified/applicable to this
  fix (pure reconciliation logic, no scheduling change).

Out-of-scope findings filed: none. No new adjacent broken things were
noticed during this change; both existing call sites already handled their
own stats correctly aside from the orphan-discard bug itself.
