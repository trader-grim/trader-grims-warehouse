# Clarification — PP-OUTBOX-001 stale-card behavior

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-22T17:16:51Z
**Clarifies:** `inbox/claude/TIGWA-DECISION-pp-outbox-v0-2026-07-22.md`, settled choice 4
**Authority:** Dave clarified the decision. This is a v0 interaction clarification only; no implementation is authorized.

Stale, unsent ready cards should **drift down the list** rather than remain promoted indefinitely. The interface/process should issue **periodic reminders** to check or clear them.

"Clear" is an explicit Dave action, recorded as a visible lifecycle outcome (for example, deferred, declined, or superseded); it is not automatic archival, deletion, or send. Nothing silently disappears.
