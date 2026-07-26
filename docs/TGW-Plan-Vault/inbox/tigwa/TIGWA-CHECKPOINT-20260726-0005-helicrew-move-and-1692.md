# Tigwa checkpoint — Helicrew move readiness and #1692 handoff

**From:** tigwa  
**Date (UTC):** 2026-07-26T00:05:49Z  
**Mode/provenance:** Live Hermes `tgw-exit` checkpoint, explicitly requested by Dave  
**PP / todo:** PP-HERMES-EA-001; #1692 is Claude/flake-maintainer work. All current Hermes session todos are completed.

## Concise close-out

Helicrew is the prepared primary Tigwa seat. It has persistent Hindsight memory, a local-only Hermes dashboard, boot-persistent user services, and a new revocable SSH identity to `tgw-prod` and `a1131`. The default Hermes gateways on tgw-prod and a1131 were disabled during the requested cutover; Helicrew's gateway is enabled. The dashboard is verified locally; Telegram adapter connection remains unconfirmed.

## Verified outcomes

- Helicrew: `hindsight.service`, `hermes-dashboard.service`, `hermes-gateway.service`, and `ydotool.service` are enabled and active under `tigwa`, with `Linger=yes`.
- Hindsight health: `{"status":"healthy","database":"connected"}`.
- Dashboard: local-only `http://127.0.0.1:9119`, HTTP 200. Node 22.23.1 was installed under `~/.local` only to build the dashboard; no host/Nix change was made.
- New dedicated Helicrew key fingerprint: `SHA256:mNL582HgtLB+S5bQL793tKMqXQUj7ykOm/hxaQDkd6c`; named SSH routes to `db@tgw-prod` and `tigwa@a1131` were authenticated live.
- tgw-prod and a1131 default `hermes-gateway.service` units are disabled. The separate a1131 `hermes-gateway-t-lite.service` was left untouched.
- #1692 evidence: fleet-input local commit `a692acb` passes `nix flake check`; source-worktree override test reports 2756 passed, 4 skipped, 2 known-unrelated C12 failures, and zero collection errors. Push request `4cd3a02f-529d-465f-9947-62b119222b87` remains queued.
- Exact human Git push target was verified: `db@tgw-prod:/opt/TGW/var/worktrees/consolidated-nix-fleet-20260725`, branch `todo/consolidated-nix-fleet-20260725`, clean, `a692acb`, exactly one commit ahead of origin.

## Durable memory update

Hermes durable memory now records Helicrew as the primary Tigwa seat in the fleet topology.

## Open risks / blockers

1. Helicrew Telegram gateway service is active but has not yet logged a successful adapter connection after cutover. Direct HTTPS reachability to `api.telegram.org` was verified; do not use Telegram as the sole move-confirmation channel until a live inbound/outbound message succeeds. Dave elected to fix it only if it remains failed after cutover.
2. Do not use `sudo -u tgw tgw flake mark-executed ...` as given: live verification from `db` returns `tgw: command not found`, and the `tgw` account is non-login. Claude has been asked for the established service-runtime/wrapper path for the human-only receipt command.
3. The Plan Vault repository was already dirty before this checkpoint (taskboard modification plus untracked inbox/archive artifacts). This checkpoint did not commit, merge, push, modify flake/source/service/queue/catalog/eBay/production configuration, or alter tracker state.

## Exact next action

After Claude supplies the verified `tgw` receipt wrapper path, Dave should, from the verified tgw-prod worktree, run the approved Git push for `todo/consolidated-nix-fleet-20260725`, then use that exact wrapper to record `mark-executed 4cd3a02f-529d-465f-9947-62b119222b87 --by dave`. After the origin push, update the source-adapter lock and perform the separate independent clean-worktree review/test before any merge decision.

This is a checkpoint/continuation note, not a duplicate task and not evidence that the queued mutation has executed.
