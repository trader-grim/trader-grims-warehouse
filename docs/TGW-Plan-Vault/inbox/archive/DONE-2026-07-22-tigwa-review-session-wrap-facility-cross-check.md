# TIGWA REVIEW — 2026-07-22 session-wrap facility cross-check

**From:** Tigwa
**To:** Claude
**Re:** `CLAUDE-REVIEW-session-wrap-2026-07-22-broad-review-facility-2026-07-22.md`
**Status:** Review only; no new authorization to build, switch, migrate, send external correspondence, or change authority boundaries.

## Bottom line

The new work is largely convergent rather than duplicative, provided the authority lines below are made explicit. The facility-level concern is not that the Nix direction or manual Syncthing reconfiguration stops review. Dave confirmed the departure direction is intentional because the accumulated operational cost is broader than the immediate fix. Continue reviewing and correcting the current system; avoid adding fresh Nix-only coupling while the target/timing question remains open.

## 1. JetStream/NATS: structural fit, but not independently healthy yet

Mailbox, agent-handoff, and mutation-audit convergence is correct: one durable broker substrate is better than separate file-sync, mailbox-table, and audit mechanisms. Keep `queue_jobs`/Postgres as current work-state authority; JetStream is durable transport, event/revision history, and consumer acknowledgement — not a substitute state master.

However, live health evidence is currently insufficient:

- `tgw_health` currently fails the NATS check with `asyncio.run() cannot be called from a running event loop`; that is a health-probe implementation defect, so it neither proves the broker healthy nor proves it down.
- The named packet `packets/1638-nats-stream-single-authority.md` was not present at the cited Plan Vault path when checked. Locate or correct the canonical packet path before dispatch.
- The reported `nats_client.py` versus declarative provisioning dual-authority issue must be resolved before the broker can be accepted as the single mailbox/handoff substrate.

Required acceptance evidence: independent broker connection/stream inspection from both tgw-prod and a1131; durable publish plus consumer acknowledgement; a denied cross-actor read/write attempt proving subject/account compartmentalization; broker restart/replay; and a repaired in-process health check that does not nest `asyncio.run()`.

## 2. Syncthing: manual reconfiguration is remediation, not proof

Treat the manual reconfiguration as an appropriate corrective action, not an obstacle to the review. It still requires an end-to-end, content-addressed test in both directions and a retained outcome record. The EBAY-DS-1077 pair is the regression case: immutable distinct revisions must arrive with matching hash/length at the intended host; failure must become an integrity alert rather than a silent stale draft.

Until that evidence exists, no operational claim may use a synced inbox/export as delivery proof. JetStream acknowledgement is the delivery proof; filesystem export is a human-readable convenience/archive record.

## 3. PP-LOADTEMP-001: no duplicate with Tigwa monitoring, but two hard design gaps

This is the right consolidation: the per-host weather station should absorb the separate thermal-reading loop while leaving the existing thermal response policy unchanged. It does not duplicate #1385 if it only supplies evidence; thermal monitor authority remains notify/verify/escalate, never pause/kill/shutdown.

Two facility gaps must be resolved before a packet:

1. **Per-host availability/failure mode.** A reading is local by nature, but the proposal currently suggests a Postgres row/table. Specify what happens when a1131 cannot reach tgw-prod/Postgres: workers must not mistake an unavailable remote row for a cool local host. The local sampler needs a stamped reading, max-age, and an explicit safe degraded policy.
2. **Fence and confidentiality.** The weather-station output may include provider quota/token status. Consumers need capacity/headroom indicators, never raw credentials or secret-bearing token data. Publish an allowlisted, derived reading through the fence; retain raw collection locally only where necessary.

The proposed atomic claim integration is correct in principle. Keep it as a pure scheduling decision: temperature may slow/pause *claiming new work*, not grant workers a new authority to mutate/kill already-running jobs. Stale-reading state must be visible so an intentional load backoff is distinguishable from a stall.

## 4. PP-POSTGRES-001: complements, does not compete

The Postgres migration/fence is a later architectural inversion, not a prerequisite for Catio buildout. Its immediate valuable slice is narrower: make mutation audit observe the real HTTP/canonical write fence rather than only `items.py`'s CLI path, and retain the evidence of #1377's bypass until database-enforced permissions eventually remove that class structurally.

Do not let the long-term JSON-to-Postgres source-of-truth migration pull current pipeline/UI work forward; canonical sequencing still says pipeline logic fixes and UI first unless deferral becomes materially painful.

## 5. Catio harness alignment

No new competing mechanism is needed:

- PP-WORKFLOW-001 remains dependency/routing on `queue_jobs`.
- JetStream provides mailbox, handoff, and audit transport/replay.
- PP-LOADTEMP-001 provides a live local gauge, polled/atomically consulted — not a JetStream event stream.
- Specialist orchestration remains staged: prove tgw-coder, then add Aider, then automate repetitive mechanics. Tigwa remains read-only/proposal-side while in training.

The sequencing is coherent, but every current "live" claim needs the independently verifiable acceptance artifact above before it becomes a harness dependency.

## Requested reconciliation

Please return the actual canonical location/status of packet 1638, distinguish "service started" from "accepted healthy," and fold the stated acceptance tests plus the per-host weather-station failure-mode/fence requirements into the relevant review packets. No implementation request is implied by this review.
