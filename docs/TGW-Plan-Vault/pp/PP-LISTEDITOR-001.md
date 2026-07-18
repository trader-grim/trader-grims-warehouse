# PP-LISTEDITOR-001 — listing editor + revision apply (full detail)

## PP-LISTEDITOR-001 — listing editor + revision apply

**Todo #1465 — eBay Seller Hub complete parity audit, reassigned to Tigwa
2026-07-16.** Filed same day as the incident below, initially addressed to
Claude. Dave redirected it: "setup the ebay parity audit with a vision
model and have tigwa manage. she has browser spinup test skills and
vision capabilities." Confirmed real — `computer_use` skill
(`/home/db/.hermes/skills/computer-use/SKILL.md`), SOM-mode screenshot
capture, works with any tool-capable model, matches the audit's own
evidence standard (live Seller Hub observation, no static/fabricated
lists). Claude did not touch Tigwa/Hermes model routing — whether this
pairs with a vision-specific model (Gemini, named in PP-HERMES-EA-001's
original design, not yet confirmed wired) is her/Dave's call, same
precedent as her a1131 MCP setup. Handoff + both original request docs:
`inbox/tigwa/CLAUDE-HANDOFF-seller-hub-parity-audit-2026-07-16.md`.

**Todo #1472 — custom-aspect checkbox redesign, DONE 2026-07-16, live-fire
confirmed by Dave.** Watched a real edit end-to-end: job queued, job landed,
unchecked aspects trimmed from the draft, then added a new custom aspect
("Thumb Size = Normal") and confirmed it rendered inline with its own
checkbox exactly as designed. Dave: "works... Nice work." Closed. Dave's own
framing of the win, same day: "we just did what eBay does a little better.
eBay discards the custom fields if you change categories unceremoniously,
never to be found again even if you immediately switch back." eBay's own
Seller Hub destroys category-orphaned specifics irrecoverably; TGW's
`category_aspect_migration.py` preserves them in `item_attributes` (Set A)
instead — same operator-facing discard behavior, non-destructive underneath,
directly enacting Prime Directive 1 where eBay's own platform doesn't. Second
confirmed instance of the beats-eBay success bar (first: OPERATOR-QUEUES-001)
— see memory `project-operator-queues-beats-ebay-example.md`.

**Follow-up, same day — destination NOT settled (todo #1473, open, not
urgent).** Dave, immediately after: "still not convinced they belong in the
inventory record. That part still needs work. Can't rebuild Rome in a day."
The mechanism (checkbox discard → live-verified working) and the destination
(`item_attributes`/Set A) are two separate claims — only the mechanism is
confirmed settled. Treat Set A as the CURRENT destination, not the DECIDED
one, until a fresh design pass with Dave. Do not build anything further on
top of "discarded aspects land in Set A" as a settled assumption.

**All 3 of 3 flagged `field_set_drift` SKUs now live-confirmed (Dave,
2026-07-16, same day):** `tgw202605051752520` (item #1, drove the #1472
redesign), `tgw202605131827555` (Brand, item #2 — worked, though Dave flagged
the required Save-Draft click as worth a second look, see #1473-adjacent
discussion), `tgw202606021133367` (Bottle Type, item #3 — "3's good. We got
this one."). Closes the earlier "1 worked 1 did not, not enough data" open
loop for this fix set specifically.

Dave, looking at `tgw202605051752520` after #1470/
#1471 landed: "I like that we have captured all of the custom aspects now but
I don't like the interface... if the aspect is not in the list of required or
recommended aspects it gets a check box, default checked, meaning keep all of
these attributes. Unchecking means discard at save. Proper attribute set
never has check boxes, never gets discarded... Regardless of how or whether
we save, this is a quality gate with a human and we have to trust any
unverifiable information when they press save, even if it discard." Replaced
#1471's standalone "Aspects not in this category" panel (its own always-
checked confirm()+immediate-apply button, disconnected from the main Save)
with an inline `.aspect-keep-cb` checkbox on every non-official aspect row,
right in `#aspects-form` next to its value input — official/required/
recommended aspects get no checkbox and are never discardable, matching Set
A/B boundary discipline. `saveEbayDraft()` now collects unchecked keys and,
after the normal field PATCH succeeds, calls #1471's existing
`/category-aspect-migration/apply` endpoint for them — same sanctioned Set
B-removal/Set A-write path, no new merge logic, one Save Draft click drives
both actions instead of two disconnected ones. Rewrote the one test that
asserted the old panel's markup. Full offline suite 2364 passed/1 skipped.
`tgw-http` restarted live; the underlying detect endpoint (now the single
write path the new Save flow drives) reconfirmed live against the real
18-orphan item. Not yet click-tested in an actual browser session — needs
Dave's own login + a live Save with a box unchecked.

**Todo #1471 — category-aspect migration, built + deployed 2026-07-16.**
Companion to #1470 (which made every stored Set B aspect visible/editable,
even ones outside the current category's official list, badged "CUSTOM
ASPECT"). Dave: "ebay behavior is to discard them [on a category change]
... I always wanted the attributes to move. They are good seo...
operator chooses discards and makes their own mess to repair if they
screw up." New module `tgw/ebay/category_aspect_migration.py` —
`detect_category_orphaned_aspects()` (live-recomputed, fails safe to
empty on a lookup error) + `apply_category_aspect_migration()` (moves
checked keys from Set B into Set A via the sanctioned accessors, removes
them from Set B, re-detects live so a stale request is a no-op). New
accessor `draft_specifics.remove_ebay_aspects()` — the first EXPLICIT
Set B deletion path (distinct from `set_ebay_aspects`'s deliberate
None-is-a-no-op rule), used only for this genuine, operator-confirmed
removal. New panel in the item-detail UI, mirroring C13's
eBay→Inventory-Record sync panel's exact pre-checked-by-default review
pattern, own button/action name (spec point 6 discipline — no shared
write path). 29 new tests (unit + accessor + HTTP), full suite 2364
passed/1 skipped. `tgw-http` restarted live; verified read-only against
the real `tgw202605051752520` — correctly detects all 18 real orphaned
aspects. Not yet live-fire tested end-to-end (an actual apply against a
real listing) — needs Dave's own test, same as today's other fixes.

**Todo #1461 — attribute-delete-reverts bug found + fixed 2026-07-16 (Dave:
"I have repeatedly deleted material, currently set to Silver, saved,
updates and that field reverts every time. likely not the only one.").**
Root cause: a frontend bug, not a backend merge issue. `saveEbayDraft()`'s
aspects-collection loop (`src/tgw/http_server.py`, the eBay Draft
Editor's `#aspects-form`) only included a field in the save payload
`if(v)` — i.e. only when non-empty. Clearing a field produced `v===''`,
silently dropped from the PATCH entirely, so `set_ebay_aspects()` never
saw an attempted change and the old value stuck forever. Affects every
aspect field uniformly (shared loop) — confirmed Dave's "likely not the
only one." **Fix:** each aspect input/select now carries
`data-initial` (its rendered value); the collection loop sends the key
whenever the current value differs from `data-initial`, including a
change to empty — matching how every other field on this form already
behaves. 2 new tests; full offline suite 2333 passed/1 skipped, no
regressions. `tgw-http.service` restarted live. Not yet manually
browser-verified end-to-end (needs Dave's own login session) — asked Dave
to confirm from his side.
**R1.1 live-fire DONE 2026-07-04 (todo #1137).** Price-only delta
(`tgw201501021970128`, $7.99→$8.49) via `revision.py`'s drift-gated apply
path (`tgw revise <sku> --set price=X --show` then `--apply --live`).
Live-verified in both directions with fresh uncached eBay API reads (not
just job-succeeded logs): real price changed on the actual listing, then
reverted; `revision_history` correctly recorded delta + baseline hash +
the exact API call made (`PUT offer/264095634018`), hash_match=true, zero
drift. **Gate cleared.** Real bug found along the way (todo #1138, minor):
the CLI's `--set` help text claims dotted-path support
(`draft_listing.price`) but the live-apply path only accepts bare field
names (`price`) — use bare names; dotted paths raise a clear "unsupported
delta field" error at apply time, not silently ignored. Next: wire the
Update-Item button to this same apply path. Design:
`archive/sections/Pending-projects-revisit.md` (promote on touch).

**Todo #1062 closed as satisfied, not built new (2026-07-04).** Its scope
("item detail page restructure + editable aspects") is already fully
covered by PP-ACTIONCONSOLE-001's s40 build — verified in code: Editor
tab + Live/Sold Listing tab, 3-layer live/proposed/edit aspect merge,
condition select, price history, reprice schedule. Consolidated into
#1085's "operator eyeball" gate instead of duplicating.

**Same-day fix, todo #1114 — auto-redraft-clobbers-operator-edit, DONE and
live-verified.** Investigated per Dave's request ("verify why we did it that
way before changing") rather than jumping straight to a fix. Root cause: the
HTTP PATCH auto-enqueue trigger (`patch_item()`) conflated two different
things under one condition — "a raw fact changed, regenerate the AI draft"
vs. "the operator polished the final draft content directly." In practice
only the second ever happens (the editor UI only ever PATCHes into
`draft_listing.*` — no code path sends bare top-level `title`/
`item_attributes` through this endpoint), so regenerating was never
correct: every operator edit to an already-live item's draft got silently
overwritten by a fresh AI regeneration before it was ever seen. Cost impact
(Dave's own estimate, confirmed): each needless regen burns 2 AI calls
(primary draft + `bulk_classify` aspect-fill) for zero benefit — a typical
2-3-edit polish session tripled the AI cost of a step that should cost
nothing. Fixed to mirror the existing "Update Listing" button exactly: push
(`ebay_stage`, `force=True`, `origin=operator`) instead of regenerate
(`ebay_draft`). Live-verified against a real published listing
(`tgw201501021970354`) all the way to a real eBay title change, confirmed
via a fresh uncached API read, then reverted. 3 new tests.

**Todo #1445 investigated 2026-07-16 (Claude, read-only, no writes) — root
cause found for the "update succeeds but live/local state doesn't match"
symptom Dave flagged against `tgw202605040949058`.** Live read-only GETs
against the real eBay offer + `inventory_item` show current eBay API data
matches the local `ebay_live`/`ebay_submitted` cache exactly — no drift at
the API level right now. `catalog-verify` flags `photo_verify_stale` at
**critical** on this SKU: `photo_verify.verified_at` (2026-07-15T02:46)
predates the most recent `ebay_publish` (2026-07-16T14:50) by 36+ hours.
Traced why: `queue_jobs` shows 6 `ebay_stage`/`ebay_publish` cycles on this
SKU today, all succeeded, zero `ebay_sync` jobs alongside them. Confirmed
in source — `ebay_publish.py` only ever enqueues a follow-up `ebay_stage`
(price-drift force-restage); the **only** code path that enqueues
`ebay_sync` as a follow-up is `http_server.py`'s `apply_revision`
(LISTEDITOR revision/apply endpoint). **A normal republish through the
ordinary auto-pipeline never refreshes the local live-mirror/photo-verify
snapshot** — it silently goes stale until some independent sync
eventually catches up. Candidate fix: have `ebay_publish` enqueue
`ebay_sync` as a follow-up on success too, same pattern `apply_revision`
already uses. Todo #1445 kept open (not closed) — this is the diagnosis,
building the fix needs Dave's go-ahead.

**Live-confirmed on one real item, 2026-07-16 23:02** — Dave cleared
Material on `tgw202605051207245` (the Cloisonné/Porcelain drift item):
save recorded correctly, staged/published with no rejection, live eBay
aspects now show only `Original/Reproduction` (fresh API read confirms
Material is gone), and an `ebay_sync` job auto-queued alongside the push.
All three of today's fixes (#1461, #1462, #1445/#1467) working together
on one real edit. **Dave's own framing: "1 worked 1 did not — not enough
data"** — more of his own live testing across other items is pending
before this counts as fleet-confirmed; none of the underlying todos are
closed yet on the strength of this one success.

**Fix built + deployed 2026-07-16 (Dave: "yes, make the fix"), then
extended same day to cover the actually-common path (invariant C14
incident, Dave: "why do we keep having to manually re-sync").** Original
fix added a post-publish sync call to `ebay_publish.py`'s two success
paths. Root-caused further same day: the far more common "Update Listing"
button on an already-live item enqueues `ebay_stage` directly and never
touches `ebay_publish` except via a conditional chain (ebay_stage's own
republish trigger, which only fires when a `listing_id` already exists) —
so `ebay_stage.py`, which runs on nearly every real edit, never refreshed
the local `ebay_live` mirror at all. Pulled the duplicate enqueue logic up
into a shared `tgw.ebay.sync.enqueue_post_push_sync()` (same precedent as
that module's existing `format_ebay_error` cross-worker helper) and wired
it unconditionally into both `ebay_stage.py`'s and `ebay_publish.py`'s
success paths — deduped per SKU (`ebay_sync:post_push:<sku>`), non-fatal
on collision or failure. 5 new offline tests total across
`tests/test_ebay_publish_post_publish_sync.py` and the new
`tests/test_ebay_stage_post_push_sync.py`; full offline suite 2338
passed/1 skipped, no regressions. `tgw-worker@ebay_stage.service` and
`tgw-worker@ebay_publish.service` restarted live. **Not yet live-fire-
confirmed against a real publish** — re-publishing the real,
already-listed `tgw202605040949058` to test it was correctly blocked by
the permission gate (a live production write against a real listing isn't
authorized by "make the fix" alone). Confirmation will come from the next
organic publish/stage, or a Dave-approved safe test item. See invariants.md
C14 for the full incident this sits inside.

