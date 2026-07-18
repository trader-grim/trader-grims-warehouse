# PP-CODEGRAPH-001 — code graph + invariant/trace infrastructure (full detail; folded into PP-KNOWLEDGE-001)

## PP-CODEGRAPH-001 — code graph + invariant/trace infrastructure (agents see design convergences) — FOLDED INTO PP-KNOWLEDGE-001, 2026-07-14

**Merged same day as filed (Dave, 2026-07-14 afternoon): "pp-codegraph also
same project now"** — PP-CODEGRAPH-001 is no longer tracked as a separate
PP; it's the concrete build-out of PP-KNOWLEDGE-001's "Graph | Graphify"
layer (see that section's 6-layer table above), which already existed as a
placeholder row before this PP was filed this morning. Both are hosted on
a1131, both were "awaiting Dave's research before build," and today's
"knowledge project on a1131" request refers to this single merged project
going forward — don't treat them as two separate initiatives requiring
separate scaffolding decisions. This section is kept in place (not deleted
— Prime Directive 1) as the detailed design record for the Graphify layer;
new work should be logged under PP-KNOWLEDGE-001 going forward, with this
section as its Graph-layer appendix.

**Origin:** filed as a deferred FUTURE-IDEAS.md entry 2026-07-14 morning
after Dave's directed Perplexity research (not blind — grounded against
this actual repo) proposed a 4-layer architecture: Tree-sitter code graph
(FalkorDB), Postgres+Z3 invariant catalog, DuckDB execution-trace store,
unified MCP layer. **Promoted to active PP same day** once Dave confirmed
he's building it — not deferred.

**The problem it solves (Dave's own framing, not a borrowed pattern):**
coders and planners lack insight into the interconnections of the design,
so cross-cutting "convergences" get missed until a manual audit sweep
finds them — and even then the finding doesn't get resolved into working
process, just logged. Real, already-paid cost: the fence-bypass pattern
(direct `ItemData/` writes skipping the tgw-api fence) was found
independently across 9+ separate files over multiple PP-COHESION-001 audit
sessions instead of in one pass; `status`/`#STATUS` write-path divergence
went undetected until forensic archive-diffing; NATS/JetStream built under
PP-AIOPS-001 wired to the wrong door relative to PP-POSTGRES-001's later
needs; PP-CATALOG-INCR-001 vs PP-POSTGRES-001 still has an unreconciled
premise conflict sitting in this plan.

**Decision (Dave, 2026-07-14): build the full stack, not a cut-down Phase
1.** An earlier Claude-authored planning pass
(`docs/ai-plans/pp-codegraph-001.md`) had proposed deferring Z3/DuckDB and
substituting Postgres-on-tgw-prod for FalkorDB, reasoning from "keep the
flake surface minimal" and "no demonstrated need yet." Dave corrected
this twice: the research was grounded in the actual repo, not generic
literature (evidence for the design was already stronger than that
Postgres-first framing credited), and the standing rule going forward is
more care before scoping down what he's already reasoned toward — see
memory `feedback-take-care-before-discarding-ideas`.

**Host: a1131, not tgw-prod.** Full stack (FalkorDB, Z3, DuckDB, Tree-sitter,
a new unified MCP server) hosted on a1131 — already Tigwa's office and
TGW's thermal-relief compute, client-shaped (no production traffic
dependent on it), 4 cores/19GB RAM/169GB free disk confirmed live
2026-07-14. This placement is what actually resolves the flake-minimal-
surface tension from the earlier draft — new infrastructure on a
non-production, already-less-minimal host doesn't compete with tgw-prod's
constraint the way it would have on tgw-prod itself.

**Status:** infrastructure-establishment planning doc written 2026-07-14 —
`docs/ai-plans/pp-codegraph-001-a1131-infrastructure.md` (components,
packaging options, data-flow, access model, resource budget, open
questions). **Dave is bringing additional research before the actual build
session** — nothing installed, no code written yet. Open questions
flagged for that session: FalkorDB packaging (flake vs. userspace),
invariant-catalog storage engine (DuckDB vs. a1131-local Postgres),
cross-host MCP access mechanism (tgw-prod packets need to reach a1131's
graph), repo-sync mechanism (a1131's checkout is known-stale, #1082), and
parse scope (`src/tgw/` only vs. also `tools/`/`scripts/`).

**Convergence with PP-HERMES-EA-001's planner/stitcher, flagged 2026-07-14
(Dave, still ideation — not yet a build decision):** the Z3 invariant
catalog isn't just a lookup an agent queries — it's a candidate trigger
for the planner's replanning decisions. If a runner's output gets checked
against the invariant catalog and Z3 confirms it holds, that's the
planner's "yeah, that's what I designed" signal to move forward; a failed
confirmation is a replan trigger, not just a bug flag. That makes the
planner/stitcher (see PP-HERMES-EA-001's "operating console/decision gate"
framing) the consumer of PP-CODEGRAPH-001's invariant-confirmation output,
and the in-process question channel (todo #1390) the plausible wire it
rides on. Not designed yet — Dave was still building this idea aloud when
it got captured; treat as a design lead for the eventual build session
(#1386), not a spec.

