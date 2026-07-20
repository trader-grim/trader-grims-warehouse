# DRAFT — eBay Developer Support follow-up: hold them to "alternative options" (todo #1590)

**Not submitted — this is a draft for Dave to review/edit/submit.** eBay
Developer Support tickets go through Dave's account; I can't submit this
on his behalf. Follow-up within the same active case
(`EBAY-DS-260605-000035`), per `EXTERNAL-SUPPORT-TICKET-REGISTER.md`.

## What prompted this

eBay's Marketplace Insights denial (received 2026-07-20) closed with:

> "We understand that this may be disappointing, and our team is here to
> assist with any questions or alternative options that might fit your
> use case. Please feel free to reach out to our technical support team
> through your eBay developer account for any API-related queries."

Dave, same day: "They are full of great ways to make life difficult...
this is why we have proactivity as part of our work ethic, so we do not
become one of those sloths... [they cannot fool me]." The read here:
that closing line is a standard deflection unless we actually take it up
and make them name something concrete — this draft does that, on the
record, in writing, in the same case.

**Also live-verified same session:** eBay has NOT silently raised the EPS
(`UploadSiteHostedPictures`) daily call limit — confirmed via a real
`getRateLimits` call just now: still exactly `limit: 5000`/day, unchanged
since the 2026-07-02 baseline. The rate-limit-increase half of this case
(todo #1076, bundled in per the register) never got its own answer —
Developer Support's reply only addressed Marketplace Insights.

## Suggested ticket text

> **Subject:** Follow-up on case 260605-000035 — requesting the
> alternative options mentioned, and status on the EPS limit increase
>
> **Account:** DaveBuko-Webkulap
>
> Thank you for the update on Marketplace Insights. Your response
> mentioned your team is available to discuss "alternative options that
> might fit your use case" — I'd like to take you up on that directly.
>
> Our use case: pricing our ~55,000-item catalog competitively requires
> visibility into actual recent sold prices for comparable items, not
> just active-listing prices. Marketplace Insights was the path we
> understood was available for that. If that scope isn't available to
> us, what specific alternative mechanism would give an independent
> seller access to sold/comp price data at a similar level of
> usefulness? A named API, report, or program — not a general pointer
> back to developer support — is what we're asking for here.
>
> Separately: this same case also included a request to increase our
> EPS (`UploadSiteHostedPictures`) daily call limit (see our prior
> message in this thread, and case reference on file). That request
> hasn't received a response yet — could you confirm its status, or let
> us know if it needs to be resubmitted as its own case?

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
- Register (`EXTERNAL-SUPPORT-TICKET-REGISTER.md`) should be updated with
  this follow-up once sent — new "prepared" artifact, case stays the same
  (`260605-000035`), state moves to whatever's accurate once you send it.
- No further action from me here — needs your review/edit and your own
  submission through Developer Support's ticket system.
