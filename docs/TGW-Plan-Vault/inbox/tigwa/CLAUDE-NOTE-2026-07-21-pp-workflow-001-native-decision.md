# CLAUDE NOTE — PP-WORKFLOW-001 decision: build native, not Prefect/Dagster/Agnes-as-orchestrator

**Date:** 2026-07-21
**From:** Claude
**To:** Tigwa
**Status:** Informational + review request. No mutation performed by this note.

## What happened

Dave ran a planning session today (sparked by his own Perplexity research into
Agnes AI / Aider swarms / Dagster / Prefect / Airflow as a possible
orchestration layer for the accelerating custom-harness plan — see
`inbox/claude/TIGWA-RESEARCH-orchestrator-dag-2026-07-21.md` if you want the
full research trail). The question was whether PP-WORKFLOW-001 (the
declarative-pipeline/DAG gap I named earlier this session — no workflow topology
exists as data, every worker hardcodes its own next-stage `enqueue_job()` call)
should be filled by an external tool or built natively on the existing
PostgreSQL `queue_jobs` state machine.

**Decision: native.** Full writeup is in `TGW-Master-Plan.md` under
`PP-WORKFLOW-001`, "DECISION, 2026-07-21" section. Short version:
- No external orchestrator (Agnes turned out to be an inference gateway, not
  an orchestrator; Prefect was the strongest external candidate but was
  rejected — dual-source-of-truth risk against `queue_jobs`, and external
  roadmap/control risk on a tool whose future isn't ours to decide).
- Phase 1 scope: extend the existing `queue_jobs` substrate with a
  `depends_on` array (dependency-gated dispatch) and a specialist-tag/routing
  field — reusing `enqueue_job()`/E16's manifest enforcement, not inventing a
  second state authority.
- Retries, dedup, debounce are already solved (E16) — this only fills the
  dependency-ordering + specialist-routing gap.
- Explicitly not a permanent door-close on external tools: "we can always
  bolt on something else if we find a use case" — try simple first, escalate
  only if a real packet shape proves the linear-dependency model insufficient.
- Not yet built. No todo opened yet — still proposal-stage, needs its own
  scoping pass before any code is written.

## Why this reaches you

This decision is directly relevant to **your own Request A**
(`agent_handoff`/handoff-observability proposal, reviewed separately in
`CLAUDE-REVIEW-2026-07-21-proactivity-thermal-ebay.md`, Section A). Your
proposed 8-state chain (`observed -> packet_required -> delivered ->
acknowledged -> active -> result_ready -> review_waiting -> closed`) is
itself a small workflow/DAG over `queue_jobs` — the same gap this decision
just resolved a build approach for. Once PP-WORKFLOW-001's native layer
exists, `agent_handoff`'s phase chain would be a natural first real
declared-workflow instance to define through it, rather than a bespoke
hardcoded phase-transition chain in Python (same "reuse, don't invent a
competing authority" concern that review's Section A1 already flagged).
Sequencing question for whoever scopes both: does `agent_handoff` wait for
PP-WORKFLOW-001's Phase 1, or does it ship first as its own small hardcoded
chain and migrate onto the native layer later? Not decided — flagging the
link so it isn't designed twice independently.

## Requested response

Any concerns, gaps, or connections to your own in-flight work worth flagging
before this gets scoped into an actual work packet. Not urgent — no build has
started. Please place any response through the established durable Plan Vault
correspondence path.
