## PP-UI-INTEGRITY-001 — Web UI Data Integrity and Visibility

**Opened:** 2026-06-20 (session 35)
**Status:** Phase 1 PENDING
**Driver:** Full audit of the web UI against the item JSON schema and API surface revealed
systematic gaps: data exists in item JSON and is returned by the API but is never rendered
in the UI; counts and badges are shown without the underlying detail; the UI makes claims
the operator cannot verify; and navigation dead-ends prevent self-service resolution.
All UI rendering is inline Python in http_server.py (~6,500 lines); no template engine.

### Gap taxonomy (from 2026-06-20 audit)

| # | Field / Area | Gap Type | Impact |
|---|---|---|---|
| 1 | `reprice_schedule` + `price_history` | MISSING | HIGH — system changes eBay prices; operator has no visibility |
| 2 | `review_block` — no UI page, no nav link | NO_DRILL | HIGH — items stuck silently |
| 3 | Dashboard cards: pending_offers, revision_draft, dead_letter — no href | NO_LINK | HIGH — dashboard is a dead end |
| 4 | `identification_history` / hint trail | MISSING | MEDIUM-HIGH — can't trace AI decisions |
| 5 | `ebay_listing.live_price` vs offer price | MISSING/TRUST | HIGH — operator sees submitted price, not what eBay shows buyers |
| 6 | `draft_listing.category_confidence: "low"` not shown in review queue | MISSING | HIGH — approving miscategorised items |
| 7 | `product_lookup` (MSRP, brand, MPN, source) | MISSING | MEDIUM — pricing sanity check invisible |
| 8 | `sku_migrate_blocked` — no UI page | NO_DRILL | MEDIUM — PP-REVIEW-001 P1 built API, no UI |
| 9 | Quality `flags` array not shown in review queue (only score number) | COUNT_ONLY | MEDIUM — operator approves without knowing why score is low |
| 10 | `offline_draft: true` — no warning in review queue or detail | MISSING | MEDIUM — drafts built without live taxonomy approved silently |
| 11 | `price_comps.p75` dropped silently | DISCARDED | LOW-MEDIUM |
| 12 | Pricing comps collapsed by default; no drill-down to actual comp listings | COUNT_ONLY/TRUST | MEDIUM — "20 comps" with no verifiable detail |
| 13 | `draft_listing.alt_text` reads from wrong source (top-level vs draft_listing) | WRONG_SOURCE | MEDIUM — wrong data silently shown |
| 14 | Pipeline job error text truncated at 60–80 chars; full error never visible | TRUNCATED | MEDIUM — diagnosis requires raw API |
| 15 | `draft_listing.seo_caption`, `title_ai`, `description_source` | DISCARDED | LOW — enrichment audit trail invisible |
| 16 | `title_history`, `description_history`, `location_history` (legacy) | MISSING | LOW |
| 17 | `ebay_offer.quantity`, `ebay_offer.category_id` not shown in detail | MISSING | LOW |

### Phases

**Phase 1 — Quick wins: links, warnings, trust fixes** (all in http_server.py, no backend)
- Dashboard cards: add hrefs to pending_offers (`/form/offers`), revision_draft (`/form/revisions`), dead_letter (`/form/pipeline`)
- Review queue: `category_confidence: "low"` warning badge on card
- Review queue: `offline_draft: true` warning badge on card
- Review queue: expand quality `flags` array below Q score (tooltip or inline)
- Item detail: `review_block` shown as a prominent blocking banner when present
- Item detail: `ebay_listing.live_price` field shown alongside offer price; highlight divergence
- Item detail: `offline_draft` warning when present
- Item detail: fix `alt_text` source (read from `draft_listing.alt_text`, not top-level)
- Item detail: add `p75` to price comps display; open the pricing section by default
- Pipeline: full error text in expandable `<details>` element (not 60-char truncation)

**Phase 2 — New data sections on item detail page**
- Reprice schedule table: `reprice_schedule` rows with stage, price, due_at, done_at, status
- Price history table: `price_history` rows with ts, price, previous_price, stage, label
- Product lookup section: shown when `product_lookup` present; MSRP, brand, MPN, source
- Identification history: collapsible section fetching `GET /api/items/{sku}/hint-trail`
- Offer fields: `ebay_offer.quantity`, `ebay_offer.category_id` added to offer section
- Price comps: make `price_source` a clickable eBay sold-listings search link

**Phase 3 — Dedicated review surfaces** (new `/form/*` pages + nav links)
- `/form/needs-review` — items with `review_block.ready=false`, grouped by stage/reason;
  links to item detail; "Mark Ready" action; distinct from existing `/form/review`
  (which is the draft approval queue, a different concept — rename that to `/form/drafts`)
- `/form/migrate-blocked` — items with `sku_migrate_blocked`; or fold into needs-review
- Nav bar audit: ensure every actionable system state has a nav entry or dashboard link

**Phase 4 — Verification and trust**
- Price comps drill-down: store individual comp records (title, price, url, sold_date) in
  `price_comps.items[]` rather than just summary stats; render as sortable table in detail
- Live price refresh: "Sync from eBay" button on item detail → calls `ebay_sync` for one item
- Category confidence: if `low`, show both the AI category and the lookup category side-by-side
  in the review queue and item detail so the operator can choose

### Architecture note

All rendering is inline in http_server.py. The file is ~6,500 lines; edits must be precise.
Each phase should be implemented function-by-function with tests after each significant change.
The `_render_item_detail_html()` function is the primary target for Phases 1–2.
Phase 3 adds new route handlers and HTML generation functions.

---

