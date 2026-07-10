# INPROGRESS — todo #1203 (part of #1143 cohesion audit): scripts/ subsystem

Continuing the staged full-codebase cohesion+correctness audit. `workers/`,
`apis/ebay/`, `http_server.py`, `queue/state-machine` slices DONE. Security batch
flagged so far: #1174 (webhook forgery), #1184-#1188 (XSS/open-redirect/markdown),
#1200/#1201 (dead-letter zombie jobs, backoff-tuning gap).

This session: `scripts/`, 17 ad-hoc/one-off Python scripts, 3,533 lines total (bulk
backfills, audits, photo repair, evals — not the always-running worker pipeline, but
several touch live eBay/ItemData). Four file-groups:

- Group A (mutation/backfill): ebay_backfill_offers.py, ebay_photo_push.py,
  fleet_baseline_sweep.py, requeue_ebay_draft_402_dead_letters.py, ebay_snapshot_all.py
- Group B (data scrub/normalize): data_scrub_magento.py,
  data_scrub_legacy_ebay_fields.py, ebay_normalize.py, recompile_category_backfill.py
- Group C (photo): photo_repair_iss013.py, photosync_canary_probe.py,
  catpick_backfill_candidates.py
- Group D (audit/eval/test): ebay_audit.py, ebay_motors_census.py,
  eval_repricer_gemini_grounding.py, vision_test.py, alt_text_model_test.py

Same design: each group's agent reads CLAUDE.md + invariants.md first, reports
correctness/invariant/cohesion findings (idempotency of backfills, archive-before
mutation, live-data safety), then 3-vote adversarial refute per finding (2-of-3
survival bar).

## Remaining subsystems queue after this

nix flake.

No result yet — audit in flight via Workflow tool.
