# TIGWA CALIBRATION — PP-POSTGRES-001 preparation is a small feasibility/contract slice; migration integration is the real work

**From:** Tigwa, recording Dave’s clarification and current evidence
**To:** Claude
**Re:** PP-POSTGRES-001 P1 sequencing/estimation
**Status:** Calibration for the migration-contract plan. No cutover or production-data change is authorized.

## Dave’s point to preserve

We are not introducing relational infrastructure or unstructured source material from zero. TGW already operates PostgreSQL for the `state_machine`/queue substrate, and the live health check currently sees a structured ItemData corpus of **55,420 items** plus a SQLite catalog of **55,420 rows updated at check time**. The item shape, stable SKU joins, hot fields, and JSON schema are already meaningful migration inputs.

Therefore, do not portray “making migration possible” as a large speculative platform build. The substantial work is the **integration/authority migration**: mapping every real reader/writer, proving import/parity, moving field families through a one-way authority transition, preserving repair/rollback, updating derived products, and finally enforcing the DB write fence.

## P1 should be short and evidence-driven

Recast P1 as a bounded feasibility/contract closure, designed to quickly answer what is already solved:

1. inventory/reuse the existing Postgres instance, role/backup tooling, schema/migration conventions, and operational ownership;
2. map the existing item schema and current consumers/writers; choose a minimal initial hot-field family;
3. run a reproducible **read-only import benchmark** on a disposable/sibling target using the existing structured dataset, recording duration, counts, rejects/orphans, and repeatability;
4. explicitly inspect the real backup/recovery state and define the missing restore acceptance evidence;
5. choose same-instance versus sibling database based on observed blast radius, not a generic premise;
6. produce the P2 shadow-import and P3 single-field pilot packet with measurable gates.

A result that says “the dataset imports cleanly and repeatably; these are the actual caller seams; these are the few remaining migration prerequisites” is success. Do not extend P1 into a broad architecture exploration once the evidence is available.

## Necessary accuracy about backups

“Already automatically backed up” is a material starting advantage, but current health does **not** yet prove the end-state backup/recovery contract: today’s check reports no successful rclone stamp, snapshot-tree freshness at 16 hours against a one-hour limit, and no encrypted secrets bundle. This does not mean backup infrastructure must be invented; it means P1/P2 must distinguish configured automation from verified off-host recoverability and perform the proportionate restore proof before P5. Treat it as a bounded evidence/remediation item, not an excuse to defer the migration.

## Scheduling consequence

Given this calibration, prioritize a small P1/P2 feasibility package in the Postgres capacity lane whenever capacity is available. If it confirms the expected low friction, advance shadow import and a narrow pilot promptly; do not leave the program artificially “far away” merely because the eventual cutover has high integration work. P5 still requires parity, restore, rollback, load/concurrency, and Dave’s explicit go/no-go.
