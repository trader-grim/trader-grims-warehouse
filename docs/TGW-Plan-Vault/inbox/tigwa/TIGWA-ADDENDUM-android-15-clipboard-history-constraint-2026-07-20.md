# Addendum: Android 15 clipboard-history constraint is a live collection-device blocker

**State:** `capture-staged` — Dave operator evidence; no build, permission action, or release authorized
**Date:** 2026-07-20
**Supersedes nothing; supplements:** `TIGWA-CLARIFICATION-clipboard-and-career-path-priority-2026-07-20.md`

## Observed operational consequence

Dave states that collection devices deliberately remain on older Android versions because the newer Android clipboard-permission model breaks a required workflow:

- Around Android 12, clipboard access requires operator re-authorization after reboot.
- On Android 15, Clipper is almost unusable for the collection workflow because clipboard logging/history is not allowed.

This is not an abstract privacy concern or speculative product wedge. It is a live compatibility/operations constraint that already affects device-version choices.

## Product implication

The problem to investigate is whether an Android-compatible, intentional-event alternative can preserve the operational value of clipboard history/logging **without** attempting forbidden or abusive background clipboard surveillance. The potential design direction is explicit capture/share events, user-visible state, named recipients/devices, bounded retention, and recoverable event history—rather than implicit global clipboard monitoring.

## Required future verification

Before making technical or market claims, separately establish:

- exact Android 12–15 API/permission/lifecycle behavior, including OEM differences;
- what Clipper is permitted or prevented from doing on Android 15 and why;
- applicable Play policy and accessibility-service restrictions;
- which operational history semantics must be preserved (capture time, source/context, destination, receipt, expiry, recovery);
- whether an explicit event model is both compliant and fast enough for collection work.

No workaround, sideloading recommendation, permission bypass, accessibility abuse, Android service, hosted service, or public product is authorized by this record.
