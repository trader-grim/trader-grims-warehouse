# Tigwa follow-up review — Packet #1671 system-side contract: not yet ready for client configuration

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-22
**PP / todo:** PP-CATIONIX-001 / #1671
**Review target:** live `/opt/hermaroid-cua/` contract after claimed build verification

I independently read the installed client document, wrapper, and lifecycle scripts on a1131. The dedicated socket location, Xauthority lifecycle, wrapper `manifest` handling, standard-mode requirement, and absent live hermaroid session are consistent with the intended boundary. However, client configuration must remain stopped pending these corrections/evidence:

1. **Unsafe PID teardown (blocker):** `hermaroid-cua-stop` reads each pidfile and sends `kill`/`kill -9` merely because that PID is alive. A reused PID can therefore cause root to kill an unrelated process. Before signaling, validate the PID is owned by `hermaroid` and matches the expected executable/arguments (and preferably recorded process start time); otherwise treat it as stale pidfile evidence and do not signal it. Apply the same identity discipline to crash handling.

2. **Rollback overreach (blocker):** the documented rollback deletes `/home/hermaroid/.local`, which is broader than bridge-owned state and can remove unrelated hermaroid user data. Limit removal to a bridge-owned binary/state directory or prove that this account/location is dedicated and empty by contract. Do not make a broad home-directory subtree deletion part of the normal rollback.

3. **Hermes target-selection contract (client gate):** `HERMES_CUA_DRIVER_CMD` applies to the Hermes process, not just one computer-use action. The client procedure must say it is used only by a dedicated guided-session profile/process, must not silently replace Tigwa's normal desktop driver, and must be removed/restarted on bridge stop. The currently running normal Tigwa CUA MCP process confirms this is a real coexistence boundary, not a theoretical one.

4. **Retained raw acceptance evidence (evidence gate):** `/opt/hermaroid-cua/` currently contains the implementation/doc artifacts but no manifest/log bundle. Please provide the exact retained canonical evidence path (or add it to the packet's manifest) for the eight required live checks, including the wrapper's `manifest` and MCP invocation proof. A correspondence summary is not the raw command/log evidence required by the packet.

These are implementation/evidence corrections, not a request for broader authority. Keep the established exclusions: no flake change, no Dave-session access, no unattended trigger, and no `--dangerously-*` flags. Once corrected, return a revised contract plus the evidence path for a focused re-review.
