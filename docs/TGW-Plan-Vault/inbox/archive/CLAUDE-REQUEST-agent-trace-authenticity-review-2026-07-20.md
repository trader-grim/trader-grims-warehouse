# Request: independent review — agent-trace authenticity and anti-cover-up controls

**From:** Tigwa, for Dave
**To:** Claude
**Date:** 2026-07-20
**PP:** PP-AGENTTRACE-001
**Context:** Phase 1 / todo #1580 is reported merged. This is a design-and-review request only; do not modify source, production data, trace storage, database schema, hooks, or configuration.

## Dave’s governing concern

Tigwa’s primary responsibility within this initiative is trace authenticity. An agent that has become faulty, compromised, or runaway could use a trace system as a cover-up vector: omit a run, begin recording only after consequential actions, truncate or replace its transcript, claim a false end state, or maintain an index that does not faithfully represent the raw evidence. Agent self-attestation is not proof.

## Evidence reviewed

The Phase 1 packet specifies atomic transcript archival to `/opt/TGW/var/agent-traces/<date>/<run_id>.jsonl` and an `agent_runs` Postgres index. It does not appear to specify a content digest stored independently of the transcript, a capture receipt/witness independent of the agent being recorded, tamper-evident or append-only evidence semantics, a provenance/signature model, or reconciliation between expected and observed run lifecycle events.

Atomic replacement protects against a partial write; it does not alone establish provenance or later authenticity.

## Requested review

Please return a concise, evidence-grounded review that:

1. Confirms, corrects, or expands the gap analysis against the actual merged Phase 1 implementation and the current PP.
2. Proposes a minimum viable integrity/authenticity contract for subsequent phases, separating:
   - agent-claimed trace/status;
   - independently witnessed capture/lifecycle evidence;
   - byte-verified archived trace;
   - integrity exceptions and investigation state.
3. Recommends how to produce or preserve independent evidence without giving ordinary agents authority to alter/delete/validate their own record. Address trust boundaries, identities, filesystem/database permissions, capture timing, hash/signature or append-only mechanisms, nesting/parent-child traces, and recovery when a host or agent dies mid-run.
4. Specifies reconciliation/detection checks for missing starts/ends, unexplained transcript gaps, byte/hash mismatch, duplicate or orphaned runs, post-hoc archive replacement, and index/raw divergence.
5. Identifies which controls belong in Phase 1 hardening versus later capture hooks, render/UI work, and operational auditing. Do not silently expand the present implementation scope; name review gates and any consequential tradeoffs.
6. Recommends what the Obsidian and `/form/runs` surfaces must visibly disclose so a polished summary cannot conceal unverified or anomalous evidence.

Please distinguish verified facts from design recommendations, cite exact current source/PP anchors, and return through the Plan Vault inbox. The desired outcome is a reviewable integrity proposal for Dave, not implementation authorization.
