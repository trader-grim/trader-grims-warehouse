# Clarification — PP-OUTBOX-001 translated prompts as an action console

**From:** Dave, relayed and recorded by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Status:** design clarification only; no implementation authorization
**Extends:** `TIGWA-CLARIFICATION-PP-OUTBOX-001-iterations-archive-pins-2026-07-19.md`

## Core framing

Dave's view is that a translated prompt becomes an **action console**. The purpose is not merely to store or retrieve prompts: it is a visible operator surface where rough intent is translated into an actionable target-specific instruction, Dave can inspect/redirect it, and Dave can take the explicit delivery action.

## Reuse and logging

- Reuse is simply choosing to **send again**; it is not an automatic scheduled dispatch or a separate automation authority.
- The delivery/use log records how the prompt/card was used, including repeated sends and their outcomes. It supplies the history rather than requiring a separate reuse mechanism to imply workflow state.

## Pinning and light edits

- A pinned prompt is a commonly reused action-console starting point.
- It may be lightly edited for a new instance before sending. Example: a pinned `research SKU xxx` prompt can be opened, have the SKU replaced with a new SKU, then be sent as the new action.
- The design should preserve the source/pinned template and log the particular rendered/sent instance, so edits for one use do not silently alter the reusable prompt for future uses.

## Boundaries retained

The action console does not make prompt translation, pinning, reuse, or a use log into authority for autonomous sends, task completion, worker enqueueing, or other side effects. Delivery remains Dave's explicit action. No schema/UI/build packet is authorized by this note.
