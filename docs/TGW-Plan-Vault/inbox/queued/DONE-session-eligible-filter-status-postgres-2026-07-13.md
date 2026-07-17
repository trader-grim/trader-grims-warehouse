Session 2026-07-13 (resumed after rate-limit). Started by finishing the
in-flight PP-COHESION-001 follow-up batch (#1371/#1372/#1373 — merged,
tested, closed), delegated the emergency Telegram channel (#1346) to
Tigwa, then Dave asked for a "quick fix" to the web UI Eligible filter,
which cascaded into a real incident chain:

- **#1377 DONE**: Eligible filter (`http_server.py` `__eligible__`) was
  silently excluding items with blank `status` — fixed to treat blank as
  active/non-terminal, matching the default "All" view. Test added,
  live-verified (1541→2351 items), deployed.
- **#1376 LOGGED (not fixed)**: root-caused via ItemArchive snapshot diffs
  + a stray `data-scrub-1053-report.json` that `data_scrub_legacy_ebay_fields.py
  --apply` stripped the legacy `#STATUS` key from 20,415 items on
  2026-07-03 22:21 with no promotion-first guard. Dave then corrected the
  read: `status` (lowercase) was always the real canonical field —
  `#STATUS` was his own manual convenience alias, "sometimes not updated."
  Real bug: `items.statusupdate()`/`verifiedupdate()`/`bulk_edit` all
  write to `#STATUS`, never `status`. Dave: "this is a big fix" — logged
  under PP-DATAINTEGRITY-001, explicitly not executed pending his scoping.
- **#1378 DONE**: found + fixed while checking "do known-solds have
  operational status" — the eBay sold-webhook handler has 500'd on every
  real call since 2026-06-04 (two imports of functions that don't exist
  under those names). Fixed, regression test added, deployed. (Zero real
  status/ebay_sale mismatches found otherwise — the one hit was a false
  positive on a multi-qty partial sale.)
- **#1375 LOGGED**: Android/Tasker emergency annunciator proposal (planned
  by Dave+Tigwa) filed into PP-HARDWARE-001, cross-linked to #1346 (same
  producer script — needs coordination, not built by either yet).
- **#1379 LOGGED**: PP-POSTGRES-001 design doc filed — PostgreSQL item
  source-of-truth migration (hybrid: Postgres=truth, filesystem=photos,
  JSON=export artifact, NATS JetStream=event bus not state master, per
  Dave's explicit framing). Triggered by the #1376 incident chain + Dave's
  own Perplexity research + the recurring SSD thermal problem. Nothing
  built — design doc + open-questions list only. Flagged an unresolved
  premise conflict with PP-CATALOG-INCR-001 (assumes JSON stays truth) for
  Dave to reconcile. One small independent first step identified: wire
  the already-built-but-wrong-door `publish_mutation()` (NATS
  ITEMDATA_MUTATIONS stream, PP-AIOPS-001 Phase 1) into the real fence
  (`http_server.py`'s `_apply_patch`/`_apply_ebay_write`) instead of just
  the narrow CLI path.

**Still open, not started:** #1370 (flaky quota-state test isolation —
own breadcrumb, INPROGRESS-1370-llm-google-direct-test-isolation.md,
worktree exists, no code written yet).

**Next step:** Dave is clearing context. Whoever picks this up next should
read PP-POSTGRES-001.md and PP-DATAINTEGRITY-001's #1376 entry before
touching either — both are explicitly "logged, not executed," waiting on
Dave's scoping/planning-pass sign-off, not ready-to-build todos.
