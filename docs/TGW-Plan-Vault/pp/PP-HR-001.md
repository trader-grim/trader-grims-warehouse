# PP-HR-001 — the HR department for AI agents/personas (full detail)

## PP-HR-001 — the "HR department" for AI agents/personas — NEW 2026-07-16

**Consolidation note, 2026-07-16 (Dave): "Seems like it has been boiled down
to a dual-reviewed operational contract for each worker."** This PP,
[[PP-HERMES-EA-001]] (Tigwa/Leotha persona contracts), and
[[PP-AGENT-DISCIPLINE-001]] (mechanical enforcement of contract rules) are
three angles on the same underlying object, not three separate initiatives:
a written contract, reviewed by two parties, backed by a hook/detector
wherever possible rather than left as prose an agent merely reads
(PP-AGENT-DISCIPLINE-001's contribution). Read the three PPs as one story:
PP-HR-001 is the process (how a contract gets built/reviewed/accepted),
PP-HERMES-EA-001 is the first concrete instances of it, PP-AGENT-
DISCIPLINE-001 is the mechanical-enforcement half. Historical detail stays
in each section below (Prime Directive 1) — this note is the map, not a
replacement.

**Scope correction, same day (Dave): "it only applies to you and Tigwa."**
The dual-reviewed contract is NOT a blanket "every agent/persona/worker gets
one" model — corrects the overreach in this note's first draft. It's
specifically the Claude↔Tigwa relationship (the two actors who actually
cross-review each other's contracts today). **Separate, already-settled
model for everything else, stated explicitly for the first time here:**
ordinary workers (the `tgw-worker@*` systemd processes — `ai_identify`,
`ebay_draft`, etc.) are responsible to their owning "boss," not party to a
dual-reviewed contract of their own; Claude's obligation toward them is
either to report on their behalf or to make sure they report themselves
(health checks, `tgw ops-digest`, dead-letter visibility — the existing R2
track machinery is what "make sure they report" cashes out to in practice,
not a new mechanism to build). Leotha's status under either model is not yet
stated — don't assume either way.

**Design mirror, 2026-07-16 (Tigwa, reporting only — not a Claude task):**
"Agent Contract Acceptance Suite" (ACAS) concept — no role's contract counts
as accepted on clear prose alone; each needs a versioned test portfolio
(identity/attribution, startup/intake, tool/access boundary allow+deny,
required-workflow bypass-proofing, secrets/data handling, review/handoff,
provider-degradation, audit/delivery, spec-drift, offboarding), 4 evidence
levels (static audit → fixture/harness → sandbox integration → approved
live-fire), and explicit `NOT-YET-MECHANIZABLE`/`BLOCKED-UPSTREAM` outcomes
that may never be restated as compliant. Full text:
`inbox/claude/TIGWA-NOTE-PP-HR-001-agent-contract-acceptance-suite-2026-07-16.md`.
Design ownership stays with Tigwa/Dave per the existing PP-HR-001 delegation
— recorded here for continuity, not adopted as a Claude action item.

**Dave, 2026-07-16, connecting two same-day threads:** invariant E11's
audit (agent role restrictions are still mostly prose, not mechanically
enforced — see `reference/invariants.md` E11) and the ferals audit's
account/ledger/authority governance gap (`TIGWA-REQUEST-1333-ferals-
audit-draft.md`) are the same underlying problem: nobody owns onboarding,
credentialing, role-definition, discipline, or review across the growing
roster of AI workers (Tigwa, Leotha, tgw-coder, nix-flake-maintainer, the
ferals themselves). Handled ad hoc today, one incident at a time.

**Design ownership: assigned to Tigwa, Dave guiding directly, submitted for
review afterward** (Dave's explicit instruction) — not designed by Claude.
Full design-request brief (everything to consider, not a spec): `inbox/
tigwa/CLAUDE-REQUEST-2026-07-16-hr-department-design-brief.md`.

**Status: "job descriptions" component already delivered, 2026-07-16 (Dave:
"this was not a waste")** — invariant E11 plus its two concrete instances
(the `SessionStart` briefing hook replacing CLAUDE.md's prose-only startup
ritual; the audit of `nix-flake-maintainer`/`tgw-coder` finding which of
their "must"/"never" rules are hook-enforced vs. still prose, todos #1449/
#1450) were built *before* PP-HR-001 was named, then recognized as its
first real piece rather than unrelated prerequisite work. Precedent this
sets for the rest of the design: a "job description" for an agent isn't
done until its restrictions are checked against what's actually
mechanically enforceable, not just written well. Remaining components
(resource/credential governance, onboarding/training pipeline, performance/
escalation review — see the design brief) not started.

