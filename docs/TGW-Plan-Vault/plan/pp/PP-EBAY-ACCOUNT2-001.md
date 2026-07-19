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
