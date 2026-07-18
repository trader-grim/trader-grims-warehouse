# Clarification — T-Lite as independent emergency contact path

**Date:** 2026-07-18  
**Clarifies:** `TIGWA-LITE-EMERGENCY-GROUNDING-AND-RESPONSE-MODE-2026-07-18.md`  
**Linked work:** #1385 / #1346  
**Status:** Dave-directed requirement; no implementation authorization

## Why this matters

T-Lite is not only an extra monitor. There will be incidents in which the a1131/full-Tigwa host, its gateway, an active agent session, or the connection to them is unavailable. In that case T-Lite on tgw-prod may be Dave’s only live point of contact.

The design must therefore treat **isolation from full Tigwa/a1131 as an expected emergency condition**, not as a reason to stop at a failed handoff.

## Required behavior in isolated mode

When the verified incident coincides with inability to reach full Tigwa/a1131 or an active responder:

1. T-Lite continues its own direct Telegram/Dave notification route; it must not depend on relaying through full Tigwa.
2. Its first message states the situation plainly: incident severity/state, that the full response path is unreachable or unknown, the thermal-backstop status, and preservation/snapshot status.
3. It continues the formal watch/verify/notify/preserve response, records an evidence timeline locally, and sends material state changes/recovery—not empty repeated heartbeat chatter.
4. It includes the most useful compact decision packet in each escalation: current trend, confirmed facts, actions completed/failed, what cannot be confirmed, and the exact decision that would require Dave.
5. It makes no attempt to compensate for isolation by starting a new agent, widening credentials, using arbitrary shell commands, or assuming power/workload authority.
6. If its own Telegram egress cannot be confirmed, it records that failure and uses any separately configured local alarm/ack path only as an additional human-annunciation leg; it must not claim Dave was reached.

## Design consequence

T-Lite’s emergency packet, runbook excerpt, named safe diagnostics, notification configuration, and snapshot-verification path must be locally available to the tgw-prod account/profile. The critical first response cannot require a live query to a1131, a Claude session, a remote MCP server, or an expensive model provider.

This makes T-Lite a small independent incident station: normally quiet, but capable of standing watch and giving Dave a truthful, actionable picture when the larger system is absent.

## Verification additions

Before enabling assisted mode, drill at least these isolated-path cases:

- a1131/full-Tigwa endpoint unavailable while a simulated elevated thermal state exists;
- no discoverable Claude/agent session;
- direct Telegram send succeeds and is received by Dave;
- direct Telegram send fails while the local Tasker/annunciator path is available or unavailable;
- the incident packet remains complete using only T-Lite-local state/runbook/capabilities;
- no prohibited operation is attempted in either isolation case.

## Non-actions

This clarification does not authorize deployment, credentials, Telegram changes, an alarm trigger, a model call, raw-shell access, or any mitigation authority. It refines the required resilience and test conditions for the existing #1385 design.
