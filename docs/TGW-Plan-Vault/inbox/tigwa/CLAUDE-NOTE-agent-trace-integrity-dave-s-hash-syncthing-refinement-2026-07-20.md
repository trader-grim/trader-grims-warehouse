# Note: Agent-trace integrity — Dave's hash+Syncthing refinement, Tigwa-lite monitoring requirement

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T15:11Z
**Todo:** #1586

Addendum to yesterday's authenticity review response (todo #1585) — Dave weighed in directly in-session 2026-07-20 with two refinements, both now drafted as a design packet (docs/TGW-Plan-Vault/plan/packets/1586-agent-trace-integrity-hardening.md, todo #1586, DRAFT — not yet authorized for build):

1. Small, hash-verified writes so changes can be detected — converges with my "lock on first archival" proposal, refined: a separate small/atomic content-hash-commitment record (its own insert-only table, sha256 + committed_at) written right after the transcript copy, rather than a mutable column. A second commitment attempt for the same run_id is rejected outright — that's the actual lock. Cheap to verify later: re-hash the archived file, compare.

2. Syncthing file versioning to an external write-only-from-the-agent's-perspective location — since Syncthing already runs dual-instance on tgw-prod/a1131, this is a new folder (not new infrastructure): tgw-prod's agent-traces/ folder set Send Only, a1131's matching folder set Receive Only with Staggered versioning. The traced agent has no credentials to reach a1131's filesystem or Syncthing config at all, so even a compromised tgw-prod overwriting/deleting a transcript propagates the change, but the prior content survives in a1131's .stversions/ — real independent-witness property. This leg is a ~/tgw-flake change, nix-flake-maintainer's to execute once separately authorized, not tgw-coder's.

3. Dave also asked to add monitoring by Tigwa-lite. Per the existing Claude/Tigwa role boundary, I'm handing this to you as a requirement, not designing it myself: a periodic (matching your existing scheduled-plan-review cadence) reconciliation pass over agent_runs + the new hash-commitment table once Leg A lands — flag stale/unclosed running rows, hash mismatches, missing commitments. Notify-only, no mutation/correction authority, same pattern as your thermal-monitoring role's 3 notify-only legs. Scoping and building this is yours per the HR-001 senior-architect-consult pattern (you design, we review after) — this is just naming the requirement Dave asked for.

Packet 1586 is design-only right now — I have not dispatched anything to tgw-coder or nix-flake-maintainer, and it explicitly stays paused (along with Phase 4) until Dave signs off on the concrete diff. Flagging back to you since this directly extends the review you opened.
