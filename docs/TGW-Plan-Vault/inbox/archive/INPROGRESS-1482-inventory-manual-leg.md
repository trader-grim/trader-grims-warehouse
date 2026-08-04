# In progress: todo #1482 — PP-INVENTORY-001 manual leg

Building the manual leg of PP-INVENTORY-001 (`pp/PP-INVENTORY-001.md`):
location-manifest-vs-physical checklist workflow, absorbing existing `tgw
ebay-sweep` (todo #11, already built — ambiguous-status checklist, not
location-manifest based) as the starting shape, but adding the actual
manifest-per-location query + operator adjudication write path the PP doc
calls for (present / confirmed-missing / confirmed-misfiled+location-fix).
No PP-VISION-001 dependency — auto-check-off (steps 3/4 of the PP doc) is
explicitly deferred to the vision-assisted leg; this leg is 100% by-eye.

UI surface is out of scope per the PP doc (deferred to PP-UIUX-001) — this
leg builds a CLI subcommand pair (`inventory-sweep` generate + `inventory-
record` adjudicate), same shape as the existing `ebay-sweep`/`tgw update`
CLI pattern.

Plan:
- `src/tgw/physical_inventory.py` — manifest generation (reuses
  `resolver.resolve(cfg, location=X)`) + record-writing (present/missing/
  misfiled, misfiled reuses `items.locationupdate`).
- Wire two subcommands into `src/tgw/api.py`: `inventory-sweep <location>`,
  `inventory-record <SKU> <result>`.
- Tests in `tests/test_physical_inventory.py`.
- Acceptance: run `inventory-sweep` against one real location from live
  ItemData, show real manifest output.

Worktree: `/opt/TGW/var/worktrees/1482-inventory-manual-leg` on branch
`todo/1482-inventory-manual-leg`.
