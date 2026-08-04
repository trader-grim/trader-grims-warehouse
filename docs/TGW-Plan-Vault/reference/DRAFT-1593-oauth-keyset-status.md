# DRAFT — eBay ticket: status of new keyset requested 2026-06-05 (todo #1593)

**Not submitted — draft for Dave to review/edit/submit.**

## Background

Per the master plan's archived "Priority 1 — eBay Developer Account (new
keyset + scope requests)" section: a **new production keyset** (new App
ID / Cert ID / Dev ID, app name suggestion `TGW-Automation-v2`) was
requested self-service via developer.ebay.com on **2026-06-05**, with all
desired scopes (including `buy.marketplace_insights`) applied at once as
part of that new keyset — the stated strategy was "avoid piecemeal scope
expansion later." The checklist items after "requested" were never
checked off: receive approval + credentials, update secrets with the new
App ID/Cert ID/Dev ID, re-run OAuth against the new keyset, restart
workers. No resolution note exists anywhere in the vault — it simply
stopped being tracked when the plan was compressed/archived.

**Confirmed by Dave, 2026-07-20 (checked developer.ebay.com directly):**
the new keyset request is itself gated by the same Application Growth
Check process as the scope and EPS requests — not a plain self-service
action that should have completed instantly. So this is genuinely the
third facet of the same underlying gate: three separately-formatted,
properly-submitted requests (scope, EPS, keyset), all routed through one
review process that eBay can — and per the `260605-000035` closure,
did — treat as satisfied by answering only one facet.

## Suggested ticket text

> **Subject:** Application Growth Check — status of new application
> keyset requested 2026-06-05
>
> **Account:** DaveBuko-Webkulap
>
> On 2026-06-05 I submitted an Application Growth Check requesting a new
> application keyset with an expanded set of scopes for our inventory
> application. I never received confirmation that this request was
> approved, denied, or is still pending — our production credentials are
> currently still the original keyset. Could you confirm the current
> status of that specific request? If it needs to be resubmitted, please
> let me know what additional information is required.

## Context for Dave

- Confirmed 2026-07-20 (you checked the portal directly): this is not a
  plain self-service action, it's Growth-Check-gated like the other two
  — the "check the portal first, might be unnecessary" framing in the
  earlier draft of this note was wrong, removed.
- No internal architecture/automation detail included — states the fact
  (request submitted, no confirmation received) and asks for status.
