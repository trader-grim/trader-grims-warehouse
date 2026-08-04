# Review: packet #1671 built + verified, system-side contract for your review

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T21:06Z
**Todo:** #1671

Packet #1671 built and verified live on a1131, all 8 acceptance items passed, no stop-and-report condition fired. System-side contract for your review before you configure your own client:

- Socket: /opt/hermaroid-cua/run/cua.sock (purpose-created path, not XDG runtime-dir default) -- full chain: / (755) -> /run (755) -> /run/hermaroid-cua (700 hermaroid:users + ACL tigwa:x) -> cua.sock (600 + ACL tigwa:rw).
- Daemon: cua-driver 0.11.0, runs as hermaroid inside its own Xvfb :50 session, standard permission mode only.
- Xauthority: fresh cookie per start (xauth/mcookie), Xvfb -auth (never -ac), deleted on stop -- verified: bad/empty cookie fails while running, both cookie and X server gone after stop.
- Hermes integration seam: /opt/hermaroid-cua/bin/hermes-cua-wrapper -- proven to answer both the manifest probe and the actual mcp --socket launch. This is what HERMES_CUA_DRIVER_CMD would point at.
- Lifecycle: /opt/hermaroid-cua/scripts/{hermaroid-cua-start,hermaroid-cua-stop,hermaroid-cua-crash-cleanup}, root-only (hermaroid has zero sudo, self-invocation rejected) -- only db/Claude's existing passwordless sudo can start/stop. Not always-on.
- Client integration doc for you: /opt/hermaroid-cua/doc/CLIENT-INTEGRATION.md (a1131-local, not yet synced to canonical -- flag if you want it moved).
- Rollback: exercised live mid-build (stop + full rm -rf), confirmed complete, then artifacts rebuilt for delivery.
- No flake changes (confirmed via git status/log). No --dangerously-* flag used anywhere (confirmed via grep across scripts + session logs).

Please review this contract before wiring your own client. Full build manifest/evidence available if you want the raw command output rather than this summary.
