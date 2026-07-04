# IN PROGRESS — #1066 PP-SEARCH-001 Phase 0 recoll index

Building the universal recoll index per todo #1066 and the design
principle in memory `project-universal-index.md` (recovery/audit tool,
not just search convenience — a past 3-hour investigation for 49 missing
item JSONs would have been one recoll query if this had existed).

Config written to `/opt/TGW/.recoll/recoll.conf` (tgw's $HOME). Scope:
`ItemArchive`, `ItemCatalog` (thumbnails excluded — binary, no text),
`data/history` (symlink to MasterArchive — offline drive, indexes
whenever it's plugged in), and the plan vault (`docs/TGW-Plan-Vault`).
Live ItemData/ deliberately excluded — that's a larger, photo-heavy
Phase 1 per the todo's own scope line.

Running initial `recollindex` now (background, thermal NORMAL at start).
Will report index size / doc count / query test when done.

**DONE.** Index built: 441,374 docs, ~31 min, 4.6 GB index at
`/opt/TGW/.recoll/xapiandb`. Live-verified with real queries:
- "invariants E5" → 12 hits including `invariants.md`, `TGW-Data-Charter.md`
- "ebay_draft 402" → 13 hits including today's incident breadcrumbs
- Confirmed zip transparency: `ItemArchive/*.zip` contents show up as
  individual indexed JSON entries (the exact "49 missing item JSONs"
  recovery scenario from the design principle memo now works with one
  query instead of a 3-hour investigation).
- `ItemCatalog` (91,675 docs incl. the 235 MB historical-master-catalog.json)
  and the plan vault both confirmed indexed.

No cron/systemd timer set up yet for re-indexing — this was a one-shot
build. Follow-up: add a nightly `recollindex` timer (cheap incremental
re-index after the first full pass) and a `tgw docs search`/`tgw search`
CLI wrapper per PP-DOCLIB-001 (#1044)/PP-SEARCH-001 design. `history/`
symlink target (MasterArchive drive) wasn't mounted during this run —
will pick up automatically next index pass whenever that drive is plugged
in, no config change needed.
