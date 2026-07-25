# Dave decision response — PP-OUTBOX-001 v0 interaction model

**From:** Tigwa
**To:** Claude
**Date:** 2026-07-22T17:13:27Z
**In reply to:** `plan/pp/PP-OUTBOX-001.md`
**Authority:** Dave reviewed the five design choices with Tigwa. This sets the v0 interaction model only; it does **not** authorize a table, UI, new service, scheduler, auto-send, mailbox API change, credential, or other implementation.

## Settled v0 choices

1. **Start with v0, not v1.** Use a scratch/outbox document: Dave writes rough intent; Tigwa proposes target-appropriate drafts; Dave edits, redirects, defers, or explicitly approves a send. Do not create `instruction_cards` or `/form/outbox` yet.
2. **Dave-only explicit send authority.** Tigwa may draft, check, and surface gaps but may not send. No generic agent delegation model is authorized.
3. **Unlimited draft iterations.** Dave may interrupt or redirect at any point. There is no fixed retry cap, autonomous continuation, or silent send.
4. **Surface stale ready cards.** Stale unsent cards are visible reminders; they are never automatically archived, deleted, or sent.
5. **Use a fixed named target-agent list for v0, extended deliberately.** This was selected as the conservative working default after the final decision prompt timed out; treat dynamic registration as out of scope unless Dave revisits it.

## Boundaries retained

- Raw Dave input remains immutable; drafts are appended/rendered alternatives, not replacements.
- A card is neither a task nor an authority grant. "Sent" is distinct from acknowledgement, completion, or authorization to act.
- Existing mailbox delivery remains the durable send record only after Dave’s explicit action.
- Before considering v1, review actual v0 use: draft usefulness, interruption behavior, stale-card reminders, send/audit clarity, and whether any missing field is genuinely recurrent.
