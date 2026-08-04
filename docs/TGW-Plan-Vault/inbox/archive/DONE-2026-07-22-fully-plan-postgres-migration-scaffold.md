# TIGWA PRIORITY CLARIFICATION — Fully plan the PostgreSQL migration scaffold now

**From:** Tigwa, recording Dave’s direction
**To:** Claude
**Re:** PP-POSTGRES-001
**Status:** Planning/scaffolding priority. Not authorization to migrate production authority or cut over.

Dave’s clarification: because the PostgreSQL substrate, structured dataset, and much of the surrounding operational infrastructure already exist, the most reasonable use of available capacity is to **fully plan the migration scaffold now**.

This means finishing the reviewable migration design and dispatch-ready packet set — not building a speculative new platform and not starting P5.

## Required planning deliverable

Make PP-POSTGRES-001’s P1/P2/P3 path concrete enough that implementation can proceed in bounded work units without re-litigating first principles:

1. current-state inventory: actual Postgres instance/roles/backups/ownership, ItemData schema and scale, current writers/readers/derived outputs;
2. target data contract: normalized hot fields, JSONB boundary, stable SKU identity, revision/conflict semantics, photos/filesystem references, JSON-export/archive contract;
3. migration topology: same instance versus sibling target decision, disposable/shadow environment, import provenance and reproducibility;
4. verification plan: row/field parity, rejects/orphans, source hashes, performance/concurrency baseline, reader comparisons, repair/replay and rollback;
5. backup/recovery plan: distinguish configured automation from proven PITR/off-host/restore; specify the proportional proof needed before authority cutover;
6. bounded implementation packets: P2 shadow import, P3 one-field-family dual-write pilot, P4 first read-only projection, P5 explicit cutover gate, P6 hard DB fence;
7. all dependencies, owners, WIP/capacity conditions, acceptance criteria, and no-go conditions.

Treat this as the first substantial item to mature in the Postgres capacity lane. The dataset-import feasibility portion should be small and promptly measured; the full planning value is in correctly enumerating and sequencing the live integration work that follows.

The output must remain compatible with the ongoing TGW operational-reality and Seller Hub audits, so those audits can inform consumer inventory, authoritative-source semantics, and migration sequencing rather than generate a competing plan.
