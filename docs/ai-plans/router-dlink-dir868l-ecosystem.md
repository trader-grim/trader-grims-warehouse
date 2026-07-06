# Plan: bring the D-Link DIR-868L router into the TGW ecosystem

**Status:** PROPOSAL — not started, no action taken. Filed 2026-07-06 per Dave's
direction ("put the whole thing... into a plan item for later").

## Why this exists

The router has never been part of TGW's config/inventory — no reference doc,
no backup, no health check touches it. Dave: "we haven't been considering the
router setup in our config... the device offers services we may be able to
use." Separately, DHCP reservations for known hosts are already mostly in
place (server tgw-prod = .100, a1131 = .101, cameras and most devices already
reserved) — this plan is about firmware/capability upgrade and folding the
router into TGW's operational picture, not about the reservations themselves
(those just need auditing/updating, not building from scratch).

## Hardware + firmware research (2026-07-06)

Device: D-Link DIR-868L. Revisions A1/B1/C1 exist, all Broadcom-based
(BCM4708 dual-core @800MHz SoC, BCM4360 5GHz + BCM4331 2.4GHz radios, 256MB
RAM) — a genuinely capable processor for a consumer router, matches Dave's
"decent processor and available RAM" read. Revision matters for which
firmware image is correct; confirm via the hardware-version sticker before
flashing.

**DD-WRT, not OpenWrt, is the right choice for this hardware:**
- OpenWrt does **not** officially support the DIR-868L — its Broadcom
  wireless chipset isn't well-supported by OpenWrt's mainline drivers, true
  since at least 2017 and still true. [OpenWrt forum thread](https://forum.openwrt.org/t/d-link-dir-868l-dir-880l-support/5738)
- DD-WRT has mature, dedicated support for this exact SoC family (built
  around Broadcom's vendor SDK). [DD-WRT Wiki](https://support.dd-wrt.com/wiki/index.php/D-Link_DIR-868L)
- Known flash path: factory-to-ddwrt.bin intermediate build (between
  01-20-2015-r25974 and 05-28-2015-r27096) before moving to a current DD-WRT
  build; revision-specific images (A1 uses TFTP method per forum reports).
- Known one-time quirk: 5GHz band can disappear from network scans post-flash
  — documented fix is switching the 5GHz interface's channel width from
  VHT80 to Wide HT40, or manually adding the network. Not ongoing fragility,
  just a known post-flash step.
- Dave has 20+ years of experience building DD-WRT/OpenWrt images across
  platforms — flashing mechanics are not something this plan needs to spell
  out; the research value here is confirming *which* firmware fits *this*
  hardware, which it does.

## Candidate services/capabilities once DD-WRT is in place

Not prioritized yet — Dave to pick when this thaws:

1. **Static DHCP reservations, audited and completed** — already mostly done
   (server .100, a1131 .101, cameras). Needs a pass to confirm every device
   TGW cares about is covered and documented somewhere (currently nowhere).
   Directly unblocks todo #1228 (NFS intake export currently subnet-wide
   because no reservation existed for the intake device at audit time —
   revisit now that reservations are more complete).
2. **VLAN segmentation** — put intake/camera devices on a restricted subnet
   that can only reach the NFS intake export, nothing else. Real
   defense-in-depth beyond IP-locking alone, relevant to the #1219 NFS
   finding (subnet-wide rw export).
3. **Router health folded into `tgw health`/ops-digest** — nothing currently
   watches WAN/DHCP/router state; an outage here is invisible to the
   self-healing philosophy until someone notices no internet.
4. **Authoritative local DNS** for tgw-prod/a1131 hostnames, as a more robust
   complement to the mDNS/avahi setup already working (confirmed live
   2026-07-06: `tgw-prod.local` resolves correctly via avahi today; this
   would be a belt-and-suspenders addition, not a fix to something broken).
5. **WireGuard** (DD-WRT supports it) as a LAN-side option alongside the
   existing Tailscale setup.
6. **Router config backed up into the DR kit** — currently the one piece of
   TGW-adjacent infrastructure with zero backup/recovery story, inconsistent
   with PP-BACKUP-001's philosophy for everything else.

## DHCP reservation inventory (as captured by Dave, 2026-07-06 — in progress)

```
48:78:5e:63:64:40    192.168.60.181    FireTab            (likely the Amazon Fire tablet from the router-tooling discussion — possible intake/camera device?)
04:0e:3c:c4:93:f8    192.168.60.100    tgw-prod
80:2b:f9:76:77:2b    192.168.60.112    hpi3               ⚠ IP CONFLICT — see below
84:a9:3e:ac:6f:38    192.168.60.112    hpi3               ⚠ same IP, different MAC
fe:7c:ab:8c:6d:88    192.168.60.103    Dave-s-Tab-A9
16:28:c0:88:94:7c    192.168.60.155    a53
c8:2a:14:2a:a1:85    192.168.60.101    a1131
```

**Not a conflict — confirmed intentional (Dave, 2026-07-06):** `hpi3`'s two
MACs are its WiFi and Ethernet interfaces, both deliberately pointed at .112
so the device keeps one consistent identity/IP regardless of which interface
is connected. Fine as long as both interfaces are never active
simultaneously (only one lease can actually hold .112 at a time) — worth a
mental note if `hpi3` is ever dual-homed (both plugged in + wifi joined at
once), but not a bug to fix.

**Status per Dave (2026-07-06): cameras/intake devices not added yet, a few
more devices still to add.** This is why #1219/#1228 (NFS export host-lock)
remain open — the actual intake camera/phone device isn't in this table yet.
Revisit #1219/#1228 once the intake device has a confirmed reservation.

## Open questions for whenever this thaws

- Confirm hardware revision (A1/B1/C1) before selecting a firmware image.
- Priority order among the 6 candidate services above — none chosen yet.
- Does DD-WRT's config export integrate cleanly with the existing
  `tgw-site-config` repo pattern, or does it need its own storage location
  (likely its own — router config isn't Python/app config)?
