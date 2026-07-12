# PP-INVENTORY-001 — physical inventory verification

**Opened 2026-07-11.** Surfaced during a `tgw todo --by-pp` triage sweep —
Dave: "11 is an entire missing PP — the tools to accomplish the job, both
the standard manual tool as well as the already supposedly in the plan AI
vision inventory helper." Confirmed: no design doc existed for this at all
— `PP-VISION-001` was only ever a bare "(GPU-gated)" mention in the Frozen
list, no substance behind it.

## Problem / intent

Verify that what's physically on the shelf matches what the record system
(item JSON + catalog) says — the reconciliation direction, not the
findability direction. Two complementary tools, not one:

1. **The standard manual tool** — operator-driven physical sweep. Absorbs
   `#11` (`tgw ebay-sweep → physical inventory review`, run after
   Perplexity brief results arrive) — the concrete, already-named starting
   point.
2. **The AI vision inventory helper** — automated/assisted verification
   using vision matching. This is the use case for `PP-VISION-001`'s
   underlying capability (GPU-gated, currently just a bare mention — see
   that PP for the technical substrate, not duplicated here).

## Relationship to PP-STORAGE-001 / PP-VISION-001

Distinct but related concerns, not the same thing:
- **PP-STORAGE-001** (semi-chaotic storage by size-class, not category —
  `size_class` in `category-groups.json`) is about *where* items live
  physically. Storage-organization decision, already partly settled.
- **PP-VISION-001** (GPU-gated) is the underlying vision-matching
  *capability* — "find this specific known item" (findability) is its
  original framing.
- **This PP (PP-INVENTORY-001)** is the *verification* use case — "does
  physical stock match records" — which consumes PP-VISION-001's
  capability but is a different question than findability. Both the
  manual sweep and the vision-assisted helper serve this reconciliation
  goal, not a locate-one-item goal.

## Status

Not started. Both legs (manual tool, vision helper) are conceptual only —
`#11` gives the manual leg a concrete starting point; the vision leg has
no design work yet beyond the GPU-gated capability itself. Needs a real
scoping pass (like the Seller Hub / data-integrity tracks got this
session) before either leg becomes buildable work.

## Cross-links
- `PP-VISION-001` — underlying vision-matching capability (GPU-gated).
- `PP-STORAGE-001` — the storage-organization decision this reconciles
  against.
- `PP-DATAINTEGRITY-001` — related but distinct: that track is about data
  *record* integrity (photo corruption, sold-order-history gaps); this
  track is about physical-vs-record reconciliation specifically.
