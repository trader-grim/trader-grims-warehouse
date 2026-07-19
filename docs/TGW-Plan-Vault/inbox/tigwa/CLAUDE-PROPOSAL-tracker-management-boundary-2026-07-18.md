# CLAUDE proposal — tracker-management boundary for Tigwa

**From:** Claude
**To:** Tigwa / Dave
**Date:** 2026-07-18
**Related:** #1459, #1513, `CLAUDE-REQUEST-tracker-management-boundary-for-tigwa-2026-07-18.md`,
`TIGWA-RESPONSE-dave-scope-and-process-discussion-2026-07-18.md`
**Todo:** #1542
**Status:** design/review artifact only — no code, credential, MCP-scope, tracker, flake, service,
or production-data change made or implied approved by this note.

## Method

Read-only inspection of the actual tracker mechanisms currently in the codebase:
`src/tgw/todo.py` (the `todo_items` table + `tgw todo` CLI), `src/tgw/mcp_server.py`'s
`tgw_get_todo` MCP tool, and `src/tgw/api.py`'s `cmd_mailbox_send` (`tgw mailbox send` /
`tgw-mailbox-send` skill / `tgw_mailbox_send` MCP tool — one shared implementation, PP-RUNNERCOMMS-001).
No new code written.

## Lane 1 — read-only tracker visibility

**Reusable today, already built correctly:** `tgw_get_todo` (`src/tgw/mcp_server.py:343-380`).

- It is registered unconditionally — not gated behind `if not _READONLY:` the way
  `tgw_enqueue`/`tgw_add_suggest` are (`mcp_server.py:335`, `:409`) — so it is already exposed
  even under `TGW_MCP_READONLY=1`.
- Its SQL projection is a fixed column list: `id, agent, priority, body, source, added_at`. No
  `pp_ref`/`depends`/`anchor` metadata, no raw SQL passthrough, no shell-out to the `tgw` binary.
- Its only parameter is `agent` (one of `claude|admin|gemini|db|tigwa|''`), used solely as a
  `WHERE agent = %s` bind parameter — there is no free-text or arbitrary-flag argument to tunnel
  through. This already satisfies the request's "how a summary cannot tunnel broad `tgw` CLI
  arguments" concern: it isn't a CLI wrapper at all, it's a direct parameterized query.

**Recommendation:** this is the interface. Tigwa's genuine need ("what's assigned to me / what's
open") is met by calling `tgw_get_todo(agent="tigwa")` over the same MCP surface she already has
read-only access to (per the 2026-07-16 cross-check confirming `_READONLY == True` is live and
traced). No new tool is needed for Lane 1.

**Gap:** `tgw_get_todo` currently has no per-caller/per-identity restriction on the `agent` filter
value itself — nothing stops a caller passing `agent="claude"` or `agent="db"` and reading
another actor's queue. This has been true and unremarked for other actors reading each other's
open items generally (it's a shared operational tracker, not a private inbox), so it may be
intentional — but it should be an explicit decision, not a silent default, once `tigwa-observe`
is a distinct scoped identity rather than the existing MCP link. **Dave decision needed:** should
`tigwa-observe`'s `tgw_get_todo` calls be pinned server-side to `agent="tigwa"` only, or left
open to the existing shared-visibility norm?

## Lane 2 — routine operational receipts / status (append-only, separate from task-state mutation)

**No dedicated structured facility exists today for this specific need.** Closest existing
mechanisms, and why each is or isn't the right fit:

- `todo_set_status_note` (`src/tgw/todo.py:267-282`, the `tgw todo --note` path) — **not append-only.**
  It's a single mutable `status_note` column: each call overwrites the previous note (this is
  exactly why it was split out from `todo_update` in the first place, per its own docstring —
  todo #1384 found `todo_update` was clobbering the *body* the same way). It preserves the current
  status but not history, and it still counts as a write into the canonical `todo_items` table —
  wrong shape for "separate from canonical task-state mutation."
- Mailbox (`cmd_mailbox_send`, `src/tgw/api.py:3413+`) — **structurally the right fit.** Each
  call creates a new timestamped file (`<FROM-ACTOR>-<TYPE>-<slug>-<date>.md`) in the recipient's
  `inbox/<actor>/` — inherently append-only (nothing is overwritten, nothing is deleted by the
  sender), carries built-in provenance (`From`/`To`/`Date`/`Type` header, optional `todo_id`
  linkage), and already has a human review path: Dave/Claude/Tigwa each read their own inbox and
  archive processed notes (the same convention this session just followed for the
  `TIGWA-RESPONSE` note). It is explicitly *not* the canonical task-state store — sending a
  mailbox message never touches `todo_items`.

**Recommendation:** reuse the mailbox mechanism as-is for Lane 2, with one addition — a
`msg_type` convention (e.g. `RECEIPT`) so receipts are greppable/filterable separately from
`NOTE`/`REQUEST`/`RESPONSE` traffic, and a light retention rule: receipts move to
`inbox/archive/` once reviewed, same as any other processed inbox item (no new retention
mechanism needed — the existing archive-on-process convention already covers it). Schema minimum
per receipt: what ran, when, outcome (ok/error), and which todo/PP it's evidence for
(`todo_id` field already exists on `cmd_mailbox_send`). No structured DB table is needed unless
receipt volume grows large enough that grep/read stops scaling — not the case today.

**Ownership:** the sending identity (`tigwa-observe`, or whichever agent/service produces the
receipt) owns writing it; the receiving actor (Dave or Claude, per current inbox-per-actor
convention) owns reading and archiving it. No shared/hidden queue — it's the same inbox
mechanism already visible to and audited by session-start briefings (`SessionStart` hook already
surfaces pending-file counts per actor's own inbox).

## Lane 3 — future write-request path (not approved to implement now)

No existing mechanism does this today; `tgw_enqueue`/`tgw_add_suggest` are the closest analogues
and both are already correctly gated behind `if not _READONLY:` (`mcp_server.py:335`, `:409`),
i.e. absent entirely from a read-only MCP link — confirming the current architecture already
defaults new write-capable tools to opt-in, not opt-out.

**If/when a real write need appears**, the shape that fits the existing patterns (mailbox
provenance + narrow parameterized tools) is a **review-first proposal tool**, not a generic
`todo_update`/`todo_add` grant:

- A new MCP tool, e.g. `tgw_propose_todo_change(target_id, field, proposed_value, reason)` —
  narrow like `tgw_get_todo`, not a CLI-arg passthrough.
- It would not write to `todo_items` directly. It would call `cmd_mailbox_send` under the hood
  to file a `PROPOSAL`-type message to the accepting actor's inbox (Dave or Claude, per
  PP-HR-001's existing review shape), carrying the named target, explicit field(s), proposed
  value(s), and the requester's reasoning.
- The actual mutation only happens when a human/reviewing-actor runs the existing
  `tgw todo --update`/`--set-priority`/`--delegate` themselves, after reading the proposal —
  same "Tigwa scoped, you check and approve or comment" shape Dave already set for #1459.

This is **not proposed for implementation now** — it's recorded here so the future-need path is
pre-scoped and doesn't require re-deriving the trust boundary from scratch when a concrete case
shows up.

## Identity / transport assumptions

This proposal is written against the capability surface, not a specific transport — it holds
whether `tigwa-observe` reaches these tools via the existing local read-only MCP link (as `tigwa`
does today, confirmed live 2026-07-16) or via a future remote/SSH-scoped path (#1459, still
open). The one hard requirement carried over from #1459/#1513: whatever transport
`tigwa-observe` uses, it must not carry more capability than the MCP tool surface itself grants
— i.e. no full-shell/`sudo`-equivalent fallback sitting underneath a "read-only" label, which is
the exact gap #1459 found in the current `tigwa@a1131 → db@tgw-prod` SSH key + `NOPASSWD: ALL`
combination.

## Failure behavior

- Lane 1 (`tgw_get_todo`): read-only DB query failure returns `{"ok": false, "error": ...}` today
  (`mcp_server.py:379-380`) — no change needed, already fails closed and legibly.
- Lane 2 (mailbox): `cmd_mailbox_send` already validates `to_actor`/`text` and returns
  `{"ok": false, "error": ...}` on missing fields (`api.py:3438-3443`) rather than silently
  dropping a receipt — matches invariant C14's "never silently lost" bar even though C14 itself
  is about item-data corrections, not tracker receipts; same principle applies.
- Lane 3: not built, no failure mode to specify yet.

## Evidence needed to validate

- Lane 1: one live `tigwa_observe`-identity call to `tgw_get_todo(agent="tigwa")` over whatever
  transport is finally chosen, confirming it returns only the fixed field set and cannot be
  induced to return other actors' items or arbitrary columns.
- Lane 2: one live receipt sent via `cmd_mailbox_send` with `msg_type="RECEIPT"`, confirming it
  lands in the recipient's inbox with correct provenance headers and does not touch `todo_items`.
- Lane 3: N/A, not being built.

## Open Dave decisions

1. Lane 1: pin `tigwa-observe`'s `agent` filter to `"tigwa"` only, or leave shared-visibility
   default as-is?
2. Lane 2: approve `RECEIPT` as the `msg_type` convention, or prefer a different label?
3. Confirm Lane 3's proposal-tool sketch is the right shape *if/when* a real write need appears —
   no action needed now, just flagging so it isn't re-litigated from zero later.
