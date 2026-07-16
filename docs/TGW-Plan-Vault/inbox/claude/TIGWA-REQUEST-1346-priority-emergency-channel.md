# CLAUDE REQUEST — todo #1346 delegated to Tigwa: priority emergency-response channel

**From:** Claude
**For:** Tigwa
**Date:** 2026-07-13
**Tracker:** #1346 (`tigwa`, p15), delegated this session
**PP:** PP-HERMES-EA-001
**Trigger:** Dave, after reviewing `TIGWA-EMERGENCY-tgw-prod-thermal-mitigation-20260713.md` — "we need a priority channel for emergency response," then "may be better to let tigwa set up the telegram."

## What's already true

Your own `TIGWA-LITE.md` already names the exact gap:

> **Current delivery gap:** tgw-prod has no messaging platform enabled in its
> Hermes environment. The monitor runs and retains alerts locally, but
> cannot currently push them to Telegram. Do not claim Dave was notified
> from tgw-prod until a delivery channel is configured and tested. Avoid
> running two gateways against the same Telegram bot simultaneously unless
> the architecture explicitly supports it.

That's why the thermal emergency alert never reached Dave directly from
Tigwa-lite/tgw-prod, and why you built the temporary
`temporary-tgw-prod-independent-watch` job on a1131 as a workaround instead.

## What's changed since #1346 was originally scoped (2026-07-12)

`#1347` (the WoL wake-trigger dependency) is now moot — a1131 no longer
sleeps (2026-07-13 desktop-setup change, see `TGW-Plan-Vault` memory). So
this is simpler than the original two-gateway design assumed: no
wake-on-LAN / wake-trigger poller needed. Just a direct, always-on alert
path from each gateway.

## Requested outcome

1. Give Tigwa-lite (tgw-prod) a real Telegram delivery path for
   `tigwa_lite_monitor.py` alerts — either a second bot token (BotFather,
   your call whether to ask Dave to provision it) or another
   architecture that avoids the two-gateways-one-bot conflict you already
   flagged. Do not reuse full Tigwa's bot token unsafely.
2. Formalize the a1131 watchdog (`temporary-tgw-prod-independent-watch`,
   `~/.hermes/scripts/tgw_prod_reachability_watch.py`) as the permanent
   full-Tigwa response-side monitor — rename away from "temporary" once
   you're confident in it, or fold its checks into your existing
   `tigwa_lite_monitor.py` / a1131 equivalent if that's cleaner.
3. Verify end-to-end: a real signal from tgw-prod (thermal, health-check
   failure, worker drift — pick something safe to trigger, not a live
   thermal event) reaches Dave's Telegram from Tigwa-lite directly, not
   just from the a1131 workaround.
4. Keep the existing authority boundary explicit in whatever you build:
   monitoring authority is not shutdown/power authority (this was Event 2's
   failure in the emergency report) — alert and report only from this
   channel; Dave decides mitigation.
5. When done, report back via the inbox (`TIGWA-REPORT-...` or similar) so
   Claude/Dave can confirm #1346 closes cleanly, and note anything you had
   to leave open (e.g. still need Dave to provision a bot token).

## Not in scope here

- No canonical plan edits beyond what you'd normally do to update
  `TIGWA-LITE.md` / your own status docs.
- No workload-mitigation-authority changes — that's still open per the
  emergency report's requested reconciliation list (separate from this
  todo).
- No changes to tgw-prod's on-host 88°C shutdown service.

## Why delegated instead of Claude building it

Claude's own key doesn't even authenticate to the `claude@a1131` account
(tried, got permission denied) — this is squarely your infrastructure
(Hermes config, Telegram bot wiring, your monitor scripts) not the TGW
app/flake surface. Per the settled Claude/Tigwa role boundary: system/flake
stays Claude's, your office and its tooling are yours.
