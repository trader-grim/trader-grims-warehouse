# IN PROGRESS — SHCS + Operational-Reality Phase 0 packets drafted, 2026-07-22

**What happened:** four new files from Tigwa landed in `inbox/claude/`
(two REQUESTs at Dave's direction, one ADDENDUM, one CLARIFICATION) asking
for two bounded Phase 0 scoping packets: a Seller Hub Capability
Specification (SHCS) audit plan (PP-SELLERHUB-001), and a TGW Application
Capability & Operational-Reality Register (new PP, since it spans
RUNBOOK/EDITOR/AIOPS/CATIONIX/POSTGRES/SELLERHUB).

**What I did:**
- Drafted `docs/ai-plans/shcs-phase0-audit-scoping.md` — register schema
  (7 status values), sample rows from known gaps, risk-ranked audit order,
  acceptance criteria, integration matrix, and a separate Dave-enhancement
  table per the clarification note. Flagged the real blocker: token-
  facility owner + least-privilege read-only seam needs defining before
  any evidence collection starts.
- Drafted `docs/ai-plans/tgw-operational-reality-register-phase0.md` —
  opened as **new PP-OPSREALITY-001** (deliberately distinct from
  PP-COHESION-001, which is a bug audit not an evidence-state register).
  Schema, source-of-truth hierarchy, bounded non-crawling Phase 0 method,
  risk-ranked domains, runbook-vs-stale-doc criteria, pull-based
  revalidation cadence, SHCS cross-reference.
- Folded pointers into `TGW-Master-Plan.md` under PP-SELLERHUB-001 and the
  new PP-OPSREALITY-001 section (placed right after PP-COHESION-001).
- Filed todos #1644/#1645, both tagged to their respective PPs.
- Sent Tigwa a RESPONSE summarizing both packets and the two open
  questions for Dave (who collects Seller Hub UI evidence given the
  credential boundary; whether PP-OPSREALITY-001 should be its own PP).
- Archived all four source files plus the earlier PP-POSTGRES-001 P1
  breadcrumb.

**Still open, next session or later this one:**
- Dave hasn't reviewed/answered either packet's open questions yet.
- PP-POSTGRES-001's own next steps are still live: the A5 restore drill
  (closes P1's last gap) and dispatching P0/#1636
  (`publish_mutation()` into the real HTTP fence) — neither started.
- Neither Phase 0 register has actually started row population — both
  are scoping-only per their own explicit boundary.
