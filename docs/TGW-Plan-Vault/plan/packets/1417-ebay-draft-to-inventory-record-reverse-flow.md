# Packet: eBay Draft → Inventory Record reverse flow (gated, default-checked diff)

Todo: #1417   PP: PP-LISTEDITOR-001   Track: design + bugfix (depends on #1416)

**Sequencing: this packet builds on #1418 (field-set schema foundation)
and #1416 (Set A/Set B boundary fix, forward translation function), in
that order. Do not start #1417 implementation before both have merged**
— the reverse flow needs `item_specifics` to be reliably the
authoritative eBay-side value first (#1416 fixes the paths that
currently corrupt it), or the diff this packet builds would be comparing
against known-bad data; and #1417's own provenance requirement (point 4
below) is largely just "use #1418's history-array accessor" once that
foundation exists, rather than a bespoke side dict invented here.

## Context budget (ALL the model may load)
This packet + #1416's packet doc (for the Set A/Set B vocabulary,
don't re-derive it) + `src/tgw/http_server.py`'s existing proposal UI:
`accept_proposals` action (~1420-1455 — will be superseded/replaced by
this packet, read it as "what pattern already exists," not "what to
keep as-is"), the "Pipeline proposed changes" banner (~5355-5370), the
three-layer aspect editor color-coding in `_CATEGORY_CONTEXT_IIFE`
(~2582, the live/proposed/edit merge logic) + `src/tgw/revision.py`
(whole file) + `reference/invariants.md` C9/C10 sections (the existing
"operator gate" precedent this packet extends) + memory context: Dave's
standing rule is that inspect-and-fix stays permanent, the target is
99%-approvable-in-seconds, never eliminating the gate for speed.

## Verified live before this packet was written
1. **No automated reverse-flow mechanism exists today.** Confirmed via
   `grep -rn "revision_draft\s*="` across `src/tgw/`: the ONLY writer of
   `revision_draft` is `revision.py:222`, itself only reachable via the
   manual `tgw revise --set ...` CLI command (an operator/agent
   deliberately proposing a specific change). No worker — not
   `ebay_draft`, not `ebay_sync`, not `ebay_pull` — ever writes a
   `revision_draft` proposing that a Set-B-discovered value (like the
   AI-vision-resolved "Brooch" vs the stale universal "Lapel Pin") should
   flow back into `item_attributes`. The "how does data migrate back into
   the inventory record" question in this packet's title is new-design
   work, not a bug to locate — confirmed by absence, not by finding
   broken code.
2. **The UI pattern to reuse already exists for the FORWARD direction**
   and should be mirrored, not reinvented: `_CATEGORY_CONTEXT_IIFE`
   (`http_server.py:2582`) renders a three-layer color-coded diff per
   aspect (live/proposed/edit) with a `liveHint` showing the
   non-winning value in dimmed text under the winning one. The "Accept
   All Proposals" banner+button (`http_server.py:5355-5370`,
   `accept_proposals` action ~1420-1455) is the existing bulk-accept
   affordance — currently wired to the WRONG target set (fixed by
   #1416 point 4 for the forward direction; this packet needs its own,
   separate accept action for the reverse direction, since forward and
   reverse proposals are different data with different destinations).
3. **Dave's explicit design requirement (2026-07-15, this session):**
   "It would be too much work to have to update both the inventory
   record and the eBay listing just to get the dataset in place...
   presenting a selectable diff offering to update the inventory
   record, all differences checked by default, operator can uncheck or
   skip altogether. Gated automatic update." And, on whether promotion
   can ever be fully silent/automatic: rejected a confidence-threshold
   auto-promotion in favor of the checked-diff pattern — every
   promotion is still an explicit operator submit, but the *default*
   state minimizes friction to "glance and submit" rather than
   "individually confirm each field."
4. **Root-cause pattern behind this being missing for so long** (context
   for why this needs an invariant, not just a feature): two separate
   sessions this past week (#1291, 2026-07-13; #1313/#1316, 2026-07-13)
   fixed real, confirmed mechanical bugs in the *forward*/accept
   plumbing (an always-False identity check; a fence-bypass) without
   ever surfacing that there was no reverse path at all, or that the
   forward path was writing to the wrong set. Both fixes were correct
   and well-tested for what they touched, and both left the conceptual
   gap fully intact. This is why #1416 point 8's invariant (C12) needs
   to name the reverse flow's absence explicitly, not just the forward
   boundary violation — otherwise this packet's own eventual bugs will
   get "fixed" the same symptom-only way a third time.

## Spec

1. **Diff engine**: a pure function,
   `diff_ebay_draft_to_inventory(item: dict) -> list[FieldDiff]`
   (exact location at implementer's judgment — propose alongside
   #1416's translation function, e.g. `src/tgw/ebay/aspect_translation.py`
   or a sibling module), comparing `draft_listing.item_specifics`
   (Set B, post-#1416 = trustworthy) against `item_attributes` (Set A)
   key-by-key, returning one `FieldDiff` per differing key: `{key,
   inventory_value, ebay_value, source: 'ebay_draft'|'ebay_sync'|...,
   detected_at}`. Keys present in Set B but absent from Set A are a
   diff too (inventory_value=None) — a new fact, not just a correction.
   Keys present only in Set A are NOT part of this diff (Set A can
   legitimately have universal facts no marketplace needs — that's not
   a discrepancy to resolve).
2. **New API endpoint** (propose `GET /api/items/{sku}/inventory-diff`)
   returning the current diff for an item, used by the UI below. Does
   not mutate anything — read-only, callable any time.
3. **UI: a new panel, distinct from the existing (forward) "Pipeline
   proposed changes" banner** — reusing its visual pattern (checkbox
   per row, live vs proposed shown side-by-side, dimmed non-winning
   value) but clearly separately labeled (e.g. "eBay → Inventory Record
   sync" or similar, your call on exact copy) so an operator can never
   confuse "accept this eBay-side proposal" with "accept this
   inventory-record-bound diff" — two different destinations, two
   different buttons, no shared action name. **Every row defaults
   checked** (per Dave's explicit requirement) — the friction-minimizing
   default is deliberate, not an oversight to "fix" later.
4. **New API action** (propose `POST /api/items/{sku}/inventory-diff/apply`
   with a body listing which keys to actually apply — the checked
   subset from the UI) that writes ONLY the checked keys into
   `item_attributes`, each with the diff's provenance (`source`,
   `detected_at`, and now `applied_at`/`applied_by`) recorded somewhere
   durable — propose a small `item_attributes_provenance` sibling dict
   or per-key metadata, your call on shape, but it must be possible to
   answer "why does the universal record say X" after the fact, not
   just "it changed." This is a genuinely new write path into Set A —
   do NOT route it through `_apply_patch`'s generic merge without
   thought; Set A being "universal, edited carefully" per Dave's stated
   intent for #1416 means this path deserves its own explicit,
   named function, not a generic PATCH passthrough.
5. **Idempotency / re-diffing**: once applied, that key should no
   longer show as a diff (Set A and Set B now agree) until they
   diverge again (e.g. `ebay_draft` re-runs and resolves something
   differently). No stored "dismissed" state needed if the operator
   unchecks/skips — an unapplied diff just reappears next time the diff
   endpoint is called, which is correct (it's still true that they
   disagree) — but confirm this reasoning in the manifest rather than
   assuming; if Dave would rather a skip be sticky (don't re-surface an
   explicitly-rejected diff), that's a design deviation worth flagging
   before building, not silently choosing.
6. **This packet does NOT touch `accept_proposals`** (the forward
   direction) beyond what #1416 already specs — keep the two proposal
   systems' code paths clearly separate even though they'll likely
   share some UI/diff-rendering helper code.
7. **Extend invariant C12 (from #1416)** — or add a paired C13 if that
   reads more cleanly — to state: Set A is written only through named,
   explicit functions (this packet's apply-diff action being one of
   them), never a generic PATCH passthrough, and every write records
   provenance. Add to the same `catalog-verify` detector family: flag
   items with an unresolved eBay→Inventory diff older than some
   threshold (propose 30 days, flag as a default worth confirming with
   Dave rather than silently picking) as a finding, so stale unreviewed
   drift becomes visible/queryable, not just a manual `curl` check.
8. Full offline suite — zero regressions.

## Out of scope
- Multi-marketplace support itself — this packet only makes the
  single-marketplace (eBay) reverse flow correct and safe, in a shape
  that generalizes (`source` field already supports future marketplace
  names) without building anything for a marketplace that doesn't exist
  yet.
- Auto-promotion / confidence-threshold-based silent writes — explicitly
  rejected by Dave for this packet.
- Changing `accept_proposals`/forward flow beyond what #1416 already
  specs.
- Any bulk backfill/sweep applying this to the existing 55k-item catalog
  — this packet builds the mechanism and proves it live on a test item;
  a bulk sweep is a separate, later decision once the mechanism is
  trusted.

## Dataset
No data loss — this is purely additive (a new read-only diff endpoint, a
new gated write path that only fires on explicit operator submit). Set A
values are only ever changed by an explicit, provenance-recorded,
operator-reviewed action; nothing here auto-overwrites anything.

## Acceptance (live)
1. Diffs shown for: diff engine, new endpoint, new UI panel, new apply
   action, provenance recording, invariant/detector addition.
2. Live test on a **throwaway test item** (same discipline as #1416 —
   no real live listings): create a deliberate Set A/Set B mismatch,
   confirm the diff endpoint surfaces it correctly with the right
   default-checked state, uncheck one field in the UI, submit, confirm
   only the checked fields landed in `item_attributes` with provenance,
   and the unchecked one still shows as an open diff afterward.
3. Confirm the forward (`accept_proposals`) and reverse (this packet's
   apply action) systems don't share write paths or get confused with
   each other in the UI — screenshot or HTML excerpt showing both
   panels distinctly labeled, if both are present on the same test
   item.
4. New catalog-verify detector run live (dry-run/read-only, no bulk
   fixes) showing it correctly flags a known-unresolved-diff item.
5. Full offline suite: zero regressions.

## Quota/risk
Low. No eBay API calls beyond what already happens (this reads
already-local `draft_listing.item_specifics`, doesn't fetch anything new
from eBay). All writes are local JSON, gated behind an explicit operator
submit on a test item only.
