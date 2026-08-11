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



### eBay pipeline condition-driven migration (2026-08-06)

This PP’s intended execution model is the **same condition-driven kernel being proven
for coding**, applied to the eBay/item pipeline. It is not a separate planning system:
Event Clips preserve observations and worker results; the evaluator derives the current
condition/fingerprint for an item generation; the state machine selects legal
transitions; the scheduler chooses one eligible treatment; that treatment returns a
structured receipt; and the item is re-evaluated from the new evidence.

For `ai_identify`, the target behavior is a bounded, generation-fenced treatment that
claims one current item generation, reads its condition, performs one legal
identification/remediation action, writes a receipt, and exits. It must **not** decide
or directly enqueue hardcoded successors such as `ebay_draft` or `alt_text`. After the
receipt, the evaluator and scheduler choose the next legal eBay treatment from current
conditions. Durable `not_before`/wait conditions represent waiting; remediation,
reconciliation, and contradiction conditions remain ordinary evaluable work rather
than dead ends.

This section records a required migration, not a claim that the eBay pipeline is
already live on that model. The missing helper/import defect below is one compatibility
condition within the migration: it must be repaired with focused regression coverage
and then carried through the native queue → local worker → receipt → re-evaluation
path. It does not authorize live eBay mutation, credentials/provider changes, queue
replay, or a parallel/ad-hoc worker route.

**Current bounded defect:** `tgw.workers.ai_identify` imports
`get_category_group_aspects` from `tgw.apis.ebay.specifics`, but the helper is absent
from the inherited source baseline. This prevents six ai-identify test modules from
collecting. Todo **#1739** owns restoration of the canonical helper/caller contract
and regression coverage. An approved PP/Todo authorizes the ordinary repair/test/
review/receipt progression; only a genuine unresolved decision, a prepared live
external effect, or final operator acceptance may pause it.


### Execution compilation and acceptance contract (Dave, 2026-08-06)

A maintained PP is the input to execution—not a prose note that must be manually retranslated into a one-off Todo list. For a requested PP root, the coding system first compiles the exact PP text, linked decisions, existing Todos, source/evidence anchors, and known dependency selectors into one versioned **execution packet**. The packet must preserve alternatives and UNKNOWN evidence until connected resolution; it uses the Luet/Portage-style resolver work tracked by **#1729**, rather than a greedy early choice or a hand-written linear checklist.

The initial planning run produces a Dave-readable proposal before coders begin:
- target outcome and governing PP/decision hashes;
- resolved and unresolved transitive dependencies, alternatives, and evidence gaps;
- ordered or parallelizable implementation/review/test/receipt treatments;
- bounded worker/orchestrator instructions, expected artifacts, and the exact consequential/live-effect boundary;
- the generation/fingerprint that will fence execution and the acceptance evidence.

Dave may accept that packet or refine it. Acceptance authorizes the ordinary execution chain it describes. The scheduler then materializes the resolved graph into durable jobs and routes bounded coder/reviewer/orchestrator packets; worker receipts update the graph and re-evaluate the affected PP/Todo condition automatically. It does not require Dave to restate each implementation step or each mechanical remediation.

The intended user roots are a PP or a Todo: conceptually, `tgw coding start PP-DERIVED-001` compiles and executes this PP’s accepted closure, while `tgw execute todo <id>` compiles and executes that Todo’s accepted dependency closure. These are required operator semantics, not a claim that the currently deployed CLI already supports PP-root execution. The implementation must expose the same PP/Todo → packet → acceptance/refinement → durable graph → worker receipts path without an SSH fallback or an ad-hoc parallel planner.


### Quick-Todo planning split path (Dave, 2026-08-06)

A quickly submitted Todo is permissible, but it is **planning input**, not an
independent authorization to dispatch coders. The defined random-Todo split path
runs automatically through duplicate/PP/decision lookup, source reconciliation,
classification, PP-root selection or attachment, and Luet-style dependency closure.
It then produces the same versioned execution packet as a PP-root request.

Automation stops at the first operator involvement point: the Action Card that presents
the compiled plan for acceptance or refinement. Approval of that planning session alone
is valid operator involvement. Until that acceptance, the system may prepare evidence,
dependencies, worker/orchestrator instructions, and the proposed durable graph, but it
must not begin implementation work merely because a Todo was quickly entered. After
acceptance, the ordinary accepted chain proceeds automatically and receipts keep the
Plan/Todo condition current.


### Current listing conditions for the next compiled execution packet (operator report, 2026-08-06)

Basic listing improved after the previous day’s committed recovery work, but the
next PP-root planning pass must treat the following as current conditions rather
than independent ad-hoc fixes:

1. **Bulk listing:** prepare and prioritize the existing bulk-listing capability;
   operator expectation is substantially higher listing throughput (approximately
   fivefold) once it is usable. Reconcile its existing plan/Todo home before any
   new implementation task.
2. **`ai_identify`:** repair both its bounded execution contract and the quality
   of its identification output. Quality must be evaluated against retained
   evidence and listing-schema consequences, not declared successful merely
   because the worker returned.
3. **Condition-driven eBay migration:** remove hardcoded successor/retry behavior
   from the eBay path in favor of evidence-driven re-evaluation. A missing
   condition or unchanged evidence is durable waiting, not an immediate one-minute
   retry loop. The current queue snapshot has `ebay_publish` and `ebay_stage`
   retry-wait records; those are evidence for the planning packet, not authority
   to bulk replay jobs.
4. **Missing `Model` specifics:** this is an active listing-surface quality
   condition; preserve the existing #1711 evidence and make taxonomy/schema
   exposure, draft construction, and operator correction one connected remedy.
5. **Photo order:** marketplace image selection and ordering must be explicit
   operator-facing metadata. Publishing must consume that selected ordered set,
   not a filesystem scan or implicit upload order. The missing reorder affordance
   is a planning condition; it must not silently reorder already-published eBay
   images.

The planning packet must reconcile these with the Plan graph and existing Todo
inventory, select the appropriate root/closure, and present the joined plan for
acceptance or refinement before any new marketplace-side action.


### Item-status and return-to-listing control (operator requirement, 2026-08-06)

The listing surface must let the operator change an item’s current work condition
and return it to the listing goal when new evidence makes that legal. This is not
a blind job requeue. A status action records the reason, actor, time, evidence,
and target goal; the evaluator then derives the next legal treatment from the
new condition.

Required first-class reasons include:
- `needs_photo_recapture` (for blurry or insufficient photos);
- `needs_measurements`;
- `discarded`;
- `policy_blocked` / `policy_violation`;
- `needs_split_review` / `item_split_required`;
- and a return-to-listing request targeting `EBAY_LISTABLE` after corrected
  evidence is available.

These are concurrent work requirements, not one overloaded lifecycle string.
They must be visible and editable from the item surface with evidence and a
clear current state. A return-to-listing action clears only the requirement
actually resolved and causes re-evaluation; it must not restart the full chain,
replay historical failures, or publish automatically.

`discarded`, an item split, and any marketplace-side action retain their separate
consequential contracts. In particular, a split remains an operator-reviewed
proposal that preserves raw assets and listing provenance; it does not silently
create child items or alter/cancel an existing eBay listing. The planning pass
must reconcile the already planned split workflow before implementing this
surface.


### Ongoing maintenance, not an intake-only flow (clarification, 2026-08-06)

These requirements apply throughout an item’s life, including after a draft,
stage, publication, later observation, or physical review—not only during initial
intake. `add_photos`, `retake_photos`, `reidentify`, measurements, condition
correction, policy review, split review, and return-to-listing are regular
maintenance treatments. New evidence can reopen the applicable requirement and
cause a new generation-bound evaluation at any time.

The lifecycle is therefore revisable but evidence-fenced: a later maintenance
receipt changes only the affected conditions and derives the smallest legal next
work. It does not erase prior provenance, silently downgrade a verified listing,
or repeat unrelated pipeline stages.
