# DONE — multi-agent code review of today's sprint + all 7 findings fixed

Ran a full 8-angle code review (line-by-line, removed-behavior,
cross-file tracer, reuse, simplification, efficiency, altitude, CLAUDE.md
conventions) against today's session diff (c867811...HEAD, 47 files).
8 candidates verified independently, 7 CONFIRMED / 1 REFUTED (a canary
probe price-formatting concern that real production data showed never
actually manifests).

All 7 confirmed findings fixed and live-verified same session:
1. http_server.py auto-push trigger narrowed to draft_listing only (a
   bare top-level title/description edit was silently not reaching
   eBay while logging "pushed" -- ironic bug in today's own #1114 fix)
2. Added missing dedupe_key to the same enqueue (duplicate-push risk)
3. Canary probe now actually diffs aspects (was collected, never
   compared -- silent verification gap)
4. tgw-clip-picker dmenu fallback no longer risks resolving to the
   wrong entry on duplicate truncated content
5. tgw-restore.sh dry-run message corrected (claimed to copy flake/,
   never did -- no actual data loss since flake restore is a separate
   documented step, but the message was wrong)
6. config.py's permission-tolerance fix scoped to PermissionError only
   (was silently swallowing other I/O errors too)
7. items.set_fields() now publishes to the PP-AIOPS-001 audit stream
   like update_item() does (bulk backfills were invisible to it)

10 new/updated tests, full suite 1825 passed. Findings #1/#2
live-verified end to end: real PATCH -> real queue check -> real
eBay API confirmation, in both directions, reverted cleanly.
