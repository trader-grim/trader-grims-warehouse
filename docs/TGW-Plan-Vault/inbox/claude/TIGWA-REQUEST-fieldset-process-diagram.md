# CLAUDE REQUEST — visualize the Inventory Record / eBay Draft set-boundary process

**From:** Claude
**For:** Tigwa
**Date:** 2026-07-15
**Tracker:** todos #1415-#1418 (`claude`), PP-LISTEDITOR-001
**Trigger:** Dave, after the packet specs were reviewed and #1418's
implementation was dispatched: "please send the details of this process
workflow to tigwa. I want to see if she can use a vision model to
diagram it visually and graph it for coder understanding."

## What this is

A same-day investigation surfaced a recurring class of bug (Dave: "this
part of the UI right here has been the problem over and over and over
again... four[th] time") and a structural fix for it, now split into
three sequenced work packets. Dave wants a **visual diagram** of the
whole thing — the data model AND the execution process — to help future
coders (human or AI) grok it faster than reading four packet docs cold.
Whether that's a vision-model-generated diagram, a hand-built one, or
something else is your call on execution; the ask is the artifact, not a
specific tool.

## The core concept (this is the part that needs to be unmistakable in
## the diagram — everything else is detail)

There are exactly **two sets** of item data, and the entire bug pattern
today came from code treating them as loose individual keys instead of
cohesive sets:

- **Set A — Inventory Record** (`item_attributes` + top-level fields:
  title, description, condition, etc.): universal, marketplace-agnostic,
  meant to translate across eBay and any future marketplace.
- **Set B — eBay Draft** (`draft_listing.*` as a whole, including
  `draft_listing.item_specifics`): eBay-specific resolved values. THIS
  is the only set that's actually pushed to eBay's Inventory API
  (`ebay/sync.py`'s `_build_offer_bodies`).

**The rule going forward:** each set is read/written as a whole, through
exactly one named accessor/translation function per crossing point —
never a per-key merge, prefill fallback, or `{**a, **b}` spread done
locally in a display or save handler. Today's investigation found FOUR
independent code paths that each grabbed one key and forgot which set it
belonged to (the eBay Draft Editor's aspects form, the "Accept
Proposals" button, an Inventory-Record summary panel's merge, and — in a
different but same-shaped bug — `draft_listing.description` vs
`draft_listing.listing_description` inside Set B itself).

## What's already fixed today (for diagram context, not new work)

**Todo #1415** (merged): within-Set-B staleness — `description` and
`listing_description` are two views of the same fact inside the eBay
Draft set; editing one without regenerating the other meant operator
edits silently never reached eBay even though the push job reported
"succeeded." Fixed by regenerating the derived field in the same write.
Live-verified on a real eBay listing (with an embarrassing detour where
a test string got left on a real listing and had to be manually
restored — worth including as a "here's what NOT to do" footnote if the
diagram covers testing discipline).

## The three sequenced packets (this is the process to diagram)

Read the full specs — they're the source of truth, this is a summary:
- `docs/TGW-Plan-Vault/plan/packets/1418-field-set-schema-foundation.md`
- `docs/TGW-Plan-Vault/plan/packets/1416-inventory-record-ebay-draft-set-boundary.md`
- `docs/TGW-Plan-Vault/plan/packets/1417-ebay-draft-to-inventory-record-reverse-flow.md`

**#1418 — schema foundation (build first, gates the other two).**
Wraps both sets in a self-describing envelope: `{"_set": "inventory_record"
| "ebay_draft", "version": N, "updated_at": ..., "fields": {...}}`. Adds
an append-only provenance-history array per set (same shape as the
already-proven `price_history`/`vision_results` pattern in this
codebase — current value stays a fast dict, every change appends to
history with source/timestamp). Adds two named accessor modules that
become the ONLY sanctioned direct-access point for each set. Adds a new
invariant (`reference/invariants.md`) + detector, a schema-doc entry,
and — the part that actually reaches future sessions automatically — a
new line in `CLAUDE.md`'s "Settled architecture" section. **Real
migration risk**: touches the JSON shape of ~55,000 live items.
Acceptance stops at dry-run + sample verification; the full-catalog run
is a separate explicit go/no-go for Dave, not bundled into "packet
done." A `bin/tgw-snapshot` btrfs snapshot was taken immediately before
dispatch as the rollback point (`/opt/TGW/.snapshots/20260715T0734`).

**#1416 — boundary fix (forward: Set A → Set B).** One named
translation function is the only legal crossing point (extracted from
`ebay_draft.py`'s existing inline prefill logic, not reinvented). Fixes
three code paths that currently reach across the boundary key-by-key:
the eBay Draft Editor's aspects-editing form (currently writes to Set A
instead of Set B, so operator aspect edits never reach eBay — confirmed
live, matches Dave's original bug report about Metal/Department
mismatches), the "Accept Proposals" button (writes to Set A despite its
own UI banner claiming it pushes to eBay), and the Inventory Record
summary panel (currently blends both sets' keys with Set A silently
winning collisions).

**#1417 — reverse flow (Set B → Set A), genuinely new, not a bug fix.**
Confirmed live that no automated reverse-flow mechanism exists at all
today (`revision_draft` is only ever written by the manual `tgw revise`
CLI command). Builds one: a diff engine comparing Set B against Set A,
a new read-only diff endpoint, and a UI panel showing every differing
key **checked by default** (Dave's explicit design call — "too much work
to update both... present a selectable diff... all differences checked
by default, operator can uncheck or skip"), gated on an explicit
operator submit, provenance recorded via #1418's history arrays, never
silent auto-promotion regardless of AI confidence (also Dave's explicit
call, after weighing and rejecting a confidence-threshold auto-merge
option).

## The execution process itself (also worth diagramming — this is the
## "how work actually moves" half, separate from the data-model half)

This is the standing branch-per-task contract (`plan/pp/PP-HERMES-EA-001.md`),
being exercised on this specific work:
1. Packet spec written and reviewed with Dave BEFORE any code — "I want
   to see the spec before implementation" was explicit this session.
2. `git worktree add /opt/TGW/var/worktrees/<id>-<slug> -b todo/<id>-<slug>`
3. Dispatch to a `tgw-coder` agent (loads only the packet, not the
   master plan) — currently running for #1418 as of this request.
4. Result manifest written to `plan/packets/results/<id>-RESULT.md`.
5. `/tgw-runner-review <id>` — checks diff against spec, invariants,
   scope; bounded fix-attempt cap; escalates rather than merging on any
   ambiguity.
6. Explicit stitch step (merge, close todo, clean up worktree/branch) —
   separate from review, never auto-merged by the reviewer.
7. Strict sequencing enforced by dependency, not parallelism: #1416 does
   not start until #1418 is reviewed AND Dave has signed off (full-
   catalog migration is its own gate, separate from "packet reviewed
   clean"); #1417 waits on both.

This same loop (worktree → coder → manifest → runner-review → stitch)
was also run today on two unrelated, already-in-flight packets (#1108,
#1407) found sitting as interrupted branches from an earlier session —
useful as a second concrete example if the diagram wants to show the
process is general-purpose, not built just for this fix.

## What we're asking

Diagram both halves — the two-set data model (Set A ⟷ Set B, with the
one legal crossing point rule) and the execution pipeline (spec → review
→ worktree → coder → manifest → runner-review → stitch) — in whatever
visual form you judge clearest for a future coder (human or AI) to grok
in under a minute, faster than reading the four packet docs cold. If a
vision model is the right tool for part of this, use your judgment on
which one and how; report back what you produced and where it lives
(presumably `docs/TGW-Plan-Vault/` somewhere, your call on exact path)
so it can be linked from the master plan / CLAUDE.md alongside the new
invariant #1418 adds.
