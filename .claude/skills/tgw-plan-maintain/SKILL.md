---
name: tgw-plan-maintain
description: Reconcile and tidy TGW-Master-Plan.md between planning sessions — trim it back toward its own ≤500-line target, resolve stale premise conflicts, fold loose lines into their PP section, flag stub PPs for a real decision. Use when the user says /tgw-plan-maintain, asks to "go over the master plan," "clean up the plan," or a session opens by noting the plan has grown large / hasn't had a reconciliation pass. Shared across actors (Claude, Tigwa) — this is the one procedure both follow so the plan doesn't drift into two maintenance styles.
---

# TGW Plan Maintain

`TGW-Master-Plan.md` states its own rule: "this file stays ≤500 lines; full
designs live in `pp/`, history in `archive/`." It drifts past that between
dedicated reconciliation passes because ordinary session work adds detail
inline faster than anyone moves it back out. This skill is the maintenance
procedure — not a design pass (that's `/tgw-plan`), a hygiene pass over the
plan document itself.

## When to run this

- Dave asks to "go over the plan," or a session's own `INPROGRESS-*` note
  says a reconciliation pass is overdue.
- The file measurably exceeds its line budget (`wc -l
  docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md`).
- Right after a batch of new PPs/incidents landed in one sitting (a busy
  session is exactly when stray lines and stub PPs accumulate).

## Archiving mechanism: the commit IS the archive (Dave, 2026-07-18)

**"We do not need to move. We archive, rewrite, and then reconcile the diff
history of the archive. That is the whole point of the architecture."**

Git already gives a byte-exact, permanent record of every prior version of
`TGW-Master-Plan.md` — the commit immediately before a rewrite *is* the
archive. `git log -p` / `git log --follow -p -- docs/TGW-Plan-Vault/plan/
TGW-Master-Plan.md` / `git show <rev>:<path>` are the reconciliation tools:
if anyone needs the old detail back, that's how it's recovered — not by
hunting through a copied-out file. This is also an indexed data source in
its own right (same Prime Directive 1 logic as everything else in the
Data Charter: a commit message that names what was compacted is itself
part of the searchable/recoverable record, not just a formality).

This means two genuinely different things were being conflated as one
"move it out" step — split them:

- **Pure historical narrative** (a resolved discussion, an incident
  timeline, a session transcript with no future standalone reference
  value) — **rewrite in place, commit with a message naming what was
  compacted, done.** No new file, no `archive/sections/` copy. The
  pre-rewrite commit already is the permanent record.
- **A living design doc** (an active PP with ongoing detail that's
  genuinely useful as its own separately-addressable, still-growing
  document) — `pp/<REF>.md` remains correct. That's not archival, that's
  giving an active project a proper home outside the top-level index.

Default to the first path unless the content is clearly still-active
project detail someone will keep appending to. When in doubt, check
whether the destination file would ever be edited again — if not, it
should have been a commit message, not a file.

**Tigwa needs to know this too** — she touches the plan/vault and should
follow the same mechanism, not re-derive a copy-out habit independently.
If this hasn't already been relayed to her, drop a note in `inbox/tigwa/`
explaining the git-history-is-the-archive principle before/while running
a pass she might also touch.

## What NOT to do

- Don't delete content to hit the line count. Git history (see above) +
  `pp/` for still-active designs preserve everything (Prime Directive 1)
  — this is about *where* something lives, never whether it survives.
- Don't invent resolutions to open decisions on Dave's behalf. Flag them;
  he decides. This skill tidies structure, not open questions.
- Don't silently reword Dave's own quoted framing — preserve his exact words
  in quotes, tidy the scaffolding around them.
- **Don't invent or own filing locations/taxonomy yourself (Dave, 2026-07-16):
  "all of the filing locations and tasks are the librarian's responsibility.
  Just tell what goes where."** Where a document type belongs (a new
  `reports/`-style folder, a retention rule, a directory convention) is the
  librarian's (Tigwa/Leotha) call, not this skill's or this session's — once
  she's trained, that includes creating new locations, not just choosing
  among existing ones (the restored pm_intake pattern, per CLAUDE.md). Your
  job is to classify — this is a report, this is PP overflow, this is an
  incident writeup — or simply hand it to her to route. Use an existing
  librarian-defined location if one fits, or flag anything provisional as
  provisional, pending her reconciliation.

## The checks, in order

1. **Size.** `wc -l` the file. If a single PP section runs more than ~2-3
   paragraphs of blow-by-blow narrative (incident timelines, session
   transcripts, full gap lists), decide which of these it is (see
   "Archiving mechanism" above):
   - **Pure historical narrative, no future standalone reference value** —
     compact it in place; the pre-rewrite commit is the archive. No new file.
   - **Still-active project detail** — `pp/<REF>.md`, the PP's own living
     design doc (promote on next touch, per the file's existing convention)
   - `reports/` — standing/periodic reports that need no action (see below)
   - a dedicated doc under `dev-workflow/research/` or `reference/runbooks/`
   Leave a pointer + a one-line status in the plan either way; write a
   commit message that names what was compacted so it's findable later.

2. **Reports vs. plan narrative.** If a report is informational only — a
   monitoring snapshot, an incident writeup where "no action necessary" is
   the actual finding — it goes in `docs/TGW-Plan-Vault/reports/`, never
   inline in the plan. The plan gets at most one pointer line. Don't route
   these through `inbox/` either; there's nothing to triage.

3. **Stub PPs.** A PP that has only ever been "given its own heading" or
   "added to index" with a note like "pointer only, promote on next touch"
   is a stub. Once the project is in build mode (not exploratory), a stub
   sitting indefinitely is itself a finding — flag it for a real planning
   pass or an explicit drop/defer decision. Don't silently promote it
   yourself; that's a planning session, not a tidy-up.

4. **Premise conflicts.** If two PPs assume mutually exclusive things (e.g.
   one assumes JSON stays the source of truth, another assumes it doesn't),
   don't let both sit "open, not yet resolved" indefinitely — that's a
   decision only Dave can make. Surface it explicitly as a question in your
   own reconciliation summary; once he answers, encode the sequencing
   decision in both PPs' sections with a cross-reference (`[[other-pp]]`),
   not just one.

5. **Convergent PPs.** If 2+ PPs have grown to describe the same underlying
   pattern from different angles (e.g. three PPs that all turn out to be
   "every agent gets a reviewed operational contract"), don't keep writing
   the same story three times. Add one consolidation note near the top of
   the most central PP, cross-reference the others, and leave their
   historical detail in place — the note is the map, not a merge.

6. **Loose lines.** Anything sitting between headings with no PP context —
   a bare one-liner, a dropped changelog entry — either belongs inside its
   PP's own paragraph, or (if it's a real PP with no heading yet) needs a
   minimal proper `## PP-XXX-001` heading of its own. Bare completed-todo
   droppings in the "Done" rollup area can be folded into a compact bulleted
   list rather than left as ungrouped bare lines.

## After the pass

- Register what changed (moves, consolidations, flagged decisions) in the
  session's `INPROGRESS-*` inbox note, same as any other work.
- If you flagged a stub PP or a premise conflict for Dave, file the todo(s)
  before ending the session — don't leave the flag as prose-only in the
  plan (same discipline as Prime Directive 5).
- Confirm `tgw plan check` is still clean after any PP heading changes.
