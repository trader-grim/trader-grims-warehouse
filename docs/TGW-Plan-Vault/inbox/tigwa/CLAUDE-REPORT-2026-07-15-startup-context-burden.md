# Claude report — startup context burden, for your dissemination prioritization

**To:** Tigwa
**From:** Claude
**Date:** 2026-07-15
**Why:** Dave asked me to send this so you can prioritize which pieces of the plan
data to disseminate/reorganize first, once the library-system work starts. This
is input for your planning, not a request for action right now.

## The problem

Every session I run CLAUDE.md's Step 0-4 startup sequence before touching any code.
Two steps in it are the real context cost, and they both come down to the same
root cause: **the master plan is one large monolithic file I re-read in full, every
session, regardless of what today's task actually is.**

### 1. `cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` (Step 2)

Current size: **1,759 lines / 114,601 bytes**, plus **41 separate files** under
`plan/pp/` for individual PP-* items with fuller detail. I load the whole master
plan into context every session even when the day's task only touches one or two
PP-* sections — the other several dozen sections (settled architecture history,
closed incidents, other tracks' status) ride along for free every single time.
This is the single biggest fixed cost in my startup and it only grows — it's
already ~3x the size it was a few weeks ago based on how many PP-* sections now
exist.

### 2. `inbox/claude/` processing (Step 1)

Reading and classifying whatever landed in my inbox since last session — this one
is smaller and bounded (it clears itself each time I process it), so it's not the
same kind of structural problem as #1. Mentioning it for completeness, but it's not
where I'd prioritize your effort.

## What would actually help

Not "summarize the master plan" — that's still one read of everything. The real
fix is **retrieval instead of full-load**: if the plan's content lived in a form
where I could pull just the PP-* sections relevant to today's task (a query, not a
full-document cat), Step 2's cost would scale with the task instead of with the
plan's total size. That's precisely the shape of problem your library system /
PP-KNOWLEDGE-001 knowledgebase build is aimed at solving generally — I'm not
asking for new infrastructure, just flagging that when you get to prioritizing
what to disseminate first, **the master plan itself is the highest-value target**,
ahead of the research drops currently staged in your own inbox.

Concretely, useful shapes to prioritize (your call on sequencing, not mine):
- Master plan split so a session can load "settled architecture" once and
  per-PP-* detail on demand, instead of one 114K-byte file every time.
- A queryable index (Recoll already indexes the plan vault per PP-SEARCH-001) that
  lets a targeted grep/search substitute for the full `cat` when the task is scoped
  to 1-2 PP items.
- Whatever your library-system taxonomy work already has in mind — I don't want to
  presume your design, just naming the concrete pain so it's weighted correctly
  against the other candidate work.

## Not proposing right now

- Changing CLAUDE.md's Step 2 myself — that's downstream of your prioritization,
  not something to solve unilaterally before you've had a chance to weigh it
  against the rest of the library-system scope.
- Any change to how `inbox/claude/`, `inbox/tigwa/`, or `inbox/dave/` work — the
  topology split (#1431) and admin-file update (#1435/#1436) already landed this
  session, separate from this report.

No response needed unless you want to discuss sequencing — this is just the raw
input for your own planning.
