# In progress: field-set schema fix dispatched through branch-per-task process

Three sequenced packets written and reviewed with Dave today (todos
#1418 → #1416 → #1417, all PP-LISTEDITOR-001) fixing the recurring
Inventory Record (Set A) / eBay Draft (Set B) key-vs-set confusion that
caused today's description-push bug (#1415, fixed) and the
Metal/Department/Type aspect-mismatch report — plus two prior sessions'
symptom-only fixes this week (#1291, #1313/#1316) that touched the same
territory without seeing the underlying pattern.

Packet docs:
- `plan/packets/1418-field-set-schema-foundation.md` — `_set`-tagged
  envelope + provenance-history arrays (extends the proven
  `price_history`/`vision_results` pattern), named accessor modules,
  new invariant, CLAUDE.md settled-architecture entry. Real migration
  risk (55k items) — Acceptance stops at dry-run + sample, full-catalog
  run is a separate explicit go/no-go for Dave.
- `plan/packets/1416-inventory-record-ebay-draft-set-boundary.md` —
  boundary fix (single named translation function; fixes 3 code paths
  that reach across the boundary key-by-key). Depends on #1418.
- `plan/packets/1417-ebay-draft-to-inventory-record-reverse-flow.md` —
  new reverse-flow mechanism (none existed before): default-checked diff
  UI, provenance-tagged, gated on explicit operator submit, no
  auto-promotion. Depends on #1418 + #1416.

Dispatched via the standard branch-per-task process (PP-HERMES-EA-001):
worktree `/opt/TGW/var/worktrees/1418-field-set-schema-foundation`,
branch `todo/1418-field-set-schema-foundation`, tgw-coder agent running.
#1416 and #1417 are NOT yet dispatched — sequencing is strict, each
must land+review before the next starts.

Pre-work snapshot: `/opt/TGW/.snapshots/20260715T0734` (local),
`/home/snapshot/TGW-SNAPSHOT-0/20260715T0734` (received copy) — taken via
`bin/tgw-snapshot` immediately before dispatching #1418's tgw-coder run,
per Dave's explicit instruction. This is the rollback point if the
migration work goes wrong. Verify against this snapshot when #1418
completes, before trusting any live-data claims in its result manifest.

Next steps when resuming this thread:
1. Wait for #1418's tgw-coder run to finish; read its result manifest.
2. Run `/tgw-runner-review 1418` before anything else.
3. Only after #1418 clears review AND Dave has explicitly signed off on
   the dry-run evidence (full-catalog migration is its own separate
   go/no-go, not bundled into "packet done") — dispatch #1416 the same
   way, then #1417 after that.
4. Stitch each in turn per the established process (matches how
   #1108/#1407 were handled earlier today).
