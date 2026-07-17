Working todo #1346 (PP-HERMES-EA-001, p15) directly in the main tgw-prod
checkout (no worktree — this touches live Hermes-lite gateway config +
a1131 watchdog, not app code). Task: build the priority emergency-response
channel — Tigwa-lite (tgw-prod) sends first-danger Telegram alert, full
Tigwa (a1131) is the response channel. Triggered by Dave 2026-07-13 after
the TIGWA-EMERGENCY-tgw-prod-thermal-mitigation-20260713.md report showed
Tigwa improvising a *temporary* a1131 watchdog job
(temporary-tgw-prod-independent-watch, ~/.hermes/scripts/tgw_prod_reachability_watch.py)
because #1346 was never actually built. #1347 (WoL wake-trigger) is now
moot — a1131 no longer sleeps (2026-07-13 desktop-setup change) — so this
is simpler than originally scoped: no wake-trigger needed, just direct
always-on alert delivery. Plan: confirm Tigwa-lite's Telegram bot is live
on tgw-prod, formalize the a1131 watchdog job as the permanent full-Tigwa
monitor (rename away from "temporary"), and verify the two-bot topology
end-to-end.

## Resolution (2026-07-13, same session)

Investigated: confirmed the gap is real (TIGWA-LITE.md's own "Current
delivery gap" note — Tigwa-lite has no Telegram delivery configured on
tgw-prod). Attempted to inspect a1131's existing Telegram wiring via
`ssh claude@192.168.60.101` to plan the second-bot setup directly —
permission denied (Claude's key isn't authorized for that account there).
Dave then redirected: "may be better to let tigwa set up the telegram."
Delegated #1346 to tigwa via `tgw todo --delegate 1346 tigwa` and dropped
`TIGWA-REQUEST-1346-priority-emergency-channel.md` in the inbox with full
context + requested outcome. Not building this myself — it's Hermes/Telegram
config on Tigwa's own infrastructure, not the TGW app/flake surface.
