# ebay-api-growth-unblock: remove every self-inflicted or eBay-side block on legitimate API volume so Growth Check requests actually land

**Status:** Draft — 2026-07-20
**PP ref:** PP-EBAY-SNAPSHOT-001 (todo #1596)

## Problem / motivation

Dave, 2026-07-20: "The brakes are off. We work on what we need from the api or they
will hold us back forever." eBay's own Growth Check process requires the app to be
live with real, growing usage before they'll raise any rate limit — but TGW is
currently sitting on several self-inflicted blocks (excluded workers, an unbuilt
compliance webhook, an unresolved orphaned-object bug) that are actively
suppressing real API volume, independent of anything eBay controls. Today's
session already found and fixed one of these (`ebay_sync` was missing from the
NixOS flake entirely, not just stopped). This plan lays out everything else in
the same category, sequenced so engineering work and Dave-only external actions
don't block each other unnecessarily.

Standing strategy this plan operationalizes (already encoded in
`TGW-Master-Plan.md` under PP-EBAY-SNAPSHOT-001 and
`EXTERNAL-SUPPORT-TGW-Plan-Vault/plan/EXTERNAL-SUPPORT-TICKET-REGISTER.md`):
when a real, legitimate workload hits a documented eBay rate ceiling, file a
Growth Check / rate-increase request promptly, following eBay's published
process to the letter — no manufactured volume, no gaming the review.

## Constraints (from settled architecture)

- tgw-api fence: any new code touching ItemData goes through the fence, never
  direct path construction.
- Workers stay thin — a new webhook handler is a `tgw-http` endpoint calling into
  a `tgw.ebay.webhooks` (or similar) module, not inline logic in `http_server.py`.
- Output contract: `{ok, ...}` from any new API call.
- Secrets from `secrets_root` only — the webhook's verification token and any new
  eBay keyset material go in `secrets_root/tgw.env`, read via
  `tgw.apis.secrets.get_api_key()`, never a new bespoke credentials file.
- Catalog rebuild is always a job — not relevant to new work here, but
  `catalog_rebuild` itself must **stay excluded from the flake** per the
  standing safety rationale below; nothing in this plan proposes re-enabling it.
- No silent substitutions — every drafted eBay ticket keeps Dave as the sending
  authority; nothing here authorizes Claude/an agent to submit external
  correspondence.

## Current state (verified live, 2026-07-20)

| Item | State | Blocking what |
|---|---|---|
| `ebay_sync` worker | **Fixed today** — re-added to flake, `nixos-rebuild switch` done, running, draining 30 queued jobs via the known 25707 fallback | Was suppressing real sync volume |
| `MARKETPLACE_ACCOUNT_DELETION` webhook | Spec written (`reference/ISS-005-REST-Signature-Verification.md`), **zero implementation** | Hard prerequisite eBay's Growth Check email states explicitly — blocks every rate-increase request until built |
| `EBAY-DS-1077` / eBay error 25707 | Orphaned Inventory API offer (57-char legacy-import SKU) unreachable by any client-side call; case `260719-000018` open, eBay gave boilerplate reply; urgency followup drafted today, **not sent** | Root cause of `ebay_sync`'s degraded fallback; also the reason `catalog_rebuild` and `ebay_legacy_sync` must stay excluded (see cascade history below) |
| `ebay_legacy_sync` worker | Excluded from flake since the 2026-07-12 20:51 incident; separately, todo #1248 names an older unresolved 6-min quota-eating retrigger loop, blocked on the same missing webhook | 235 real jobs stuck queued |
| `catalog_rebuild` worker | Deliberately excluded, **must stay excluded** until #1077 is actually fixed — its own history: `ebay_sync`'s 25707 fallback throttles 24h, and every throttle expiry re-swept the full catalog, firing `catalog_rebuild` nonstop (~57s/cycle, every 30-90s) until stopped by hand (2026-07-12 incident, 4-day silent resurrection caught as todo #1349) | Not a "quick win" — re-enabling before #1077 lands would very likely repeat the exact incident that got both workers excluded in the first place |
| EPS (`UploadSiteHostedPictures`) rate-limit ticket | Drafted (`DRAFT-1591-eps-growth-check.md`, todo #1591), **not sent** | Nothing — ready to send, but should go out *after* the webhook exists per eBay's own stated prerequisite order |
| Alternative sold-price data ticket | Drafted (`DRAFT-1592-alternative-options-sold-price.md`, todo #1592) | Independent of the API-volume thread; can send any time |
| New keyset status check | Drafted (`DRAFT-1593-oauth-keyset-status.md`, todo #1593) | Independent; recommend checking the Developer Portal directly before sending, per the draft's own note |
| Growth Check request form | Dave is having Tigwa obtain a copy | Nothing yet — informs how #1591 and future requests get filled out |

## Proposed approach

Three tracks, largely independent, sequenced by real dependency (not by
convenience):

### Track A — build the missing compliance webhook (pure engineering, unblocks everything else)

This is the one item that gates every future Growth Check submission, per
eBay's own stated order of operations. Build it first.

1. **Data audit** — before writing the handler, determine what (if any) buyer/
   order data tied to an eBay account TGW actually retains today (grep
   `item_attributes`/`draft_listing`/order-adjacent fields for buyer usernames,
   shipping addresses, etc. — `api.py`'s `_COL_BUYER` handling is the known
   starting point). This determines what the purge action on receipt of a
   deletion notification actually has to do. If TGW holds nothing purgeable,
   the handler still must exist and ack correctly — the requirement is
   subscription + correct handling, not "has PII to delete."
2. **Endpoint validation (GET)** — new `tgw-http` route, `challenge_code` →
   SHA-256(`challenge_code + verification_token + endpoint_url`) → JSON
   response, per the spec.
3. **Payload verification (POST)** — decode `X-EBAY-SIGNATURE`, fetch/cache the
   public key from eBay's Notification API (24h cache, per spec), verify with
   `cryptography`'s ECDSA/SHA-256, then run the purge action (or no-op if Track
   A.1 found nothing to purge) and log receipt durably (Prime Directive 1 —
   every notification is an asset the moment it arrives).
4. **Secrets** — `verification_token` goes in `secrets_root/tgw.env`, not a new
   file.
5. **Register in the Developer Portal** — Dave-only action once the endpoint is
   live and tested (this is external account configuration, not something an
   agent can do).

### Track B — clear the orphaned-offer / 25707 chain (mixed: Dave external action + eBay-side wait)

1. Dave sends the already-drafted, now-urgency-framed followup on
   `EBAY-DS-1077` (case `260719-000018`).
2. While waiting on eBay support: no engineering action is possible on our
   side — the object is unreachable by any client call, confirmed by two
   independent live sweeps already on record. Do not re-attempt workarounds;
   this is genuinely support-only per the existing finding.
3. Once eBay clears it: re-verify live, then reconsider re-enabling
   `catalog_rebuild` and root-causing `ebay_legacy_sync` (Track C) — both are
   currently excluded *because* of this chain, not for independent reasons in
   `catalog_rebuild`'s case.

### Track C — root-cause `ebay_legacy_sync` before any restoration (engineering, but gated)

Two separate concerns layered on this one worker:
- The 2026-07-12 20:51 catalog_rebuild-cascade incident (tied to #1077 — see
  Track B).
- The older, still-unexplained #1248 quota-eating 6-minute retrigger loop,
  independent of 25707, blocked on the same webhook Track A builds.

Do not restore this worker on the strength of Track A or Track B alone — #1248
needs its own root-cause investigation (log archaeology on the historical
6-min retrigger pattern) before it's safe to re-enable, even after #1077
clears. Track A landing may remove one blocker (the webhook dependency named
in #1248) without fixing the retrigger loop itself.

### Track D — send the ready-to-go tickets (Dave-only, no engineering dependency)

- `EBAY-DS-1592` (alternative sold-price data) — independent, send any time.
- `EBAY-DS-1593` (keyset status) — check Developer Portal first per the draft's
  own recommendation, then send if still needed.
- `EBAY-DS-1591` (EPS increase) — technically ready, but per eBay's own stated
  prerequisite order, hold until Track A's webhook is live and registered so
  the request isn't immediately bounced on that basis.

### Track E — apply existing tooling more broadly to the existing catalog (Dave, 2026-07-20: "keep doing what we are doing but apply more to the existing items. More data. Better listings.")

Not new tools — more/repeated use of what's already built, aimed at the
existing catalog rather than just new intake. Real legitimate API volume as
a side effect, real listing-quality improvement as the actual goal.

- **`PP-PROMO-001` (dead-stock markdown sale events)** — fully built (`tgw
  promo draft`/`apply`/`start`/`end`, P1-P3 done 2026-06-29) but **never
  configured or run in production**: no `"promo"` key exists yet in
  `/opt/TGW/config/tgw-api-config.json`, `promo.enabled` defaults false. This
  is Dave's "tool to run sales" — it already exists, dormant. First real use
  is a config addition + one supervised `tgw promo draft` → operator review
  → `tgw promo apply` cycle, not new engineering.
- **Alt-text/SEO caption backlog** — same worker already run today on the
  501-item backlog (Track-independent, done). Re-run periodically as items
  drift back into eligibility (new photos, re-identification) rather than a
  one-time sweep.
- **`ai_identify` re-run / re-enrichment** — existing worker, already
  supports forced re-identify (`DONE-1167-ai-identify-force-reidentify-flag.md`).
  Re-running it against older catalog items (not just new intake) is "more
  data" in Dave's words — pulls in whatever lookup/vision improvements have
  landed since the item was first processed. Needs a scoped backlog sweep,
  same shape as the alt-text batch run today, not new code.
- **`tgw bulk` (PP-BULKEDIT-001)** — already exists: bulk-edit one field
  across matched items, dry-run unless `--apply`. Covers "edit the
  description boilerplate... saved change/launch" directly — no new tool
  needed, just a defined selector + field + value, reviewed dry-run first.
  Not yet documented in `TGW-Master-Plan.md` — worth a short section there
  since it's real, shipped, and about to see real use.
- **Inactive/ended listing repair** — the operator UI already has an
  "Inactive" badge + "Relist" button (`http_server.py`) for ended listings.
  What's missing is a *regular* sweep that surfaces the inactive list for
  the operator, rather than relying on someone noticing — this is the one
  small real gap in Track E (a report/dashboard view, not new write logic).

None of Track E requires new eBay-facing engineering. The work is: turn on
`promo.enabled`, run a supervised sale-event cycle, schedule a recurring
re-identify/alt-text sweep over older SKUs, define a first `tgw bulk`
description campaign, and add an inactive-listings summary view. All
operator-gated per existing patterns (Prime Directive 4 — verify live, not
"tests pass").

## Files to change (Track A only — the only track with source changes)

| File | Change |
|------|--------|
| `src/tgw/apis/ebay/webhooks.py` (new) | Challenge-response + signature verification logic, mirrors the ECDSA verification code already sketched in the ISS-005 spec |
| `src/tgw/http_server.py` | New route(s) for the webhook GET/POST, following existing `/api/`-style or a dedicated unauthenticated webhook path (eBay can't send a Bearer token — needs its own auth model, signature IS the auth) |
| `secrets_root/tgw.env` | New `EBAY_WEBHOOK_VERIFICATION_TOKEN` entry |
| `tests/test_ebay_webhooks.py` (new) | Challenge-response test, valid-signature test, invalid-signature-rejected test, replay/staleness check per spec's `publishDate` recommendation |
| `docs/TGW-Plan-Vault/reference/ISS-005-REST-Signature-Verification.md` | Mark implemented once shipped, link the actual module |

## Acceptance criteria

- [ ] Data audit documented: what buyer/order data (if any) TGW retains tied to
      an eBay account, and what the purge action does with it.
- [ ] Challenge-response endpoint returns the correct hash for a live eBay test
      GET (verified via the Developer Portal's "Test" button, not just unit
      tests).
- [ ] A real or eBay-sandbox-simulated deletion notification is received,
      signature-verified, and acknowledged with 204/200 — shown live, not just
      passing tests (Prime Directive 4).
- [ ] `secrets_root/tgw.env` holds the verification token; no new bespoke
      credentials file created.
- [ ] `tgw health` clean after deploy.
- [ ] EBAY-DS-1077 followup sent by Dave (tracked in
      `EXTERNAL-SUPPORT-TICKET-REGISTER.md`).
- [ ] `EBAY-DS-1591` sent only after the webhook is registered live in the
      Developer Portal.
- [ ] `ebay_legacy_sync` restoration explicitly deferred until its own
      root-cause todo is opened and closed — not bundled into this plan's
      "done" state.

## Open questions

- Does TGW currently retain any buyer-identifiable data at all (shipping
  address, buyer username persisted beyond transient order-sync fields)? Needs
  the Track A.1 audit before the purge action can be scoped precisely.
- Where should the webhook endpoint live in `tgw-http`'s routing — a new
  unauthenticated `/webhooks/ebay/account-deletion` path, distinct from both
  the Bearer-token `/api/` style and the session-cookie `/form/` style? Needs
  a decision before Track A.2 starts (signature verification IS the auth
  here, so it can't reuse either existing middleware as-is).
- Should todo #1248 (ebay_legacy_sync quota-eating loop) get its own dedicated
  investigation todo now, or wait until Track A/B land and the field is
  narrower? Recommend opening it now so it doesn't get lost again the way it
  did the first time (dropped from plan tracking once already, per the
  keyset-status draft's own note about a similar pattern).
- Confirm with Dave: does "the brakes are off" extend to authorizing Track A's
  build to start immediately (routed to `tgw-coder` as a packet), or does he
  want to see this plan and sequence it against Tigwa's Growth Check form
  first?
