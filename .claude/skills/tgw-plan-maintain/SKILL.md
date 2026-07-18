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

## What NOT to do

- Don't delete content to hit the line count. Git history + `pp/`/`archive/`
  preserve everything (Prime Directive 1) — this is about *where* something
  lives, never whether it survives.
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
   transcripts, full gap lists), that detail belongs in one of:
   - `pp/<REF>.md` — the PP's own full design doc (promote on next touch,
     per the file's existing convention)
   - `reports/` — standing/periodic reports that need no action (see below)
   - a dedicated doc under `dev-workflow/research/` or `reference/runbooks/`
   Leave a pointer + a one-line status in the plan; move the rest.

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
