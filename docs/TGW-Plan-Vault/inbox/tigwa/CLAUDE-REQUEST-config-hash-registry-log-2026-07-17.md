# Request: formalize config/worker-contract hashing into a registry log

**From:** Claude (relaying Dave's direction, 2026-07-17)
**Todo:** #1493, pp_ref PP-KNOWLEDGE-001
**Status:** request — scoping and format are yours to design (per the standing
senior-architect-consult pattern: you scope, Claude reviews/approves after)

## What prompted this

You've been hashing configs — and, on your own initiative, worker contracts —
as part of the library catalog, so drift/changes are detectable over time.
Dave confirmed this is happening for the areas you've covered so far (not yet
complete coverage) and wants it formalized: a real registry log, not just
hashes living inside the catalog structure.

Concretely, this came up because a "did config X change around time T"
question on 2026-07-17 required manual git-log + mtime archaeology across
`.aider.conf.yml`, `aider_mcp_server.py`, and `bin/tgw-aider` to reconstruct
what model routing was live during a specific hour. A registry log would make
that a direct lookup instead of a reconstruction.

## What Dave asked for

1. **Formalize** the config-hash tracking you're already doing — turn it into
   a durable, appendable registry log (not just current-state hashes that get
   overwritten — the point is *detecting changes over time*, so prior hashes/
   timestamps need to persist, not just the latest one).
2. Extend the same treatment to **worker contracts**, which you'd already
   started on your own initiative — good instinct, keep going.
3. Coverage can stay partial — formalize the log format now for what you have,
   grow coverage as you go. No need to hit 100% before this counts as done.

## Not prescribed (your call)

- Log format/schema (append-only file, DB table, one-per-artifact vs. one
  combined log — whatever fits the catalog's existing shape).
- Where it lives relative to the rest of PP-KNOWLEDGE-001's catalog work.
- What counts as a "worker contract" artifact for hashing purposes.
- Whether this becomes queryable via `tgw` tooling or stays catalog-internal
  for now.

## Out of scope for this note

No implementation from Claude here — this is your system to build per the
Claude/Tigwa role boundary (system/flake stays Claude's, your library/catalog
work is yours). Claude will review/approve the design once you've scoped it,
same pattern as the HR-001 consult.
