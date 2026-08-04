# Decision — PP-OUTBOX-001 bounded action-console deliberation

**From:** Dave, relayed and recorded by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Status:** design decision; no implementation authorization
**Extends:** prior PP-OUTBOX-001 decision/clarification notes in `inbox/claude/` dated 2026-07-19.

## Deliberation bound

Dave wants deliberate translation and the ability to redirect it, but does not want an unattended/forgotten action-console session to deliberate indefinitely.

Use this proposed default for a later implementation/design packet:

- A single active translation/drafting run may consume at most **10 minutes of active wall-clock deliberation** or **8 substantive agent-generated re-drafts**, whichever comes first.
- A substantive re-draft is a newly proposed target-specific rendering after the raw input/current correction; trivial UI refreshes, Dave's own edits, and merely viewing a draft do not count.
- On either bound, preserve the original raw input, current draft(s), checker findings, and visible progress; set the card to a clearly labelled **paused / awaiting Dave** state. Do not auto-send, discard, archive, retry, or resume it.
- Dave may return later, edit/clarify/re-voice-type, and explicitly resume/re-draft. A resumed run receives a new bounded deliberation window.

This is a resource/attention safeguard, not a limit on Dave's ability to iterate or reuse an action-console prompt. No schema, UI, worker, or authority change is authorized by this decision.
