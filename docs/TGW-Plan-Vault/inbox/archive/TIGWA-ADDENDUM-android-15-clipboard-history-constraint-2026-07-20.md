# Addendum: Android 15 clipboard-history constraint is a live collection-device blocker

**From:** Tigwa, recording Dave’s operator evidence
**To:** Claude
**Date:** 2026-07-20
**Related:** clipboard monetization/funding review
**Status:** awareness and future validation input only; no implementation authority

Dave adds a material fact: collection devices remain on older Android versions because newer Android clipboard permissions break the needed workflow. Around Android 12, clipboard access requires re-authorization after device reboot; on Android 15, Clipper is almost useless because clipboard logging/history is not allowed.

This turns the potential clipboard product wedge into a live operational compatibility problem. The question is not how to bypass Android controls. It is whether an explicit, user-visible event/history model can preserve the valuable operational semantics—capture, destination, receipt, expiry, recovery—without ambient clipboard surveillance, broad accessibility abuse, or policy violations.

Please include this concrete constraint in the pending review. Exact Android/API/OEM/Play-policy behavior still needs future source verification. No build, workaround, service, permissions action, or release is authorized.