# PP-OUTBOX-001 — agent instruction outbox / prompt-improvement interface

**Opened:** 2026-07-18 (Dave, captured/structured by Tigwa,
`DAVE-CONCEPT-agent-instruction-outbox-2026-07-18.md`). **Status: DESIGN EVALUATION
ONLY — not implementation-authorized.** Claude's response to Dave's 5 questions below.

## The concept, restated

A personal, agent-aware instruction outbox — not a todo list, not a prompt editor.
Dave captures rough intent, Tigwa proposes a clearer target-appropriate rendering
(with alternatives), Dave chooses/edits/defers, a checker flags gaps, and only an
explicit Dave send action delivers it. Raw input is permanent and immutable; drafts
are proposed renderings, never silent replacements. "Sent" is not "done" — outcome
links back to the card.

## 1. Smallest durable data model

Reuse what already exists rather than building new infrastructure for storage:

- **Delivery channel: the existing mailbox mechanism** (`cmd_mailbox_send`,
  `tgw.apis.fence`-adjacent, already the one send mechanism behind `tgw mailbox
  send`/the `tgw-mailbox-send` skill/the `tgw_mailbox_send` MCP tool — see
  PP-RUNNERCOMMS-001). Sending a card to Claude means writing a mailbox message
  into `inbox/claude/` — the exact mechanism every design/triage exchange in this
  session already used. No new delivery infrastructure needed.
- **Staging/drafting state: one new Postgres table**, `instruction_cards`, in the
  existing `state_machine` database (already "the work ledger" — settled
  architecture). Minimum columns, matching the card model Dave/Tigwa proposed
  almost directly:
  - `id`, `created_at`
  - `raw_input` (text, immutable once set — never overwritten by a draft)
  - `target_agent`, `intent_type`, `delivery_boundary`
  - `drafts` (jsonb array — `[{version, text, created_at}]`, append-only, never
    replaces `raw_input`)
  - `context_links` (jsonb array of `{type, ref}` — PP/todo/artifact/SKU/prior card)
  - `checker_findings` (jsonb array — advisory, see §4)
  - `state` (enum matching the lifecycle: captured → drafting → ready →
    deferred/queued → sent → acknowledged → resolved/archived)
  - `delivery_record` (jsonb, null until sent — `{rendered_text, mailbox_path,
    sent_at}`; `mailbox_path` is the durable proof, same pattern as every other
    mailbox-backed record in this project)
  - `outcome_link` (jsonb, null until a response exists — points at the reply
    mailbox message, resulting todo id, or a follow-up card id)

This is deliberately thin: `instruction_cards` only ever holds pre-send state.
Once sent, the mailbox message is the canonical durable record (matches the
project's established "reuse mailbox for append-only records" pattern, same one
just used for the tracker-boundary Lane 2 receipts decision today).

## 2. Coexistence with the mailbox and taskboard — not a second tracker

- **Mailbox** stays the canonical "what was actually communicated" record —
  unchanged, cards just use it as their send mechanism.
- **`todo_items`** stays the canonical "what work needs doing" record — unchanged.
  A card MAY produce a todo (e.g., Dave drafts an instruction, sends it, Claude's
  response is "filed as todo #X") but a card is never itself a todo, and nothing
  about `instruction_cards` grants task-write authority — it only ever writes to
  its own table and, on explicit send, calls the existing mailbox function.
- **`instruction_cards` has no pipeline/work authority of its own.** It cannot
  mark anything done, cannot enqueue a worker job, cannot mutate an item. Its
  entire write surface is: append a draft, change lifecycle state, and (on
  explicit send) call `cmd_mailbox_send`. This is the same shape as Lane 1/Lane 2
  of today's tracker-boundary proposal — a narrow, purpose-built table, not a
  general write capability.

## 3. Minimal first UI/workflow to prove value

**Recommend NOT building a UI or the Postgres table first.** Pilot the *workflow*
with near-zero new infrastructure before committing to schema/UI:

- **v0 (no code):** Dave drops rough notes into a single scratch file (or a new
  `inbox/dave/OUTBOX-<date>.md`-style doc, following the existing per-actor
  mailbox folder convention). Tigwa periodically reads it, proposes a structured
  rendering back in the same doc or a reply note, Dave edits/approves inline,
  Tigwa sends via the existing `tgw-mailbox-send` mechanism once Dave says so.
  This proves the *interaction loop* (capture → improve → explicit send → outcome
  link) costs nothing to build and can be abandoned or reshaped freely if the
  workflow itself turns out wrong in practice.
- **v1 (if v0 proves out):** the `instruction_cards` table + a small webui page
  (`/form/outbox` in the existing `tgw-http` service, same pattern as every other
  operator-facing page already built) — capture box, card list by state, draft
  shown beside raw input, explicit Send button, target-agent selector. Still no
  new service, no scheduling, no autonomous dispatch.
- **Not in v0 or v1:** multi-agent orchestration, scheduled/milestone-triggered
  auto-send, or any inference-based "this milestone happened, so send now" —
  matches Dave's own explicit principle #7 ("none should be automatic") and
  delivery-mode note (auto-send-on-inferred-milestone needs an explicit rule and
  visible confirmation, not silent inference).

## 4. Safety/integrity boundaries — advice vs. delivery

- **The checker (Tigwa) never has write access to anything but the card's own
  draft/checker_findings fields.** It cannot call `tgw_mailbox_send`,
  `tgw_enqueue`, or any other write tool as a side effect of drafting or
  checking — same boundary already enforced for Tigwa's read-only MCP link
  elsewhere in this project. Only Dave's explicit send action reaches the
  mailbox.
- **`delivery_boundary` (discussion-only/inspect/propose/implement/
  side-effecting) is a label, not a grant.** Choosing "implement" on a card does
  not itself authorize the receiving agent to implement anything — real
  authority is still whatever that agent's own contract (tools/hooks,
  PP-AGENT-DISCIPLINE-001) already permits. This must be stated explicitly
  wherever the field is surfaced, so it's never mistaken for a permission
  escalation mechanism.
- **A card is not a todo and cannot mark one done.** Prevents the exact
  "second canonical tracker" risk named in §2 from resurfacing as a mutation
  path.
- **Raw input is genuinely immutable.** `drafts` is append-only; nothing ever
  overwrites `raw_input`, matching Prime Directive 1's data-preservation
  principle applied to communication instead of item data.

## 5. Open design choices for Dave, before any PP/build packet is filed

1. **v0 vs. v1 first:** run the zero-infrastructure pilot (§3) before committing
   to the Postgres table + UI, or go straight to v1? (Recommend v0 first —
   cheap, reversible, proves the workflow.)
2. **Send authority:** is Dave the only actor who can ever trigger a send, with
   no delegation ever, or should Tigwa eventually be able to send on Dave's
   behalf under some narrow condition? (Given the project's standing
   "relayed authorization never trusted" pattern, recommend: Dave only, always,
   no exceptions, at least until there's a concrete reason to revisit.)
3. **Draft iteration cap:** unlimited "try again" cycles, or a bounded attempt
   count (mirrors the bounded-fix-attempt pattern already used elsewhere in
   PP-AGENT-DISCIPLINE-001)? Low stakes either way since nothing auto-sends, but
   worth a stated default.
4. **Stale-card handling:** do unsent cards ever get surfaced as "you have N
   cards sitting in `ready` for over a week," or stay silently queued forever?
   (Recommend: surface, never auto-archive — matches the project's
   surface-don't-silently-drop convention, e.g. C11's "a skip is a finding.")
5. **Multi-agent target list growth:** is "future similarly capable agents"
   just future named agents added to a fixed enum, or does the model need to
   support a dynamically registered agent list? (Recommend: fixed enum, extend
   manually — matches how `tgw_get_todo`'s `agent` parameter already works, no
   need for more flexibility than the rest of the system has.)

## Not done by this note

No table created, no UI built, no mailbox `msg_type` added, no agent authority
changed. This is the requested design evaluation only.
