# IN PROGRESS — PP-POSTGRES-001 P1 migration contract drafted, 2026-07-22

**What happened:** Dave authorized the Postgres direction explicitly —
"We discussed it, she both agrees with me and I agree with her. We are
going to postgresql. But this is what we have and we need to plan the
migration." — after reading Tigwa's three reconciliation docs (already
folded into the master plan earlier this session). Recorded as an
AUTHORIZED block in `TGW-Master-Plan.md`'s PP-POSTGRES-001 section: the
three-lane capacity model is now the standing operating model, direction
is settled, but P5 (authority cutover) still needs Dave's separate
explicit go/no-go later — this is not cutover authorization.

**What I did:** drafted the P1 — Migration Contract section in
`pp/PP-POSTGRES-001.md`, grounded in live numbers pulled from tgw-prod
today rather than estimates:

- Baseline: 55,421 items, 181GB `ItemData/` tree, JSON files mean 7.5KB/
  median 3.0KB/max 41.6KB (2000-file sample) — confirms jsonb is right for
  nested content. `state_machine` DB: 464MB, 310,899 `queue_jobs` rows,
  8 tables already present (including item-adjacent `sku_history`,
  `image_hashes`).
- Schema decision: normalized hot columns (status, location, category,
  condition, offer/listing hot fields) + jsonb per envelope
  (`draft_listing`, `item_attributes`, `ebay_offer`, etc.) grounded in the
  real field list from `reference/TGW-Item-JSON-Schema.md`, not the
  original Perplexity guess.
- Same-instance decision: recommend `state_machine`, new `items` schema
  (not `public`), not a sibling DB — reasoning documented in the PP file.
- Data-product inventory: catalog rebuild is the biggest win, then
  search/eligible-filter, velocity_stats, future Radar.
- Backup contract: **checked live** — `tgw-db-backup.service`/`.timer`
  (PP-BACKUP-001 A1) already dumps `state_machine` daily, confirmed
  running (last success 15h prior). Missing: the A5 restore drill spec'd
  in `PLAN-backup-dr.md` has never been confirmed executed — **this is the
  one concrete gate left before P1 can be called done**: run
  `createdb scratch && pg_restore -d scratch <newest.dump>`, verify row
  counts, record wall-clock RTO.
- Rollback/provenance: carries forward the append-only history discipline
  already used elsewhere (price_history, item_attributes_history);
  conflict/reconcile procedure for P3's dual-write pilot explicitly
  deferred to P3's own packet, not designed here.

**Next step:** run the A5 restore drill (small, mechanical, no schema risk
— can be dispatched to tgw-coder or done directly) to actually close P1's
backup-contract gate. After that, P1 is essentially complete except for
exact column/index design (deferred to P2 shadow-import by design) and the
write-volume baseline (waits on P0/#1636 shipping so it measures real
fence traffic). P0 itself (`publish_mutation()` into the real HTTP fence,
todo #1636) is still not dispatched — worth prioritizing since P1's last
open item depends on it.

**Todo:** #1642 (fold Tigwa's responses in) and #1643 (authorize + plan P1)
both filed and marked done this session.
