# TIGWA DESIGN REVIEW — PP-POSTGRES-001 as a capacity-funded parallel lane

**From:** Tigwa, reflecting Dave's 2026-07-22 guidance
**To:** Claude
**Status:** Design/review proposal only. No source-of-truth cutover, schema migration, data mutation, or database privilege change is authorized by this note.

## Reframed intent

Dave confirms PostgreSQL is the eventual intended direction, not merely a response to the `#1377` fence-bypass incident. The plan should retain the broader value proposition:

- real transactional collision/concurrency control rather than independent file writes and best-effort coordination;
- schema constraints, foreign keys, uniqueness, and field typing that make invalid state fail at write time;
- atomic multi-record updates and a durable queryable history/outbox;
- indexes, joins, aggregates, and materialized/derived views that make the catalog, reporting, Radar, workflow, and future agent tools data products rather than repeated filesystem scans/projections;
- centralized authorization primitives and the eventual database-enforced canonical-write fence;
- WAL/PITR-capable backup and restore discipline, plus a compact current-state backup target compared with thousands of independently mutable JSON files, per-item archive ZIP creation, and recursive rebuild work;
- reduced small-file/rename/archive I/O pressure. This is a performance/thermal hypothesis to measure, not a conclusion to assert without baseline evidence.

## Recommendation: promote it to a capacity-funded parallel lane, not an unbounded "end of queue"

The month-sprint capacity is a reason to prepare and de-risk the migration now. It is not a reason to let an all-or-nothing data cutover consume the active pipeline/UI/harness sequence.

Use three lanes with an explicit WIP limit:

1. **Critical integrity runway:** real write-fence mutation audit, NATS/Syncthing acceptance, current pipeline fixes, and production regressions. Never blocked by the Postgres program.
2. **Product/harness runway:** UI, Catio specialist/handoff workflow, and operator-visible data products.
3. **Postgres capacity lane:** only runs when the first two have available capacity; each phase creates reviewable evidence and can stop without changing production authority.

This makes the migration real work rather than a someday idea, while keeping it from becoming a vague one-month rewrite gamble.

## Suggested gated sequence

- **P0 — fence/audit now:** publish mutations from the real HTTP canonical write fence. Independent value regardless of migration.
- **P1 — contract and measurement:** schema field inventory; normalized-vs-jsonb decision record; current workload/IO/concurrency baseline; data-product inventory; backup/RPO/RTO and restore-drill requirements; same-instance-versus-sibling-DB risk decision.
- **P2 — shadow database:** immutable snapshot import plus repeatable verifier; no production reads/writes depend on it. Establish row counts, field parity, orphan/error/conflict register, and source-hash provenance.
- **P3 — bounded dual-write pilot:** a small explicitly selected field family behind the existing fence, with optimistic version/revision checking, idempotency/outbox behavior, automatic parity checks, and a tested rollback. This is not full database authority.
- **P4 — staged read cutovers/data products:** move one read-only catalog/query/Radar-style consumer to the verified DB projection only after it can fall back and compare results.
- **P5 — authority cutover:** only after sustained parity, restore drill, load benchmark, rollback rehearsal, and Dave's explicit go/no-go. Then Postgres becomes state truth and JSON becomes derived/exported archive.
- **P6 — hard write fence:** privilege separation/`GRANT`-`REVOKE`/stored procedure boundary after the DB is real authority, with an independent bypass test.

## Pushback / non-negotiable cautions

1. A database does not automatically make backup better. It replaces file-level recovery with a new non-negotiable obligation: tested base backups, WAL/PITR retention, off-host copy, and restore drills. The currently unhealthy rclone/snapshot evidence means this must be a first-class P1/P5 gate, not a presumed benefit.
2. Locks prevent physical write races, not semantic lost updates. The fence still needs revision/version semantics, clear conflict behavior, idempotency for retries, and transactional outbox handling.
3. Dual-write is the most dangerous phase. It needs a one-way authority statement per field, a deterministic repair/replay procedure, and measured parity; never a permanent two-master arrangement.
4. Keep photos as filesystem evidence with path/hash metadata; do not turn the migration into a binary-media ingestion project.
5. Avoid fresh Nix coupling. The system must remain portable; database schema, migration tooling, exports, test fixtures, and worker-facing API contracts must not depend on Nix-specific behavior.
6. The performance/thermal claim must be benchmarked against the actual current workload. Success is not "Postgres exists"; success is reduced write/rebuild cost, reliable query latency, and restored correctness/recovery properties.

## Decision requested from Dave after review

Authorize PP-POSTGRES-001 as the bounded third capacity lane described above, with P0/P1/P2 eligible for parallel preparation and P3+ gated by explicit evidence and Dave review — rather than either deferring the entire program indefinitely or treating a month of capacity as authorization for a full immediate cutover.
