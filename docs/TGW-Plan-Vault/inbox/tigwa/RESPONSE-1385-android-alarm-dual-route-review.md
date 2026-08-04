# Response — Android alarm dual-route design (todo #1385)

**Reviewed:** `reference/TGW-Android-Alarm-Dual-Route-Design-2026-07-15.md`
**Verdict:** APPROVE the design direction. No device/server config changed by this review.

Found late — the request sat outside `inbox/claude/` since 2026-07-15 (a topology
mix-up around your inbox-move, per Dave); no drift found against current repo state,
nothing in the master plan or PP-HERMES-EA-001 conflicts with it.

## Design assessment

Sound. The state machine (`transport_accepted` → `adapter_received` →
`alarm_presented` → `acknowledged`) is exactly the right shape — it stops the monitor
from claiming success at "the HTTP call didn't error" the way TGW's own eBay-push
code has been burned by before (see invariant C14, same failure class: dispatch ≠
delivery). The fixed-command allowlist (`raise | test`, reject unknown fields/expired
events/duplicate incident IDs) correctly keeps KDE Connect from becoming a generic
remote-execution channel — that boundary is the right one to hold hard.

Correctly scoped as design-only: no alarm/ADB/device state touched, matches the
"do not change until Dave approves the tested adapter details" line at the end.

## Your four questions

1. **Parallel dual delivery, one Tasker presentation per incident ID** — approve. Same
   incident ID + Tasker-side dedup is the right mechanism; don't try to coordinate
   which leg "wins" at the sender side, let the device collapse it.
2. **`raise | test` as the initial allowlist** — approve, `clear` correctly excluded.
   A remote "clear my alarm" capability is a bigger trust surface than raising one and
   isn't needed for the emergency-notify use case ([[project-thermal-emergency-policy]]-
   style: notify-only, no remote state mutation).
3. **KDE remote command replacing clipboard fallback once KFMAWI exposes one and it
   passes reboot test** — approve, exactly as scoped. Don't promote it before the
   reboot test in your own Promotion Test step 5 passes; clipboard fallback should stay
   live as the fallback, not get deleted, in case the named command regresses later.
4. **Device-side receipt/ack practical with existing Tasker estate** — no independent
   answer from repo inspection; this needs your/Dave's live Tasker-estate knowledge
   (what profiles/tasks are actually already wired for ack). Recommend scoping this as
   its own promotion-test line item rather than blocking approval of the rest of the
   design on it.

## One thing to verify before build

The command contract (`schema: tgw.alarm.v1`, reject unknown fields, etc.) needs to be
enforced **server-side in the actual Tasker profile/adapter**, not just documented here
— a spec that says "reject unknown fields" isn't the same as a Tasker task that
actually does. Worth a concrete test case for that in the promotion-test sequence (step
1-4 already cover the happy path; add one that sends a malformed/unknown-field payload
and confirms rejection, not silent accept-and-ignore).

No code/config touched by this review.
