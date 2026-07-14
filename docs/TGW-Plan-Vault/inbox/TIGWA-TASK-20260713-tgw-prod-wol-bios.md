# TIGWA TASK — verify tgw-prod has WoL enabled in BIOS

**From:** Tigwa  
**Date:** 2026-07-13  
**PP:** PP-HERMES-EA-001  
**Related:** Tigwa office topology (a1131 woken on demand, tgw-prod always-on)

## Task

Confirm that tgw-prod (`192.168.60.100`, running Proxmox / Debian) has
Wake-on-LAN enabled in its BIOS/UEFI settings.

## Context

PP-HERMES-EA-001's office topology splits Tigwa across two hosts: full
Tigwa on a1131 (woken on demand via WoL), Hermes-lite on tgw-prod
(always-on). If tgw-prod itself ever goes down (power loss, crash,
maintenance), it needs WoL as a recovery path — there is no other
always-on host on the LAN to initiate a lights-out power-on from.

The wake-trigger structure (designed but not fully built) also assumes
a1131 can wake tgw-prod when needed, not just the reverse.

## Actions needed

1. Check tgw-prod's BIOS for WoL settings (likely during next
   scheduled downtime or physical access).
2. Enable if disabled.
3. Verify with `ethtool` after boot: `ethtool <interface> | grep Wake-on`
   should show `Supports Wake-on: pumbg` and `Wake-on: g`.
4. Record the NIC PCI address and interface name for the wake config.

## Not a blocker

tgw-prod has been generally stable. This is prophylactic — do not
schedule a downtime just for this; piggyback on the next maintenance
window or physical visit.
