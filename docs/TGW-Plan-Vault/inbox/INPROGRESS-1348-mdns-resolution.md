# In progress: todo #1348 — mDNS resolution confirmed, updating stale IP note

**PP:** PP-HERMES-EA-001
**Status:** in_progress

## What was found

Dave noted `*.local` resolves everywhere. Verified live:

- `tgw-prod.local` → `192.168.60.100` (resolves from both tgw-prod itself and from a1131)
- `a1131.local` → `192.168.60.101` (resolves from tgw-prod)
- Avahi/mDNS is active and enabled on tgw-prod (`avahi-daemon.service`, up ~8h)

This means the hostname-resolution gap noted in `/home/db/.hermes/TIGWA-LITE.md`
("a1131 connects to tgw-prod using `192.168.60.100` until hostname resolution is
fixed") is resolved — mDNS names work in both directions today.

## Action taken

Updating `TIGWA-LITE.md`'s "Host split" section to use `tgw-prod.local` instead of
the hardcoded IP, and noting resolution is confirmed working both ways.

## Relevance to #1347 (Tigwa-lite → a1131 wake path)

Tigwa's inbox request for #1347 listed hostname resolution as an open unknown
(item 3: "use the verified LAN IP `192.168.60.100` where appropriate until naming
is fixed"). That's now answered: the wake-path readiness checks (SSH-up, etc.)
can target `a1131.local` rather than a hardcoded IP. Will fold this into the
#1347 design when that work starts.
