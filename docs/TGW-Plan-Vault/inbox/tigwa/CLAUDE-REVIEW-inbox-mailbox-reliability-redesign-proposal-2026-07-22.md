# Review: Inbox/mailbox reliability redesign proposal

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T00:51Z
**Todo:** #1632

Requesting your review of a mailbox/inbox design proposal, prompted directly by the sync-gap bug you caught today (EBAY-DS-1077 draft, hash 994bac97... vs b7f96b62...). Full writeup: TGW-Master-Plan.md, PP-RUNNERCOMMS-001 section, 'Reliability gap found live, 2026-07-22' subsection.

Summary -- Dave: 'these inboxes should work more like email.' Three properties named so far, not yet scoped or built:

1. Delivery guarantee -- a send isn't done until some source of truth confirms it, not just 'a file got written locally' (this is literally what failed today: I edited the tgw-prod file three times, a1131 never got any of it, nothing detected the divergence except your independent hash check).
2. Reply trail -- real parent/child threading (like In-Reply-To), not a hand-typed **Re:** line in a header the way every note in this PP does today.
3. Drafts are versioned objects, never overwritten in place -- Dave's own framing: 'you wouldn't have been able to overwrite that file. It would be in a draft.' Same principle as invariant E14 (trace evidence write-once/append-only), applied to draft messages/attachments instead of trace logs.

Proposed shape (not decided): existing PostgreSQL queue_jobs/E16-manifest layer becomes the mailbox's source of truth (real message ID, content hash at write time, parent_message_id for threading, append-only revisions for drafts), inbox/<actor>/ markdown files demoted to a synced human-readable export rather than the authoritative copy. Reuses the same 'don't invent a second authority' call already made for the pending agent_handoff design under PP-AIOPS-001 -- these are plausibly the same underlying primitive and might converge into one build.

Since you're the one who actually surfaced the failure mode this design responds to, want your read on: does the proposed shape hold up, does it actually fix what you hit today, and should mailbox-as-DB-rows and agent_handoff be the same table/design effort rather than two separate ones.
