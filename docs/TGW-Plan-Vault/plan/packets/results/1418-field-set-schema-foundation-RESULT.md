# Result: 1418 field-set-schema-foundation
Status: done (dry-run + sample acceptance only — full-catalog migration deliberately NOT run, see below)
Todo: #1418   PP: PP-LISTEDITOR-001

## Files touched
New:
- `src/tgw/inventory_record.py` — Set A ("Inventory Record") accessor module.
  `get_inventory_field(s)`, `wrap_inventory_attributes`, `set_inventory_fields`.
  Banner comment states the two-set rule, names #1418/#1416/#1417, points at C12.
- `src/tgw/ebay/draft_specifics.py` — Set B ("eBay Draft") accessor module,
  parallel shape: `get_ebay_aspect(s)`, `wrap_ebay_specifics`, `set_ebay_aspects`.
- `scripts/migrate_field_set_envelope.py` — dry-run-by-default migration
  script (invariant E9 `announce_script_run` at the top of `main()`).
- `tests/test_inventory_record.py`, `tests/test_draft_specifics.py`,
  `tests/test_migrate_field_set_envelope.py`,
  `tests/test_invariant_c12_field_set_accessors.py` — new coverage (35 tests).

Modified (all sites from the session's pre-built read/write inventory,
re-verified live as I touched each — see Deviations for the two that had
shifted):
- `src/tgw/workers/ai_identify.py` — Set A writer now routes through
  `inventory_record.set_inventory_fields` (still "existing wins", still
  fills gaps only).
- `src/tgw/workers/ebay_draft.py` — Set A prefill read + Set B writer both
  routed through their accessors; history recorded for changed keys even
  though the draft is rebuilt fresh each run (full-replace `fields`, diff
  computed against the item's prior envelope for history purposes).
- `src/tgw/http_server.py` — `_apply_patch`'s item_attributes special-case
  merge now envelope-safe for both a full-envelope caller (accessor output)
  and a legacy bare-partial-dict caller (routes through the accessor
  instead of merging onto the envelope's top level); `accept_proposals`,
  the aspects-prefill JSON, the item-specifics table, and the Inventory
  Record summary panel all read via the accessors now.
- `src/tgw/ebay/sync.py` — `_build_offer_bodies`'s aspect-push (the ONE
  code path that reaches eBay's live Inventory API) reads via
  `get_ebay_aspects`.
- `src/tgw/ebay/pull.py` — the ebay_live-sourced draft-creation writer
  wraps `item_specifics` in the Set B envelope.
- `src/tgw/draft_sync.py` — `pin_draft_to_live` (M4/S1 re-pin) wraps the
  live-mirror-derived aspects in the envelope.
- `src/tgw/listing_quality.py`, `src/tgw/apis/lookup/base.py`,
  `scripts/photosync_canary_probe.py` — read sites converted to
  `get_ebay_aspects`.
- `tests/test_http_server.py` — 2 pre-existing tests
  (`test_accept_proposals_persists_item_attributes_edit`,
  `test_accept_proposals_item_attributes_absent_before`) updated to read
  the new envelope shape via the accessor instead of the bare dict — this
  is an intentional shape change these tests needed to catch up to, not an
  unintentional regression (see "Full offline suite" below).
- `docs/TGW-Plan-Vault/reference/TGW-Item-JSON-Schema.md` — `item_attributes`
  documented for the first time (previously zero grep hits), new "Set A
  vs. Set B" and "Field-set envelope shape" sections, `item_specifics` row
  updated, both `*_history` rows added.
- `docs/TGW-Plan-Vault/reference/invariants.md` — new **C12** entry.
- `CLAUDE.md` — new Settled-architecture bullet under the existing list,
  pointing at C12.

## Live evidence (Acceptance)

**1. Diffs** — see `git diff` on this branch (13 modified + 8 new files,
250 insertions / 32 deletions across the modified set).

**2. Migration dry-run against real data** (read-only, zero writes — run
as the `tgw` user against the real production `/opt/TGW/data/ItemData`,
55,419 real items):
```
$ sudo -u tgw ... python3.12 scripts/migrate_field_set_envelope.py --limit 100 \
    --report /tmp/migrate_dryrun_sample.json
dry_run=True ok=True scanned=55419 planned=100 skipped_already_enveloped=0
skipped_no_data=457 round_trip_failures=0
```
Sample verified against a real item on disk (`tgw201412211145262`):
`item_specifics` bare dict on disk has 11 keys (`Brand`, `Type`, `Color`,
`Set Includes`, `Vintage`, `Antique`, `Original/Licensed Reproduction`,
`Material`, `Shape`, `Features`, `Number of Items in Set`); the dry-run
plan's `b_field_count` for that SKU reported 11, matching exactly, and
`_round_trip_ok()` confirmed the planned envelope's `fields` dict is
byte-for-byte identical to the pre-existing bare dict. (Full sample report
covered 100 items across a range of SKUs/`draft_listing_state` values —
see `/tmp/migrate_dryrun_sample.json` on tgw-prod; not copied into the
repo, per "no bulk artifacts committed.")

**3. Accessor read test against a real (unmigrated) item**, run as `tgw`
against the worktree's own copy of the code (confirmed via `__file__`):
```
Set A fields (bare dict, back-compat): {}
Set B fields (bare dict, back-compat): {'Brand': 'Indulge', 'Type': 'Skillet/Griddle', ...}
inventory_record.__file__ = /opt/TGW/var/worktrees/1418-field-set-schema-foundation/src/tgw/inventory_record.py
```
Matches the item's real on-disk `item_specifics` values exactly — the
accessor correctly reads the pre-migration bare-dict shape.

**Direct dict-index bypass detector (C12), live**: wrote a deliberate
bypass file (`src/tgw/_scratch_bypass_probe.py`, `item.get("item_attributes").get("Brand")`)
and ran `pytest tests/test_invariant_c12_field_set_accessors.py`:
```
FAILED test_no_new_direct_field_set_access_outside_accessors
AssertionError: Invariant C12 violation: ... [.../src/tgw/_scratch_bypass_probe.py', 3)]
```
Detector correctly flagged it. Scratch file removed immediately after;
re-ran the same test clean (`3 passed`).

**4. Full offline suite — zero regressions**, run twice (once before, once
after fixing the two pre-existing tests that needed to catch up to the
intentional shape change):
```
$ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH pytest -q
2278 passed, 1 skipped, 1 warning in 149.44s (0:02:29)
```
Confirmed testing the worktree's own copy (not the shared checkout) via
`tgw.inventory_record.__file__` resolving under
`/opt/TGW/var/worktrees/1418-field-set-schema-foundation/`.

**5. Full-catalog migration — explicitly NOT run.** Per the packet's
Acceptance point 5 and this task's own hard constraint: this packet stops
at the 100-item dry-run + round-trip verification above. Running
`scripts/migrate_field_set_envelope.py --apply` against the full ~55k-item
catalog is a separate, explicit go/no-go decision for Dave. The script
does not enforce a hard ceiling itself (it's a general-purpose,
re-runnable tool per the "recompile, not one-shot" precedent) — the
operator is responsible for scope on every `--apply` invocation.

## Deviations from spec (flagged per Prime Directive 3)

1. **`_apply_patch`'s item_attributes merge made envelope-aware (not
   explicitly named in the packet's numbered spec, but required for
   structural correctness).** The packet's spec item 4/6 says the accessor
   modules are "the ONLY sanctioned direct-dict-access points" and forbids
   "a generic PATCH passthrough," but `http_server.py`'s `saveEbayDraft()`
   JS (`#aspects-form`) still sends a bare partial `item_attributes` dict
   via PATCH today — that rewiring is explicitly #1416's job (point 3),
   out of scope here. Left as-is, this generic PATCH path would have
   silently corrupted the Set A envelope (merging bare field-update keys
   onto the envelope's *top level*, polluting `_set`/`version`/`fields`
   with sibling scalar keys) the moment `item_attributes` becomes
   enveloped. I judged "don't corrupt the container shape" to be squarely
   this packet's job (point 1: envelope shape) even though the *routing*
   fix (WHERE this write should land) is #1416's — so I made `_apply_patch`
   detect a non-envelope (legacy) incoming dict for the `item_attributes`
   key and route it through `inventory_record.set_inventory_fields`
   instead of a raw top-level dict merge. This does NOT change what set
   the JS writes into (still Set A, same as before — #1416 changes that)
   — it only makes the write structurally safe. Flagging this as a
   deviation because it wasn't literally named in the numbered spec items,
   though I believe it's required by the spec's own container-shape intent.

2. **`ebay_draft.py`'s Set B write is a full-replace of `fields`, with
   history computed as a diff against the item's prior envelope, not a
   pure merge.** `ebay_draft` rebuilds the entire aspect set from scratch
   every run (not incremental), so I used
   `draft_specifics.set_ebay_aspects()` only to compute the history-array
   diff, then `wrap_ebay_specifics(item_specifics)` (full replace) for the
   actual envelope `fields` — preserving the pre-existing "the fresh
   rebuild wins outright" behavior (no regression) while still getting
   provenance history for genuinely-changed keys. Not explicitly specced;
   judged as the correct behavior-preserving interpretation.

3. **Invariant C12's detector: static grep-based commit-time check, not a
   catalog-verify data-scan rule** — the packet explicitly left this "your
   call." Implemented as `tests/test_invariant_c12_field_set_accessors.py`,
   an allowlist-diff test (every current hit outside the two accessor
   modules is either a write of the accessor's own returned patch, or an
   unrelated dict that happens to share a key name — e.g. an AI model
   response or `revision_draft.delta` — each reviewed and pinned; any NEW,
   un-reviewed hit fails the test). Chosen because this is fundamentally a
   CODE-hygiene invariant (which file touches which dict), not a DATA-drift
   invariant — a static check catches it before a corrupting write ever
   happens; a catalog-verify rule could only notice after the fact. Live-
   verified working against a deliberate bypass (see Acceptance #3 above).
   #1416's own planned drift detector (Set A/Set B *value* disagreement on
   live items) is the complementary data-drift half and is correctly out
   of this packet's scope — noted in the C12 invariant entry as "not yet
   built as of this entry."

4. **`updated_at_backfilled` always `true` for migration-derived
   timestamps, even when a proxy timestamp (not the migration run time)
   was found.** The packet's spec item 2 says "backfills `updated_at` from
   the item's own `draft_listing_state`/most-recent-known-modification
   timestamp if available, else the migration run time (note clearly ... if
   this is a backfilled/unknown timestamp)." I judged that even a found
   proxy timestamp (`baseline_at`, `ebay_listing.synced_at`,
   `ebay_offer.staged_at`, or the last `price_history` entry — checked in
   that priority order, see `_best_known_timestamp()`) is still not a real
   "when was `item_attributes`/`item_specifics` itself last edited"
   timestamp — none of those proxies ever recorded that fact pre-migration.
   Marking it `backfilled: true` unconditionally for any migration-derived
   value (proxy or run-time fallback) is the more conservative, Prime-
   Directive-1-safe reading; a reader can still distinguish "we have SOME
   proxy" vs. "pure fallback to run time" via the separate
   `timestamp_is_proxy` field in the dry-run report (not persisted on the
   item itself — only `updated_at`/`updated_at_backfilled` are).

5. **Module locations**: `src/tgw/inventory_record.py` (top-level, as the
   packet's own spec item 4 suggested) and `src/tgw/ebay/draft_specifics.py`
   (alongside `sync.py`, matching the existing `tgw.ebay.*` module family
   rather than folding into #1416's future translation function file).
   Packet flagged this as implementer's call — noted here per its own
   instruction to flag the choice.

6. **Migration script has no self-imposed item-count ceiling** — it will
   happily run `--apply` against the full catalog if invoked without
   `--limit`. The packet's constraint (full-catalog run is a separate
   Dave go/no-go) is enforced by convention/operator discipline (matching
   `recompile_category_backfill.py`'s precedent — a general-purpose,
   re-runnable tool, not a single-shot gated script), not by a hard-coded
   guard in the script itself. Flagging this explicitly since it's a
   meaningful trust boundary: nothing stops someone from running `--apply`
   with no `--limit` today. If Dave would prefer a hard default cap
   (e.g. refuse to run unbounded without an explicit `--confirm-full-catalog`
   flag), that's a one-line follow-up, not built here.

## Out-of-scope findings filed
None new. All out-of-scope items already belong to #1416/#1417 (explicitly
named in both packets as depending on this one) — no additional,
unrelated findings surfaced during this packet's implementation.

## Notes for #1416/#1417 (the two packets that build on this one)
- Both accessor modules are ready to be built on top of: #1416's
  translation function should call `inventory_record.get_inventory_fields()`
  / `draft_specifics.set_ebay_aspects()` (not raw dicts); #1417's diff
  engine should read both sets via their accessors and its apply-action
  should call `inventory_record.set_inventory_fields()` with
  `source='inventory_diff_apply'` (or similar) for provenance.
- `saveEbayDraft()`'s JS (`http_server.py`, `#aspects-form`) still writes
  bare `item_attributes` PATCHes — safe now (routes through the accessor
  inside `_apply_patch`) but still targeting the WRONG set per #1416's
  point 3. That rewiring is untouched here, exactly as specced.
- The full-catalog migration (`scripts/migrate_field_set_envelope.py --apply`,
  no `--limit`) should probably run before #1416/#1417 start touching
  live data at scale, so their new code paths aren't exercised against a
  mix of enveloped and bare-dict items forever — but that's Dave's
  sequencing call, not decided here.
