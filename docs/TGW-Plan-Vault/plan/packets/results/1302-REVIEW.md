Status: cleared
Reviewer: Claude (runner-review)
Todo: #1302   PP: PP-COHESION-001
Checked: diff (`git diff 714de85 todo/1302-ebay-pull-orphan-finding`)
against the todo brief's stated bug, scope (ebay/pull.py + new test file
only), result manifest completeness. Independently confirmed the claimed
root cause: `stats['orphans']` was already populated during the scan loop
(pre-existing lines 470/586/594) and returned in the stats dict, but
`workers/ebay_legacy_sync.py:113` does `combined.pop('orphans', None)`
before persisting anything — confirmed that line exists exactly as
described. The fix persists at the source, inside `sync_active_listings()`
itself, so it's independent of what any caller does with the returned
dict afterward — correctly root-caused, not patched at the caller.
Summary: new `ORPHAN_REGISTRY` JSON file (`/opt/TGW/var/ebay-orphan-listings.json`),
same atomic tmp-write-replace pattern as the established
`migrate-blocked.json` precedent, keyed by listing_id, tracks
first_seen/last_seen/seen_count, prunes only on a full unfiltered scan
(never on a sku_filter'd partial run — correct, avoids false-pruning entries
a partial scan simply didn't look at). No catalog-verify rule added, with
reasoning given (catalog-verify operates on ItemData docs; orphans by
definition have none) — reasonable, the registry file itself is the
queryable store. 5 new tests cover persistence, recurrence tracking, and
both pruning behaviors. Full suite green modulo the known #1370 flake. No
triggers fired. Cleared for stitch.
