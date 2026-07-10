# PP-LISTEDITOR-001: Listing editor — full item detail page replacing Seller Hub

**Status:** BUILDING — 2026-06-25
**PP ref:** PP-LISTEDITOR-001; absorbs todos #876 #877 #878; implements PP-REVISION-001 apply path

## Problem / motivation

Dave cannot use eBay Seller Hub and has been operating without it for a month. 18 items have
been sitting UNPUBLISHED since June 3–5. The pipeline brings items to a staged/priced state
but there is no UI to review, correct, and confidently publish them. Everything Seller Hub does
for listing management must exist in the TGW web UI. The design must also accommodate future
marketplaces (Facebook Marketplace, TGW website, Etsy) from the same interface.

## Settled architecture constraints

- All ItemData reads/writes go through tgw-api (PATCH /api/items/{sku})
- Output contract: every API call returns `{ok, ...}`
- PP-REVISION-001 (decided 2026-06-12): changes via draft → review → apply; sparse delta + pinned baseline
- Inventory API PUT is full-replace; composition (live mirror + delta) at apply time
- `_APPLY_ENABLED = False` in revision.py is the only gate on live writes

## Field model (settled 2026-06-25)

### Canonical fields — owned by TGW, never touched by marketplace workers

| Field | Written by | Notes |
|-------|-----------|-------|
| `title` | ai_identify, operator "Save to Catalog" | Our name for the item. Never changed by listing workers, title rotation, or ebay_sync |
| `description` | ai_identify, operator | Short internal description (2–3 sentences) |
| `condition` | ai_identify, operator | Human-readable: "Very Good", "Good", etc. |
| `category` | ai_identify | TGW internal category |
| `item_attributes` | ai_identify, operator (intake + editor) | Type-specific facts keyed to eBay category aspects. Canonical store — maps to draft_listing.item_specifics at stage time |
| `floor_price` | operator | Minimum acceptable price across ALL marketplaces. Repricer never goes below this |
| `cost` | operator (optional) | What we paid. Informs floor but not mandatory |
| `location` | intake, operator | Bin label |

### Marketplace listing blocks — each marketplace owns its own fields

```
draft_listing.*          ← eBay (exists, keep as-is)
  .title                 ← eBay SEO title (≤80 chars); seed from canonical title once, then independent
  .description           ← eBay HTML long-form
  .condition             ← eBay display label
  .condition_enum        ← eBay API enum (USED_EXCELLENT etc.)
  .condition_description ← eBay free-text condition supplement (eBay-only field)
  .item_specifics        ← derived from item_attributes at stage time
  .price                 ← eBay listed price (110% max comp launch)
  .target_price          ← eBay markdown floor (p25 comp)
  .price_comps           ← eBay Browse API comps
  .imageUrls             ← EPS hosted URLs

fb_listing.*             ← future Facebook Marketplace
  .title / .description / .condition / .price

tgw_web_listing.*        ← future TGW website
  .title / .description / .price / .sale_price
```

**Rules:**
- Editing `draft_listing.title` never writes back to canonical `title`
- Editing `draft_listing.price` never writes back to canonical `floor_price`
- `ebay_live` pull never overwrites any canonical field
- `ebay_sync` never overwrites any canonical field
- Title rotation worker (PP-TITLEROT-001, future) writes only to `draft_listing.title`

### Two explicit save paths in the editor

- **"Save to Catalog"** — writes canonical fields. Marks draft as stale if title/description/condition changed. Prompts "Re-draft for eBay?"
- **"Save to eBay Draft"** — writes `draft_listing.*` only. No effect on catalog fields.

## What exists (do not rebuild)

| Component | State |
|-----------|-------|
| `revision.py` | Slice 1 done. Slice 2 written, `_APPLY_ENABLED = False`, eBay PUT is TODO stub at line 399 |
| `/api/ebay/aspects/{category_id}` | Live — returns required/recommended aspects with allowed values |
| `ebay_put()` in `ebay/sync.py` | Used by `stage_draft()` |
| `ebay_live` on items | ~19,300 Inventory API items |
| `ebay_offer.price_comps` | Written by `ebay_price` (p25/median/p75/max) |
| Stage / Publish / Re-draft buttons | Exist in current item detail |
| `draft_listing.item_specifics` | Written by `ebay_draft` |

## Readiness checker (marketplace-agnostic pattern)

New module `src/tgw/readiness.py`:

```python
class ReadinessChecker:   # abstract
    def check(item) -> list[ReadinessField]

@dataclass
class ReadinessField:
    name: str           # field key
    label: str          # display label
    status: str         # 'ok' | 'missing' | 'warning'
    severity: str       # 'required' | 'recommended' | 'optional'
    marketplace: str    # 'ebay' | 'facebook' | etc.
    value: Any          # current value or None
    jump_to: str        # anchor id in the editor form

class EbayReadinessChecker(ReadinessChecker):
    # Full eBay field list checked:
    # title (length), category_id, condition (vs. category policy),
    # price (not null), EPS photos (≥1 uploaded, not just on disk),
    # fulfillment/payment/return policy (auto-resolved, shown as info),
    # merchant_location (auto-resolved), each required aspect by name,
    # recommended aspects by name, description length, UPC/GTIN
```

The UI renders `ReadinessField[]` as a checklist:
- ❌ required + missing → red, blocks publish, clicks jump to field
- ⚠️ recommended + missing → yellow warning
- ✅ present → green
- ℹ️ auto-resolved → grey (shown for transparency)

Same component used in intake form and editor. Replaces the aggregate
`aspects_required_filled/total` counts with named per-field status.

## Build phases

### Phase 1A — EPS photo strip + ebay_live panel (THIS SPRINT)
- Show `ebay_live.inventory_item.product.imageUrls` as thumbnails on item detail
- Show `ebay_photos` local uploads alongside
- Collapsible ebay_live raw data panel with last-synced timestamp
- Low risk, read-only, immediate value

### Phase 1B — Editable listing editor (THIS SPRINT)
- Editable title, price (with comps range bar), condition dropdown, description textarea
- Aspects editor: fetch `/api/ebay/aspects/{category_id}`, render with REQUIRED/RECOMMENDED
  badges, SELECTION_ONLY → select, FREE_TEXT → input, pre-fill from item_attributes
- Two save buttons: "Save to Catalog" and "Save to eBay Draft"
- Readiness checklist panel (EbayReadinessChecker)
- floor_price field in catalog section

### Phase 3 — Clear the 18 staged items
- With editor in place: open → review/correct → Publish
- Publish button already works

### Phase 2 — Enable revision apply (after Phase 1B is stable)
- Fill eBay PUT stub in revision.py:399
- Extract build_inventory_body/build_offer_body from sync.py stage_draft()
- Set _APPLY_ENABLED = True
- Add revise_apply action to http_server.py
- "Push to eBay" button for live items
- Tests: test_revision.py with mocked ebay_put

### PP-TITLEROT-001 stub (future)
Title rotation worker for unsold items. Reads canonical title → generates variant →
writes only to draft_listing.title → triggers revise_apply. Never touches canonical title.
Configurable interval (default 60 days). Logs to draft_listing.title_history.

## Files to change

| File | Change |
|------|--------|
| `src/tgw/http_server.py` | Item detail restructure: EPS strip, ebay_live panel, editable fields, aspects form, price comps, readiness checklist, two save paths |
| `src/tgw/readiness.py` | New — EbayReadinessChecker + ReadinessField |
| `src/tgw/revision.py` | Phase 2: fill eBay PUT stub at line 399; set _APPLY_ENABLED = True |
| `src/tgw/ebay/sync.py` | Phase 2: extract build_inventory_body/build_offer_body from stage_draft() |
| `src/tgw/workers/ebay_draft.py` | Read item_attributes first, fall back to AI if blank |
| `docs/TGW-Plan-Vault/reference/invariants.md` | Add invariant: canonical fields never written by marketplace workers |
| `tests/test_readiness.py` | New — EbayReadinessChecker unit tests |
| `tests/test_revision.py` | Phase 2: cmd_revise_apply with mocked ebay_put |

## Acceptance criteria

- [ ] Item detail shows EPS thumbnails from ebay_live.imageUrls
- [ ] Price comps (p25/median/p75) shown inline next to price field
- [ ] Aspects render with REQUIRED/RECOMMENDED badges; SELECTION_ONLY → dropdown
- [ ] Missing required aspects named explicitly in readiness checklist (not just counts)
- [ ] Saving catalog fields never touches draft_listing.* and vice versa
- [ ] floor_price editable; no marketplace price can be saved below it
- [ ] Publish works for all 18 UNPUBLISHED staged items
- [ ] Phase 2: editing live item + "Push to eBay" updates listing without end/relist
- [ ] Phase 2: drift gate blocks apply if eBay changed an overlapping field
- [ ] Trading API items (no ebay_live): "Push to eBay" hidden, note shown

## Open questions / known iteration points

- Section layout will need refinement after first real use — design for easy restructure
- Trading API items (389 active): Phase 2 covers Inventory API only; Trading API revision via ReviseFixedPriceItem is a follow-on
- item_attributes migration: one-time pass to copy draft_listing.item_specifics → item_attributes for existing items (non-destructive, draft_listing unchanged)
