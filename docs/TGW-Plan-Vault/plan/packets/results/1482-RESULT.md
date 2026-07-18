# Result: todo #1482 inventory-manual-leg
Status: done
Todo: #1482   PP: PP-INVENTORY-001

Files touched:
- `src/tgw/physical_inventory.py` (new) — manifest generation
  (`build_manifest`, wraps `resolver.resolve(cfg, location=X)`), checklist
  rendering (`inventory_sweep_checklist`), and operator adjudication write
  path (`inventory_record`: present / missing / misfiled).
- `src/tgw/api.py` — two new CLI subcommands: `inventory-sweep <location>`
  (generate manifest checklist) and `inventory-record <sku> <result>`
  (record one adjudication); both added to `_HELP_GROUPS["Write / Update"]`.
- `tests/test_physical_inventory.py` (new) — 10 tests covering manifest
  scoping, empty-location, checklist output (file + stdout), all three
  adjudication results, misfiled location-tree symlink correctness, unknown
  SKU, and invalid result value.
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1482-inventory-manual-leg.md`
  (breadcrumb, to be cleared by next session-start sweep).

## What was built

Per `pp/PP-INVENTORY-001.md`'s "manual leg" (buildable now, no
PP-VISION-001 dependency): a location-scoped manifest-vs-physical checklist
workflow.

- **Manifest** = every item whose `location` field equals the target
  location (no new schema — direct `resolve(cfg, location=X)` query, as
  the PP doc specifies).
- **Checklist** (`tgw inventory-sweep <location>`) — markdown table,
  Obsidian-friendly, same rendering shape as the existing `tgw ebay-sweep`
  (todo #11) but scoped to one location's full expected contents rather
  than system-wide ambiguous-status items.
- **Adjudication** (`tgw inventory-record <sku> present|missing|misfiled`)
  — the write-back step `ebay-sweep` never had (it only prints which `tgw
  update` command to run by hand):
  - `present` — writes a durable `inventory_sweep` finding
    (`{result, checked_at, location_at_check}`) via the existing
    `_write_field` audit-trail path (queryable later with `tgw
    audit-trail <sku>`), per invariant C11 (finding persisted, not just
    logged).
  - `missing` — same durable finding, `note` optional. Deliberately does
    **not** auto-change `status` — "missing" is a different fact than
    "sold" and conflating them would be a silent substitution (Prime
    Directive 3); the operator resolves separately (e.g. `tgw update
    <sku> status sold`), same as `ebay-sweep`'s existing pattern.
  - `misfiled` — requires `--to-location`; routes through the existing
    `items.locationupdate()` so the location field, symlink tree, and
    audit trail all stay consistent with every other location correction
    in the system (no bespoke write path).

`tgw ebay-sweep` (todo #11) was left unmodified — it still answers a
different question ("what's ambiguous-status system-wide"), not
duplicated or removed; `inventory-sweep`/`inventory-record` are the new
location-manifest-scoped pair the PP doc actually asked for.

## Live evidence

Ran the new command against real ItemData/location data, location `AF102`
(`/opt/TGW/data/ItemCatalog/by-location/AF102`, 6 real SKUs):

```
$ tgw inventory-sweep AF102
# Physical Inventory Checklist — location AF102 — 2026-07-18 20:54 UTC
Manifest size: 6

Walk the location, check each SKU against what is physically
present, then record the result with:
  `tgw inventory-record <SKU> present|missing|misfiled --location AF102`
(misfiled additionally needs `--to-location <NEW_LOC>`).

| Done | SKU | Status | Last check | Title |
|------|-----|--------|------------|-------|
| [ ] | tgw201711080238177 | In Stock | — | Girl Graduation Art Painting 10x12 Inches |
| [ ] | tgw201711080239393 | In Stock | — | Herbs and Vegetables Art Print Poster 10x12 Aspeli |
| [ ] | tgw201711080240356 | unknown | — | Starts On Ice 1994-1995 Ice Skating Program Discov |
| [ ] | tgw201711080241521 | In Stock | — | Flowers and Bee Art Painting 9x10.75 Inches |
| [ ] | tgw201711080243020 | In Stock | — | Happiness Is Homemade Art Painting 9x12 Inches |
| [ ] | tgw201711080244042 | In Stock | — | Happy Duckling Art Painting 8x9.75 Inches |
```

This is a genuine manifest-vs-physical checklist, generated read-only from
live `location_tree_root` + `ItemData` — no production write performed
(the acceptance step calls only for demonstrating checklist generation;
the adjudication/write path's correctness — including the misfiled
reversibility case, apply→confirm→correct-location-set→confirm — is
proven live with `tmp_path`-isolated fixtures in
`tests/test_physical_inventory.py`, not against real inventory, since the
packet does not call for a live production write and the tests already
demonstrate both directions: `test_inventory_record_present_persists_finding`
+ `test_inventory_record_misfiled_corrects_location` show the symlink tree
correctly loses the old-location link and gains the new-location link).

Full test suite, run under the worktree-isolated PYTHONPATH/LD_LIBRARY_PATH
(confirmed `tgw.physical_inventory.__file__` resolved to the worktree, not
the shared checkout):

```
$ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=/opt/TGW/var/worktrees/1482-inventory-manual-leg/src:$PYTHONPATH python3 -m pytest -q
2580 passed, 1 skipped, 1 warning in 194.14s
```

No regressions. `test_shell_grouping.py` (which enforces every registered
subcommand appears in `_HELP_GROUPS`) passes with the two new commands
included.

## Deviations from spec

- **Scope boundary on "manifest/checklist UI"**: the PP doc explicitly
  defers "the actual manifest/checklist UI — web UI vs. Flutter/mobile"
  to `pp/PP-UIUX-001.md`, out of scope for this planning pass. This
  packet therefore built a CLI-only interface (`inventory-sweep` /
  `inventory-record`), matching the existing `ebay-sweep` CLI pattern,
  not a web/Flutter surface. Flagging this explicitly since the packet
  brief's own framing ("likely: a new CLI subcommand or web UI view")
  left it open — CLI was chosen as the buildable-now, no-new-dependency
  option consistent with the PP doc's own scope note. A worktree
  `1483-ui-inventory` was observed to exist (separate todo, presumably
  the UI-surface follow-on) — not touched, not investigated beyond
  confirming it's a distinct in-progress task, per context-budget
  discipline.
- **`missing` does not auto-set `status`**: the PP doc's own manual-leg
  framing doesn't specify what field changes on a missing result. Chose
  to keep `missing` as a pure durable finding (no automatic `status`
  write) rather than silently conflating "not seen on this sweep" with
  "sold" or any other terminal status — flagged here per Prime Directive
  3 rather than left as a silent choice.
- **`re-photograph` adjudication state (from the PP doc's step 5) omitted**
  — that option only makes sense once there's a photo to retake (the
  vision-assisted leg). The manual leg's three outcomes are
  present/missing/misfiled only; noted as an intentional, not silent,
  narrowing to what "by eye" adjudication can actually produce.
- **`tgw ebay-sweep` (todo #11) left as-is, not merged/refactored into the
  new commands** — the PP doc says the manual leg "absorbs" #11
  conceptually (as the workflow's spiritual predecessor / degenerate
  case), not that the existing ambiguous-status tool must be deleted or
  rewritten. Judged safer to add the new location-manifest tool
  alongside it than to risk breaking `ebay-sweep`'s existing callers for
  a PP that explicitly says the two questions ("ambiguous status
  anywhere" vs. "is this location's manifest accurate") are different.
  If Dave wants `ebay-sweep` formally retired/merged, that's a follow-up
  todo, not guessed here.

Out-of-scope findings filed: none — no adjacent broken thing was found
during this work; the `1483-ui-inventory` worktree's existence was noted
above as context, not filed as a new finding (it's presumably already its
own todo, not investigated further per context-budget discipline).
