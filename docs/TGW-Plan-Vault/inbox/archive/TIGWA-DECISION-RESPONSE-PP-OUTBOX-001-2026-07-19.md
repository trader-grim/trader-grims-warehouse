# Dave decision response — PP-OUTBOX-001 instruction outbox

**From:** Dave, relayed and recorded by Tigwa
**To:** Claude
**Date:** 2026-07-19
**Status:** partial design decisions; no implementation authorization

## Settled decisions

1. **Start with v0.** Use the zero-code workflow first; do not build the table or UI yet.
2. **Send authority remains Dave-only.** Dave also wants an operator-visible **“I’m feeling lucky”** button: when Dave clicks it, it may send the currently fixed-up/rendered version directly through the existing mailbox. This is still Dave’s explicit send action, not agent-initiated or scheduled delivery. The button’s exact preview/confirmation semantics remain to be specified before any build packet.
3. **Target agents:** fixed manual list for now; additions are deliberate/manual.

## Dave requested clarification before deciding

- **Draft iteration cap:** clarify what is being counted (e.g. each checker/Tigwa redraft of one card after raw input, versus Dave edits, versus all changes) and what operational problem a cap solves.
- **Stale-card handling:** define precisely what it means to surface an unsent ready card, including where/when it is shown and what action, if any, is taken. Dave has not yet selected a policy.

## Boundaries retained

- No v1 table, UI, mailbox protocol change, authority expansion, automatic/scheduled dispatch, worker/taskboard action, or implementation is authorized by this response.
- Raw input remains distinct from drafts; sending a card remains distinct from work completion.
