# PP-EBAY-ACCOUNT2-001 — second eBay account: Seller Hub audit sandbox + multi-marketplace capability

**Opened:** 2026-07-18 (Dave). **Status: PROPOSAL — not yet scoped, no code/config work started.**

## Origin

Surfaced as a side proposal while going over the buried-fixes survey (PP-CATALOG-INCR-001's
sibling review). Two distinct motivations, both pointing at the same underlying need — a second
eBay account:

1. **Seller Hub audit sandbox.** PP-SELLERHUB-001 has a standing "proposed but not-yet-run
   Gemini audit of Seller Hub's full feature surface vs. TGW's current capability" — running that
   kind of exploratory audit (clicking through every Seller Hub surface, possibly creating test
   listings/policies to see how controls behave) against the live production account risks
   touching real inventory/listings. A second, dedicated account gives a safe place to explore
   Seller Hub's actual behavior without that risk.
2. **Multi-marketplace capability, Dave's own suggestion:** "I suggest we also use it to add
   multi marketplace capability." Ties into PP-EBAY-MOTORS-001's confirmed gap — TGW has never
   explicitly modeled non-US-general marketplaces (EBAY_MOTORS, and by extension any other eBay
   site — EBAY_UK, EBAY_DE, etc.). A second account is a way to build/test multi-marketplace
   listing flows without risking the primary account's live listings while the code path is
   still unproven.

## Decision so far (2026-07-18)

Account creation is Dave's own action, not TGW's — eBay account signup isn't something the
system can do on its own behalf. **Dave registers the new eBay seller account himself**
(standard eBay signup, same as the primary account originally was), then hands over the
developer app credentials (API keys / OAuth client) for Claude to wire into TGW's existing
secrets facility, same pattern as the primary account uses today
(`secrets_root/tgw.env` + `tgw.apis.secrets.get_api_key()` — see CLAUDE.md's Settled
Architecture entry). No account exists yet as of this writing.

## What's NOT yet scoped (real open work, not decided here)

- **Config shape for a second account.** Today's config (`tgw-api-config.json`,
  `tgw-models.json`, secrets) is implicitly single-account. Adding a second live eBay identity
  needs a "which account" dimension threaded through wherever credentials/tokens are resolved —
  unclear yet whether this is a top-level config key, a per-item field, or something else. Needs
  its own design pass once real requirements (which of the two use cases above is primary) are
  clearer.
- **Relationship to PP-EBAY-MOTORS-001 — resolved 2026-07-18 (Dave):** "Motors needs to be
  available for every eBay account. It is a barnacle marketplace. We just have to accommodate
  it." Motors and account are different axes (confirmed: Motors is a *marketplace* dimension on
  the SAME seller account, a second account is a different seller identity entirely), but the
  fix is NOT account-specific — `marketplace_id`/`site_id` handling is shared infrastructure both
  the primary account and this future second account ride on top of, with no per-account
  special-casing. So this PP does not own "build Motors handling" (that stays
  PP-EBAY-MOTORS-001's job, done once, account-agnostic) — this PP only owns what's genuinely
  second-account-specific: credential/config plumbing for a second identity, and whatever the
  Seller Hub audit sandbox needs that the Motors work doesn't already cover.
- **Scope of the Seller Hub audit itself.** PP-SELLERHUB-001's Gemini-audit proposal was never
  scoped (mechanism, cost/quota estimate) even before this — a second account removes the
  *safety* blocker but not the *scoping* blocker.
- **Cost/quota implications.** A second eBay developer app likely has its own separate rate
  limits — worth checking eBay's per-developer-account vs per-application quota model before
  assuming this doubles available headroom for anything.

## Next step

No todo filed yet — this is a proposal record, not ready to slice into build work. Once Dave has
registered the account and has credentials in hand, the first real todo is: decide which of the
two motivations (audit sandbox vs. multi-marketplace build/test) is the account's primary
purpose, since that materially changes the config-shape design work above.

## Config-shape sketch, unfolded 2026-07-22 (Dave: "plan until nothing left to plan")

**Genuinely blocked on Dave's own action** (account registration + credentials in hand) — this
section pre-sketches the two candidate shapes so the actual decision, once he's ready, is a
5-minute pick rather than a fresh design pass. Not committing to either.

**Option A — top-level config key (`accounts` block in `tgw-api-config.json`).** Every config
lookup that currently assumes one account gets an `account` parameter (default = primary, so
zero behavior change for existing code); secrets become
`secrets_root/tgw.env`'s existing single-facility pattern extended with an account suffix
(`EBAY_CLIENT_ID`, `EBAY_CLIENT_ID_ACCOUNT2`). **Best fit if the primary purpose is Motors/
multi-marketplace build-and-test** — a small number of code paths (draft/upload/publish) need to
know "which account," everything else stays untouched.

**Option B — per-item field (`item['ebay_account'] = 'account2'`).** Items themselves carry which
account they belong to, resolved at the fence (`tgw-api`) rather than threaded through every
config lookup. **Best fit if the primary purpose is the Seller Hub audit sandbox** — the sandbox
account would hold its own throwaway test items, never mixed with primary-account inventory, and
this shape keeps that separation enforced at the data level (an item simply belongs to one
account or the other) rather than as an ambient config parameter every call site has to remember
to pass correctly.

**The deciding question, unchanged from the original doc, now sharpened**: is this account
primarily a place items *live* (Option B) or a separate *identity* the same item-agnostic API
surface calls through (Option A)? Motors/multi-marketplace leans Option A (same item, different
marketplace_id, same or different account); Seller Hub audit sandbox leans Option B (distinct,
disposable, never-real inventory). If both motivations end up mattering, Option A is the more
general shape (Option B is expressible as "items resolve their account via Option A's account
parameter, filtered by item field") — worth noting Option A subsumes Option B if forced to build
only one, but the *decision* still belongs to Dave once the account exists.

**One factual pre-check worth doing now, free of the account-registration blocker**: whether
eBay's rate limits are scoped per-developer-application (`client_id`) or per-seller-account. If
per-application, a second seller account under the SAME developer app might not need a full
second `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` pair at all — just a second OAuth user token
resolved the same way the primary account's refresh flow already works, substantially simplifying
the secrets-plumbing shape above. **Not verified here** — this is a factual claim about eBay's
API policy, not something to assert from memory; needs a real check (eBay developer docs or a
direct support question) before assuming either shape.
