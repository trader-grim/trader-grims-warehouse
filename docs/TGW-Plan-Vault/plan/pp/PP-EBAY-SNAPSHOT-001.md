# PP-EBAY-SNAPSHOT-001 — eBay submission provenance, observed baselines, and drift assurance

**Opened:** 2026-06-16 (session 34)
**Corrected status, 2026-07-31:** the original Phase 1/full-audit-trail claim was false for the current population. A fresh complete provider-active census found 19,350 unique SKUs/listing IDs, but only 297 have `ebay_submitted.inventory_item` and none have a submitted offer snapshot. The remaining 19,053 have unknown submitted provenance.

## Current authority contract

Keep four states distinct:

1. **`ebay_submitted_operations`** — append-only evidence of exact future TGW stage/publish requests and successful operation receipts. Preserve inventory-item and offer/listing request bodies and hashes, operation type, SKU, offer/listing IDs, source revision, actor/job, timestamp, and supersession linkage. Failed external operations must not create a successful submitted record.
2. **Provider-observed baseline** — a timestamped, source-linked observation of current eBay state for listings whose submitted provenance is missing. This is provider-read evidence, never submitted history.
3. **`ebay_live` / fresh provider observations** — what eBay currently exposes after completeness and freshness checks.
4. **`draft_listing`** — editable local intent, which may contain legitimate pending changes and must not be counted automatically as provider drift.

Never copy a provider observation into `ebay_submitted`, never rewrite sparse legacy evidence to look complete, and never infer equality or drift when the required source is absent. Missing provenance is `UNKNOWN`.

## 2026-07-31 evidence and Dave decision

Fresh read-only census:

- current provider-active: 19,350 unique SKUs and listing IDs;
- provider-active but not local `PUBLISHED`: 61;
- local `PUBLISHED` but not provider-active: 12;
- current-active with legacy submitted inventory snapshot: 297 (1.534884%);
- current-active missing submitted inventory provenance: 19,053;
- current-active with submitted offer snapshot: 0.

Dave approved retaining one fresh full provider observation for the 19,053 listings as a distinct append-only **provider-observed baseline**, with source, observation time, object/field coverage, and deterministic hashes. It must not be labeled as submitted history and it authorizes no eBay write or automatic local-status correction.

The approved baseline is a starting point for future drift detection, not a declaration that either current eBay state or current TGW state is correct.

## Implementation state

### Packet A — future submission provenance

A development candidate was produced in the isolated `todo/1716-submission-provenance` worktree. It preserves sparse legacy `ebay_submitted`, adds append-only future operation records, and routes ordering/linkage through the existing serialized fence boundary. Reported focused evidence: 91 relevant tests passed, Ruff/compileall/diff-check passed; the broad suite reached 18% without failures before a 300-second timeout. The candidate is uncommitted and still requires controller rerun, complete diff inspection, independent review, and admission. No provider or production action occurred.

### Packet B — provider-observed migration baseline

Approved in principle by Dave. Before any data write, implementation must provide:

- complete fresh Inventory and offer/listing reads for the frozen provider-active population;
- explicit per-object and per-field coverage;
- append-only source/time/hash provenance distinct from submitted evidence;
- collision handling, restart safety, bounded rate use, and a dry-run receipt;
- rollback that removes only the new derived baseline records;
- no eBay write and no local lifecycle/status correction.

### Packet C — lifecycle classification

Classify the 61/12 disagreement sets using fresh provider lifecycle evidence and sold/order evidence. Do not map absence from the active set directly to `ENDED`; preserve sold, ended, unpublished, local-only, stale, and unknown separately.

### Packet D — lossy-relist guard

Before relist or re-push, require explicit source lineage for every material field that would be sent. Show missing/contradictory fields and fail closed for automatic lossy recovery while preserving an operator review path. Existing sparse records and current provider observations are recovery evidence, not permission to overwrite.

## Complete eBay state-drift detector — todo #1718

Build a read-only detector over a fresh, complete provider-active census. Compare provider state against the latest accepted submitted operation when available, otherwise the approved provider-observed baseline. Report these populations separately:

- duplicate active listing identities (linked implementation task #1713);
- provider/local lifecycle divergence;
- material submitted-or-baseline versus live field drift;
- provider canonicalization/non-material structure differences;
- pending draft versus live edits;
- sold/order evidence and unresolved sale state;
- missing or incomplete provenance (`UNKNOWN`).

The detector must emit a durable timestamped/hash receipt and an operator review queue. It may block/review stage, publish, relist, or re-push when identity or lineage is unsafe. It must never automatically end a listing, push a local snapshot, rewrite local status, or treat ordinary cross-API visibility of one listing as a duplicate.

The existing `catalog-verify` `submitted_live_drift` rule is only a partial predecessor: it compares sparse legacy inventory snapshots to `ebay_live` for a limited field set and does not establish complete population coverage, offer/listing parity, duplicate identity, sold state, or an operator reconciliation surface. Todo #1718 must extend/reconcile this mechanism rather than quietly creating a conflicting second truth.

## Historical phase disposition

- **Original Phase 1 claim (“every payload saved”)** — disproven for the current population; retained as historical intent, superseded by append-only Packet A.
- **Post-publish and periodic photo verification** — remains useful as one field-family check, but is not whole-listing drift assurance.
- **`tgw ebay re-push`** — existing capability; broad/automatic use is held when lineage is incomplete. Re-push is not a safe substitute for reconciliation.
- **Original backfill into `ebay_submitted`** — superseded. Current provider reads belong only in the separately named provider-observed baseline.

## Linked work

- #1716 — recover data, retain observed baseline, and block lossy relist.
- #1718 — complete read-only eBay state-drift detector and review queue.
- #1713 — complete active-listing duplicate detector.
- #1681 / PP-SOLD-001 — truthful sold/order reconciliation.
- #1719 / PP-INVENTORY-001 — manual physical-inventory reconciliation surface consuming these source-labelled states.
