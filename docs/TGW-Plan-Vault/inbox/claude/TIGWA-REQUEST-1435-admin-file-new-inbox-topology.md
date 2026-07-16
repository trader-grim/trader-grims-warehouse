# Tigwa request — #1435: update `admin-file` for the new Plan Vault inbox topology

**To:** Claude  
**From:** Dave via Tigwa  
**PP:** PP-HERMES-EA-001  
**Related:** Tigwa librarian/PM-intake ownership #1434  
**Status:** implementation request; do not activate the worker

## Why

Dave has established that Plan Vault inboxes are intake/staging, not a permanent library. `tgw admin-file` remains valuable as an explicit, deterministic, operator-invoked rote-work dispatcher, but its current root-only `inbox/*.md` behavior predates the new owner queues.

## New intake topology

```text
inbox/                       shared/unassigned intake staging
inbox/dave/                  Dave-originated intake
inbox/tigwa/                 Tigwa working intake
inbox/claude/                Claude handoffs/requests — NOT an admin-file source
inbox/queued/                worker staging — NOT a source
inbox/archive/               retained transient history — NOT a source
inbox/review/                review holds — NOT a source
```

Control files such as `README.md` and `Untitled.base` are not intake candidates.

## Required scope

Update `tgw admin-file` and its deterministic scan/stage helpers so that it:

1. Discovers eligible source files from the shared root, `inbox/dave/`, and `inbox/tigwa/`.
2. Excludes the operational/control directories/files above, and never recursively consumes archive/review/queued/Claude handoffs.
3. Adds an explicit `--dry-run` mode that reports a stable manifest before any move or queue write. The manifest should expose relative source path, owner/source queue, file type, size, mtime, SHA-256, eligibility/delay decision, and planned queue path.
4. Preserves source identity in queued job payloads: at minimum relative source path, owner/source queue, source filename, SHA-256, and intake timestamp. Avoid filename-only identity.
5. Uses checksum-aware/idempotent queue identity so same-name files from different owners cannot collide and unchanged content is not needlessly re-enqueued.
6. Maintains the existing delay gate and `--now` bypass semantics.
7. Keeps `admin-file` deterministic: it may inventory, stage, and enqueue. It must **not** invoke an LLM, write the Master Plan, choose a permanent library shelf, normalize a document, or delete/rewrite source research.
8. Keeps file moves collision-safe and reversible enough for review. Do not silently overwrite an existing queued artifact.

## Explicitly out of scope

- Activating/restarting `pm_intake` worker.
- Automatic permanent-library shelving or index taxonomy: Dave will supply that direction; Tigwa #1434 owns the subsequent workflow design.
- General PDF/HTML/text normalization. It may be inventoried now, but semantic cleanup is a later supervised stage.
- `flake.nix`, service units, provider/model changes, or autonomous plan edits.

## Tests and handoff

Add/update offline tests covering:

```text
root/Dave/Tigwa discovery
exclusion of claude/queued/archive/review/control files
--dry-run causes no filesystem or queue mutation
same filename across owners does not collide
same content/idempotent rerun is safe
queue payload contains source/provenance fields
collision-safe queued staging
```

Run the focused test suite. Then create the required Plan Vault review artifact for Tigwa/Dave with changed paths, test evidence, remaining limitations, and a request for review. Keep #1435 open until review/linking is complete.
