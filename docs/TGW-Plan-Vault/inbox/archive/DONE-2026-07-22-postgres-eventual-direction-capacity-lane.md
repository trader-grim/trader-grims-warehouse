# TIGWA DECISION RECORD — PP-POSTGRES-001 eventual direction and capacity lane

**From:** Tigwa, recording Dave’s direction
**To:** Claude
**Status:** Direction and planning authorization. It is not authority for a database cutover, production-data mutation, or flake/configuration change.

## Dave’s direction

PostgreSQL item truth is the intended eventual destination. This is not only a reaction to the current JSON write-fence incidents. Dave’s reasoning includes the durable benefits of a real database: transactional collision/locking behavior, schema and relational integrity, query/data-product capability, cleaner central backup/recovery primitives when designed correctly, and eliminating the accumulating JSON-era coordination and write-handling workarounds.

The transition may not be perfectly smooth, but it is considered doable and preferable to indefinitely expanding temporary JSON-era machinery that will later be removed anyway.

## Chosen operating model

Place PP-POSTGRES-001 at the **end of the active implementation sequence as a capacity-funded parallel lane**:

- It is real planned work, not a vague someday proposal.
- It runs only from spare capacity after active critical integrity/reliability obligations and the product/harness lane have their needed attention.
- It may advance through bounded, evidence-producing stages without blocking current work.
- The state-master cutover remains an explicit Dave decision following objective acceptance evidence; availability of Max-plan capacity alone does not authorize that transition.

## Suggested path

### Stage A — immediate independent value
Move mutation/audit publication to the actual HTTP/canonical write fence, rather than only the narrow CLI path. This benefits the current JSON system and is a prerequisite evidence seam for the migration.

### Stage B — migration readiness contract
Prepare, review, and preserve:

1. hot-field / `jsonb` / photo-metadata schema decision grounded in real current item fields;
2. same-instance-versus-sibling-database decision and blast-radius analysis;
3. baseline measurements for write volume, small-file/archive/rebuild cost, lock/conflict patterns, query needs, and recoverability;
4. data-product inventory: catalog/filter/search/reporting/Radar/workflow consumers that can later obtain simpler queryable views;
5. backup contract: base backup, WAL/PITR retention, off-host copy, RPO/RTO, and a restore drill — no claim that DB backup is intrinsically better until this is proven;
6. rollback, migration provenance, and conflict/revision semantics.

### Stage C — shadow import and proof
Create a reproducible, read-only shadow import of existing item truth. Verify row counts, field parity, orphan/conflict/error registers, source-hash provenance, and repeatability. No production reader or writer depends on it.

### Stage D — narrow dual-write pilot
After review, select one bounded field family through the existing fence. Add revision/idempotency/outbox behavior, automatic parity verification, a repair/replay procedure, and a tested rollback. JSON remains authoritative; this is not a permanent two-master state.

### Stage E — staged database-read data products
Move a selected read-only consumer to a verified DB projection only after comparison/fallback validation. Prefer work that proves the database’s operator value — query, filtering, reporting, workflow/Radar views — before the authority cutover.

### Stage F — authority decision and cutover
Only after sustained parity, a restore drill, load/concurrency evidence, rollback rehearsal, and Dave’s explicit go/no-go: Postgres becomes current state authority; JSON becomes generated export/archive. Photos remain external filesystem evidence with metadata references.

### Stage G — unbypassable database fence
After the DB is authority, enforce the dedicated fence role/function and column-level permission boundary. Independently prove that an application-default role cannot bypass it.

## Guardrails / required pushback

- A database prevents physical write races but not semantic lost updates; version/revision/conflict behavior remains a required contract.
- Do not equate dual-write with safety unless parity, repair, rollback, and one-way field authority are demonstrated.
- Do not make photos database blobs.
- Do not introduce fresh Nix coupling. Migration tools, data contracts, exports, fixtures, and worker interfaces must stay portable while TGW lives in the current environment.
- Do not let this lane block current incident work, NATS/mailbox acceptance, or active product/harness delivery.

## Requested Claude response

Review this as a sequenced PP-POSTGRES-001 direction. Identify: missing real-database benefits or risks, conflicts with the existing PP/phased plan, the smallest useful Stage A/B packet boundary, and any acceptance criteria that would make the later authority decision unsafe or ambiguous. Return a review artifact; do not begin a cutover or alter the canonical Master Plan without Dave’s separate approval.
