# NOTE — plan archiving mechanism (git history IS the archive)

**From:** Claude
**To:** Tigwa
**Date:** 2026-07-18
**Re:** `tgw-plan-maintain` skill update, following today's master plan reconciliation pass

## What changed

Today's `TGW-Master-Plan.md` reconciliation pass (2364→1235 lines) initially extracted
overgrown PP sections into new `pp/<REF>.md` files as the archival move. Dave corrected
the framing afterward:

**"We do not need to move. We archive, rewrite, and then reconcile the diff history of
the archive. That is the whole point of the architecture."**

## The principle, so you're working from the same mechanism

Git already gives a byte-exact, permanent record of every prior version of any tracked
file. The commit immediately before a rewrite *is* the archive — `git log -p` /
`git log --follow -p -- <path>` / `git show <rev>:<path>` are the retrieval tools if
old detail is ever needed back. No copy-to-a-new-file step is required for content that
has no future standalone reference value.

This splits what looked like one "archive it" action into two genuinely different cases:

- **Pure historical narrative** (resolved discussion, an incident timeline, a session
  transcript nobody will keep editing) — rewrite the plan in place, commit with a
  message that names what was compacted, done. The pre-rewrite commit is the record.
- **A living design doc** (an active PP with detail that's still genuinely growing and
  worth its own separately-addressable file) — `pp/<REF>.md` remains correct for this
  case. That's not archival, that's giving an active project a proper home.

Default to the first path unless something will clearly be edited again later.

## Also worth noting

The commit message itself is part of the indexed/searchable record (same Prime
Directive 1 logic as the rest of the Data Charter — everything received is an asset the
moment it lands) — so a commit message naming what was compacted isn't just housekeeping
courtesy, it's the thing that makes the archived content findable later without
grepping through file history blind.

Full detail folded into `.claude/skills/tgw-plan-maintain/SKILL.md` under "Archiving
mechanism: the commit IS the archive" — read that section if/when you touch the plan
vault for a reconciliation-style pass, so we don't drift into two different maintenance
habits.

**No action requested** — informational, so your own plan-vault touches follow the same
mechanism going forward.
