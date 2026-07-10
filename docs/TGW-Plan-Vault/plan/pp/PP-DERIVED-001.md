## PP-DERIVED-001 — Full Capture of Derived and Acquired Data

**Opened:** 2026-06-17 (session 34)
**Status:** PLANNED — not yet started (blocked on budget)
**Core principle:** Every API call, every vision scan, every barcode lookup returns richer
data than we currently store. We are paying for that data with time, compute, and API quota.
Discarding it is waste. All raw responses should be preserved, all photos should be scanned,
and the data should be associated with the specific tool and input that produced it.

### Audit findings (2026-06-17)

| Data source | What we capture | What we discard |
|-------------|-----------------|-----------------|
| Vision scan (`ai_identify`) | 4 fields: title, category, description, condition | Full model response; which photo was used; model reasoning; all other fields returned |
| Photos per item | 1 photo scanned (first alphabetically) | Avg 5.5 photos/item unscanned; max 39 photos on one item |
| Product lookup (upcitemdb, Discogs, etc.) | ~8 distilled fields | `extra` dict explicitly stripped; full API response (images, MSRP, dimensions, offers, tracklists, genres) |
| `LookupResult.to_dict()` | Excludes `extra` by design | All source-specific fields go to `extra` and are discarded |
| Alt text (`alt_text` worker) | One alt text string per item | Which photo was analyzed; intermediate descriptions; confidence |
| eBay aspects (`ebay_draft` worker) | Filled aspect values only | Full aspect list fetched from eBay (required + recommended); browse API hints used to fill them; unfilled aspects with available options |
| Category suggestions (taxonomy) | Top match only | All suggestions with confidence scores |
| `identification_history` | 6 summary fields per round | Raw result dict; full prompt used; token usage; response latency |

**Numbers (sample of 5,000 items):**
- Items with multiple photos: 4,066 / 4,999 (81%)
- Items with vision raw stored: **0**
- Items with per-photo results: **0**
- Items with product lookup raw stored: **0**
- Newer items (tgw2026*): avg 5.5 photos, max 39 — all scanning photo[0] only

### What we're losing

**From photos:** Each photo may contain information the primary doesn't: barcode on the back,
model number on a label, serial number sticker, condition damage detail, copyright date,
manufacturer info, quantity markings. A 39-photo item has 38 unscanned photos.

**From vision raw:** Models return richer text than the 4 fields we extract. The full response
often includes brand guesses, era clues, condition evidence, related item suggestions, and
natural-language reasoning that would help the `ebay_draft` worker fill aspects.

**From product lookups:** upcitemdb returns dimensions, weight, category tree, multiple images,
and competitive pricing offers. Discogs returns full tracklist, all artists, label matrix number,
pressing country. Open Library returns subject classification, publisher, edition details, cover
scans. All of this is in `LookupResult.extra` and discarded.

**Impact:** Titles, descriptions, and item specifics are harder to determine and less accurate
than they should be. The model is making educated guesses from one cropped photo when it could
be cross-referencing 5+ angles plus a full product database record.

### Core architectural principle

**The raw scan is the asset. Derived values are disposable.**

`title`, `description`, `category`, `condition`, `item_specifics` — these are derived outputs.
They should be recomputed from the stored raw scan whenever a better model, a better prompt,
or additional context (product lookup, barcode data) becomes available. The photo never changes.
One complete scan stored with its full metadata = permanent raw material that improves in value
over time as models improve.

The scan record must be fully reproducible: given the stored `photo_hash`, `model`, `prompt`,
and `prompt_context`, we can confirm that re-running produces equivalent output, or flag drift.

**What this unlocks:** When we upgrade from Gemini Flash to a better model, we don't need to
re-scan photos. We re-derive from stored raw responses. When we improve the prompt, we can
identify which items were scanned with the old prompt and re-derive just those. When we add a
barcode lookup result after the initial scan, we can re-derive aspects without touching eBay.

### Design

```
item JSON after full capture:

"vision_results": [
  {
    "photo":         "filename.jpg",        // which photo
    "photo_hash":    "<dhash>",             // content fingerprint
    "model":         "openrouter/google/gemini-2.5-flash",
    "prompt_type":   "enriched|hinted|plain",
    "prompt_context": "brand/product hint injected",  // reproducibility
    "prompt_version": "v3",               // prompt template version tag
    "scanned_at":    "ISO",
    "raw_response":  "<full model text>",  // THE ASSET — never discard
    "extracted": {                         // distilled fields from raw
      "title": "...", "category": "...", "description": "...", "condition": "..."
    },
    "token_usage":   { "prompt": N, "completion": N }
  }
  // one entry per photo × per scan (re-scans with new model append, not replace)
],

"product_lookup": {
  // current distilled fields stay at top level
  "source": "...", "title": "...", etc.,
  "raw": { /* full API response from source — currently discarded */ }
},

"photo_inventory": [
  {
    "filename":   "...",
    "hash":       "<dhash>",
    "size_kb":    N,
    "scanned_by": ["ai_identify", "alt_text"],  // which workers have touched it
    "added_at":   "ISO"
  }
]
```

`vision_results[]` grows over time — each scan appends. Old results are never deleted.
This gives us a training dataset of photo → result pairs as a side effect of normal operation.

`photo_inventory` is the scan scheduler: items where `len(scanned_by) < len(photos)` have
unscanned photos. A background command can target exactly those.

`product_lookup.raw` captures the full API response; derived fields stay at the top level
for backward compatibility.

### Phases

- **Phase 1 — Store full vision result per scan** (small change, high value):
  - Save `raw_response` alongside `extracted` in each history event
  - Record `photo` (filename) and `photo_hash` in each history event
  - No schema change to existing fields; additive only
  - Files: `workers/ai_identify.py` (2 line change), `image_hash.py`

- **Phase 2 — Scan all photos, not just first** (high value, moderate cost):
  - `_primary_image()` → `_all_images()` iterator
  - Run vision on each photo; store result in `vision_results[]`
  - Merge/rank results: prefer the photo that produced the most complete extraction
  - Use pHash cache to skip re-scan of unchanged photos
  - Rate limiting: cloud provider models (OpenRouter/Google) can do 5+ photos in parallel
  - Files: `workers/ai_identify.py` (significant change), `apis/llm.py`

- **Phase 3 — Store full product lookup raw responses**:
  - `LookupResult.extra` → keep in `product_lookup.raw` (remove the `pop('extra')` in `to_dict()`)
  - Each source stores its full API JSON under `product_lookup.raw.<source>`
  - Files: `apis/lookup/base.py` (1 line), each lookup module

- **Phase 4 — Photo inventory**:
  - At intake, scan the item directory and write `photo_inventory` block
  - Track `scanned_by` list per photo; update when each worker processes it
  - Enable "rescan unscanned photos" as a targeted command
  - Files: `workers/ai_identify.py`, `api.py`, possibly `multi_intake.py`

- **Phase 5 — Retroactive backfill**:
  - Re-run ai_identify on ALL photos for items that have been identified
  - pHash cache makes this cheap for already-scanned photos (cache hit = instant)
  - New photos get full vision scan; merge into `vision_results`
  - Estimated cost: ~$0.001/photo on Gemini Flash; 50k items × 5 avg photos = ~$250 one-time

### Provenance weighting and derivation engine

Evidence is collected from multiple sources. The derivation engine assigns weights by source
authority and picks the winner for each field. Vision data and operator data are fully decoupled:
scanning can run (or re-run) independently of derivation, and derivation can re-run independently
of scanning.

**Source weight hierarchy (highest → lowest):**

| Weight | Source | Examples |
|--------|--------|---------|
| 100 | Operator direct entry | Editor form save, `tgw update title` |
| 90 | Operator hint | `SETTEMPLATE:`, `ai_hint` field, `tgw hint` command |
| 80 | Authoritative product DB | Discogs barcode match, Open Library ISBN, IGDB, UPCItemDB |
| 60 | Vision scan — enriched | Model saw photo + product DB context |
| 50 | Vision scan — hinted | Model saw photo + operator hint |
| 30 | Vision scan — plain | Model saw photo only |
| 10 | Category default / fallback | ebay_draft assumed value for unfilled required aspect |

**Conflict resolution:** When two sources disagree on a field, the higher-weight source wins.
A weight-80 Discogs result overrides a weight-30 plain vision result for `brand`. An operator
direct-entry at weight-100 overrides everything and is never touched by re-derivation.

**Derivation is a separate pass:** After evidence is collected (scans, lookups), the derivation
engine walks every required field, collects all evidence with weights, picks the winner, and
writes the derived value along with its provenance:

```json
"derived": {
  "title": {
    "value": "Junior M.A.F.I.A. — Get Money The Remix Cassette Single",
    "source": "vision/enriched",
    "weight": 60,
    "from_scan": "vision_results[1]",
    "derived_at": "ISO"
  },
  "brand": {
    "value": "Big Beat Records",
    "source": "product_lookup/discogs",
    "weight": 80,
    "derived_at": "ISO"
  }
}
```

**Deficiency detection:** After derivation, the engine checks each required field:
- Missing → deficiency (no evidence at all)
- Low-weight winner (≤30) → candidate for improvement
- Conflicting sources within 10 weight points of each other → flag for review

Detected deficiencies go to a patch queue. Patches are targeted jobs:
- "no barcode lookup yet" → enqueue product_lookup
- "only plain vision scan, product found" → re-enqueue ai_identify with enriched prompt
- "required aspect unfilled" → flag for operator attention in editor

**Parallel execution:** Because scan ≠ derive, all photos can be scanned concurrently while
derivation waits. When all scans for an item are complete, derivation runs once and produces
the best possible values from the full evidence set. If a new scan arrives later (operator
adds a photo), derivation re-runs only on the affected item.

**Improvement loop:**
```
photos → [scan workers, parallel] → vision_results[]
barcodes → [lookup workers] → product_lookup.raw
operator entries → directly written at weight 100
all evidence → [derivation engine] → derived{}
derived{} → [deficiency detector] → patch_queue[]
patch_queue[] → targeted re-scan or re-lookup
```

This means the listing quality improves automatically as better evidence arrives, without
any pipeline re-runs or manual intervention beyond the initial patch queue drain.

### Research and refinement gate

Existing research in `docs/TGW-Plan-Vault/dev-workflow/research/` covers model selection and
provider comparison. Before implementing Phase 2+, run through Perplexity deep research to
refine: optimal model for multi-photo item identification, prompt structure for maximum raw
data capture, cost per photo at scale, and whether to parallelize per-photo calls or batch.
The refined research becomes the spec for implementation.

### Dependencies / gates

- Phase 1, 3: no cost, implement anytime — purely additive, no new API calls
- Phase 2: cloud vision API key (Google AI Studio key already available); design after Perplexity research
- Phase 5: budget for API calls (~$250 one-time at Gemini Flash rates for 50k items × 5 photos)

---

