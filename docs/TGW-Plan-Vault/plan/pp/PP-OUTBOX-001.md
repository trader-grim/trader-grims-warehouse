# PP-OUTBOX-001 — agent instruction outbox / prompt-improvement interface

**Opened:** 2026-07-18 (Dave, captured/structured by Tigwa,
`DAVE-CONCEPT-agent-instruction-outbox-2026-07-18.md`). **Status: DESIGN EVALUATION
ONLY — not implementation-authorized.** Claude's response to Dave's 5 questions below.

**Vision, as of 2026-07-19 (Dave): "What ends up being floatable and a seriously
useful operations interface is the clipboard altogether."** The 2026-07-19 design
session (see sections below) reframes this PP: it started as one narrow
feature (translate rough intent into a sendable instruction) and ends up naming
the clipboard itself — `tgw-clipd` + the rofi picker (PP-CLIP-001), typed
entries recognized on write (PP-EVENTD-001), inline per-entry mini-apps, a
`prompt` type filtered via a `Prompts` chip — as TGW's general-purpose,
floatable (on-demand overlay, not a fixed dedicated window) operations
interface. The instruction outbox becomes the first serious application of
that surface, not a separate thing bolted alongside it. Still design-only;
this is the destination the sections below are converging on, not a build
authorization.

## The concept, restated

A personal, agent-aware instruction outbox — not a todo list, not a prompt editor.
Dave captures rough intent, Tigwa proposes a clearer target-appropriate rendering
(with alternatives), Dave chooses/edits/defers, a checker flags gaps, and only an
explicit Dave send action delivers it. Raw input is permanent and immutable; drafts
are proposed renderings, never silent replacements. "Sent" is not "done" — outcome
links back to the card.

## 0.1 Flake execution card — settled console integration direction, 2026-07-26

`flake_mutation` becomes a typed **Flake execution** card in this PP's integrated floatable command console, alongside instruction/outbox and pending-approval cards. It is a view over the existing authoritative job/audit record, never a duplicate tracker or an ambient shell.

The card removes operator archaeology, not the human decision boundary: it renders action kind, human-readable target, repository/worktree identity, exact immutable commit SHA, concise summary, linked PP/todo, requestor, current state, available preflight evidence, and the resulting verified receipt. It does not require Dave to discover a machine, Unix user, absolute path, command spelling, or a separate post-action receipt command. Internal host/user/path resolution remains in the reviewed handler; errors are rendered as a precise unavailable/mismatch state.

Its sole consequential control is an explicit `Confirm push` or `Confirm switch`, backed only by the future #1625 human-only executor. Before side effects, that handler must recheck that it is on the recorded host and that the local checkout `HEAD` exactly equals the job's recorded SHA; it then requires visible confirmation, automatically records completion only after verified success, and preserves a failed/queued job with diagnostic receipt on error. An agent identity, card render, card refresh, notification, or selection cannot invoke it. Before #1625 exists, render truthful status/evidence plus `executor not installed`; do not pretend that an inert button works or send Dave to reconstruct a shell command.

Scope guard: no generic shell field, arbitrary host/path arguments, credentials, generic task mutation, or broad deployment console. Initial acceptance uses synthetic queued push and switch jobs, verifies display and deep-link evidence, proves host/HEAD mismatch rejection before execution, proves an agent identity cannot execute, and produces a linked receipt only after a human-confirmed successful handler run. This settled product direction does not authorize implementation, a flake mutation, service change, or rebuild.


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

**v0 is not hypothetical — it has already been running successfully since at
least 2026-07-16, confirmed by Dave 2026-07-19: "I get a better quality prompt
where it matters, and you do not have to be a glorified grammar and spell
checker. Look at tigwa's requests. Those are my prompts now."** The
`inbox/tigwa/*-REQUEST-*.md` / `DAVE-REQUEST-*.md` (recorded-by-Tigwa) files
already ARE the v0 loop below, in production use, not a pilot to try —
concrete proof: `CLAUDE-REQUEST-ebay-listing-form-parity-audit-2026-07-16.md`
is headed "From: Tigwa, recording Dave's direction" and is a fully scoped,
unambiguous spec with explicit acceptance criteria and evidence standards
("do not fabricate values," "no guessed backing lists") — exactly the
"structured rendering" v0 below describes, already happening. This resolves
§5.1's open question: don't treat this as "run the pilot before committing" —
it's already proven; the open question is whether/when to formalize into v1,
not whether v0 works.

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

## 2026-07-19 decisions and clarifications (Dave, relayed/recorded by Tigwa)

Resolves several of the §5 open questions and adds new framing/scope. Still
**design decisions only — no schema, UI, worker, or authority change authorized.**

**Core framing — action console.** A translated prompt is an *action console*: a
visible operator surface where rough intent becomes a target-specific instruction
Dave can inspect/redirect/explicitly send — not merely a store/retrieve mechanism.

**§5.3 draft-iteration cap — resolved.** No hard cap on redraft *count*; instead a
combined budget per active drafting run: **10 minutes active wall-clock
deliberation OR 8 substantive agent-generated re-drafts, whichever comes first**
(a "substantive re-draft" = a new target-specific rendering; UI refreshes, Dave's
own edits, and viewing a draft don't count). On hitting either bound: preserve raw
input, current draft(s), checker findings, visible progress; set state to a
labelled **paused / awaiting Dave**; never auto-send/discard/archive/retry/resume.
Dave can resume later (edit/clarify/re-voice-type), which grants a fresh bounded
window. This is a resource/attention safeguard, not an iteration limit on Dave.

**§5.4 stale-card handling — still open.** Settled: never auto-archive, delete,
send, or expire a card; manual archive/delete only, with retention/audit semantics
(especially manual-deletion-vs-immutable-raw-input) to be designed explicitly
before any build packet. NOT yet settled: the precise surfacing policy (where/when
an unsent `ready` card gets shown to Dave, what if any action follows) — Dave
explicitly deferred this pending clearer definition of "surface."

**§5.2 send authority — reaffirmed, with one addition.** Dave-only, always, no
delegation — as recommended. New: an operator-visible **"I'm feeling lucky"**
button — when Dave clicks it, sends the current fixed-up/rendered version directly
through the existing mailbox. Still Dave's explicit action, not agent-initiated;
exact preview/confirmation semantics before that button ships remain unspecified.

**§5.1 / §5.5 — reaffirmed as recommended.** v0 (zero-code pilot) before any table
or UI; fixed manual target-agent list, additions deliberate/manual only.

**New: reusable/pinned prompts.** A commonly-reused action-console starting point
can be pinned. Reuse = choosing to send again, not scheduled/automatic dispatch —
the delivery/use log (which records every send, repeat or not, and its outcome)
supplies workflow history rather than requiring a separate reuse-tracking
mechanism. A pinned prompt may be lightly edited per instance (e.g. swap a SKU in
a pinned "research SKU xxx" template) before sending — the source/pinned template
must be preserved and the particular rendered/sent instance logged separately, so
a one-off edit never silently mutates the reusable template. Still undecided:
whether a pin holds one immutable raw template plus versioned drafts,
target-specific renderings, and/or a copy-to-new-card flow — deferred to a later
design pass, no schema/UI decision authorized by this note.

**New: reverse direction — agent-to-Dave delivery (Dave, 2026-07-19):** "You
and Tigwa can deliver things to my clipboard and I can act on them without 10
extra steps." Everything above was Dave capturing outward (copy → translate →
send). This is the sibling direction: Claude/Tigwa deliver content *into*
Dave's clipboard surface so he can act on it (paste, approve, dismiss) in one
step instead of context-switching to a mailbox message and manually copying
the relevant piece out. Directly attacks the "10 extra steps" friction of the
current `inbox/dave/` mailbox pattern (Dave has to open the file, find the
part he wants, copy it by hand).

**This inverts the safety posture, not just the data flow.** Every clipboard
rule settled so far ("Dave-initiated only," "no silent overwrite," "no
background writes/polling") was written for *Dave copying out*. An agent
*writing in* is a different risk: a silent write could clobber whatever Dave
currently has legitimately copied for something unrelated, with no visible
cause. **Resolved (Dave, 2026-07-19): request-initiated, not agent-initiated.** "This
would be a request initiated action. Similar to drop it in my Sync folder."
Dave asks for delivery; Claude/Tigwa then drop the content somewhere Dave
picks it up from — same shape as an existing pattern already in daily use
(placing a file in his Sync folder for him to find), not an unsolicited push
onto his live, currently-in-use clipboard. Resolves the open question as
option (a): the delivered item lands as a discrete, addressable artifact/entry
Dave explicitly consumes when ready, never a silent direct write onto the live
OS clipboard the moment it's produced. Matches this project's existing
"review is pulled not pushed" convention elsewhere. Mechanism (a picker entry,
a drop-folder-equivalent, or something else) still undecided — the *trigger
model* (Dave-requested, agent never initiates unprompted) is now settled. No
implementation authorized by this note.

**Worked example, from real friction today (Dave, 2026-07-19).** The eBay
support request Tigwa just prepared (`EXTERNAL-SUPPORT-TICKET-REGISTER.md` +
its attachments — `/home/db/Sync/ebay-dev-support-orphaned-offer-25707.txt`,
`/home/db/Sync/ebay-dev-support-eps-limit-increase.txt`) cost Dave real time
just locating the request text and its supporting documents before he could
even start submitting it — a ~5-minute startup tax before any actual work on
the ticket began. Had the prepared text + attachment references already been
sitting as one ready-to-use clipboard entry (on request), that entire hunt
disappears. This is the concrete case the reverse-direction delivery model
above is for: Tigwa/Claude finish preparing something that needs Dave's
follow-through (a support ticket, a prepared message, anything with
scattered supporting artifacts), Dave requests delivery, and the relevant
prepared content + artifact paths land as one addressable, ready-to-act-on
entry instead of requiring him to reassemble it by hand from the register/
taskboard/Sync folder each time.

**New: initial-prompting gap.** The mailbox/inbox path only reaches an
already-inbox-polling agent — it does not solve getting an instruction into an
agent's *active terminal/session* when nothing is watching the inbox promptly.
Dave's current observation: this needs a separate, explicit operator-triggered
handoff — e.g. a tightly scoped `tmux send-keys` action, or Dave manually
triggering final delivery himself. **Not authorized by this note**: any
`tmux send-keys` implementation, adapter, service, credential, or authority change
— this is a named gap for a later design pass, not an approval to build it.

**New: clipboard integration direction.** Dave sees clipboard integration as
potentially a better interface than mailbox-only delivery for some flows — but
strictly as an operator-facing handoff/pre-fill surface, never an ambient command
channel. The console may copy a selected rendered prompt to the clipboard for
Dave to paste himself; may later support named target adapters (e.g. a permitted
tmux session) *if separately designed and approved*; clipboard capture/use must
be visibly Dave-initiated/confirmed — no background polling, silent overwrite, or
automatic dispatch. Design consequence: the action console should expose distinct
delivery modes (`mailbox/in-process`, `copy-to-clipboard/manual-paste`, and any
future explicitly-approved `active-session interrupt`), each surfacing its own
delivery state in the use log, each retaining Dave-only final authority. No
clipboard integration, tmux automation, adapter, service, credential, or
authority change is authorized by this note either.

## Architecture reframe — clip types as discrete apps sharing one interface (Dave, 2026-07-19)

This changes §1/§3 above, not just adds to them. Each recognized clip type
(SKU, URL, freeform-text, combined-buffer, and now `prompt`) is effectively its
own small application sharing the one clip-picker interface — the way a file
manager hosts a different handler per file type, not a single dumb string
list. "Turn this into a prompt" does not navigate to a separate outbox screen:
it instantiates a tiny app **in place, directly over the clip entry itself**
(inline state change on that entry, not a new window). That instantiated
thing — draft/checker/send state and all — is itself just another entry in the
same list, typed `prompt`.

Consequence: **the action console is not a distinct UI.** It's the existing
clip picker (`tgw-clipd` + rofi picker, PP-CLIP-001, already local, already
shipped) with a filter chip — e.g. a `Prompts` button — that shows only
entries of type `prompt`. This directly answers §3's "minimal first UI"
question: there may be no separate outbox UI to build, ever — just a type
filter on infrastructure that already exists. `instruction_cards` (§1) stops
being an independent table with its own page and becomes one more type-handler
plugged into `tgw-clipd`'s existing model, alongside SKU/URL/combined-buffer
handlers. `combine-clips` (PP-CLIP-001) is the same pattern already live for
one type; `prompt` becomes the next type using the same host mechanism.

Still open: how a clip entry's type gets set (recognized automatically at
capture, per PP-EVENTD-001's "any recognized clipboard write" framing, vs.
promoted explicitly by the "turn this into a prompt" action) — not resolved by
this note. No schema/UI decision authorized here either; this reframes the
target shape, it doesn't build it.

## Per-clip-type logging (Dave, 2026-07-19)

The use/delivery log for a clip-derived card is likely **not one uniform shape
across clip types** — what's worth recording differs by what was captured.
Follows directly from PP-EVENTD-001's existing "recognized clipboard write"
framing (a SKU write vs. a URL vs. freeform text are already distinct
recognized types there) and the §1 data model's `intent_type`/`context_links`
fields. Sketch, not a schema decision:

- A **SKU-typed** clip's log should link back to the item (`context_links`
  entry, matches every other SKU-anchored record in this project).
- A **URL-typed** clip's log plausibly wants the resolved target (page
  title/fetch result), not just the raw string.
- A **freeform-text prompt** clip's log is closest to the existing model:
  rendered text + send record, nothing else to resolve.
- A **combined/joined** clip (multiple fragments via `combine-clips`) needs its
  log to preserve which source clips went in, not just the merged result — same
  "raw input stays distinguishable" principle already settled for drafts.

Not decided: whether this is one `context_links`-driven log format that
branches by `type`, or genuinely separate log shapes per clip type. Flagged
for the later schema/UI design pass, not resolved here.

## Prior art — clipboard-triggered inline actions (Dave, 2026-07-19)

Dave: "The concept is not new. Personalizing, automating and TGWifying it is new."
The clipboard-as-capture-surface direction above (event clipboard → inline
per-entry action → still a normal pasteable clip) is a known pattern in
established Linux clipboard managers, not a novel mechanism:

- **CopyQ** — closest match. "Automatic Commands": rules fire on clipboard
  content matching a pattern (MIME type/regex), offering a custom action menu
  or running a script, while the entry stays normal clipboard history. Also
  supports combining/editing multiple clips into one before paste — same shape
  as PP-CLIP-001's existing `combine-clips` action.
- **KDE Klipper** — simpler precedent: copy text matching a configured pattern
  (e.g. a URL) and it offers inline actions ("open in browser") directly from
  the clipboard applet.
- **Raycast / Alfred** (macOS, launcher-tied rather than a standalone clipboard
  daemon) — same "history entry + inline per-entry action" pattern, different
  host mechanism.

What's actually new here is not the trigger pattern but the destination:
TGWifying it means the inline action hands off into PP-OUTBOX-001's own
draft/checker/Dave-review/explicit-send lifecycle (not a generic script or
launcher command), reuses PP-CLIP-001's existing local-only `tgw-clipd` +
`combine-clips` instead of adopting a new clipboard daemon, and ties into
PP-EVENTD-001's "clipboard write is the trigger" framing already designed for
this project. Still design-only — no CopyQ-equivalent scripting layer, no
inline action UI, no capture-time-vs-picker-time trigger decision made by this
note.

## Not done by this note

No table created, no UI built, no mailbox `msg_type` added, no agent authority
changed, no tmux/clipboard integration built. This remains design evaluation only.
