# Request: provision the dedicated `tigwa` OS account on tgw-prod

**From:** Dave via Tigwa
**Owner:** Claude
**Related project:** #1505 / #1346 — Tigwa-lite production cutover
**Date:** 2026-07-17

## Decision
Dave has chosen the proper production foundation: Tigwa-lite must run under a dedicated Linux account named `tigwa` on `tgw-prod`, rather than as a `db` profile.

## Authorized scope: account foundation only
Please provision and verify the OS account foundation on `tgw-prod`:

1. Create the primary group and Unix user `tigwa` if absent.
2. Create `/home/tigwa` with `tigwa:tigwa` ownership and a non-login/sensible service-safe shell appropriate to Hermes user services (document the choice).
3. Do **not** give `tigwa` blanket `sudo`/wheel/admin membership. Any future access must be deliberately scoped.
4. Enable user-systemd lingering for `tigwa` so a future user gateway can survive logout.
5. Verify the new account can run user services and can resolve the installed Hermes executable or document the exact environment issue if it cannot.

## Explicit exclusions
Do **not** yet:
- create or start a Tigwa-lite Hermes profile/gateway;
- copy Telegram, DeepSeek, SSH, or other credentials;
- move the currently tested `@TigwaLitebot` polling connection off a1131;
- modify the TGW source tree, workers, queues, database, eBay, or catalog;
- change the flake/Nix configuration;
- change existing `db` or `tgw` credentials/services.

## Evidence required
Return a compact completion artifact with:
- `getent passwd tigwa` and relevant group evidence;
- home directory ownership/mode;
- linger status;
- exact Hermes executable visibility/result for the `tigwa` account;
- any non-default access decisions or blockers.

This is a foundation handoff only. Deployment/cutover follows after review.
