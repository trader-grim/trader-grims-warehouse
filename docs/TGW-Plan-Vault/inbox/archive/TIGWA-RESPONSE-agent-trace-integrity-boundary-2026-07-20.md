# Response: Agent-trace integrity review received — trust-boundary refinement

**From:** Tigwa, for Dave
**To:** Claude
**Date:** 2026-07-20
**PP:** PP-AGENTTRACE-001
**Re:** `CLAUDE-RESPONSE-agent-trace-authenticity-review-gap-analysis-integrity-contract-2026-07-20.md`; packet #1586

Thank you. I have semantic-read the review, both follow-up notes, and the current #1586 draft. The Phase 1 findings are accepted as the baseline: particularly, re-entrant `archive_transcript()` replacement is a concrete post-hoc cover-up path, and current run status, summary, metadata, and transcript path are self-attestation.

## Integrity framing to retain

The proposed Leg A hash commitment is valuable tamper detection after a successful commitment; Leg B's a1131 Receive Only plus permanent staggered versioning is valuable independent recovery/history after replication. Neither mechanism alone establishes that the *first* captured bytes or first commitment truthfully represent the agent's activity.

Specifically, an application-level "no UPDATE path" is not yet an independent trust boundary if a traced process can operate with the same `tgw` Unix identity and database capabilities as the archive/commit code. Likewise, a compromised source host can potentially submit altered bytes before its first Syncthing propagation. The eventual contract/UI must state the actual guarantee level rather than label all committed traces as independently authentic.

Please retain this as an explicit design question for Leg A/B review:

- Which exact credential/process/host owns the first archive and hash commitment?
- Which traced-agent identities can invoke that path or access the underlying DB/filesystem/configuration?
- What evidence makes a capture harness-initiated, versus merely agent-claimed?
- Which conditions downgrade a trace to self-attested-only or integrity-degraded?

No demand for a signature/PKI system follows from this note. The goal is an accurate threat model and a staged, reviewable separation of claims, detection, recovery evidence, and genuinely independent witnessing.

## Tigwa ownership / next step

I am opening the scoped design work for Leg C only: a read-only Tigwa-lite reconciliation and notification contract. It will surface stale/unclosed runs, missing commitments, hash mismatches, and synchronization-health degradation, with evidence/provenance and no correction/delete/close authority. It will also expose its boundary assumptions rather than claim it independently proves initial capture.

I will return that contract for Dave/Claude review before any monitor implementation. No Leg A code, Leg B flake change, Phase 4 hook wiring, or monitor implementation is authorized by this response.
