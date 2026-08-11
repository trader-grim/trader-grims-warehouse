# PP-DEADLETTER-001 — pipeline dead-letter root-cause triage — NEW 2026-07-14

**Origin:** surfaced while monitoring #1265's ebay_draft 402 bulk requeue —
that requeue only covers 2658 of 2771 ebay_draft dead-letters (the 402
pattern specifically). The remaining 113 in that queue, plus 234 across 7
other queues, had never been triaged. Dave, 2026-07-14: "those are our
known edge cases, let's get them covered. Let's run it through the
process" — same discipline as PP-COHESION-001: real findings, packets
before dispatch, tgw-coder execution, tgw-runner-review, live verification.

**Live snapshot at triage time (2026-07-14 ~14:15):**

| Queue | Dead-letters | Real bug findings | Transient-only |
|---|---:|---:|---:|
| ebay_draft | 2771 total, 113 not covered by #1265 | 95 non-JSON, 12 taxonomy-429, 5 truncated image, 1 other | — |
| ebay_legacy_sync | 148 | — | 148 (quota/lease/rate-limit/token — all clear now) |
| ebay_stage | 28 | 17 non-leaf category, 2 ImageLinks size, 3 title-length (known #1318-1320 class) | 1 title-trim (by-design, operator action) |
| ebay_sync | 16 | 9 offer-endpoint 400 | 7 (lease/token) |
| ebay_upload | 14 | 10 dimension-limit, 3 XML parse error, 1 KeyError('api_key') | — |
| pm_intake | 14 | 3 PermissionError (worth a look) | 8 stale plan-section refs (worker stopped, moot) |
| ebay_sku_migrate | 11 | — | 11 (lease expired) |
| ebay_publish | 3 | 1 Brand-missing (item-specific) | 2 (waiting on ebay_stage, resolves itself) |

## Execution plan

Two tracks, don't conflate:

1. **Transient-only buckets (quota/lease/token/rate-limit already cleared,
   or waiting on another queue to catch up)** — no code fix needed, just
   verify current state (token valid, quota not currently halted) then
   requeue with the same job_id-derived-dedupe + run-once-marker pattern
   #1265 established (`scripts/requeue_ebay_draft_402_dead_letters.py` is
   the template — a generic version of this pattern, parameterized by
   queue + error-pattern match, is worth building once rather than one
   bespoke script per queue).
2. **Real bug findings** — each gets its own packet + todo, same shape as
   #1367/#1383 today: verify live before writing the packet, scoped fix,
   tests, `tgw-runner-review` before stitch. Filed as individual todos
   below, tagged `PP-DEADLETTER-001`.

## Cross-references

- Truncated image files (5, ebay_draft) may overlap with
  PP-DATAINTEGRITY-001's photo-integrity legs — check before treating as
  a separate finding; don't duplicate that PP's detect/recover/prevent
  design.
- ebay_stage title-length findings (3) may already be covered by the
  #1318-1320 title-length-guard work — verify live whether these predate
  that fix or are a residual gap, per the "verification-only closure"
  pattern (#1273/#1285/#1286 today) before writing a new packet.
- #1265's own script (`scripts/requeue_ebay_draft_402_dead_letters.py`,
  now E9-compliant) is retained as historical evidence for the former
  transient-requeue track; it is not the default forward workflow contract.

## Condition-derived correction — 2026-08-03

PP-WORKFLOW-001 now governs forward pipeline eligibility and mitigation. A
`dead_letter` row records an immutable failed/exhausted attempt; it does not make
the business record terminal or authorize blanket replay. `stuck` and
`dead_letter` are structured, evidence-linked conditions to evaluate.

For each affected record/treatment, the evaluator must determine the reason
class, record generation and condition hash, attempted external-effect state,
evidence/receipts, legal mitigations, and operator/reconciliation needs. A new
attempt is eligible only after changed evidence, a new record generation, an
explicit scheduler-owned `not_before` expiry, or applicable authority. The same
treatment must not repeat against an unchanged generation/condition hash.

Jobs run once and exit. They do not sleep, poll, hold a lease while waiting for
prerequisites, repeatedly requeue themselves, or enter `retry_wait` merely
because nothing changed. Waiting is durable condition data. Possibly committed
provider effects always enter `RECONCILIATION_REQUIRED` and are never ordinary
retry or bulk-requeue candidates.

The original triage tables and receipts remain historical evidence. Their
forward treatments must be reclassified through the PP-WORKFLOW-001 condition
and treatment contract before any queue replay or production action.
