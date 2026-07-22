# In progress: todo #1510 — nats-server native NixOS module (PP-AIOPS-001 Phase 1)

Building a NixOS module/systemd unit for `nats-server` in `~/tgw-flake` on tgw-prod,
JetStream enabled, per the 2026-07-22 decisions in TGW-Master-Plan.md's PP-AIOPS-001
section: native package (no Docker), 90-day/50GB uniform retention across streams,
tgw-prod only (not a1131, not clustered).

Scope: stand up the broker + enable JetStream + create the ITEMDATA_MUTATIONS stream
with the retention policy + verify live with a real nats-py connect/publish/consume
round trip. NOT scoped: wiring `items._write_field()`/`write_item()` to actually
publish (that's the rest of Phase 1, separate work).

Following PP-FLAKEGATE-001: commit locally, then `tgw flake request-push`/
`request-switch` and stop — Dave runs the actual push/switch by hand.

If interrupted: check `~/tgw-flake` git log for a nats-server commit, check
`systemctl status nats-server` on tgw-prod, check `tgw flake queue` for pending
request-push/request-switch jobs.
