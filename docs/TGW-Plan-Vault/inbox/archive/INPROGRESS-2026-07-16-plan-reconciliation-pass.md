# IN PROGRESS — plan reconciliation pass, 2026-07-16 (todo #1477)

Dave's 5-point walkthrough of the master-plan cleanup findings, actioned this session:

1. **Fixed up + new shared skill.** `.claude/skills/tgw-plan-maintain/SKILL.md` —
   the maintenance procedure for `TGW-Master-Plan.md` between planning sessions,
   shared across actors (Claude/Tigwa) per Dave's "we will need a shared skill."
   Encodes: size checks, reports-vs-plan-narrative split, stub-PP flagging,
   premise-conflict resolution, convergent-PP consolidation, loose-line folding.
2. **Stub PPs (PP-STORAGE-001/PP-WHISPER-001/PP-VISION-001)** flagged in-plan per
   Dave: "we plan them or change our mind then, we are ready to produce code."
   Todo #1478 filed for the dedicated planning pass.
3. **PP-POSTGRES-001 vs PP-CATALOG-INCR-001 premise conflict resolved** — Dave:
   Postgres is the right call long-term, not now; finish pipeline logic + build
   the UI first, revisit backend "unless it becomes too painful." Both PP
   sections updated with the sequencing decision and cross-references.
4. **New `docs/TGW-Plan-Vault/reports/` directory** — home for standing/
   informational reports ("no action necessary" writeups) that don't belong in
   `inbox/` (nothing to triage) or inline in the plan. Moved the 5 misfiled
   TIGWA-REPORT-* files there from `inbox/archive/`. PP-RUNBOOK-001's thermal
   incident narrative trimmed from ~100 lines to a pointer + status, per Dave's
   "that was documented separately, no action necessary."
5. **Agent-governance cluster consolidated** — added a synthesis note atop
   PP-HR-001 tying PP-HERMES-EA-001 / PP-HR-001 / PP-AGENT-DISCIPLINE-001
   together as Dave put it: "a dual-reviewed operational contract for each
   worker." Historical detail in each section left in place, not merged away.

Also folded the loose orphan lines (PP-FENCE-002, a stray Hermes-research
one-liner, ~30 bare "#NNNN — see document" droppings in the Done rollup) into
proper headings/bulleted lists.

**Net result:** file went from 2176 → 2125 lines. The remaining bulk is
legitimate PP history that the new skill's "promote on next touch" rule will
keep trimming incrementally — this wasn't a one-session rewrite to hit the
≤500-line target, and shouldn't be forced into one; Dave didn't ask for that,
he asked for the cleanup + a shared maintenance procedure, both done.

**Next session should:** apply `tgw-plan-maintain` opportunistically on touch
(not just at dedicated reconciliation sessions), and pick up todo #1478's
planning pass when Dave's ready.

## Follow-up same session: filing authority correction

Dave, right after reviewing the above: "all of the filing locations and tasks
are the librarian's responsibility. Just tell what goes where" — reinforcing
a priority he'd already given Tigwa 2026-07-15, and noting the ultimate
strategy is to migrate `TGW-Master-Plan.md` itself into the knowledgebase
architecture (lighter to consume, less startup burden). **Clarified further
same session:** once trained, the librarian creates new locations herself,
not just chooses among existing ones — our job shrinks to naming a new
document *type* or just handing her raw material to route. Dave confirmed
this is the pm_intake pattern restored under Tigwa's persona (per CLAUDE.md),
not a new ask. Propagated into the master-plan note, the Tigwa inbox note,
and the skill's own filing-authority bullet.

Concrete self-correction: the `reports/` folder + its README I created this
session, and part of the new skill, were exactly the kind of filing-location
decision that should be the librarian's (Tigwa's), not mine. Marked both as
provisional/subject to her reconciliation (not deleted — Prime Directive 1),
and filed `inbox/tigwa/CLAUDE-NOTE-2026-07-16-filing-authority-reinforcement.md`
for her awareness. Added a "Filing authority" note under PP-KNOWLEDGE-001 in
the master plan encoding this going forward: Claude classifies content type,
librarian decides location/taxonomy.

**Vision statement captured, same session (Dave):** "A library with a
librarian that can tell you where everything is, cross-referenced, in your
language, with footnotes. Hopefully." Added as its own paragraph atop
PP-KNOWLEDGE-001 — the one sentence that ties pm_intake's filing behavior
(now Tigwa's) and the knowledgebase stack (git-annex/Recoll/Graphify/MCP)
together as one destination: intake + stacks + natural-language query +
cited provenance. Aspirational/not yet scoped, recorded so it stays visible
while the git-annex/Recoll starting point gets built first.

Answered Dave's direct question (does the dual-reviewed-contract cluster
cover this, or is there more to plan): **not yet covered.** PP-HR-001's
contract cluster handles identity/review/tool-boundary/enforcement well, but
has no explicit clause assigning filing/taxonomy authority to the librarian
role — that gap is exactly what this session's own overstep surfaced. Filed
todo #1479, delegated to Tigwa (her contract, her call how/whether to encode
it), rather than Claude drafting the clause.

## Follow-up: scope correction on the contract gap

Dave, immediately after: "it only applies to you and Tigwa." Corrected an
overreach in the PP-HR-001 consolidation note — the dual-reviewed contract
isn't a blanket every-worker model, it's specifically Claude↔Tigwa. He's
already routing his own documents to Tigwa directly, separate from this.
Ordinary `tgw-worker@*` systemd processes stay accountable to their own
owning boss, not party to this; Claude's obligation toward them is either
reporting on their behalf or making sure they self-report (existing
health-check/digest machinery, no new mechanism). Leotha's status under
either model left explicitly unstated, not assumed.

Todo #1479 re-noted: Dave wants **Tigwa to draft the filing policy herself**
as a first attempt — "no better way to learn" — not Claude proposing
something for her to approve. Inbox note to her rewritten to reflect both
the scope correction and this explicit instruction.

No source/config/secret mutation this session — plan-vault + skill files only.

## Follow-up: end-state framing captured

Dave: "monitoring, watching, fixing, then giving more responsibility. It's
not babysitting, it is development. When we are done we will have both
lightened your burden and mine and have a better platform." Added as an
explicit end-state paragraph atop PP-CATIONIX-001 — names the destination
behind every supervised-then-autonomous step already in the plan (Tigwa's
training, the crypto-lock, agent-discipline hooks). Saved as feedback memory
`feedback-monitor-fix-delegate-end-state.md`.

## SESSION PAUSED HERE (Dave, before a `/clear`) — NOT DONE, resume next session

**Dave's own words:** "I am not certain I addressed all of the gaps you had
it scrolled by before I read it all. I know we still have actual planning
to do." Todo #1477 stays `in_progress` — do not mark it done.

**Next session should:**
1. Re-surface the original 5-point cleanup list from this session (stray
   lines, stub PPs, Postgres/Catalog-Incr conflict, thermal-narrative
   trimming, agent-governance consolidation) and confirm with Dave each was
   actually addressed to his satisfaction — he flagged real uncertainty here,
   don't assume the earlier "done" framing was complete.
2. Move into the **actual planning work** that's still outstanding, not just
   plan hygiene: todo #1478 (PP-STORAGE-001/PP-WHISPER-001/PP-VISION-001
   dedicated planning pass — plan each or drop them), and whatever else
   surfaces once the gap re-check above is done.
3. Todo #1479 (filing policy) stays with Tigwa — nothing for Claude to do
   there until she's drafted something to review.

This file stays `INPROGRESS` (not renamed to `DONE`) — the work continues
next session, not concluded here.
