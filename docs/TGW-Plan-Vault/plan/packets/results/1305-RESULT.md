# Result: 1305 itemdata_scrub.py fence path/read fix
Status: done
Todo: #1305   PP: PP-COHESION-001
Files touched:
- src/tgw/workers/itemdata_scrub.py — `derive_item_path()` now delegates to
  `config.sku_json()` (the canonical shared path-building helper) instead
  of hand-joining `Path(itemdata_root) / sku / f"{sku}.json"`; the locally
  duplicated `_is_safe_sku()` regex check was removed in favor of catching
  the `ValueError` `config._safe_segment()` already raises (via
  `sku_json()`) for an unsafe segment; `process_queue_job()`'s raw
  `json.loads(file_path.read_text())` read was replaced with
  `resolver.load_item_doc()` (sku-injection idiom used elsewhere in the
  fence), with a `resolver.find_current_sku()` alias-fallback added for a
  job referencing a renamed item's old SKU before concluding "not found"
  (mirrors #1312/#1313-#1316's fix in the same cohesion batch).
- tests/test_audit1143_workers_cohesion.py — two new tests:
  `test_itemdata_scrub_uses_canonical_sku_json_path_helper` (byte-identical
  path output vs. the old hand-built path for a normal sku) and
  `test_itemdata_scrub_resolves_old_sku_via_sku_old_fallback` (renamed-item
  sku_old alias resolution now succeeds instead of "not found").
- docs/TGW-Plan-Vault/plan/packets/1305-itemdata-scrub-fence-paths.md —
  self-authored packet (none existed for #1305; drafted mirroring sibling
  #1312/#1313 fence-bypass packets from the same PP-COHESION-001 batch).

Live evidence:
- `PYTHONPATH=<worktree>/src LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH pytest -q tests/test_audit1143_workers_cohesion.py -k itemdata_scrub -v`
  → `6 passed` (4 pre-existing SKU/root-validation tests unchanged +
  2 new tests), confirmed `tgw.workers.itemdata_scrub.__file__` resolves
  under the worktree path before running.
- Full offline suite: `PYTHONPATH=<worktree>/src LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH pytest -q`
  → `2048 passed, 1 skipped, 1 warning in 125.48s` — zero regressions.
- Thermal status checked before and after the full-suite run:
  `NORMAL|58|...` → `NORMAL|61|...`, no alarm.

Deviations from spec: none from the self-authored packet. One scope
decision flagged explicitly per the packet's own reasoning: the
recursive/pattern-based key-deletion write (`scrub_value`/
`scrub_itemdata`, and the `atomic_write_json` call itself) was
deliberately left untouched — `items.strip_fields()` only removes a flat
list of top-level field names in one call, it does not recurse into
nested dicts/lists, and the production denylist config
(`/opt/TGW/config/queue-workers/itemdata_scrub_denylist.json`) has
`"recursive": true` live-configured (a real, currently-used capability,
not dead code to simplify away). Building fence-API support for
recursive/pattern-based deletion would be a genuine fence-redesign, not a
mechanical cohesion-batch fix — this matches the reasoning already
in-file (PP-FENCE-001 gap comment, audit#1143 #1235) and the same
reasoning already applied to defer the queue-model migration as todo
#1261. Also note: this worker has no installed systemd unit and is not
currently scheduled (re-verified live during this task, matching the
2026-07-10 finding in TGW-Master-Plan.md) — this fix has zero production
traffic exposure today.

Out-of-scope findings filed: none — no adjacent issues found while
confined to the packet's declared scope (path construction + read
mechanics in `src/tgw/workers/itemdata_scrub.py` only).
