# Response — retrieval-first Plan Vault access review (todo #1439)

**Reviewed:** `reference/TGW-Context-Burden-Retrieval-First-Review-2026-07-15.md`
**Verdict:** APPROVE — prioritize Phase 1 + Phase 2 as recommended. No source/startup
rules/index config changed by this review.

Found late — same inbox topology mix-up as #1385/#1441, no drift found.

## Verified since your review

- Master Plan has grown **1,759 → 2,140 lines (114,601 → 142,609 bytes, +22%)** in the
  day since you wrote this — the burden your diagnosis names got measurably worse, not
  better, in the interim (today's session alone added the OPERATOR-QUEUES-001 and
  #1470-#1474 sections). This strengthens the case, doesn't weaken it.
- `tgw plan` still only has `render`/`check`/`status` — `tgw plan brief --pp` has not
  been built. Your Phase 2 status ("not yet built") is still accurate, no drift to
  correct.

## Assessment

The core judgment — canonical plan stays canonical and versioned, retrieval layer
points into it rather than replacing it, disposable/rebuildable index vs. authority —
is exactly right and matches this project's own standing rule against a second plan
authority. Your boundaries section is the load-bearing part of this doc; I'd hold
whoever builds Phase 1/2 to it literally (source hash + section anchors + generator
version on every retrieval, no summary treated as authority).

## Your four questions for Dave

Passing these through with my own read as input, not a decision — this is his call:

1. **Always-loaded common packet: settled architecture/gates only, or current-state
   snapshot too?** My lean: gates only. A current-state snapshot is exactly the kind of
   thing that goes stale and becomes a second truth — the retrieval layer's whole
   point is to fetch current state on demand (via `tgw plan status`), not cache it.
2. **`tgw plan brief --pp` vs. file-based packets** — my lean: the CLI command. It can
   be tested/versioned/scripted the same way `tgw plan check`/`status` already are, and
   it composes with the existing todo-tracker tooling rather than adding a second
   generated-file convention to keep in sync.
3. **Missing/ambiguous PP: hard-stop or warn?** My lean: hard-stop for ambiguous (more
   than one plausible PP is worse than none — silently picking wrong is the failure
   mode to avoid), warn-and-fall-back-to-full-plan-read for missing (matches your own
   Phase 4 escalation list).
4. **Recoll freshness: scheduled incremental index now, or separate infra decision?**
   My lean: separate decision, later. Nothing in Phase 1/2 depends on Recoll being
   fresh — you scoped that correctly as Phase 3, keep it decoupled so the freshness
   question doesn't block the part that actually relieves the burden.

## Note on the CodeGraph cross-check

Good catch flagging the capture's Z3 overstatement ("prove arbitrary generated Python
correct regardless of plan or prompt" — not what Z3 does) and keeping PP-CODEGRAPH-001
folded into PP-KNOWLEDGE-001 rather than treating the source as a new architecture to
adopt. No objection to how you scoped that; agrees with the master plan's own recorded
2026-07-14 decision.

No files changed by this review.
