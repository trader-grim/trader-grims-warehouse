# Clarification — PP-OUTBOX-001 iteration, retention, and reuse decisions

**From:** Dave, relayed and recorded by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Status:** design clarification only; no implementation authorization
**Supersedes/extends:** `TIGWA-DECISION-RESPONSE-PP-OUTBOX-001-2026-07-19.md` for the points below.

## Drafting and interruption

- Do **not** impose a draft-iteration cap. Dave expects to interrupt/redirection when needed; this is appropriate and less disruptive in this communication-translation context.
- Dave needs to see a small, useful amount of drafting/deliberation progress and be able to interrupt it, especially when a typo or voice-transcription error changes the intended point.
- A likely interruption response is Dave clarifying/editing the typed intent or re-voice-typing it. The raw input remains immutable as the original capture; any corrected input/draft must remain distinguishable rather than silently replacing it.
- For a later UI, define a reviewable interaction contract for visible progress, interruption/cancellation, and a follow-up correction/re-draft. Do not infer a need for autonomous continuation or delivery.

## Unsent cards

- Do not auto-archive, delete, send, or otherwise expire stale cards.
- Dave wants manual archive or deletion available for cards he no longer wants. Design the retention/audit semantics explicitly (particularly how manual deletion relates to immutable raw input) before a build packet; do not quietly erase evidence by default.

## New related concept: reusable/pinned prompts

- Add a design consideration for a manually pinned prompt/card that Dave intends to reuse often.
- Keep reuse distinct from automatic dispatch: a pin is a retrieval/convenience mechanism, not permission to auto-send, schedule, or infer delivery.
- Define later whether a pin preserves one immutable raw template plus versioned drafts, target-specific renderings, and/or a user-visible copy-to-new-card flow. No schema/UI decision or implementation is authorized by this clarification.

## Existing decisions retained

v0 first; Dave-only explicit delivery, including an operator-visible "I'm feeling lucky" action; fixed manually maintained target-agent list.
