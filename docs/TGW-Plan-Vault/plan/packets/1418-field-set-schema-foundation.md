# Packet: field-set schema foundation — make Set A/Set B unmissable

Todo: #1418   PP: PP-LISTEDITOR-001   Track: schema + docs (foundation packet — build FIRST)

**Sequencing: this packet must land and merge before #1416 and #1417
start implementation.** Both depend on the envelope shape and naming
this packet establishes; building the boundary fix or the reverse flow
against the old bare-dict shape would mean re-touching the same files
twice. This is deliberate — a schema change under a moving fix makes
things worse, not better (session note, 2026-07-15).

## Context budget (ALL the model may load)
This packet + #1416 and #1417's packet docs (for full context, don't
re-derive the investigation) + `src/tgw/workers/ebay_draft.py` (whole
file — current writer of `item_specifics`) + `src/tgw/workers/ai_identify.py`
lines ~300-330 (current writer of `item_attributes`) + the existing
`price_history` implementation (`http_server.py` price-edit-history
block, session-42 pattern — this is the proven precedent being
extended, read it as the reference shape) + `reference/TGW-Item-JSON-Schema.md`
+ `reference/invariants.md` (whole doc is short enough; this packet adds
a new entry near C11/C12).

## Verified live before this packet was written
1. **The problem is legibility, not just correctness.** Dave, this
   session, after agreeing the envelope+history design is technically
   right: "the important thing is that the sets are immediately
   recognizable to anyone updating around it." Two prior sessions this
   week (#1291, #1313/#1316) each fixed a real bug in this territory
   without ever noticing the set-boundary problem underneath — meaning
   the current bare-dict shape (`item_attributes` as a plain dict, no
   self-identifying marker, indistinguishable at a glance from any other
   dict in the item JSON) actively failed to signal its own nature to
   two separate careful, test-writing sessions. This is the failure this
   packet fixes directly — not a nice-to-have.
2. **Proven precedent already in this codebase**: `price_history` is
   exactly the shape being extended here — current value stays a cheap
   scalar/dict for fast reads, every change appends to a history array
   with `{ts, price, previous_price, source, label}`. `alt_text_results[]`/
   `vision_results[]` are the same pattern for raw AI-call preservation.
   This packet is the third application of an already-validated shape,
   not a new invention — cite this precedent in the schema doc addition
   so a future reader sees the pattern is established, not ad hoc.
3. `item_attributes` is entirely undocumented in
   `TGW-Item-JSON-Schema.md` today (confirmed zero grep hits) — this
   packet is also the first time it gets a home in that document.

## Spec

1. **Envelope shape** for both sets. Replace the bare dicts with:
   ```json
   "item_attributes": {
     "_set": "inventory_record",
     "version": 1,
     "updated_at": "2026-07-15T...Z",
     "fields": { "Type": "Brooch", "Brand": "Unbranded", ... }
   }
   ```
   and, inside `draft_listing`:
   ```json
   "item_specifics": {
     "_set": "ebay_draft",
     "version": 1,
     "updated_at": "2026-07-15T...Z",
     "fields": { "Type": "Brooch", ... }
   }
   ```
   `_set` is a literal, hardcoded, self-describing string — its entire
   purpose is that someone looking at raw JSON with zero other context
   knows immediately which set they're looking at. Do not make it
   derived/computed from the key name; it must be present in the data
   itself so a `grep '"_set": "inventory_record"'` across ItemData finds
   every instance directly, independent of where in the tree it's
   nested.

2. **Migration**: every existing item's `item_attributes` and
   `draft_listing.item_specifics` need wrapping in this envelope.
   Propose a one-time migration function (mirror the shape of existing
   migration tooling — check `sku_migration.py`/`scrub.py` for the
   established pattern in this codebase) that wraps the existing bare
   dict as `fields`, sets `version: 1`, and backfills `updated_at` from
   the item's own `draft_listing_state`/most-recent-known-modification
   timestamp if available, else the migration run time (note clearly in
   the migrated data if this is a backfilled/unknown timestamp, per
   Prime Directive 1 — never claim false precision). Per invariant E5,
   archive before overwrite. This is a real live-data migration across
   ~55k items — treat it with the same care as any other bulk operation
   in this codebase (dry-run first, spot-check a sample, no thermal-risk
   surprises, log progress).

3. **Provenance history arrays**, one per set:
   `item_attributes_history: [{ts, key, value, previous_value, source,
   applied_by}]` (top-level, sibling to `item_attributes`) and
   `draft_listing.item_specifics_history` (same shape, nested to match
   where `item_specifics` itself lives). Append-only — never edited or
   truncated, matching `price_history`'s existing discipline. Written
   only by the named accessor functions (point 5), never appended to
   directly by ad hoc code.

4. **Naming carries the distinction into Python, not just JSON.**
   Propose a new module, `src/tgw/inventory_record.py`, owning ALL reads
   and writes to the `item_attributes` envelope (`get_inventory_field()`,
   `set_inventory_fields()`, each taking/returning explicitly-named
   types or at minimum explicitly-named dict shapes — no generic `dict`
   in a signature where "which set" matters). A parallel accessor for
   the eBay-draft side — could live in `src/tgw/ebay/draft_specifics.py`
   or alongside #1416's translation function, implementer's call, but
   name it so a function signature alone (`get_ebay_aspect(item, key)`
   vs `get_inventory_field(item, key)`) tells a reader which set they're
   touching without needing a comment. These modules are the ONLY
   sanctioned direct-dict-access points for these two envelopes —
   #1416's translation function and #1417's diff/apply functions should
   be built on top of these accessors, not reimplement raw dict access.

5. **Loud banner comment** at the top of each new accessor module,
   matching this codebase's existing incident-comment style (see
   `http_server.py`'s session-42/session-43 comments for the tone/format
   to match) — state the two-set rule, name this packet + #1416/#1417,
   and point at the new invariant (point 6) so a future editor hits the
   context immediately, not three files deep into a packet doc.

6. **New invariant** (`reference/invariants.md`, next available C-series
   ID) stating: item field-sets (`item_attributes`/Set A,
   `draft_listing.item_specifics`/Set B, and any future marketplace-
   specific set) are self-describing envelopes (`_set` tag + provenance
   history), read/written ONLY through their named accessor module —
   never a generic PATCH passthrough or ad hoc dict merge. Cite this
   packet + #1291/#1313/#1316 as the "why" (two prior sessions each
   fixed a symptom without seeing this). Detector: a `catalog-verify`
   rule (or a repo-level grep-based check, your call on which fits
   better — a static check might catch this earlier, at commit time,
   than a data-scan detector) flagging any file outside the two
   accessor modules that indexes directly into `item_attributes` or
   `item_specifics` keys.

7. **Add `item_attributes` (and the envelope shape generally) to
   `TGW-Item-JSON-Schema.md`**, citing the `price_history`/
   `vision_results` precedent explicitly so a future reader sees this is
   an established pattern being reapplied, not a one-off.

8. **Update `CLAUDE.md`'s "Settled architecture" section** with one new
   bullet stating the two-set rule in the same terse style as the
   existing entries ("tgw-api is the fence," "One folder per SKU") —
   this is the mechanism that actually reaches every future session
   automatically, before any code gets touched, per Dave's explicit
   "future you's... need to recognize the sets as sets" requirement.
   Keep it to 2-3 lines, point to the invariant for full detail.

9. Full offline suite — zero regressions. This packet touches a lot of
   read sites indirectly (anything reading `item_attributes`/
   `item_specifics` needs to go through the new envelope's `fields` key
   instead of the dict directly) — the audit from #1416's investigation
   already enumerated every read/write site; use that list as the
   checklist for what needs updating to the new envelope shape, don't
   re-derive it from scratch.

## Out of scope
- The actual boundary-fix logic (#1416) and reverse-flow logic (#1417)
  — this packet only establishes the container shape and naming both
  will be built on top of.
- Multi-marketplace sets beyond eBay — the envelope shape supports a
  future `_set: "amazon_draft"` etc. without change, but this packet
  doesn't build anything for a marketplace that doesn't exist yet.
- Any change to WHAT data lives in either set, only HOW it's shaped and
  named.

## Dataset
This is the one packet in this sequence with real migration risk — every
existing item's `item_attributes`/`item_specifics` dict gets wrapped.
Per Prime Directive 1 and invariant E5: archive before overwrite, dry-run
against a sample first, verify round-trip (old bare-dict data fully
recoverable from the new envelope's `fields` key) before running against
the full catalog. No data is discarded — this is pure reshaping plus new
metadata (version/timestamp/`_set` tag), and the history arrays start
empty (no retroactive history reconstruction — that data was never
captured, don't fabricate it).

## Acceptance (live)
1. Diffs shown for: envelope shape (schema doc), migration script,
   both new accessor modules + banner comments, updated invariant,
   updated CLAUDE.md, updated schema doc.
2. Migration dry-run output shown against a real sample (propose 50-100
   items across a range of `draft_listing_state` values), confirming
   old-shape data is fully preserved inside the new envelope's `fields`.
3. Live test: read an item through both new accessor modules, confirm
   correct values; attempt a direct dict-index bypass in a scratch
   script and confirm the detector (point 6) flags it.
4. Full offline suite: zero regressions — this is the biggest risk in
   this packet, since it touches every existing read site indirectly.
5. Explicit sign-off checkpoint before running the migration against the
   full 55k-item catalog — dry-run + sample verification shown in the
   manifest is Acceptance for THIS packet; the full-catalog run is a
   separate, explicit go/no-go for Dave, not bundled into "done."

## Quota/risk
Moderate — not from API usage (none), but from touching every item's
JSON structure. Mitigated by: dry-run-first requirement, sample
verification, invariant E5 archive-before-overwrite, and treating the
full-catalog migration as a separate explicit decision point rather than
something this packet auto-executes.
