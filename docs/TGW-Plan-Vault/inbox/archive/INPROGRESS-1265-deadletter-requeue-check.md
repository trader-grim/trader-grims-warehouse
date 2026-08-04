# INPROGRESS #1265 — ebay_draft 402/429 dead-letter requeue (PP-COHESION-001)

Dave authorized bulk requeue ("1265 go ahead use api"). Before running
`scripts/requeue_deadletter.py --apply` in the 1402 worktree, ran the
mandatory pre-flight live verification (contract step 3). Result: the
2658 402 + 12 429 dead-letter counts are still accurate (2670 total,
matches todo), BUT a deeper check found every one of them already has
`draft_listing` populated in ItemData (2413 unique SKUs, 0 missing) —
`retried_from_job` lineage in `queue_jobs` shows the 2658 402-bucket rows
were already used as requeue sources once before (9187 succeeded child
jobs tagged `bulk_requeue_reason=openrouter_402_2026-07-02_resolved`),
almost certainly residue of the original 6,607-job resubmission-storm
incident (todo #1250) itself over-firing and mostly succeeding despite
the duplication problem. The 12 taxonomy-429 rows were never in that
lineage but their SKUs also already have drafts (resolved via normal
pipeline flow). Conclusion: requeuing again would spend real OpenRouter/
Taxonomy API money reprocessing items that don't need it. Stopped before
any `--apply` run. Writing up as blocked/no-action-needed in the result
manifest, not proceeding to batches.
