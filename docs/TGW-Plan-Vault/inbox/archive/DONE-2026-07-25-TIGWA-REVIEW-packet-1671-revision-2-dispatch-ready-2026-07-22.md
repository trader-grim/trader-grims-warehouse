# Tigwa review — Packet #1671 revision 2 is dispatch-ready

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-22
**PP / todo:** PP-CATIONIX-001 / #1671
**Review target:** `plan/packets/1671-hermaroid-cua-bridge-build.md`, revision 2

I re-read the revised packet and confirm that the four blockers from my first review are now explicit, testable acceptance requirements:

1. Socket reachability requires either the complete parent traversal/ACL chain or a purpose-created path under the packet's control.
2. X authentication requires a fresh hermaroid-owned cookie, Xvfb `-auth`, no `-ac`, and deletion/failed-without-cookie evidence on stop.
3. Hermes integration must prove a real supported client seam rather than treating a generic CUA CLI proof as equivalent. The packet correctly requires the named seam to be exercised for the benign click/keystroke test.
4. Trigger identity, no-self-trigger boundary, stop/crash cleanup, and complete rollback are explicit and independently verifiable.

One implementation-sensitive verification point is preserved, not waived: an `HERMES_CUA_DRIVER_CMD` wrapper must support Hermes's initial `manifest` probe as well as the later MCP launch, and the live acceptance proof must demonstrate that exact path. If it cannot, stop and report rather than substituting a generic CLI proof.

**Disposition:** the system-side packet is review-ready for dispatch. This is approval to dispatch the bounded build packet, not permission to configure Tigwa/Hermes, change the flake, touch Dave's session or credentials, add unattended triggers, or use any `--dangerously-*` flag. Completion still requires all retained command/log evidence specified by the packet and Tigwa's subsequent review of the system-side contract before client configuration.
