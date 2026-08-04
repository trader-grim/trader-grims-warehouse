# Runbook: eBay API operational incidents (quota drain, orphaned objects, aspect rejections)

**Status:** canonical, written 2026-07-18 (todo #1380, PP-RUNBOOK-001 — the eBay-ops
half of the runbook-hardening effort; thermal half shipped 2026-07-14, see
[thermal-emergency-response.md](thermal-emergency-response.md)).

**What this covers vs. what already exists:** this repo already has solid,
narrow eBay runbooks — [ebay-token-failure.md](ebay-token-failure.md),
[ebay-stage-publish-rejections.md](ebay-stage-publish-rejections.md),
[sold-sync-gaps.md](sold-sync-gaps.md), [dead-letter-triage.md](dead-letter-triage.md).
This runbook does **not** duplicate them. It covers three real incident
classes this project has actually hit that had no runbook home: eBay API
**quota/rate-limit exhaustion**, **orphaned server-side eBay objects** that
break bulk calls, and the eBay Inventory API's **rejection of empty aspect
values** (the C14 incident class). It also answers gap #15 from
`reports/TIGWA-REPORT-runbook-gaps-20260713.md` (an eBay API
"responsibility map") by pointing at where that already lives rather than
re-deriving it.

## eBay API responsibility map (gap #15)

Full API-by-API detail: `reference/eBay-API-Landscape.md` (rate limits,
scopes, which REST/Trading APIs are in use, and the "TGW Pipeline × API
Map" section mapping each pipeline stage to the API(s) it calls). Do not
re-derive this here — that file is the source of truth and gets updated
independently as APIs are added.

Quick orientation for incident triage:

| API surface | Owns | Workers |
|---|---|---|
| Inventory API (`sell.inventory`) | inventory_item, offer, publish | `ebay_stage`, `ebay_publish`, `ebay_price`, `ebay_price_reducer`, `ebay_sync` (offer mirror) |
| Trading API (SOAP) | legacy listings, `GetOrders` (sold), store categories | `ebay_legacy_sync`, `ebay_sku_migrate` |
| EPS (Picture Services) | photo upload | `ebay_upload` |
| Taxonomy API | category suggestion | `ai_identify`, `ebay_draft` (QA telemetry only — see incident 1 below) |
| Browse API | pricing comps | `ebay_price` |
| Account/Fulfillment/Metadata APIs | policies, store category tree | `ebay_stage` (offer body), config sync |
| Identity API | OAuth token refresh | `token_refresh` (see [ebay-token-failure.md](ebay-token-failure.md)) |

## Incident 1 — eBay API quota/rate-limit exhaustion

**Failure mode:** an eBay API family (Inventory Sell API, Taxonomy API,
etc.) returns 429 or the operator sees "api token limit exhausted" —
this is a **rate/quota limit**, unrelated to OAuth token expiry (that's
[ebay-token-failure.md](ebay-token-failure.md)).

**Real incident, 2026-07-02 (session 41), `ebay-quota-drain-fix.md`:**
Dave hit "api token limit exhausted" on the first item opened that
morning. Root-caused to two concurrent drains:

1. **Taxonomy API 429** — `ebay_draft.py:_validate_category_suggestion()`
   fired a live `get_category_suggestions` call on *every* drafted item
   purely for QA telemetry (a `category_agreement` field), duplicating
   `ai_identify`'s category call moments earlier. Fixed: the redundant
   telemetry call was removed — it was non-essential and already fail-soft.
2. **Sell Inventory API bulk-fetch drain** — see Incident 2 below (the
   25707 orphaned offer); the per-SKU fallback it triggers is
   ~2,000+ individual GETs per `ebay_sync` run (every 6h) once the bulk
   path is permanently blocked. This alone is enough to drain the day's
   Sell API quota by itself.

**A prior session (39) had already predicted both failure modes in an
audit and left them unfixed pending Dave's go-ahead** — the lesson: if an
audit flags a live-looking quota risk, that's not a "maybe someday," it
recurs. This is at least the third eBay API exhaustion recorded.

**A separate root cause worth remembering when diagnosing "why did this
fire at an unexpected time":** session 39 silently substituted a stated
cadence ("crawl it once daily, at day's end, right before the quota
resets") with "every 6h `ebay_sync` cycle" without flagging the deviation
— that undocumented substitution is what caused the drain to fire at
4:50am, before Dave's day started, instead of once and safely. See
Prime Directive 3 / `feedback-implement-as-specified` — this is the
concrete incident that rule exists to prevent.

### Diagnosis

```bash
# 1. Which API family is 429'ing? Check recent worker logs for the errorId/status
journalctl -u 'tgw-worker@ebay_*' --since "-6 hours" | grep -i '429\|quota\|limit exhausted'

# 2. Is ebay_sync stuck in the expensive per-SKU fallback (Incident 2)?
journalctl -u tgw-worker@ebay_sync.service --since "-24 hours" | grep -i 'fallback\|25707\|checked .* SKUs'

# 3. Is a non-essential call duplicating work already done elsewhere this pipeline run?
#    (the 2026-07-02 root cause — a QA/telemetry call re-doing a category lookup
#    ai_identify already made) — grep for extra API calls per item in the worker
#    you suspect, not just the one that's currently 429'ing.
grep -rn "get_category_suggestions\|_validate_category_suggestion" src/tgw/workers/ebay_draft.py
```

### Recovery

```bash
# There is no "clear the quota" action — eBay quotas reset on their own schedule
# (daily, per API family). Recovery is stopping the drain, not un-draining it:

# 1. If ebay_sync's per-SKU fallback is the drain source, confirm the circuit
#    breaker is active (caps the ~2,000-call fallback to once/24h once the 25707
#    block is confirmed persistent — see Incident 2, "Session-41 circuit breaker"
#    in src/tgw/workers/ebay_sync.py):
grep -n "circuit_breaker\|persistent" src/tgw/workers/ebay_sync.py

# 2. If a duplicate/non-essential call is found, disable or dedupe it — same
#    pattern as the 2026-07-02 fix (cut the redundant Taxonomy telemetry call).
#    This is a code change — flag it, don't fix silently, same as any spec change.

# 3. Wait out the reset window for the affected API family (varies by API —
#    check reference/eBay-API-Landscape.md's Rate Limits section for the
#    specific family hit).
```

### Verification

```bash
# Quota-affected queues draining again (no repeated 429s in fresh logs)
journalctl -u 'tgw-worker@ebay_*' --since "-30 minutes" | grep -i '429\|quota'

# ebay_sync's per-SKU fallback firing at the throttled cadence, not every 6h
journalctl -u tgw-worker@ebay_sync.service --since "-48 hours" | grep -c 'fallback_persistent'
# expect at most 1 per 24h window once the breaker is engaged

sudo -u tgw tgw health
```

## Incident 2 — 25707 orphaned offer blocking bulk fetch (todo #1077, ongoing)

**Failure mode:** `ebay_sync.py`'s `fetch_all_offers()` — the bulk GET
for all Inventory API offers — fails globally with eBay error **25707**
("SKU value violates the syntax rule," their own alphanumeric/50-char
limit). This is not a transient error; it is caused by **one specific
orphaned offer, permanently stuck on eBay's side**, that TGW cannot
reach or delete through any API path.

**Root cause (confirmed live, session 38, re-confirmed exhaustively in
todo #1077):** a legacy Trading-API-era import swapped title↔SKU for one
item (`tgw201607172015419`) at some point, leaving an eBay-side offer
whose SKU is a 57-character book title with spaces — eBay's own
25707 validation would reject this SKU today, but the orphaned offer
predates that validation and cannot be reached to fix:

- `fetch_all_offers` (bulk) → always 25707.
- `getOffers` with the exact SKU (URL-encoded, `%20`, `+`, double-encoded)
  → still 25707.
- `getOffers` with no SKU param (enumerate-all/location mode) → does not
  exist as an option.
- Full 19,486-offer enumeration → offer has no backing inventory item, no
  `offerId` ever returned to TGW.
- No legacy Item# anywhere in local records for it.
- Seller Hub drafts are confirmed a **separate, pre-Inventory-API system**
  — a known-good `offerId` tested in the Seller Hub URL is not
  addressable there, so no UI-side wipe is possible either.
- **eBay Developer Support is the only remaining path** (todo #1077) —
  all API avenues are exhausted. As of 2026-07-16, still waiting;
  status-only, no TGW-side action available. (Side note logged on the
  ticket, not actionable here: the support rep who initially hung up on
  Dave mid-call has since been promoted into eBay's business-division
  decision leadership — a bad sign for ticket velocity, not something
  TGW can influence.)

**Current mitigation, live and working (fixed session 38, hardened session
41):**

1. `fetch_all_offers()` catches the 400/25707 and falls back to
   `_fetch_offers_by_local_skus()` — iterates every local item with an
   `offer_id` individually. Slower (~2,000+ GETs per run) but correct.
2. **Session-41 circuit breaker** (`src/tgw/workers/ebay_sync.py`): once
   the 25707 block is confirmed persistent across consecutive runs, the
   expensive per-SKU fallback path is capped to run at most once per 24h
   instead of every 6h `ebay_sync` cycle — this is what stops the
   fallback itself from becoming a second quota drain (see Incident 1).

### Symptoms

- `ebay_sync` logs `fetch_all_offers: eBay error 25707` on (nearly)
  every run.
- `journalctl -u tgw-worker@ebay_sync.service` shows `"checked N SKUs..."`
  with N in the thousands — the fallback is active.
- `tgw health`'s eBay quota/rate indicators look unusually consumed for
  the amount of real work done.

### Diagnosis

```bash
# 1. Confirm 25707 is still firing on the bulk path (expected — this is chronic,
#    not new, until #1077's support ticket resolves)
journalctl -u tgw-worker@ebay_sync.service --since "-24 hours" | grep -i '25707\|fallback'

# 2. Confirm the circuit breaker is engaged (fallback capped to ~once/24h, not
#    every 6h cycle)
journalctl -u tgw-worker@ebay_sync.service --since "-48 hours" | grep -c 'checked .* SKUs'
# expect ~1-2 full fallback passes per 24h, not one per 6h cycle (4/day)

# 3. Status of todo #1077 (external, eBay Dev Support)
sudo -u tgw tgw todo brief 1077
```

### Recovery

There is no TGW-side fix — this is an external eBay-side orphaned object.
**Do not** attempt a new deletion/workaround path without checking
todo #1077's history first (invariant C11 / `feedback-check-history-before-
building`: this exact problem has already been exhaustively tried every
addressable way). If the circuit breaker itself regresses (fallback firing
every 6h again instead of once/24h), that's a code regression — file it,
don't work around it live.

### Verification

```bash
# ebay_sync completing (with the fallback active) rather than dead-lettering
psql -U tgw state_machine -c "
  SELECT state, count(*) FROM queue_jobs WHERE queue_name='ebay_sync' GROUP BY 1;"

sudo -u tgw tgw health
```

## Incident 3 — eBay Inventory API rejects an empty/cleared aspect value

**Failure mode:** the Inventory API's `PUT /offer` rejects an aspect
(`item_specifics`) whose value has been cleared to empty by an operator,
with a garbled generic error dumping the entire aspects dict rather than
naming the offending key. **This is a real production incident, not a
hypothetical** — full narrative in `reference/invariants.md` C14.

**Real incident, 2026-07-16:** an operator (Dave) repeatedly tried to
clear the `Material` field on a live listing (`tgw202605040949058`,
wrongly listed as "Sterling Silver and Gold") through the item-detail
aspects form. Each attempt reported "✓ Saved" but the value never
changed anywhere — no error, no visible failure. The wrong material claim
stayed live and uncorrectable through the UI until root-caused same day.
**The listing had to be manually ended on eBay as the only available
remedy** — there was no in-product way to fix it.

Two distinct, stacked bugs, both eBay-API-facing:

1. The save payload builder only included a field `if(v)` (non-empty) —
   a cleared field's key was dropped from the outgoing payload entirely,
   so eBay never even saw an attempted change (fixed, todo #1461).
2. Once that was fixed and an empty value actually reached eBay, **the
   Inventory API rejected it outright** — `_build_offer_bodies()` had no
   rule for translating "operator cleared this" into eBay's own
   convention (omit the aspect key entirely to clear it, never send an
   explicit blank string) (fixed, todo #1462).

**Two further live instances of the same eBay-API-facing bug class were
found 2026-07-18 while building the fleet-wide regression suite for this
invariant (todo #1468) — both still open:**

- **Todo #1523** — `tgw/revision.py`'s live-push body-builder
  (`_place_delta_in_bodies`, behind `POST /api/items/{sku}/revision/apply`)
  never received the #1462 fix. A revision-apply delta clearing an aspect
  sends `{"Brand": [""]}` straight to eBay instead of omitting `Brand` —
  the exact same rejection this incident is about, on a different push
  path. Regression test exists and is `xfail`:
  `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value`
  in `tests/test_revision.py`.
- **Todo #1522** — a different C14 mechanism (padlock auto-sync silently
  reverting an unlocked cleared field) — not an eBay API rejection per se,
  see `reference/invariants.md` C14 for the full detail; noted here only
  because it's part of the same open invariant.

### Symptoms

- Operator clears an aspect/field value in the item-detail aspects form,
  gets "✓ Saved," but the live eBay listing (or the local `draft_listing`
  mirror after a push) still shows the old value.
- A push (`ebay_stage`/`ebay_publish`/revision-apply) succeeds with `{ok:
  true}` but a diff against what was actually sent shows a blank-string
  aspect value rather than the key being omitted.
- eBay's error response, when it does surface, is a generic/garbled
  message dumping the whole aspects dict rather than naming one field —
  do not assume "no specific error" means "nothing is wrong" for this
  class of push.

### Diagnosis

```bash
# 1. Confirm the outgoing payload — does the cleared key appear with an empty
#    value, or is it correctly omitted?
sudo -u tgw tgw get <SKU>   # check draft_listing.item_specifics for the field

# 2. Which push path was used? Aspects form / Update Listing → ebay_stage /
#    ebay_publish (fixed, #1461/#1462). Revision-apply UI → tgw/revision.py
#    _place_delta_in_bodies (NOT fixed, #1523 — check if this is the path hit).

# 3. Run the relevant regression test to confirm current (non-)coverage:
PYTHONPATH=/opt/TGW/var/worktrees/1380-ebay-ops-runbook/src:$PYTHONPATH \
  pytest -q tests/test_revision.py -k c14_aspects_delta
PYTHONPATH=/opt/TGW/var/worktrees/1380-ebay-ops-runbook/src:$PYTHONPATH \
  pytest -q tests/test_http_server.py -k c14
```

### Recovery

- **Aspects form / Update Listing path**: fixed (#1461/#1462) — a cleared
  aspect now correctly omits the key. If this regresses, treat as a C14
  invariant violation, file immediately, do not silently patch around it.
- **Revision-apply path (#1523, open)**: not yet fixed. Until fixed, do
  not use revision-apply to clear an aspect value — use the aspects form
  path instead (which is fixed), or clear it manually through the fence
  and re-stage (`tgw enqueue-sku ebay_stage <SKU>`, which rebuilds the
  full offer body correctly).
- **A listing already live with a wrong, uncorrectable-through-normal-
  means value** (the actual 2026-07-16 scenario): end the listing in
  Seller Hub (see [ebay-stage-publish-rejections.md](ebay-stage-publish-rejections.md)'s
  Rollback section — "Accidentally published" applies here too, same
  end-and-remirror pattern), fix the value locally, then re-list/re-stage
  fresh once the push path is confirmed fixed.

### Verification

```bash
# Round-trip proof a cleared value actually persists (the C14 detector standard):
# set → save → clear → save → re-read, confirm the re-read shows cleared, not
# reverted. Existing green coverage for FIXED paths:
PYTHONPATH=/opt/TGW/var/worktrees/1380-ebay-ops-runbook/src:$PYTHONPATH \
  pytest -q tests/test_http_server.py -k c14
# Any push path not covered by an explicit test_c14_* case or the documented
# exclusion list in test_http_server.py's C14 section header comment is an
# UNVERIFIED path — treat operator complaints about it with the same urgency
# as the original incident, not as routine.
```

## What this runbook does not cover

- OAuth token expiry/re-consent → [ebay-token-failure.md](ebay-token-failure.md).
- Stage/publish rejection error codes (25021, 25002, duplicate listings,
  stripped-field regressions) → [ebay-stage-publish-rejections.md](ebay-stage-publish-rejections.md).
- Sold-order sync lag/mismatch, picklist recovery → [sold-sync-gaps.md](sold-sync-gaps.md).
  This partially answers gap #14 from the 17-gap report (authoritative
  source, sync latency, worker health checks, dedup/state files); it does
  **not** yet cover completed-order pagination/time-window edge cases or
  cancellation/refund/combined-order handling in explicit detail — those
  remain open sub-items, not yet individually filed (see manifest).
- Generic dead-letter triage across all queues → [dead-letter-triage.md](dead-letter-triage.md).
- Full API-by-API scope/rate-limit/status detail → `reference/eBay-API-
  Landscape.md`.
- Thermal incidents → [thermal-emergency-response.md](thermal-emergency-response.md)
  (unrelated failure domain, kept separate by design).
