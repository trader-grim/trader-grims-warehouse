# Thermal Emergency Response Policy — Tigwa-lite monitor

**Status:** formal policy, effective 2026-07-14. Governs Tigwa-lite's automated
response to a tgw-prod thermal event. This is what her monitor upgrades
against — treat every numbered action below as a literal instruction, not
a suggestion to interpret.

**Origin:** the 2026-07-13 tgw-prod NVMe thermal CRITICAL incident
(`inbox/TIGWA-EMERGENCY-tgw-prod-thermal-mitigation-20260713.md`) exposed two
gaps — Tigwa's monitoring didn't alert (Event 1), and Tigwa took an
unauthorized power-control action without Dave's approval (Event 2). This
policy closes both: it defines what the monitor watches, and draws a hard
line the monitor may never cross on its own authority. Companion decision:
`pp/PP-HERMES-EA-001.md`'s "Thermal emergency response" section (the 3-leg
notify design + authority grant for todo #1382).

## The authority boundary — read this first

**Tigwa-lite's monitor role is watch, verify, notify, and preserve. It is
never workload-control or power-control.** This is unconditional — no
threshold, temperature, or duration escalates the monitor into an actor
that pauses, kills, throttles, reboots, or powers off anything, on
tgw-prod or any other host, including Claude's own session. The one
exception, unchanged from before this policy: the existing **on-host
automatic 88°C shutdown service** remains the sole automatic power-control
authority. The monitor never replicates, overrides, or second-guesses it —
it only checks that it's still alive (action 2 below).

Any actual mitigation beyond the ordered actions below requires Dave's
explicit, real-time approval.

## Trigger

Any `thermal.status` transition to `HOT`, `THROTTLE`, or `SHUTDOWN`
(or the underlying SMART/sensor read the monitor already uses, whichever
fires first) starts this response. Re-entrant: if the state is still
elevated, keep repeating the notify/verify cadence already established
(existing chronic-warning suppression rules apply — don't spam).

## Ordered response

Execute in this order. Each action is independent — a failure or "can't
reach" at one step does not block starting the next one; they proceed in
parallel where the monitor's architecture allows it, but priority order
here reflects urgency and default action if resources are constrained.

### 1. Notify Claude, if a Claude session is active — do NOT start one

Leg 3 of the 3-leg design (`PP-HERMES-EA-001.md`): tmux interrupt into
Claude's active pane, if and only if one exists and is discoverable.

**If no Claude session is active or discoverable: do not start one.**
This is a hard rule, not a judgment call for the monitor to make case by
case — an unsupervised agent session spun up mid-incident, with nobody
directing it, recreates the exact risk this policy exists to prevent.
Move on to the remaining actions instead; a snapshot-babysitting monitor
script needs no LLM judgment to do its job.

What Claude is expected to do with the notification (throttle its own
activity, investigate root cause if warranted) is Claude's own Prime
Directive 2 obligation — not something Tigwa verifies, enforces, or acts
on if it doesn't happen.

### 2. Verify the on-host thermal monitor is responding correctly

Before anything else, confirm the local 88°C automatic shutdown service
is alive and functioning — this is checking the checker, and it's the
single most safety-critical verification available, since it's the actual
backstop if every other leg of this response fails or can't reach anyone.

**If the on-host monitor appears unresponsive or its status can't be
confirmed while temperatures are elevated: treat this as a distinct,
higher-urgency escalation** from a routine temperature alert — the safety
net itself may be down. Say so explicitly in the notification to Dave
(action 3), don't fold it into the routine alert text.

### 3. Try to reach Dave

Telegram, already built. Standard escalation path, no change from the
existing design.

### 4. Do NOT autonomously open a Claude session on a1131

Dropped from the automated response (Claude's recommendation, Dave
concurred 2026-07-14): opening a fresh, unsupervised Claude session on
a1131 mid-incident doesn't help tgw-prod directly and reintroduces the
same "unsupervised actor during a crisis" risk as action 1's "don't
auto-start" rule — it would be inconsistent to forbid one and allow the
other. The monitor may **mention in its notification to Dave that this
option exists** (a1131 has read-only NFS views of tgw-prod's data/logs
for exactly this kind of remote look-in) — but only Dave, deciding with
real context in the moment, starts that session himself. The monitor
never does.

### 5. Babysit the btrfs snapshot — the default action when nobody is actively responding

This is the single highest-value, lowest-risk automated action available,
and the one the monitor should treat as its actual job during an
unattended incident (nobody active on action 1, Dave not yet reached on
action 3): **ensure a snapshot is taken and verified,** using the same
cool-boot-immediate-snapshot pattern proven live during the 2026-07-13
incident (`.snapshots/<stamp>` → verified read-only Btrfs subvolume →
received copy at `TGW-SNAPSHOT-0/<stamp>`, UUID linkage checked).

This directly serves Prime Directive 1 (the local dataset IS the
business) — if the host doesn't survive the thermal event, a good, recent,
verified snapshot is what actually matters, more than any other action
available to an unattended monitor.

**If the snapshot itself fails, or its integrity can't be verified, while
the host is thermally critical: escalate this harder than a normal
alert.** This is the scenario where the asset itself is genuinely at
risk — the alarm text should say plainly that snapshot verification
failed during a critical-temperature event, not just report a routine
health-check failure.

## What this policy does not cover

- The mechanics of leg 3 (tmux pane-discovery, dedup/rate-limiting) —
  still open build questions, tracked in todo #1382.
- The Android/Tasker alarm leg (todo #1375) — separate build.
- Non-thermal incidents (worker crash loops, quota 429s, etc.) — those
  follow the existing runbooks in this directory; this policy is thermal-
  specific.
- Root-cause attribution of what's driving the heat (process/I/O
  attribution) — explicitly flagged in the 2026-07-13 incident report as
  "not yet proven," out of scope for the monitor to determine live during
  an active event; that's post-incident investigation work.

## Verification this policy is being followed

- The monitor's own logs/alert history should show, per triggered
  incident: whether a Claude session was found and notified (or correctly
  found none and did not start one), the on-host monitor's confirmed
  status, the Dave-reach attempt and outcome, and the snapshot
  action/verification result.
- No log entry should ever show the monitor pausing/killing a process,
  power-cycling any host, or starting a Claude session — if one does,
  that's a policy violation to report to Dave immediately, the same
  severity as the original Event 2 overstep.
