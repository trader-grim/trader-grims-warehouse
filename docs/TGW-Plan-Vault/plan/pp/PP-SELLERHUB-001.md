# PP-SELLERHUB-001 — TGW as a full Seller Hub replacement

**Opened 2026-07-11.** Surfaced while triaging a homeless todo (#895,
fulfillment_policy_shipping_cost config) during a `tgw todo --by-pp` sweep —
Dave's framing made clear this wasn't a one-off config gap but a symptom of
a much bigger, previously-unstated principle.

## The principle (Dave, 2026-07-11, verbatim intent)

> "There is one thing missing that answers many of the questions. Our app
> needs to be able to do everything eBay Seller Hub does, but better."

TGW should be feature-complete against eBay's own Seller Hub — not as a
thin wrapper around it, but a genuine replacement, better where it matters
(the whole platform's reason to exist is doing this better than eBay's own
tools). This isn't scoped narrowly — Dave explicitly declined to limit it:
"I do not see any reason to limit the scope of the audit. There will
certainly be things we don't need, but we are missing tons."

## Why this needed its own PP, not a fold-in

Its value is as much organizational as prioritizing: **"Having them in the
plan gives us a place to park interface and other related notes also."**
Several previously-homeless todos (#895, #12 — both literally about
Seller-Hub-class functionality) had nowhere to live because this principle
had never been written down. This PP is that home, going forward, for any
"TGW should be able to do X the way Seller Hub does" note — even ones
that won't get built for a long time.

## Priority #1 (concrete, near-term): categories + business policies

Named directly by Dave: **"a better sync with eBay on the categories. We
need to be able to manage them too"** — plus business-policy management
(shipping, the immediate trigger via #895's shipping-cost config gap).
Today TGW has category data (the `category-groups.json` template table,
PP-CATPICK-001's candidate backfill) but no live management surface —
editing/syncing categories and business policies (shipping, payment,
return) happens on eBay's side only, never in TGW.

**Absorbed:**
- `#895` — fulfillment_policy_shipping_cost config gap (the trigger).
- `#12` — 9 wrong-shipping Seller Hub listings (same class of gap — TGW
  should have caught/managed this, not needed a manual Seller Hub sweep).

## Priority — everything else (parked, not prioritized, but enumerated as found)

Dave's own list, not exhaustive by design — the audit (below) is what
actually enumerates this: no way to edit seller profile, no way to manage
business policies broadly (beyond shipping), and whatever else the audit
surfaces (promotions, reports, messages, and more are likely candidates
but not yet confirmed against Seller Hub's actual current feature set).
**"The rest fall right behind and won't change much if at all by the time
we get to them"** — i.e., don't gate starting priority #1 work on fully
scoping the rest; the audit's enumeration is valuable even for items that
sit untouched a long time.

## The Gemini audit (proposed mechanism, not yet run)

**Scope: unlimited, by design** — Dave explicitly declined to bound it.
Full comparison of eBay Seller Hub's actual feature surface against TGW's
current capability (API + web UI + Flutter app), producing a structured,
durable gap map — not just answering the categories question.

**Not yet run.** This is real, substantial work deserving its own scoped
packet/session, not something to improvise inline. Open questions for that
packet:
- Mechanism: Gemini with Search grounding (already proven capable in this
  codebase — see PP-MARKETING-001/PP-PRICING-001's #1109 eval, though that
  was for a different task and grounding LOST there; worth re-evaluating
  for this specific research-not-pricing use case) researching Seller
  Hub's current feature set, cross-referenced against an enumeration of
  TGW's actual current capability (I'd need to supply that side — API
  surface, web UI screens, Flutter app screens).
  See `reference/LLM-Providers-Quotas.md` before choosing a model/quota
  pool.
- Output format: a structured gap list (Seller Hub feature → TGW status:
  have it / partial / missing) is the obvious shape, feeding directly into
  this PP's "everything else" section above once run.
- Cost/quota: not yet estimated — flag before running, per standing
  practice for any bulk AI operation.

## Cross-links
- `#895`, `#12` — absorbed here (see above).
- PP-CATPICK-001 — existing category *data* work (candidate backfill);
  this PP is about *management*, a different (missing) capability.
- PP-EDITOR-001 — the web UI PP; wherever category/policy management gets
  a UI, it likely lives there, not as a separate surface.

## Open questions
- Full scope enumeration — pending the audit.
- Where category/policy management actually gets built (own surface vs.
  extending PP-EDITOR-001) — not decided, revisit once the audit exists.
- Audit mechanism/cost — see above, needs its own scoping pass before running.
