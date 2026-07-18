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

## Out of scope (this planning pass)

- The actual manifest/checklist UI — web UI vs. Flutter/mobile is an open
  question folded into the broader UI/UX unification project (see
  master-plan's UI/UX section) rather than decided here; this PP defines
  the workflow the UI needs to support, not which surface builds it.
- PP-VISION-001's embedding mechanics — cross-referenced, not duplicated.
- Confidence-threshold tuning for "auto-check-off vs. flag for operator"
  — a real calibration question for whoever builds step 3/4, not
  pre-decided here.

## Next step

The manual leg (point 1 above) is buildable today, independent of
PP-VISION-001's timeline — file a todo for it once Dave prioritizes it
against the rest of the active build queue. The vision-assisted leg
follows naturally once PP-VISION-001 Phase 2 lands; no separate design
work needed at that point beyond wiring steps 3/4 into whatever manifest/
checklist UI the manual leg already built.

## Cross-links
- `pp/PP-VISION-001.md` — underlying vision-matching capability + phasing.
- `pp/PP-STORAGE-001.md` — the storage-organization data this reconciles
  against.
- `pp/PP-DATAINTEGRITY-001` (master-plan section) — shared "operator
  adjudicates ambiguity" discipline.
- `#11` — the manual-sweep starting point, already named before this PP
  existed.
