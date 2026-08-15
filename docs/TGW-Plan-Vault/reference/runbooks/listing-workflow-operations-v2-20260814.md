# TGW listing workflow operation and recovery — v2 (2026-08-14)

**Owner:** shared; eBay publish authority: operator

**Last verified:** 2026-08-14 on `tgw-prod`

**Applies to:** new inventory and older catalog items which are not yet on eBay,
including PP-WORKFLOW-001 graph-bound attempts

**Last drill:** authenticated item-page actions, AI re-identification, manual pricing
affordance, exact photo convergence, current-graph publish action, receipt-failure
recovery, and live rendered-control verification

## Operator outcome

An item is listable when the current item generation has truthful evidence for:

```text
local photos
→ AI identity/category
→ draft
→ price
→ every local photo uploaded to EPS in exact order
→ staged offer with current content receipt
→ valid condition/category/title
→ operator-authorized publish
```

The web page is the normal operator surface:

```text
http://tgw-prod:7373/form/items/<SKU>
```

Use the browser login session. Do not treat an unauthenticated API response or a
button label alone as proof the downstream effect succeeded.

## What the item-page actions mean

| Action | Meaning | Provider effect? |
|---|---|---|
| **Prepare Listing** | Starts AI identify when needed, otherwise draft generation | No eBay listing publish |
| **AI Identify / AI Reidentify** | Runs image identification using current photos; reidentify is available on already-identified/older items | Model/local state work; no eBay publish |
| **Save Draft** | Persists operator-edited draft fields | Local item mutation |
| **Save & Re-price** | Saves search terms and requests current pricing evidence | May query configured pricing providers; does not publish |
| **Set Price** | Focuses manual price input when automated pricing correctly has no positive evidence | Local operator input |
| **Resync Photos** | Reconciles current local photos with EPS and draft URL order, queueing upload when needed | eBay photo upload may occur |
| **List on eBay** | Saves the draft and requests the complete current `tgw.ebay_listable` goal | May stage/publish; explicit operator effect |
| **Update Item** | Pushes an edited draft to an already-live listing | eBay write |
| **Reset Draft** | Regenerates an unpublished draft from canonical item state | Local mutation |
| **Reset Draft from Live** | Re-pins a live item's local draft to current eBay state | Read/reconciliation plus local mutation |
| **Retry** | Only for an exact legacy retryable dead letter | Never use for governed/ambiguous work |
| **Needs attention** | No safe automatic action exists; inspect evidence/history | No effect |

Buttons are state-derived. Missing or wrong controls are a UI/runtime defect or an
unmet condition, not permission to call a worker directly.

## Recommended sequence for an older catalog item

1. Open the item page and confirm SKU, status, local photo strip, draft, category,
   condition, and price.
2. If identification is absent or stale, click **AI Identify** or **AI Reidentify**.
   Wait for the page's polling cycle to finish and reload. Verify the generated fields;
   do not assume a succeeded queue row filled every draft field.
3. Review/edit draft title, category, condition, description, aspects, quantity, and
   search terms.
4. Click **Save & Re-price** when current pricing evidence is wanted. If pricing
   returns `PRICE_REQUIRES_OPERATOR_INPUT`/no positive price, enter the operator price
   and save it. Repeated automated retries cannot create evidence that does not exist.
5. Inspect **Photos on eBay**. If it is missing photos, stale, or out of order, click
   **Resync Photos** and wait for the page to report exact confirmed/submitted counts
   and reload.
6. Verify the draft still contains the intended ordered photos, price, and content.
7. Click **List on eBay** only when ready to authorize the provider effect. Confirm the
   dialog. The server saves the draft first, then requests the complete current goal;
   it does not blindly enqueue one stale worker job.
8. Observe the Workflow Action Card and job history until the goal is satisfied or an
   explicit gate/failure appears.
9. Verify the live listing/offer identifiers, content, price, and photos. A queued or
   succeeded local action is not by itself proof of a live eBay listing.

## Exact photo convergence

The `photos_uploaded` condition is intentionally stricter than “at least one EPS URL.”
It compares:

- every current local photo from the same ordered-photo source used by `ebay_upload`;
- one unique valid `ebay_photos` local→URL mapping for each photo;
- no stale extra mapping;
- no invalid or duplicate mapping;
- the draft `imageUrls` list equal to those EPS URLs in the same order.

The workflow records a SHA-256 fingerprint of local names, hosted URL hashes, draft
URL hashes, invalid/duplicate count, and extras. `ebay_stage` must wait until this
condition is true.

Read-only diagnosis:

```bash
sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw get <SKU>
```

On the page, compare the local photo strip with **Photos on eBay**. `Resync Photos`
reloads the page after a successful refresh so the displayed strip is not stale.

Do not manually fabricate `ebay_photos`, copy URLs between SKUs, or stage while the
fingerprint says waiting. A photo upload may be a provider effect and can consume
quota; use the operator action and observe its exact result.

## Identification and draft fields

`AI Reidentify` is available even when `ai_identified=true`. It sets a new current
identification request rather than requiring the item to be new. This is the supported
path for older catalog items whose AI fields/draft were never populated correctly.

After completion, inspect the actual draft. A successful AI step may establish
identity/category evidence without supplying an acceptable operator price or every
listing aspect. The Action Card represents these as separate conditions.

For pricing:

- automated `ebay_price` uses positive market evidence;
- a partial `PRICE_REQUIRES_OPERATOR_INPUT` result is truthful, not a crash;
- use **Set Price**/draft price for operator input;
- saving a manual price makes the price condition current for that generation;
- changing draft content may invalidate downstream staged-content evidence.

## What **List on eBay** does now

The current handler:

1. saves the draft;
2. submits action `ebay_publish` through the item action endpoint;
3. routes the request through the workflow authority/evaluator;
4. schedules only the treatments needed by the current item generation;
5. preserves dead-letter history;
6. waits/polls and reloads the page.

The listing pipeline can include `ebay_upload`, `ebay_stage`, and `ebay_publish`, but
their order and eligibility come from current evidence and the goal graph. Do not
manually run those workers in a guessed sequence.

## The repaired receipt-identity failure

The 2026-08-14 incident on `tgw202510161310076` had a historical non-retryable
`ebay_upload` dead letter:

```text
receipt binding rejected: INVALID_RECEIPT_IDENTITY
```

Its structured reason is stored at:

```text
queue_jobs.payload_json.result.evidence.reason_code
```

not in a top-level `result` column. The page now recognizes this exact historical
failure. When a valid current draft exists, it offers **List on eBay** to create a new
current-graph/operator-authorized attempt. It does not requeue or mutate the stale
dead letter.

At live acceptance the action row was:

```text
List on eBay · AI Reidentify · Archive · Delete
```

The installer/verifier did not click List; provider publication remains an operator
decision.

Unrelated non-retryable failures still show **Needs attention**.

## Read-only attempt history

```bash
sudo -u tgw psql state_machine -x -c "
  SELECT job_id, queue_name, state, attempt_count, max_attempts,
         error_code, error_detail,
         payload_json->>'treatment_id' AS treatment,
         payload_json->>'graph_id' AS graph_id,
         payload_json->>'object_generation' AS object_generation,
         payload_json->>'condition_hash' AS condition_hash,
         payload_json->'result' AS result,
         created_at, updated_at, finished_at
  FROM queue_jobs
  WHERE (entity_type='item' AND entity_id='<SKU>')
     OR payload_json->>'sku'='<SKU>'
  ORDER BY created_at DESC;"
```

Also inspect the authenticated Action Card (`GET /api/items/<SKU>/workflow`) and
record current generation, graph, condition hash, unmet conditions, active attempts,
operator gates, and reconciliation gates.

## Retry and recovery decision table

| Evidence | Safe action |
|---|---|
| Legacy dead letter, `retry_allowed=true`, no graph/generation/condition binding | Use its exact Retry control or legacy dead-letter procedure |
| Graph-bound failed/partial result | Correct evidence or operator input, then request the current goal again |
| `INVALID_RECEIPT_IDENTITY` historical upload failure with valid current draft | Use the fresh **List on eBay** action; preserve the old row |
| `PRICE_REQUIRES_OPERATOR_INPUT` | Enter/save a manual price or provide better search evidence |
| Photo sync waiting | Use **Resync Photos**, wait for exact convergence |
| Active matching attempt | Observe; do not duplicate |
| `dispatched`, `ambiguous`, `reconciliation_required` provider effect | Read-only reconciliation; never resend |
| Older failure superseded by later same-queue success | Treat older row as history |
| Unrelated hard non-retryable failure | **Needs attention**; inspect reason/effect/authority |

For provider ambiguity, use `pp-workflow-001-provider-reconciliation.md`. For leases,
timers, generation conflict, or `REPAIR_REQUIRED`, use
`pp-workflow-001-item-recovery.md`.

## Provider-effect checks

Before any resend or new publish attempt when prior provider work may have occurred:

```bash
sudo -u tgw psql state_machine -x -c "
  SELECT effect_id, provider, operation, entity_type, entity_id,
         object_generation, graph_id, treatment_id, condition_hash,
         state, error_detail, created_at, dispatched_at, finished_at
  FROM provider_effects
  WHERE entity_type='item' AND entity_id='<SKU>'
  ORDER BY created_at DESC;"
```

An unresolved `dispatched`/`ambiguous` effect fences later generations. Reconcile with
read-only provider evidence. Never send another POST/PUT/PATCH/DELETE merely because
the page timed out.

## Verification after an operator action

- page polling terminates and reloads;
- current Action Card reflects new evidence/gates;
- exactly one intended attempt/effect exists;
- no stale-generation completion overwrote current ItemData;
- photo fingerprint is exact before stage;
- staged-content receipt matches current content before publish;
- offer/listing ID and state agree with read-only eBay reconciliation;
- journals contain no unexpected new warning/error;
- historical failures remain queryable.

## Escalation bundle

Capture SKU, current ItemData generation/hash, Action Card, structured queue result,
effect/authority/observation IDs and states, photo fingerprint/counts, selected
application generation, relevant worker start times, and bounded journals. Do not
include OAuth tokens, credentials, full private request payloads, or private listing
data unnecessary to diagnose the condition.
