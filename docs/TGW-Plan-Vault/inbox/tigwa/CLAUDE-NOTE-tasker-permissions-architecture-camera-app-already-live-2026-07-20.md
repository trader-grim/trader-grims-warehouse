# Note: Tasker permissions architecture - camera app already live

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T20:24Z
**Todo:** #1600

Correction/addition to the monetization review just sent (CLAUDE-REVIEW-monetization-and-funding-research-review-2026-07-20.md): Dave clarifies the camera app + event server isn't just a proof of concept sketch, it's been live/working for quite a while ('already have this cobbled together'). More importantly, the Android clipboard-permission question my review flagged as needing future validation (reboot re-auth on 12+, history restrictions on 15) already has a working answer: the current camera app uses a Tasker-based permissions architecture to handle this, which Dave can explain in more detail. This changes the framing on one point in my review — the code-extraction feasibility test (can the event-transport mechanism come out clean of TGW-specific plumbing?) is still the right first step before any NLnet application, but the underlying Android-permission mechanism itself is apparently already solved and running, not an open research question. Worth getting the Tasker architecture detail on record before it's lost, since it's real prior art directly relevant to the clipboard-event-server product thesis.
