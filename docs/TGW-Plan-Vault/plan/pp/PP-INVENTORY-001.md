# PP-INVENTORY-001 — physical inventory verification

**Status: PLANNED 2026-07-16** — real workflow design below, replacing the
prior "not started, needs its own scoping pass" placeholder. Opened
2026-07-11 (Dave: "11 is an entire missing PP — the tools to accomplish
the job, both the standard manual tool as well as the already supposedly
in the plan AI vision inventory helper.")

## Not the same thing as PP-VISION-001's findability — clarified 2026-07-16

Dave, when asked whether this PP is just PP-VISION-001 applied: it's
related but the actual workflow is more specific than generic vision
matching, and worth spelling out because the two are easy to conflate:

> "Vision worker finds items in photos of items in box taken by inventory
> worker and starts checking off boxes on the location inventory. Operator
> completes the rest to cleanup, looks for missing or marks missing etc."

PP-VISION-001 (`pp/PP-VISION-001.md`) is the underlying *capability* —
given a photo, return ranked candidate SKUs by visual similarity, queried
one photo at a time. PP-INVENTORY-001 is a specific *workflow* built on
top of it: batch reconciliation against a known expected-contents list
per location, not a one-off "find this specific item" query.

## The workflow

1. **Location has an expected-contents manifest.** Each storage
   location/box has a set of SKUs the record system says should be there
   (derivable from the existing per-item location field — no new schema
   needed to define "what's expected," just a query: all items whose
   current location == this box/shelf).
2. **Inventory worker (a person) photographs the location/box contents** —
   one or more photos covering what's physically inside, same capture
   pattern as existing intake photography, not a new camera workflow.
3. **Vision worker (PP-VISION-001's capability) identifies items in the
   photo(s)** and matches them against the location's expected-contents
   manifest from step 1 — this is the same embedding-similarity search
   PP-VISION-001 designs, just scored against a *known candidate set*
   (the manifest) rather than the whole catalog, which should make this
   an easier matching problem than open-set findability (smaller
   candidate pool, higher expected precision).
4. **Auto-checks off found items** — each manifest item that gets a
   confident match from step 3 is marked verified-present, with the
   match confidence and photo reference retained (provenance, same
   discipline as any other AI-assisted write — never silently overwrite,
   always attributable).
5. **Operator completes the reconciliation** — reviews whatever step 3/4
   left unresolved: items on the manifest with no confident match
   (missing? misfiled? just a bad photo angle?) and items visible in the
   photo with no manifest match (found something unexpected — wrong
   location, mislabeled, or a genuine data error). Operator marks
   confirmed-missing, confirmed-misfiled (with a location correction), or
   requests a re-photograph before deciding. This is the actual "record
   integrity" write path — mirrors PP-DATAINTEGRITY-001's reconciliation
   pattern (operator adjudicates, system never silently resolves an
   ambiguous case).

## Relationship to PP-STORAGE-001 / PP-VISION-001 / PP-DATAINTEGRITY-001

- **PP-STORAGE-001** — *where* items live (size-class organization). This
  PP consumes location data PP-STORAGE-001 organizes, doesn't change it.
- **PP-VISION-001** — the underlying visual-matching capability. This PP
  is one of its two named consumers (per PP-VISION-001's own doc), the
  concrete shape of the "automated verification leg."
- **PP-DATAINTEGRITY-001** — data *record* integrity generally (photo
  corruption, sold-order-history gaps). This PP is specifically
  physical-stock-vs-record reconciliation, a narrower and more concrete
  case that happens to share the same "operator adjudicates ambiguity"
  discipline.

## Two legs, sequenced

1. **Manual leg (buildable now, no PP-VISION-001 dependency)** — absorbs
   `#11` (`tgw ebay-sweep → physical inventory review`), gives an operator
   the manifest-vs-physical-check workflow above with step 3/4 done by
   eye instead of by the vision worker. This can ship before the vision
   leg exists and immediately becomes more useful once it does (same
   manifest/checklist UI, just gains an auto-check-off step).
2. **Vision-assisted leg** — steps 3/4 above, gated on PP-VISION-001's
   Phase 2 (full-catalog embedding index) existing, since matching against
   a manifest still needs stored reference embeddings per SKU to compare
   the box-photo against. Not buildable before PP-VISION-001 Phase 2
   lands.

## 2026-07-31 priority correction — build through the Catch-22

Dave needs to inventory now, while neither TGW's current state nor eBay's current state can be presumed complete or correct. That uncertainty is not a reason to postpone the manual leg; it defines the surface's central contract.

The manual workflow must keep independent claims separate:

1. **TGW expected state** — the current location manifest and local lifecycle fields, visibly source-labelled and never silently treated as physical truth.
2. **Fresh eBay state** — active identity/lifecycle and drift evidence, with retrieval time and completeness. Presence at eBay does not prove physical presence; absence from the active set does not by itself prove sold.
3. **Sold/order evidence** — provider order lines and retained sale records, including unresolved/failed reconciliation states. A sold candidate is not silently converted into a local status.
4. **Operator physical observation** — present, missing, unexpected, misfiled, quantity/count, needs recheck, or unknown, retained with inventory-session, actor, time, and evidence.

The first site must be location-by-location and resumable. It presents contradictions rather than picking a winner, and it provides explicit review queues for duplicate, drift, sold-uncertain, unexpected, and missing states. It must remain usable when eBay or sold-data retrieval is stale/unavailable by showing that source as unavailable instead of collapsing it into "no difference."

No physical observation, eBay read, or model proposal automatically overwrites TGW, changes a sold/stock status, moves an item, or performs a marketplace action. Those are separate reviewed transitions with receipts.

**Prioritized implementation:** todo #1719 (P1) is the manual inventory-reconciliation site. Todo #1718 supplies complete eBay drift classifications, #1713 supplies duplicate-active identity evidence, and #1681/PP-SOLD-001 supplies trustworthy sold-order reconciliation. The site must integrate these when available but must not wait for them to become perfect; unavailable inputs remain explicit `UNKNOWN` states.

## Identity preservation and bounded listing healing — Dave, 2026-07-31

Inventory reconciliation must retain both identities rather than choose one prematurely:

1. **TGW/local identity snapshot** — the canonical/draft identity and historical local values as they existed at batch freeze time.
2. **eBay/provider-observed identity snapshot** — current provider fields plus every known historical/current listing and offer ID, explicitly provider-observed rather than submitted history.
3. **AI identity proposal** — a third object produced by the existing identify/reidentify pipeline using both snapshots as labelled hints. It is a proposal, never a rewrite of either source identity.

Todo #1720 builds a dedicated reconciliation/healing workbench on top of #1719. For each item it shows the two retained source identities side by side, their field conflicts and provenance, the exact hints given to AI identify, the proposed identity, and explicit accept/reject/defer controls. Acceptance may heal the local canonical record and draft only through the existing serialized state-machine path with a receipt. eBay revise/end/relist/publish remains a separate named, reviewed action; accepting identity is not marketplace authority.

The batch contract is strict: at most **200 items per checkpointed batch**, never the catalog at once. Each batch freezes its membership and source hashes, resumes safely, reports proposed/accepted/rejected/deferred/error/unknown counts separately, and cannot silently spill into the next batch. Preserve source snapshots, model/config identity, raw proposal, operator decision, and supersession linkage append-only so both original identities remain referable after healing.

Healing does not retire assurance. Duplicate-active, eBay state-drift, sold/order, submitted-provenance, and physical-inventory monitors remain independent of the workbench and continue after a proposal is accepted. A healed local record may reduce a discrepancy; it must not suppress a monitor without new source evidence.

### Image alt-text evidence and proposals

The same workbench includes image-specific alt text. Preserve each raw/selected image under a stable asset identity/hash, its ordering and provider URL observations, and every existing local, model, or operator alt-text value with provenance. AI identify receives the actual image plus the two labelled identity snapshots and may propose alt text for that image as part of the third candidate; it must not infer unseen condition, contents, or claims merely from either text identity.

Show the image, source history, and proposed alt text together, with accept/edit/reject/defer per image. Never silently overwrite accepted/operator alt text. An accepted value enters only the local candidate/draft through the serialized reviewed path, retaining model/config/time/source and supersession receipt; acceptance is not authorization to revise or publish on eBay. The 200-item cap remains the batch boundary, while receipts report item, image, proposed-alt-text, accepted, edited, rejected, deferred, unknown, and error counts separately.

## Out of scope for the first manual slice

- A separate second inventory authority or client-specific business logic. The first operator site should use the existing TGW web/API substrate and a shared typed contract that Flutter can later consume; it must not wait for a complete Flutter/offline build.
- PP-VISION-001's embedding mechanics — cross-referenced, not duplicated.
- Confidence-threshold tuning for "auto-check-off vs. flag for operator"
  — a real calibration question for whoever builds step 3/4, not
  pre-decided here.

## Next step

Todo #1719 is now P1 and is the dispatchable first manual slice. Build and verify the resumable source-labelled location checklist before adding vision assistance. Integrate #1718/#1713/#1681 evidence through explicit available/stale/unknown states; do not make their full completion a prerequisite for the manual checklist.

## Cross-links
- `pp/PP-VISION-001.md` — underlying vision-matching capability + phasing.
- `pp/PP-STORAGE-001.md` — the storage-organization data this reconciles
  against.
- `pp/PP-DATAINTEGRITY-001` (master-plan section) — shared "operator
  adjudicates ambiguity" discipline.
- `#11` — the manual-sweep starting point, already named before this PP
  existed.
