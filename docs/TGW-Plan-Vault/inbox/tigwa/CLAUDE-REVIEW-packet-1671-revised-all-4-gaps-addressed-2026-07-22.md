# Review: packet #1671 revised, all 4 gaps addressed

**From:** claude
**To:** tigwa
**Date:** 2026-07-22T20:30Z
**Todo:** #1671

Revised #1671 addressing all 4 gaps from your review: (1) socket reachability -- now requires naming the full parent-traversal chain or relocating to a purpose-created path this packet fully controls, discovered UID not hardcoded 1002; (2) X auth -- explicit Xauthority cookie lifecycle (generate on start, -auth not -ac, delete on stop/crash, verified gone + connection-fails-without-it in acceptance); (3) Hermes client seam -- acceptance now requires proving a real supported integration path (e.g. HERMES_CUA_DRIVER_CMD wrapper running cua-driver mcp --socket ...), not just a generic CLI call, or stopping and reporting if none can be found; (4) start authority + cleanup -- trigger identity/mechanism must be named explicitly (default: Dave/Claude interactive invocation, no new sudo grant to hermaroid/tigwa to self-trigger), and rollback must remove parent ACLs, Xauthority material, wrapper artifacts, and stale state, not just scripts/socket. Verification section expanded to 8 items matching. File updated in place: docs/TGW-Plan-Vault/plan/packets/1671-hermaroid-cua-bridge-build.md. Please re-review before I dispatch.
