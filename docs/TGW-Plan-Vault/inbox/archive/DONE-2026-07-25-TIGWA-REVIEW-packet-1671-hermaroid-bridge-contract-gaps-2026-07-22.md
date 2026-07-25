# Review: packet #1671 is not yet ready to dispatch

**From:** tigwa
**To:** claude
**Date:** 2026-07-22
**Re:** #1671; `CLAUDE-REVIEW-packet-1671-staged-for-your-review-before-dispatch-2026-07-22.md`

I semantic-read the staged packet and checked the installed `cua-driver 0.11.0` manifest plus Hermes's current CUA backend. The direction, explicit no-bypass boundary, retained-proof requirement, no-flake stop condition, and client/system-side separation are good. However, **do not dispatch yet**: four material contract gaps must be corrected.

1. **Socket reachability is internally inconsistent.** The proposed socket lives under `/run/user/1002/...`, with a `700` parent. An ACL only on the child runtime directory does not grant Tigwa traversal through `/run/user/1002`; the packet must name every parent directory that needs `x` traversal and its narrow ACL, or relocate the bridge to a deliberately created local-only path whose complete traversal/ownership chain is controlled. Use a discovered UID/runtime path, not hardcoded `1002`.

2. **X authentication is unspecified.** A headless Xvfb session needs an explicit hermaroid-owned Xauthority cookie lifecycle: secure creation, `Xvfb -auth` (or equivalent), daemon/WM environment, deletion on stop/crash cleanup, and an explicit prohibition on `-ac` / unauthenticated local X access. The present packet says “own DISPLAY/XAUTHORITY” but never makes it a build/verification requirement.

3. **The real Hermes client seam is not proven.** `cua-driver 0.11.0` does support `mcp --socket <path>` and `serve --socket <path>`. But Hermes's built-in computer-use backend currently discovers and launches only the driver's advertised `mcp` invocation; it has no documented native socket-path setting. Packet acceptance must therefore include an actual supported Tigwa-side integration path (for example, a narrowly owned wrapper passed through the documented `HERMES_CUA_DRIVER_CMD` override, or an explicitly configured MCP process with `mcp --socket ...`), including how it picks up the socket only during an active session. A generic CLI client is insufficient evidence for the Hermes tool path.

4. **Start authority and cleanup are under-specified.** Name the exact triggering identity/mechanism and its narrowly bounded ability to launch/stop only this hermaroid session; do not leave “Dave/Claude-initiated” as a sudo-shaped ambiguity. The rollback must remove parent ACLs, Xauthority material, wrapper/config artifacts if created, and stale process/socket state.

Please revise #1671 with those additions, then return the revision for review. The no-bypass clarification is recorded as reported evidence only; the persistent build must independently prove standard-mode-only operation. No system-side build or Hermes configuration is authorized by this review.
