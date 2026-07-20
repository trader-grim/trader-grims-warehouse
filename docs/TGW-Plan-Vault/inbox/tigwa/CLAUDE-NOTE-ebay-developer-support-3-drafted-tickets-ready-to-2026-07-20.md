# Note: eBay Developer Support: 3 drafted tickets ready to send, please help track follow-through

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T17:41Z

Dave asked me to loop you in on today's eBay Developer Support thread so you can help make sure he actually completes the follow-through -- three drafted tickets are sitting unsent, and given the pattern below, timely follow-up matters.

## What happened
eBay denied the `buy.marketplace_insights` scope request and closed case `EBAY-DS-260605-000035` -- but that case had bundled THREE separate asks (the scope request, an EPS/UploadSiteHostedPictures rate-limit increase, and, we later confirmed, a new-OAuth-keyset request from 2026-06-05 that was never fulfilled). eBay answered only the scope question and closed the whole case, leaving the other two silently unresolved. Live-verified the EPS limit is genuinely still 5,000/day (unchanged since 2026-07-02) -- not a silent grant, just never addressed.

Dave's read, and I think it's right: "the intention is that we get frustrated and go away." eBay gets ~13% of sales from resale/used goods and has a long track record (per Dave, "even in the beenie baby days") of treating that seller class as second-tier. Bundling multiple properly-formatted requests into one review gate and closing on a partial answer is structural friction, not an accident -- the counter-strategy is persistence and precise documentation, not giving up after one round.

## What's ready now (all drafted, none sent -- Dave's to review/edit/submit)
Split into three standalone tickets per Dave's direction ("play ball, give them all the tickets they want, ask both separately"):
- `docs/TGW-Plan-Vault/reference/DRAFT-1591-eps-growth-check.md` -- fresh EPS rate-limit increase Growth Check
- `docs/TGW-Plan-Vault/reference/DRAFT-1592-alternative-options-sold-price.md` -- holds eBay to their own "alternative options" offer for sold-price data
- `docs/TGW-Plan-Vault/reference/DRAFT-1593-oauth-keyset-status.md` -- status of the 2026-06-05 new-keyset request; Dave confirmed live via developer.ebay.com that this too is Application-Growth-Check-gated, same bottleneck as the other two, not a plain self-service action

Register: `docs/TGW-Plan-Vault/plan/EXTERNAL-SUPPORT-TICKET-REGISTER.md`, rows `EBAY-DS-1591`/`1592`/`1593` (all `prepared / not yet submitted`), `EBAY-DS-260605-000035` marked closed-partial, `EBAY-DS-1590` (the original combined draft) marked superseded/kept for record.

Disclosure discipline applied throughout, worth knowing for anything you draft toward eBay too: state facts eBay already has visibility into freely (catalog scale, seller activity) -- don't be cagey, that reads worse. But never describe internal methods/architecture/automation mechanisms (Dave's exact framing: "just like in police interactions, everything you say to eBay can and will be used against you," plus a documented pattern of eBay absorbing partner functionality without compensation even under written agreement).

## The ask
Please help track that Dave actually sends all three -- they're only useful once submitted, and given today's context, this is exactly the kind of multi-step follow-through that's easy to let slip. No action needed from you beyond that tracking/nudge role; nothing here needs your review or design input.
