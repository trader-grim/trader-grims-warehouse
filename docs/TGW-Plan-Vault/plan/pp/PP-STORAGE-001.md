# PP-STORAGE-001 — semi-chaotic storage: size_class as a size/weight signal

**Scope revised 2026-07-18 (Dave):** "I could make a csv, but the tool has to be
able to accommodate not only what is there now but editing and adding new
shipping profile classes. Even when I give the data to start with it will need
to be tweaked over time." The deliverable is a small admin config-editing tool
(list/edit/add size_class ranges), not a one-time CSV import. `flat`/`packet`/
`small_box` are today's 3 classes but the tool must support adding a 4th, 5th,
etc. going forward — same "small owned-config admin surface" pattern as the
Store Category / fulfillment-policy dropdowns just wired to live data this
session (different problem, same shape). This doesn't change the underlying
`size_class_ranges` config design below, only the delivery mechanism: build
the edit UI first, let Dave populate/tweak the real numbers through it, rather
than Claude round-tripping a CSV.

**Status: PLANNED 2026-07-16, ready to slice into todos.** Elevated from
pointer-only stub (since 2026-07-12) per Dave's 2026-07-16 direction: "we
plan them or change our mind then, we are ready to produce code." Direction
confirmed same day, framing the actual value Dave sees in this: **"this is
a great way to determine a size weight range. Especially size."** — not
storage layout for its own sake, but `size_class` as an existing,
zero-cost signal for estimating an item's physical size (and secondarily
weight) before/without a manual measurement.

## The problem this solves

Two real gaps, both verified live 2026-07-16:

1. **No shipping weight/dimension estimate exists except manual entry.**
   `weight_oz` (`reference/TGW-Item-JSON-Schema.md`, `src/tgw/ebay/
   sync.py:590-603`) is the only real per-item shipping datum in the
   system, and it is purely operator-entered at intake — no category
   default, no size_class-derived fallback, no dimension field at all
   (confirmed: `weight` is legacy-only, no `length`/`width`/`height` key
   anywhere in the schema). When an operator skips it, there is nothing.
2. **Findability in physical storage has no size/weight cue.** Storage is
   size-class-organized (`category-groups.json`'s 25 groups tagged
   `flat`/`packet`/`small_box`), but the only existing consumer of
   `size_class` for *finding* something is `cmd_locate`
   (`api.py:2197`) — an image-similarity search that narrows candidates
   by size_class but gives no independent physical cue (an operator
   standing in front of a shelf still can't use size_class alone to guess
   "this is roughly shoebox-sized, a few ounces").

`size_class` already exists on every category group, for free, as a side
effect of the existing category taxonomy. This PP is: turn that tag into
an actual numeric size/weight **range**, not just a bucket label.

## Design

### 1. New config structure: `size_class_ranges`

Add a top-level key to `/opt/TGW/config/category-groups.json` (or a new
sibling config file if Dave prefers keeping ranges separate from category
taxonomy — open question, default to same file since `size_class` is
already defined there):

```json
"size_class_ranges": {
  "flat": {"weight_oz": [null, null], "dims_in": {"l": [null, null], "w": [null, null], "h": [null, null]}},
  "packet": {"weight_oz": [null, null], "dims_in": {"l": [null, null], "w": [null, null], "h": [null, null]}},
  "small_box": {"weight_oz": [null, null], "dims_in": {"l": [null, null], "w": [null, null], "h": [null, null]}}
}
```

**The actual numbers are NOT Claude's to fabricate** — per Prime Directive
1 (never claim false precision) and Dave's own framing of the value being
in *his* size/weight judgment applied to a size_class he defined. This
design doc proposes the mechanism; **Dave supplies the real ranges** for
each of the (currently 3 distinct, `flat`/`packet`/`small_box`) size
classes from his own physical handling knowledge, or from a quick sample
measurement pass (weigh/measure ~5-10 real items per class already in
storage) if he'd rather ground it empirically than estimate from memory.
Ranges are intentionally wide bands, not point estimates — the whole
point is "this envelope, not that one," not false precision.

### 2. Estimation fallback, not replacement

When `weight_oz` is unset at draft/publish time, fall back to the
midpoint (or a configurable percentile) of the item's `size_class`
range — **but the item must carry a marker showing the value is
estimated, never silently presented as a real operator measurement.**
Add `weight_oz_source: "measured" | "estimated"` alongside `weight_oz`
(mirrors the existing `pricing.source` pattern already in
`category-groups.json`, e.g. `"source": "velocity_p25"`). A `catalog-
verify` rule or dashboard count of `weight_oz_source == "estimated"`
items becomes the visible worklist of "still needs a real measurement" —
same self-healing-system pattern as other TGW facilities (surface, don't
just log).

**Never write an estimated weight onto an eBay listing silently as if
measured** — this is a live-listing accuracy concern (same class of issue
as invariant C14's Material-field incident, just for shipping weight
instead of an aspect). Whether an estimated weight is even acceptable to
publish live, or should block publish until measured, is Dave's call —
flag as an explicit open question for the first todo, not decided here.

### 3. Findability use case

Once ranges exist, `cmd_locate`'s output (and any future PP-VISION-001
findability flow) can print the size/weight envelope alongside each
candidate match — "candidate SKU, size_class=packet (2-8oz, fits in a
shoebox)" — giving an operator a physical sanity check independent of the
image match itself. Low-effort addition once the ranges exist; not a
separate build phase.

### 4. Backfill

Extend the existing `data_scrub_size_class_backfill` pass
(`src/tgw/scrub.py:151`) — which already backfills `size_class` from
`category_group` — with a parallel backfill that sets
`weight_oz_source: "estimated"` + a range-midpoint `weight_oz` for any
item currently missing weight entirely. Same dry-run-first, sample-verify
discipline as any other bulk scrub pass (invariant E5 pattern, mirrors
`PP-LISTEDITOR-001`'s field-set migration precedent for how a bulk
backfill packet should be structured).

## Out of scope (this planning pass)

- Actual dimension/weight numbers — Dave's to supply, not a code task.
- Any change to `fulfillment_policy_by_size_class`'s existing
  policy-selection logic (`ebay/sync.py:258-340`) — that's a different
  size_class consumer (shipping *policy*, not weight estimate) and stays
  as-is.
- PP-VISION-001's actual image-matching upgrade — this PP only adds the
  size/weight *display* alongside whatever locate mechanism exists at the
  time (perceptual-hash today, vision-embedding later).

## Next step

File a todo for the first real packet once Dave supplies (or authorizes a
measurement pass to derive) the actual `size_class_ranges` numbers — the
schema/config/backfill work above is otherwise fully spec'd and
delegatable per the planner rubric (`reference/PP-HERMES-EA-001-planner-
rubric.md`).

## Cross-links
- `reference/TGW-Item-JSON-Schema.md` — `weight_oz` field, needs the new
  `weight_oz_source` key documented once built.
- `src/tgw/scrub.py:151` — existing size_class backfill pass this extends.
- `src/tgw/ebay/sync.py:258-340` — the *other* size_class consumer
  (fulfillment policy), unaffected by this PP.
- `pp/PP-VISION-001.md` — the findability consumer this PP feeds.
- CLAUDE.md invariant C14 — precedent for why estimated-vs-measured data
  must never silently pass as real on a live listing.
