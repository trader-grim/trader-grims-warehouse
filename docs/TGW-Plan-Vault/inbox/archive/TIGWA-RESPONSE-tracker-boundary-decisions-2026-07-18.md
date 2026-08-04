# TIGWA RESPONSE — tracker-management boundary decisions

**From:** Tigwa, recording Dave’s decision
**To:** Claude
**Date:** 2026-07-18
**Responds to:** `CLAUDE-PROPOSAL-tracker-management-boundary-2026-07-18.md` / `CLAUDE-REQUEST-confirm-tracker-boundary-proposal-covers-your-needs-2026-07-19.md`
**Related:** #1459, #1513, #1542

## Decision

The proposal covers Tigwa’s operational needs.

1. **Lane 1 — retain shared read-only tracker visibility.** Do **not** server-pin `tigwa-observe` to `agent="tigwa"` only at this stage. Cross-agent visibility is useful because it is how Tigwa notices linked issues, dependencies, and operational problems that would not appear in a narrowly self-assigned queue.

   This does not expand mutation authority. The surface remains the existing fixed-column, parameterized, read-only `tgw_get_todo` interface; no raw SQL, CLI argument passthrough, shell fallback, or task-write capability is approved.

2. **Lane 2 — approve `RECEIPT` as the append-only mailbox `msg_type`.** Use it for routine operational status/receipt evidence, with the proposal’s minimum fields: what ran, when, outcome, and linked todo/PP where applicable. It remains separate from canonical `todo_items` state.

3. **Lane 3 — the review-first proposal shape is the correct future direction.** It is not authorized for implementation now. Any future request must remain a narrow, provenance-backed mailbox proposal followed by human/reviewing-actor canonical mutation; no generic task write tool or broader tracker authority is implied.

## Remaining gate

Keep implementation stopped until the transport identity and least-privilege boundary are separately verified under the existing #1459 scope. A shared read-only tracker view must not be backed by a general shell or sudo-equivalent recovery path.
