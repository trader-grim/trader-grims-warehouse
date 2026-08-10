# PP-WORKFLOW-001 listing topology inventory

Status: source inventory for the six core `EBAY_LISTABLE` treatments at
`acc22adc1bb94cf1ef0b59599be96290ced1651b`.  This is not migration or live
acceptance evidence.

## Current authority paths

The current system has two competing dispatch authorities:

1. operator/API surfaces enqueue concrete queue names directly in
   `http_server.py`, `api.py`, `mcp_server.py`, and `bundle_intake.py`; and
2. `workflow.item_pipeline` can derive a treatment from fingerprints and map
   it to those same queues through `workflow.scheduler`.

The first remains the production authority.  The second is not wired to
durable receipt-triggered re-evaluation in production.

## Core listing treatments

| Treatment / queue | Current prerequisites and reads | Writes / effect | Success evidence | Current successor or wait behavior | PP disposition |
|---|---|---|---|---|---|
| `ai-identify` / `ai_identify` | item exists, local photos; reads model/taxonomy configuration | local model/provider observation; writes identity, title, category, condition and history through the item fence | structured `ai-identify` receipt | hard-coded listing successors were removed; caller/scheduler must re-evaluate | partially migrated local treatment |
| `ebay-draft` / `ebay_draft` | title/identity; category/aspect lookup; local photos | provider reads plus model calls; writes `draft_listing`, aspects and category fields | structured `ebay-draft` receipt | does not enqueue the ordinary next listing stage; live-listing changes remain operator-gated | partially migrated local treatment, though provider reads and offline fallback need explicit evidence classes |
| `ebay-price` / `ebay_price` | valid draft/title/category/condition; protects operator price history | provider Browse/comps reads; writes price/comps/offer fields through the eBay fence | structured `ebay-price` receipt | catalog rebuild side effect remains; no ordinary stage enqueue in current source | partially migrated local treatment; operator-price protection remains a required fingerprint/gate |
| `ebay-upload` / `ebay_upload` | local photos and existing upload map | external EPS uploads; persists partial uploaded-photo progress | structured `ebay-upload` receipt on completion | quota branch directly enqueues another `ebay_upload` job with `not_before`; partial external effects ordinary-retry | not migrated: violates one-shot/no-self-requeue and needs effect intent/ambiguity evidence |
| `ebay-stage` / `ebay_stage` | draft, price, uploaded photos, category, upstream queue checks | external Inventory API upsert/create offer; writes `ebay_offer` and `ebay_submitted` | structured `ebay-stage` receipt | raises retryable waits for upstream queues/photos; calls post-push sync | not migrated: inherited queue-state eligibility, waiting-in-worker, and provider-effect fencing remain |
| `ebay-publish` / `ebay_publish` | staged offer, exact staged price, upstream queue checks, operator action | external publish; writes listing/offer/photo verification; catalog rebuild and post-push sync | structured `ebay-publish` receipt | directly enqueues forced `ebay_stage` on price drift, then raises retryable wait | not migrated: hard-coded predecessor dispatch, waiting-in-worker, and ambiguity/reconciliation remain |

## Goal fingerprints currently implemented

`TGW_EBAY_LISTABLE` requires:

- `item_has_photos`
- `ai_identified`
- `draft_generated`
- `priced`
- `photos_uploaded`
- `staged`
- `valid_condition`
- `valid_category`
- `title_ok`
- `published`

The ItemData snapshot has deterministic local checkers for all ten.  Provider
effect ambiguity is a separate evaluator input and cannot be asserted or
cleared by an `item.json` field.

## Missing or newly named treatments

- `normalize-condition` is now a bounded LOCAL contract for
  `valid_condition=false`, owning only `item.condition`.  It is not yet an
  installed worker and must not be deployed as an executable registry entry
  until its PP-ITEM-MUTATION generation/CAS path exists.
- No bounded remediation contract yet exists for `valid_category=false`.
- No bounded remediation contract yet exists for `title_ok=false`.
- EPS partial-progress reconciliation and ambiguous stage/publish effects are
  evidence/gate gaps, not ordinary retry treatments.

## Exact inherited authority to remove treatment-by-treatment

- Direct operator/API queue submission remains intentionally retained until
  each replacement seam has parity and rollback.
- `ebay_upload.py` directly self-enqueues on quota exhaustion.
- `ebay_stage.py` reads other queue states and asks the generic worker retry
  machinery to wait for prerequisites.
- `ebay_publish.py` reads other queue states, directly enqueues
  `ebay_stage`, and waits through retry machinery.
- `QueueWorker` still translates named transient strings into `retry_wait`;
  this includes upstream-step waits, quota walls, and missing photo URLs.
- Catalog rebuild and post-push sync enqueues are derived-projection work, not
  listing-stage authority, but still require explicit retention evidence.

## Next bounded migration order

1. Implement and fixture-prove `normalize-condition` through the local item
   generation/CAS boundary.
2. Persist attempt receipts and feed authoritative attempts into evaluator
   re-evaluation; prove unchanged failed attempts do not redispatch.
3. Add durable record/evidence change invalidation that invokes one-shot
   evaluation without a polling worker.
4. Migrate `ebay-upload` quota handling from self-requeue to a durable
   scheduler `not_before`/waiting record with partial-effect evidence.
5. Remove stage/publish prerequisite polling and forced predecessor enqueue
   after parity fixtures pass.
6. Hold provider writes until intent reservation, ambiguous-response
   reconciliation, and idempotency acceptance are independently proven.
