# Correction: Android clipboard authorization behavior

**From:** Tigwa, recording Dave’s correction
**To:** Claude
**Date:** 2026-07-20
**Related:** `TIGWA-ADDENDUM-clipboard-and-career-path-priority-2026-07-20.md`
**Status:** operator evidence for future platform validation; no build or permission action

Dave clarifies the Android gap is specific and real in his experience: beginning around Android 12, clipboard-accessing apps require the operator to grant/re-authorize access after each device reboot.

This makes the opportunity more concrete than a generic privacy concern. The future discovery task is to precisely verify the OS/API mechanism, affected Android versions and OEM variance, Play distribution implications, compliant alternatives, and how existing tools behave. Do not generalize the approximate version or mechanism until source-verified.

No implementation, Android permission request, service creation, or release is authorized.