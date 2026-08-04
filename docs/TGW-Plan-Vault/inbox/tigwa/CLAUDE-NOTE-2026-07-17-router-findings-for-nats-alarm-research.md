# Note — router findings, for your NATS/JetStream-on-router alarm research

Dave mentioned you already looked into running NATS JetStream on the
router as part of the alarm-system idea (separate from TGW's own
PP-AIOPS-001 JetStream use — his words: "unrelated to our other use").
I don't have visibility into your research, so I can't reconcile it
myself — but I found a real, previously-lost planning doc on the router
itself while doing an unrelated "recover lost PPs" sweep tonight, and
Dave asked me to send it your way rather than have two separate threads
on the same hardware.

## What exists: `docs/ai-plans/router-dlink-dir868l-ecosystem.md`

Filed 2026-07-06, never given a PP number, never referenced in the master
plan — genuinely orphaned until tonight. Summary:

- **Hardware:** D-Link DIR-868L (rev A1/B1/C1, Broadcom BCM4708 dual-core
  @800MHz, BCM4360/BCM4331 radios, 256MB RAM). Dave's read ("decent
  processor and available RAM") is right for this SoC family.
- **Firmware: DD-WRT, not OpenWrt** — OpenWrt doesn't officially support
  this Broadcom wireless chipset (confirmed via OpenWrt forum + DD-WRT
  wiki, true since ~2017, still true). Known flash path documented
  (factory → intermediate DD-WRT build → current build; revision-specific
  images, A1 uses TFTP). One known post-flash quirk: 5GHz band can vanish
  from scans, fixed by switching VHT80→HT40 or manually adding the
  network. Dave has 20+ years building DD-WRT/OpenWrt images, so flashing
  mechanics itself isn't a blocker.
- **256MB RAM is the real constraint for anything you're planning** — if
  your NATS/JetStream research assumed more headroom, worth checking
  against this number specifically. Whether JetStream (with its persistent
  stream storage) fits comfortably in what's left after DD-WRT's own
  footprint is exactly the kind of thing your research may already answer
  and mine doesn't.
- **Six other candidate capabilities the doc names, not prioritized:**
  static DHCP reservation completion, VLAN-isolating intake/camera
  devices, router health folded into `tgw health`, authoritative local
  DNS, WireGuard, DR backup of router config. None of these depend on or
  conflict with a NATS/JetStream leg — separate concerns on the same box.
- **One live, unresolved finding buried in the doc, worth flagging
  regardless of what happens with NATS:** the DHCP reservation table has
  two different MAC addresses both claiming `192.168.60.112` under the
  name "hpi3" — a real IP conflict nobody's looked at since 2026-07-06.

Full doc has the complete DHCP reservation table and sourcing/links if you
want the raw material rather than this summary.

## What I need from you

Nothing urgent — Dave just wants the two threads (my router-ecosystem find
+ your NATS/alarm research) to end up reconciled into one place rather
than living separately. Your call on whether that's a note back to me, a
merge into your own alarm-system design doc with a pointer to the router
doc, or something else — this isn't mine to architect, just mine to make
sure you have the router-side context.
