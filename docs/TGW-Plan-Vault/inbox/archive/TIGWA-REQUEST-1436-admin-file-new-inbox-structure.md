# Request for Claude — #1436: update `admin-file` for the new inbox topology

**PP:** PP-HERMES-EA-001  
**Owner:** Claude  
**Coordinator:** Tigwa #1434  
**Requested by:** Dave, 2026-07-15

## Why

`admin-file` is a valuable explicit, operator-invoked base for rote PM-intake work. Its current implementation only scans `Plan Vault/inbox/*.md`, moves eligible files to `inbox/queued/`, and enqueues `pm_intake` jobs. The Plan Vault now has deliberate incoming queues:

- `inbox/` — root staging/research entries
- `inbox/dave/` — Dave-originated intake
- `inbox/tigwa/` — Tigwa-originated intake

`inbox/claude/` is correspondence for Claude and must not be consumed as general intake. `queued/`, `review/`, and `archive/` are operational subtrees, not fresh intake.

## Requested implementation

Update `tgw admin-file` / its `pm_intake` scan-and-enqueue path so the explicit command can safely discover the new incoming topology.

1. Add a non-mutating dry-run/manifest mode that shows each candidate's source queue and planned action.
2. Discover root staging, `inbox/dave/`, and `inbox/tigwa/` deliberately; do not recurse into or consume `inbox/claude/`, `queued/`, `review/`, or `archive/`.
3. Preserve source identity/provenance through queue payload and subsequent logging. Avoid filename-only collisions across queues; use an idempotency key appropriate to the source path/content.
4. Retain the age gate and explicit `--now` behavior, extending them consistently to the eligible queues.
5. Update the stale inbox README so it describes the actual current topology and manual/supervised operating model.
6. Add focused tests for inclusion, exclusion, dry run, collision/idempotency, and age-gate behavior.

## Boundaries

- Do **not** activate the `pm_intake` worker or run `admin-file` on live Plan Vault material for this task.
- Do **not** introduce automatic Master Plan edits, permanent library-shelf decisions, or unattended semantic classification.
- Keep the change narrow and source-preserving. Permanent library taxonomy/indexing is coordinated through Tigwa #1434 and still needs Dave's direction.
- Return implementation evidence, tests, and any design question that cannot be made deterministic.

## Review questions

- What is the least surprising queued-path layout that preserves the original owner/source queue?
- Does a content hash belong in the initial queue dedupe key, or should it be recorded in the future intake ledger while retaining path-based delivery identity?
- Which non-Markdown types should remain visibly staged rather than being silently ignored until a separately specified normalizer exists?
