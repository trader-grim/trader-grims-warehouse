# clipboard-agent-delivery: request-initiated delivery of agent-prepared content into Dave's clipboard, as Phase 0 of the clipboard-as-operations-interface

**Status:** Draft — 2026-07-19
**PP ref:** PP-CLIP-001 (extends), PP-OUTBOX-001 (implements the reverse-direction decision), PP-EVENTD-001 (referenced, not required for this phase)

## Update, 2026-07-19 (Dave, same session)

Two resolutions to the draft above:

1. **The `launch_rofi_picker()` prefix-match bug is not just theoretical hardening —
   Dave confirms it's the likely cause of a real, currently-occurring symptom**: "it bites
   often and I have to edit clips to get them to fully paste." Paste corruption from
   picking a row whose 120-char truncated prefix collides with (or is a prefix-match subset
   of) another row's content — pulling back the WRONG or truncated full content — matches
   this exactly. **This fix should not wait behind the delivery feature** — worth shipping
   as its own fast fix regardless of when Phase 0 delivery lands, since it's actively
   corrupting Dave's daily clipboard use today, independent of anything agent-delivered.
2. **Delivery mechanism, cross-machine case: "same standard pattern. MCP."** Resolves the
   open question below — not an ad-hoc SSH command invocation, but the same MCP-tool pattern
   already established for every other cross-boundary write in this project
   (`mcp__tgw__tgw_enqueue`, `tgw_add_suggest`, `tgw_mailbox_send`). Add `tgw_clip_deliver`
   as a new MCP tool on the existing `tgw` MCP server, callable by both Claude (same-machine)
   and Tigwa (a1131, cross-machine, over her existing MCP link) — no new SSH-based mechanism
   needed, no new infrastructure, consistent with how every other agent-initiated TGW write
   already works. **Important wrinkle this surfaces**: Tigwa's MCP link is currently
   READONLY (`TGW_MCP_READONLY=1`, excludes `tgw_enqueue`/`tgw_add_suggest` "while Tigwa is
   IN TRAINING" — see CLAUDE.md's Hermes-lite section). `tgw_clip_deliver` is the same class
   of write as those two, so it needs the same exclusion decision made explicitly for it,
   not silently left write-enabled for Tigwa by omission.

## Problem / motivation

Today, when Claude or Tigwa prepares something Dave needs to act on — a support-ticket
submission, a prepared message, anything with scattered supporting artifacts — Dave has to
locate the request text and any supporting documents by hand before he can start (register
file → attachment paths → manual copy) before he can even begin using it. Dave named this
concretely today: locating the eBay support-ticket text + its two attachment files cost him
a real ~5-minute startup tax before he could start submitting the ticket.

Dave's own framing (2026-07-19 design session, recorded in `pp/PP-OUTBOX-001.md`): "You and
Tigwa can deliver things to my clipboard and I can act on them without 10 extra steps" —
and when asked to prioritize among several open threads from that session, this is the one
he named directly: "That's what I am really after right now. It will relieve a lot of
bottlenecks."

This plan scopes ONLY that reverse-direction delivery feature — request-initiated,
Dave-consumed — as Phase 0 of the larger clipboard-as-operations-interface vision (full
vision and all other decisions already recorded in `pp/PP-OUTBOX-001.md`; this plan does not
relitigate any of that, only sequences this one piece for execution).

## Constraints (from settled architecture + today's design decisions)

- **Local-only, forever** (PP-CLIP-001 ratified 2026-07-11): `tgw-clipd`/the `tgw clip` CLI
  stay per-machine, per-user (`~/.local/share/tgw-clip/history.db`). This feature runs
  entirely within that existing local store — no new daemon, no cross-machine socket for
  Phase 0.
- **Request-initiated only, never unsolicited** (Dave, 2026-07-19, resolved in
  `pp/PP-OUTBOX-001.md`): the agent never pushes to Dave's clipboard on its own initiative.
  Dave asks; the agent then delivers.
- **Never a silent direct write onto the live OS clipboard** (same resolution): delivered
  content lands as a discrete, addressable **entry** in the existing clip history that Dave
  explicitly selects/loads when ready — reusing the picker's existing select-to-load flow
  (`tgw clip get --id N --copy`), not an immediate overwrite of whatever's currently on
  Dave's live clipboard.
- **App-code change, routes through `tgw-coder`** (invariant E12): `src/tgw/clip.py` and
  `src/tgw/clipd.py` are both under `src/tgw/` — any edit goes to `tgw-coder`'s isolated
  worktree+branch, not a direct edit in the shared checkout, same as every other app-code
  change this session.
- **Secrets from `secrets_root`; no hardcoded paths** — not directly relevant here (this
  store has no secrets), but any cross-machine delivery path (see Open Questions) that adds
  credentials must follow this rule.
- **A worker/tool's skip or guard is a finding, not silent** (invariant C11 spirit, applied
  here even though this isn't a pipeline worker): if delivery fails (e.g. DB locked, disk
  full), the CLI must return `{ok: false, error: ...}`, never silently drop the content.

## Proposed approach

Reuse 100% of PP-CLIP-001's existing local infrastructure — no new service, no new daemon,
no new database. Add one new CLI verb and two new (nullable, additive) schema columns.

**Schema** (`src/tgw/clip.py`, `_connect()`): add two nullable columns to the existing
`clip_history` table via `ALTER TABLE ... ADD COLUMN` guarded by a `PRAGMA table_info`
check (SQLite has no `ADD COLUMN IF NOT EXISTS`) — existing rows get `NULL`/default,
fully backward compatible with the current schema and every existing query:
- `origin TEXT NOT NULL DEFAULT 'clipboard'` — `'clipboard'` (a real clipboard capture,
  current behavior) or `'agent'` (delivered via this feature). Matches the `origin:
  "operator"` stamping pattern already used elsewhere in this project (invariant C10).
- `label TEXT` — optional short human-readable description for agent-delivered entries
  (e.g. `"eBay support ticket text + attachments"`), since delivered content won't always
  be self-describing at a glance in the picker the way a raw copied string usually is.

**New CLI verb**: `tgw clip deliver "<content>" [--label "..."] [--requested-by claude|tigwa]`
in `src/tgw/clip.py`'s `cmd_clip()` and `api.py`'s `clip` subparser (add `"deliver"` to the
existing `clip_action` choices list at `api.py:647`, plus a `--label` argument and a
`--requested-by` argument defaulting to `"claude"`). Calls a new `deliver_clip()` function
(same shape as the existing `record_clip()`) that inserts with `origin='agent'`. Returns
`{ok: True, id: <rowid>}` on success — same contract as every other TGW CLI command.

**Picker display** (`src/tgw/clipd.py`, `cmd_clip`'s `list`/`search` print formatting, and
`launch_rofi_picker()`): show a distinguishing tag for `origin='agent'` rows, same pattern
as the existing `[SKU]` tag — e.g. `[AGENT]` — so Dave can tell at a glance which entries
are agent-delivered vs. actual clipboard history, and show `label` instead of/alongside the
truncated `content` preview when present.

**Required fix while touching `launch_rofi_picker()`**: its current reverse-lookup after a
rofi selection does a `content LIKE '<truncated-120-char-selection>%'` match against the DB
— fragile even today (two rows sharing a 120-char prefix collide), and outright broken once
list rows show a `label`/tag prefix that isn't literally in the `content` column. Fix:
feed `id` alongside the display text into rofi (e.g. `f"{id}\t{display_text}"`, split on
tab after selection) and look up by `id` directly, not by content-prefix matching.

**Delivery mechanism: `tgw_clip_deliver` MCP tool** (resolves the cross-machine open
question — Dave, 2026-07-19: "same standard pattern. MCP."). `src/tgw/mcp_server.py`
already gates write-capable tools behind `TGW_MCP_READONLY` at registration time —
`tgw_enqueue`/`tgw_add_suggest` are only registered `if not _READONLY:` (lines ~336, ~410).
Add `tgw_clip_deliver(content, label=None)` following the exact same shape as the existing
`tgw_enqueue`/`tgw_mailbox_send` tool functions (thin wrapper calling `deliver_clip()`),
registered the same way: `if not _READONLY: mcp.tool()(tgw_clip_deliver)`. This makes it
callable identically by Claude (same-machine, direct) and Tigwa (a1131, over her existing
MCP link) with zero new infrastructure — no SSH command invocation, no new daemon, reusing
the exact boundary every other agent-initiated TGW write already goes through. Since
`tgw_clip_deliver` is the same class of write as `tgw_enqueue`/`tgw_add_suggest`, it inherits
their READONLY gate by construction — Tigwa's current "IN TRAINING" read-only mode
automatically excludes it too, same as those two, until that mode is lifted for her.

Dave then opens the existing rofi picker (already bound to a keybind per PP-CLIP-001 Phase
2) and selects the `[AGENT]`-tagged entry to load it onto his live clipboard when he's
ready — exactly the existing select-to-load flow, no new UI to learn.

## Files to change

| File | Change |
|------|--------|
| `src/tgw/clip.py` | Add `origin`/`label` columns (additive `ALTER TABLE`); add `deliver_clip()`; add `'deliver'` branch to `cmd_clip()`; update `list_history()`/`search()` SELECTs to include the new columns |
| `src/tgw/clipd.py` | Update `cmd_clip`'s list/search print formatting to show `[AGENT]` tag + `label`; fix `launch_rofi_picker()` to key selection by `id`, not content-prefix match; show `[AGENT]`/`label` in the rofi feed |
| `src/tgw/api.py` | Add `"deliver"` to the `clip` subparser's `clip_action` choices (~line 647); add `--label` and `--requested-by` arguments (CLI path kept for local/manual use and testing, alongside the MCP tool) |
| `src/tgw/mcp_server.py` | Add `tgw_clip_deliver(content, label=None)` tool function, same shape as `tgw_enqueue`/`tgw_mailbox_send` (~line 273/525); register with `if not _READONLY: mcp.tool()(tgw_clip_deliver)` (~line 336/410 pattern) |
| `tests/test_clipd.py` (or a new `tests/test_clip.py` if that split doesn't already exist — verify at execution time) | Cover: `deliver_clip()` inserts with `origin='agent'`; schema migration is idempotent on an existing pre-migration DB file; `list`/`search` surface the new columns; rofi picker id-based lookup returns the correct full content for two rows sharing a truncated prefix |
| `tests/test_mcp_server.py` (verify exact filename at execution time) | Cover: `tgw_clip_deliver` is registered when `TGW_MCP_READONLY` is unset/`0`, and NOT registered when it's `1` — same assertion pattern as the existing `tgw_enqueue`/`tgw_add_suggest` READONLY tests |

## Acceptance criteria

- [ ] `tgw clip deliver "test content" --label "test"` on a fresh or existing DB returns
      `{"ok": true, "id": <int>}` and does not corrupt/lose any pre-existing rows
      (run against a copy of a real `~/.local/share/tgw-clip/history.db`, not just a fresh
      throwaway DB, to prove the migration is safe on real data — Prime Directive 1)
- [ ] `tgw clip list` shows the delivered entry tagged `[AGENT]` with its label, distinct
      from real clipboard-captured entries
- [ ] Rofi picker (`tgw clip pick` / the existing bound keybind) shows the delivered entry,
      and selecting it loads the correct full content onto the live clipboard — verified
      live on tgw-prod's actual Sway session, not just unit-tested (Prime Directive 4)
- [ ] Selecting two rows that happen to share the same 120-character truncated prefix in the
      rofi picker loads the CORRECT, different full content for each (proves the id-based
      lookup fix, not the old fragile prefix match)
- [ ] `tgw_clip_deliver` is registered on the MCP server when `TGW_MCP_READONLY` is
      unset/`0`, and absent from the tool list when it's `1` — proves Tigwa's current
      training-mode restriction covers this new write path automatically
- [ ] Full test suite green; `tgw health` unchanged after the change
- [ ] Live worked-example run: deliver the actual eBay support-ticket text (or an equivalent
      real prepared artifact) to Dave's clipboard on request, and confirm Dave can select
      and use it — this is the concrete case the feature exists for
- [ ] Separately, live-verify the rofi picker id-based-lookup fix resolves the paste-
      corruption symptom Dave already experiences today — pick several existing real clip
      history entries (not just synthetic test rows) and confirm each pastes its true,
      complete content

## Open questions

- **Retention/pruning interaction:** `record_clip()`'s existing retention (max 2000 rows /
  14 days) prunes oldest-by-id. Should agent-delivered rows be exempt from pruning (like SKU
  rows already are exempt from `wipe_nonsku`), so a delivered item Dave hasn't gotten to yet
  doesn't silently age out? Leaning yes, given Prime Directive 1 and the "never silently
  drop" boundary from `pp/PP-OUTBOX-001.md`'s stale-card discussion — but not yet asked.
- **Does `deliver` need a `--requested-by` distinction at all for v0**, or is `origin='agent'`
  alone sufficient until there's an actual need to tell Claude-delivered from
  Tigwa-delivered apart? Included in the schema above as optional/cheap to add now; could be
  dropped from v0 if Dave doesn't want it.
- **`Prompts`-filter / full action-console UI** (the rest of `pp/PP-OUTBOX-001.md`'s
  2026-07-19 design) is explicitly NOT in this plan — this is Phase 0 only, scoped to what
  Dave named as most urgent. A follow-up plan should cover the `prompt` clip-type, inline
  action-console instantiation, and the `Prompts` picker filter once this phase is live and
  proven.
