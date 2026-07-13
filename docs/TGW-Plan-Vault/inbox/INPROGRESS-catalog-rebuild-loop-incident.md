# Incident: continuous catalog_rebuild loop, 2026-07-12 ~20:51

**Status:** investigating/stopping live, PP-NIXOS-001

## Symptom

Dave observed catalog rebuilding constantly with "no activity."

## Root cause

1. Reboot at 11:49:37 today resurrected three workers documented as
   intentionally stopped: `ebay_sync`, `ebay_legacy_sync`,
   `catalog_rebuild`. Same class of bug as the 2026-07-11 incident
   (#1322/PP-NIXOS-001 durable-stop gap) but a different reboot and a
   different worker set — not caught by the earlier fix/audit.
2. `ebay_sync`'s bulk `fetch_all_offers` call has been failing for 429+
   consecutive runs (eBay error 25707, invalid SKU — an orphaned offer,
   tracked at todo #1077, never fixed).
3. Bulk-fetch failure triggers a per-SKU fallback sync, throttled to
   once/24h. That throttle expired this afternoon and it began a full
   sweep of the 55,419-item catalog, oldest SKU (2019) forward.
4. Every per-item `ebay_write` fires a `catalog_rebuild` job. Full rebuild
   takes ~57s; jobs were firing every ~30-90s, back to back, continuously.

## Action taken

Stopped `tgw-worker@ebay_sync.service`, `tgw-worker@ebay_legacy_sync.service`,
`tgw-worker@catalog_rebuild.service` live (systemctl stop, not disable —
durable-stop fix is separate work under #1322).

## Open follow-ups

- todo #1077 (orphaned bad-SKU offer blocking bulk fetch) is the real fix —
  until it's cleared, any future per-SKU fallback sweep will repeat this.
- #1322 (durable stop across reboots) now has a second, larger instance:
  6 workers total resurrected across two reboots (5 on 2026-07-11 + these
  3 on 2026-07-12, no overlap). Full audit of ALL supposedly-stopped
  workers' actual systemd enablement state is warranted, not just spot
  checks after each incident.
- Need to check whether the per-SKU fallback sweep caused any live eBay
  API quota exhaustion before it was stopped — check quota/rate-limit
  logs before resuming any eBay work today.
