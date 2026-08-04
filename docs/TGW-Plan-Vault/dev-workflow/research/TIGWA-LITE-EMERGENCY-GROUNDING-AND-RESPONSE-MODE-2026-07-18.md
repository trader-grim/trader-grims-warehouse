# Tigwa-lite emergency grounding and response-mode proposal

**Date:** 2026-07-18  
**Owner:** Tigwa  
**Linked work:** #1385 / #1346 / #1382  
**Status:** proposed training/operating design — no implementation or authority expansion

## Intent

Dave’s desired behavior is not a permanently larger or more autonomous T-Lite. In normal operation it is the inexpensive, quiet monitor: like a NASA engineer watching a stable instrument panel, it should not invent work around a flat line.

A verified anomaly should cause a deliberate **mode transition**: the engineer sees the blip, sits forward, gathers the bounded evidence, follows the pre-directed emergency response, and makes the next responder materially more effective. If no other responder is reachable, it may troubleshoot and help **inside its explicit emergency capability contract**.

This is “a little smarter” by being grounded, responsive, and prepared—not by gaining unconstrained authority.

## Existing policy that remains controlling

`reference/runbooks/thermal-emergency-response.md` is formal policy. Its thermal authority boundary remains unchanged:

- T-Lite watches, verifies, notifies, and preserves.
- It never pauses, kills, throttles, reboots, or powers off workloads/hosts.
- It never starts a Claude session in an emergency.
- The on-host 88°C shutdown service remains the sole automatic power-control authority.
- Any mitigation beyond the ordered response requires Dave’s explicit real-time approval.

The existing ordered thermal actions are: notify an already-active Claude session if discoverable; verify the on-host backstop; reach Dave; do not auto-open Claude on a1131; and babysit/verify the Btrfs snapshot when unattended.

## Three operating modes

### 1. Quiet watch (normal)

Use deterministic scripts and state files. Stay silent on healthy unchanged state. No model call is needed merely because a scheduled check ran.

### 2. Grounded alert (verified state transition)

On a canonical transition or a detector failure that the runbook classifies as urgent, assemble an **emergency evidence packet** before composing any natural-language alert. The packet should contain only current, timestamped facts:

- triggering signal, prior state, current state, and trend;
- relevant service/status-file freshness and the checker-of-checker result;
- which alert legs were attempted, reached, failed, or were unavailable;
- snapshot request/result/verification evidence where applicable;
- runbook identifier/version/hash and the exact ordered actions already attempted;
- explicit authority envelope and prohibited operations;
- the next safe diagnostic question or human decision.

The alert must distinguish `observed`, `attempted`, `confirmed`, `unavailable`, and `unknown`. It must not attribute root cause during the incident unless a pre-approved diagnostic establishes it.

### 3. Assisted emergency response (no human/agent available)

If no active Claude session is discoverable and Dave cannot yet be reached, T-Lite does not become a free-running incident commander. It continues the pre-directed response and may perform the narrow **assist set** below:

1. Repeat read-only evidence collection on the approved cadence and retain the incident timeline.
2. Re-check that the 88°C on-host shutdown backstop is alive; elevate the wording if it cannot be confirmed while temperature is elevated.
3. Retry configured notification paths with dedup/rate limits.
4. Trigger, babysit, and verify the policy-authorized snapshot path; state plainly if verification fails.
5. Produce a compact decision packet for Dave/a later responder: state, trend, evidence, actions attempted, remaining safe options, and prohibited actions.

This mode is useful troubleshooting and preservation. It does **not** include discretionary workload/process/power control, starting agents, guessing root cause, writing production data, changing config, or enlarging access during the incident.

## Grounding design

Emergency cognition should be a constrained function, not an open-ended chat:

```text
incident packet + named runbook + allowed actions ->
  ordered actions, clear alert, evidence timeline, decision packet
```

Requirements:

- The runbook and authority envelope are injected as immutable context for the incident.
- Commands are named, parameter-bounded capability functions—not arbitrary shell construction.
- The response has a fixed schema: severity, facts, actions completed, failures, authority boundary, next decision.
- Any unsupported claim must be rendered `UNKNOWN`, not inferred.
- Every model-generated recommendation is advisory unless it maps to an already-authorized named action.
- If the model is unavailable, the deterministic ordered response and alert path still work.

## Model and cost posture

The deterministic path must cover first response. A model is an optional second-stage aid for summarizing a packet, correlating pre-approved evidence, and creating an intelligible handoff.

Use a cheap selected provider/model only after the packet is assembled; an incident must never depend on a paid long-context call or a particular provider being available. A higher-effort/long-context model may later be selected for post-incident review, but not as a prerequisite to safety actions.

## Training and proof before enabling assisted mode

1. Build synthetic/replayed incident packets from the 2026-07-13 thermal event and benign lookalikes.
2. Test each mode transition, including stale status, unreachable Dave, no active Claude, unresponsive backstop, snapshot failure, and duplicate alarm suppression.
3. Assert negative capabilities: no generated or dispatched action can pause/kill/throttle/reboot/power off, start an agent, mutate production state, or bypass named capabilities.
4. Review alert clarity with Dave: an alert should let a tired human recognize the blip, safety-net condition, preservation status, and exact decision needed in seconds.
5. Run a labelled drill before production enablement; retain timing/evidence and correct gaps.

## Review questions

1. Is the assisted-mode boundary correctly limited to watch/verify/notify/preserve plus bounded troubleshooting?
2. Which non-thermal runbooks, if any, should receive this same packet-and-assist pattern after thermal v1 proves out?
3. Which named diagnostics may be safely added to the assist set without creating an arbitrary-shell or mitigation capability?
4. What level of human acknowledgement should change the cadence/verbosity, without being confused with authority approval?

## Non-actions

This proposal authorizes no profile/gateway/config change, model call, SSH credential change, raw-shell expansion, production change, or change to the formal thermal runbook. It is a reviewable design input for #1385.
