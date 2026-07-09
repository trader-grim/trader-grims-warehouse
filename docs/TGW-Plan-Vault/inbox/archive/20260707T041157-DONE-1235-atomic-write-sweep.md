Todo #1235 (audit#1143 merged #1162+#1163+#1164+#1177+#1208+#1212) — DONE.

All 6 sites fixed, one shared mechanism per data type (no bespoke designs):

1. itemdata_scrub.py:121 — raw file_path.write_text() → items.atomic_write_json(
   ..., archive_root=archive_root). Documented as a new PP-FENCE-001 gap (like
   multi_intake.py) since scrub_itemdata's recursive/pattern-based key removal
   has no delete-semantics equivalent in the fence's patch_item() — added to
   tests/test_invariants_items_fence.py's _FENCE_GAPS with the same style of
   in-source comment multi_intake.py already carries.
2. multi_intake.py:170 — existing_json overwrite (Item number strip) now
   passes archive_root=self.config.get('archive_root') to the atomic_write_json
   call it already used (closes half of its own documented fence gap).
3. pm_intake.py:616 AND :660 (both master_plan_path.write_text call sites —
   same root cause, same file, todo only named one) — new items.atomic_write_text()
   helper (tmp+rename+archive-before-overwrite, mirrors atomic_write_json for
   non-JSON docs) added to items.py and used at both sites.
4. get_access_token.py + refresh_access_token.py save_token_state() — both
   now do tmp+rename before the 0600 chmod, instead of a direct write_text
   that could leave a truncated ebay-token.json on crash mid-write. No
   archive_root (secrets are out of ItemArchive scope) — atomicity only.
5. data_scrub_magento.py --execute mode — rewritten to call
   items.strip_fields(cfg, sku, fields, check_only=not execute) instead of a
   raw json.dump() straight to the target path; this replaced the manual
   read/dry-run logic too since strip_fields already has a check_only mode
   that gives identical dry-run preview output. Full fence + archive-before
   coverage, matching the sibling data_scrub_legacy_ebay_fields.py script.
6. photo_repair_iss013.py:270 (alt_path.rename) — added a copy2-to-history
   step before the rename, matching alt_text.py's _history_sku_dir
   convention (HISTORY_ROOT = ITEMDATA_ROOT.parent/history/ItemData, same
   layout, copy-only-if-not-already-archived).

Note: #1209 (recompile_category_backfill.py / data_scrub_legacy_ebay_fields.py
ordering) was correctly left alone per the brief — that's a sequencing bug,
not an atomicity bug.

Evidence:
- New tests: test_items_atomic_write_text.py (3), test_token_state_atomic_write.py
  (2), test_data_scrub_magento.py (3) — 8 new tests, all passing.
- Full offline suite: 1861 passed, 10 pre-existing failures unrelated to this
  diff (same google_direct/openrouter + pricing-invariant failures seen before
  this session started).
- tests/test_invariants_items_fence.py's fence-gap grep audit passes with the
  itemdata_scrub.py exception documented (flagging this deviation per Prime
  Directive 3 — a fence redesign to add delete-semantics was out of scope for
  an atomicity fix).

Deviation flagged: itemdata_scrub.py's write stays outside the fence
(documented gap, same class as multi_intake.py) rather than extending
patch_item() with delete semantics — that's a larger PP-FENCE-001 scope than
this todo covered.
