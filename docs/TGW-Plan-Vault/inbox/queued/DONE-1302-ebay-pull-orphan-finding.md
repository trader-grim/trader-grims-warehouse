Todo #1302 (PP-COHESION-001, invariant C11) — `src/tgw/ebay/pull.py`'s
`sync_active_listings()` was counting orphaned active eBay listings (no
`custom_label`, or a `custom_label` with no matching local ItemData) but
discarding the actual list after logging/printing it once — no durable,
queryable record for an operator to act on later, the exact C11 anti-pattern.
Fixed by adding `record_orphan_listings()` + `ORPHAN_REGISTRY`
(`/opt/TGW/var/ebay-orphan-listings.json`), a flat JSON registry keyed by
listing_id, same pattern as `workers/ebay_sku_migrate.py`'s
migrate-blocked.json — chosen over an ItemData field because orphans have no
ItemData record to attach one to. Wired into `sync_active_listings()` itself
so both callers (`ebay-pull` CLI in api.py, `ebay_legacy_sync` worker)
benefit without duplicating persistence logic. Added
`tests/test_ebay_pull_orphan_registry.py` (5 new tests: persisted not just
counted, recurrence tracking, pruning on full scan, no pruning on filtered
scan, registry shape). Full offline suite green except the known pre-existing
`test_llm_google_direct.py` flake (todo #1370). Work complete; result
manifest written; not merged/pushed.
