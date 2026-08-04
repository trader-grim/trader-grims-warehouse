# DONE #1209 — recompile/scrub order-dependency (audit#1143)

## Bug
`recompile_category_backfill.py` used `velocity._category()` to decide whether
an item "already had a category" — that function correctly falls back to the
legacy raw `eBay category 1 number` field for read paths, but using the same
fallback as an "already handled, skip" gate here meant items whose ONLY
category source was the legacy field were never promoted to the canonical
`ebay_category_id`/`ebay_category_name`. `data_scrub_legacy_ebay_fields.py`
later strips that legacy field once it matches a historical source — leaving
the item with zero category signal in the live JSON.

## Fix
`scripts/recompile_category_backfill.py`:
- New `_canonical_category()` — checks only `draft_listing.category_id` /
  `ebay_category_id`, no legacy fallback.
- New `_legacy_category()` — reads the raw field only.
- Per-item loop now: canonical present → skip; legacy-only → promote via the
  existing `recovered`/`items.set_fields()` apply path (idempotent,
  `only_if_absent=True`, so safe to re-run); neither → fall through to the
  existing 3 external-source lookups as before.
- Report gained a `promoted_from_legacy` counter alongside `already_had_category`.

## Tests (tests/test_recompile_category_backfill.py)
- `_canonical_category` / `_legacy_category` unit tests (7 cases).
- `TestPromotionSurvivesLegacyScrub`: full reproduction using the real
  `items.set_fields()` (promote) → `data_scrub_legacy_ebay_fields._scan_item()`
  + `items.strip_fields()` (scrub) pipeline, asserting the canonical field
  survives after both steps run in production order.
- `pytest -q` full suite: 1883 passed (was 1876), 9 pre-existing unrelated
  failures confirmed via `git stash` to predate this change (model_routing,
  quota, invariants_pricing) — not touched.

## Live verification (dry-run, no writes — pre-flight per tgw-packet step 2)
Ran `sudo -u tgw ... recompile_category_backfill.py` (no `--apply`) against
live ItemData:
- 55,419 scanned
- 25,275 already had canonical category
- **8,802 currently legacy-only** — real, live exposure this bug describes,
  confirmed present today, not just theoretical
- 21,342 still genuinely unrecoverable (verified: absent from all 3 flat-file
  sources too, e.g. tgw201411151759014 — no eBay category ever assigned in
  any historical export)
- (Investigated the `recoverable: 0` figure in the existing Jul-4
  category-recompile-report.json — NOT a bug: 26,709→21,342 no-category delta
  matches an earlier successful `--apply` run already recovering 5,367 items;
  current 0 is the expected idempotent-no-op state per the script's own
  "safe to re-run" design.)

## Open follow-up for Dave (not applied — flagging per Prime Directive 3)
The 8,802-item exposure is still live: nothing has promoted these items yet.
`--apply` would write `ebay_category_id`/`ebay_category_name` to 8,802 items
(only_if_absent, additive, E5-archived) closing the exposure window before
any future scrub re-run. Did not run --apply myself — this todo's brief was
the code fix + tests, and a bulk write to 8,802 production items is a
judgment call, not implied scope. Recommend running it soon given the risk
description in #1209 itself.
