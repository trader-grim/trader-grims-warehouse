# Clarification — PP-RADAR-001 staged sequencing

**From:** Tigwa, relaying Dave's direction
**Date:** 2026-07-19
**Status:** Clarification of existing direction; no new broad build authority
**References:** PP-EVENTD-001, PP-RADAR-001, todo #1573

## Correct framing

Do not present #1573 as the sole blocker to Radar work, or as a prerequisite that
must be fully designed before any implementation begins.

Dave's intended staged implementation is:

1. **Build PP-EVENTD-001 / Go `clip-route` now.** Its design is complete; the
   PP is unfrozen; PP-CLIP-001 Phase 2 is DONE; the Master Plan explicitly
   says this first event surface is unblocked. It is the recognized-input,
   active-context foundation.
2. **Feed and observe real data from `clip-route`.** This establishes what
   current-entry context is actually available rather than inventing a Radar
   contract from assumptions.
3. **Then complete Tigwa-owned #1573.** Tigwa translates that real surface
   into the precise Radar data/action/transport contract for Dave: the
   anticipatory heads-up layer, explicit-recipient encrypted clipboard
   replacement, and artifact lifecycle integration.
4. **Build PP-RADAR-001 against that proven surface.** The server-based,
   encrypted, explicit-recipient direction itself is settled and
   build-authorized once the concrete contract exists.

## Why this distinction matters

PP-RADAR-001 is explicitly the *second* event surface. Its design must be
scoped to data actually available after PP-EVENTD-001 lands. #1573 therefore
is a staged librarian deliverable, not an excuse to hold the already-unblocked
`clip-route` foundation.

Conversely, do not build the full Radar heads-up/clipboard-replacement layer
prematurely or as a parallel design based on imagined event data.

## Requested handling

Please align future planning/status language to this staged sequence:
`clip-route` build first → real-data proof → #1573 contract → Radar build.
This is a clarification of sequencing, not authorization to alter services,
Syncthing, KDE Connect, or clipboard collection outside the scoped first
surface.
