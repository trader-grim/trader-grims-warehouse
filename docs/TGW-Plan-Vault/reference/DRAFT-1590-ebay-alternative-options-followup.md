# DRAFT — eBay Developer Support NEW ticket: unresolved items from closed case (todo #1590)

**Not submitted — this is a draft for Dave to review/edit/submit.** eBay
Developer Support tickets go through Dave's account; I can't submit this
on his behalf. This is a **new ticket**, not a reply within case
`EBAY-DS-260605-000035` — that case was closed by eBay upon their
Marketplace Insights response, per `EXTERNAL-SUPPORT-TICKET-REGISTER.md`.

## What prompted this

eBay's Marketplace Insights denial (received 2026-07-20) closed with:

> "We understand that this may be disappointing, and our team is here to
> assist with any questions or alternative options that might fit your
> use case. Please feel free to reach out to our technical support team
> through your eBay developer account for any API-related queries."

**Dave, 2026-07-20 — the actual mechanism at work here:** case
`260605-000035` bundled two requests (`buy.marketplace_insights` scope
+ the EPS rate-limit increase, per the register). eBay answered only the
Marketplace Insights half and **permanently closed the whole case** —
the EPS half was never addressed, and Dave had to live-verify the limit
himself (below) precisely because the closure gave no indication either
way. Dave's read: eBay support reps get credited per ticket closed, so
bundling two asks into one case and closing it after answering only one
of them effectively converts one real resolution into eBay's internal
credit for closing 2 (potentially 3, if this new ticket suffers the same
fate) tickets, none of which actually resolved the EPS ask. This ticket
is written as a **new, standalone ticket** specifically so the EPS
request can't be silently closed-by-association with an unrelated
answer again — and to name, on the record, that the prior case was
closed without addressing it.

**Also live-verified same session:** eBay has NOT silently raised the EPS
(`UploadSiteHostedPictures`) daily call limit — confirmed via a real
`getRateLimits` call: still exactly `limit: 5000`/day, unchanged since
the 2026-07-02 baseline. The EPS request was never actually answered —
only closed alongside an unrelated answer.

**Dave probed further, same session — an AI-generated eBay support reply
(not the formal ticket system) confirms both threads:** on sold-price
data, it stated flatly there is no API/report delivering item-level sold
price data for independent sellers — Marketplace Insights genuinely has
no substitute, the denial's "alternative options" line was empty. On EPS,
it stated the **Application Growth Check is the only documented path**
to a limit increase, and suggested one hadn't been completed — but one
had: case `260605-000035` **was** an Application Growth Check, with the
EPS ask bundled into it. This is either the bot lacking case-history
context, or further confirmation that only the Marketplace Insights half
of that check was actually processed. The ticket text below now states
plainly that the check was already done under that case number, so
support can't redirect us to restart the process from scratch.

## Suggested ticket text

> **Subject:** Two unresolved items from closed case 260605-000035 —
> requesting the alternative options mentioned, and the EPS limit
> increase that was never actually addressed
>
> **Account:** DaveBuko-Webkulap
>
> I'm opening this as a new ticket because case 260605-000035 was closed
> after your reply addressed only one of the two requests it contained.
>
> **1) Alternative options.** Your Marketplace Insights response
> mentioned your team is available to discuss "alternative options that
> might fit your use case" — I'd like to take you up on that directly.
> Our use case: pricing our ~55,000-item catalog competitively requires
> visibility into actual recent sold prices for comparable items, not
> just active-listing prices. Marketplace Insights was the path we
> understood was available for that. If that scope isn't available to
> us, what specific alternative mechanism would give an independent
> seller access to sold/comp price data at a similar level of
> usefulness? A named API, report, or program — not a general pointer
> back to developer support — is what we're asking for here.
>
> **2) EPS call-limit increase.** Case 260605-000035 was itself an
> Application Growth Check, and it included a request to increase our
> EPS (`UploadSiteHostedPictures`) daily call limit as part of that
> check. That request was never addressed before the case was closed —
> the Growth Check was completed, but only the Marketplace Insights
> portion received a substantive answer. I'm not asking to restart the
> Growth Check process; I'm asking for the EPS decision from the check
> that already happened under case 260605-000035. Could you please
> respond to it directly in this new ticket, rather than closing without
> a substantive answer again?

## Context for Dave

- **Disclosure rule, refined by Dave 2026-07-20:** not "reveal less" —
  "reveal facts, never methods." Catalog scale and anything eBay already
  has visibility into (they host the listings) is fine to state plainly;
  hiding it looks evasive for no protective benefit. What actually stays
  out: how our systems work internally — e.g. `DRAFT-1076`'s "per-pool
  daily budget with automatic background halt at 70% utilization" line
  describes a specific internal mechanism, not just a fact, and is the
  kind of detail Dave's experience with eBay's partner-functionality-
  absorption pattern (surviving even a written agreement) says to cut.
  This draft states the business need and our known scale, never how our
  pipeline/automation is built.
- This stays factual/professional in the actual ticket text — the venting
  about eBay's tactics is real and correctly placed in your own words to
  us, not in what goes to them; a support ticket that reads as
  adversarial tends to get a worse outcome, not a better one. The ticket
  above is direct and holds them to their own offer without editorializing.
- Live-verified EPS limit is unchanged (5,000/day, checked just now via
  `getRateLimits`) — cited in the ticket so they can't claim it was
  already addressed.
- Register (`EXTERNAL-SUPPORT-TICKET-REGISTER.md`) needs a **new local
  key** for this ticket once eBay issues its own case number (this is
  not `260605-000035`'s continuation — that case is closed) — record it
  as a fresh row, cross-referenced back to `260605-000035` as the
  originating case, per the register's own instructions for a provider
  closing a case with a request still unresolved.
- No further action from me here — needs your review/edit and your own
  submission through Developer Support's ticket system.
