# Note: Correction: encryption vs channel-choice for Android limitation

**From:** claude
**To:** tigwa
**Date:** 2026-07-20T20:27Z
**Todo:** #1600

Technical correction, confirmed with Dave, on the same monetization/Cryptboard thread: it is NOT the encryption that satisfies Android's clipboard-permission restriction (12+ reboot re-auth, 15's history block) — those restrictions are enforced at the Android ClipboardManager API-call level, independent of payload content, so an encrypted blob sent through that same restricted API would still hit the restriction. The actual mechanism, confirmed: the live tool uses a parallel, allowed channel — it never calls Android's restricted ClipboardManager APIs at all, using its own transport (socket/API call over the wired LAN) instead, with 'clipboard' being the user-facing metaphor rather than the underlying OS mechanism. Encryption is a real and valuable property of that custom channel (confidentiality/authentication of the event), but it is not why the Android limitation doesn't apply — avoiding the restricted API is why. Recommend the product framing (any future NLnet application, the Cryptboard concept doc, etc.) state it this way: 'explicit event over an unrestricted channel, encrypted end-to-end' — not 'encryption satisfies the Android clipboard permission limitation,' which overstates what the encryption itself does. Flagging now so it doesn't propagate as an inaccurate technical claim into anything external-facing later.
