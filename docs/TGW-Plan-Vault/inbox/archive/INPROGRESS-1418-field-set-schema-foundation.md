# In progress: todo #1418 field-set schema foundation

Working on branch `todo/1418-field-set-schema-foundation` in worktree
`/opt/TGW/var/worktrees/1418-field-set-schema-foundation`. Built the
envelope+provenance-array schema for `item_attributes` (Set A) and
`draft_listing.item_specifics` (Set B), two new sanctioned accessor
modules (`tgw.inventory_record`, `tgw.ebay.draft_specifics`), updated all
enumerated read/write sites to route through them (back-compat with the
pre-migration bare-dict shape preserved), a dry-run-only migration script,
new invariant C12 + schema doc + CLAUDE.md bullet, and a static commit-time
C12 detector test. Full offline suite green (2278 passed). Live dry-run
run against the real 55k-item catalog (read-only, zero writes) confirmed
round-trip preservation. Result manifest and commit next.
