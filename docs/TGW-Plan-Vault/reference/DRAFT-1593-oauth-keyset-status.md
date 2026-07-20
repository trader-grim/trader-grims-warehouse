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

**Open question this ticket needs to resolve:** is this a self-service
action that should have completed instantly (in which case checking the
developer.ebay.com portal directly, not a support ticket, is the right
first move), or did it require the same Developer Support approval path
as the scope/Growth-Check requests? The draft below assumes the latter
(a ticket makes sense) since the account's live App ID as of 2026-06-05
was still the original (`DaveBuko-DaveBuko-P-66170566`), suggesting the
new keyset was never actually issued/activated.

## Suggested ticket text

> **Subject:** Status of new application keyset requested 2026-06-05
>
> **Account:** DaveBuko-Webkulap
>
> On 2026-06-05 I created a new application keyset via the Developer
> Program portal, requesting a full set of scopes for our automated
> inventory application. I never received confirmation that this new
> keyset was approved/issued, and our production credentials are
> currently still the original keyset. Could you confirm the status of
> that request — was it approved, is it still pending, or does it need
> to be resubmitted? If resubmission is needed, please let me know what
> additional information you require.

## Context for Dave

- **Before sending this, worth a quick direct check of
  developer.ebay.com's Application Keys page yourself** — if a second
  keyset already exists there (approved but never noticed/adopted), this
  ticket is unnecessary and the actual next step is just updating
  `secrets_root/ebay-credentials.json` and re-running
  `get_access_token.py` against it, not a support ticket at all. I can't
  check the portal myself (it's account-management UI, not an API this
  session can reach) — flagging so this doesn't turn into an unnecessary
  ticket if the answer is already sitting in your account.
- No internal architecture/automation detail included — states the fact
  (keyset requested, no confirmation received) and asks for status.
