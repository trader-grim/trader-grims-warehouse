# radar: a centralized, encrypted, networked entry/action service — "our own responsive Radar O'Reilly clipboard"

**Status:** Design settled, ready for build — 2026-07-19 (Dave: "we should build it
regardless if it is perfect because it is a hell of a lot better than what we are doing
now and we can always improve it")
**PP ref:** PP-RADAR-001 (NEW — proposed by this plan, needs Dave's confirmation to open
formally). Supersedes PP-OUTBOX-001 as the vehicle for that design (all decisions there
carry over unchanged, this plan sequences them for execution). Retires PP-CLIP-001's
`tgw-clipd` from the primary-interface role (not deleted — see below). References
PP-EVENTD-001 for the "Radar" name/anticipatory framing, and reuses its already-settled
"Postgres, not SQLite, for shared cross-machine state" call — but this is its own build,
not PP-EVENTD-001's Go daemon.

## Revision note

This plan went through three shapes in one session, each superseding the last — recorded
here so a future reader doesn't have to reconstruct the reasoning from chat history:
1. Extend `tgw-clipd` (PP-CLIP-001) → rejected: OS-clipboard interception is structurally
   insecure (no X11 access control, indiscriminate persistent capture = designed-in
   exfiltration target).
2. A new but still per-machine, local-SQLite-only app → superseded same session: doesn't
   give Dave real multi-device reach of his own entries, only one-way agent delivery.
3. **A centralized, networked service on the existing shared Postgres, fenced through
   `tgw-http`** — this is the settled design below.

## Problem / motivation

Same root motivation as the prior revision (full detail there / in `pp/PP-OUTBOX-001.md`):
Dave needs a way to (a) deliberately capture/act on typed content, (b) receive
agent-prepared content on request without hunting for it by hand (concrete cost measured
today: ~5 minutes lost locating the eBay support-ticket text + attachments), and (c) do
both without the security exposure of an OS-clipboard-interception daemon.

What changed this session, after the local-only design was already drafted: Dave asked
"what if we built this as a fully encrypted networked clipboard service?" — i.e., not just
agent-to-Dave delivery, but genuine multi-device reach: the same entries visible from
tgw-prod, a1131, and eventually a phone, not a separate local store per machine. His
reasoning, and it holds up: TGW already runs a shared, trusted, cross-machine-reachable
state store (Postgres `state_machine`) — centralizing there isn't new infrastructure risk,
it's reusing the exact pattern every other piece of shared TGW state already relies on. It
also buys one audit trail instead of N, and avoids fanning content out to every host
"just in case."

## Constraints (from settled architecture + this session's decisions)

- **App-code change, routes through `tgw-coder`** (invariant E12): all of `src/tgw/` is
  gated — dispatches to `tgw-coder`'s isolated worktree+branch, not a direct edit in the
  shared checkout.
- **`tgw-http` is the fence for Radar, same principle as `tgw-api` is the fence for
  ItemData** (settled architecture, extended by this plan, not a new principle): no client
  — CLI, MCP tool, future Flutter leg — talks to Postgres directly except `tgw-http` itself.
  One function owns the write path, one owns the read path; every entry point calls
  through those, never duplicates them.
- **Request-initiated delivery only, never unsolicited** (Dave, `pp/PP-OUTBOX-001.md`): an
  agent never creates an entry on its own initiative — Dave asks, then delivery happens.
- **Same MCP-tool pattern as every other cross-boundary agent write** (Dave: "same standard
  pattern. MCP"): `tgw_radar_deliver`, `TGW_MCP_READONLY`-gated exactly like
  `tgw_enqueue`/`tgw_add_suggest`/`#1563`'s `tgw_clip_deliver` — but now the tool calls
  `tgw-http`'s endpoint (or the same underlying function `tgw-http` calls), not a local
  SQLite write, since the store is centralized.
- **Encryption is server-side, at the `tgw-http` fence, using the OS Secret Service**
  (`org.freedesktop.secrets`, confirmed live via gnome-keyring on tgw-prod). Explicitly
  scoped, and documented in-code as scoped: defense-in-depth against generic/automated
  DB-scraping and passive network snooping — **not** true end-to-end encryption, and not a
  claimed boundary against a targeted compromise of `tgw-http` itself or the Postgres host.
  This is the deliberate cheap-version choice (Dave: "seems doable" for the shared-key
  approach; real per-device key exchange named as a later, harder upgrade, not this build).
- **Security-by-obscurity is a legitimate secondary layer here, explicitly not primary**
  (Dave, this session) — TGW's actual threat model is opportunistic/generic, not a
  targeted nation-state actor; a non-standard protocol that no generic scraper recognizes
  is real, if soft, defense. Never substitute this reasoning for the encryption/exclusion
  work above.
- **A worker/tool's skip or guard is a finding, not silent** (invariant C11 spirit): a
  delivery/read failure returns `{ok: false, error: ...}`, never silently drops content.
- **Secrets from `secrets_root`; no hardcoded paths** — no new secrets file; the encryption
  key lives in the keyring, not in `secrets_root` or anywhere in `src/`.
- **PP-CLIP-001 is not deleted.** Dave: "I may still ask for a clipboard linked entry point
  later." `tgw-clipd` stays as-is, available as a possible future *input adapter* into
  Radar — not this plan's concern. `#1563` and `#1565` are both DONE (see below) and their
  logic is directly portable into this design.
- **Accepted tradeoff, named explicitly rather than discovered later**: centralizing means
  the picker on any device needs live network reachability to `tgw-http` to work at all —
  a real dependency `tgw-clipd`'s local SQLite file never had. For tgw-prod itself this is
  nearly free (same host as the service). No offline fallback is being designed for this
  build; if that turns out to matter in practice, it's a named future improvement, not a
  blocker to shipping now (matches Dave's own framing: ship the real improvement, iterate).

## Proposed approach

**Data layer.** New table in the existing `state_machine` Postgres database (not a new
database, not SQLite):

```sql
CREATE TABLE radar_entries (
    id           BIGSERIAL PRIMARY KEY,
    entry_type   TEXT NOT NULL DEFAULT 'prompt',   -- 'prompt' | 'sku' | 'url' | 'combined' (v1: prompt only, see Open Questions)
    content_enc  BYTEA NOT NULL,                    -- encrypted content, never plaintext at rest
    label        TEXT,
    origin       TEXT NOT NULL DEFAULT 'local',      -- 'local' (Dave-created) | 'agent' (delivered)
    requested_by TEXT,                               -- 'claude' | 'tigwa' | null for local
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    consumed_at  TIMESTAMP WITH TIME ZONE            -- set when Dave picks/loads it; null = still pending
);
CREATE INDEX idx_radar_type ON radar_entries (entry_type);
CREATE INDEX idx_radar_pending ON radar_entries (consumed_at) WHERE consumed_at IS NULL;
```

**Fence layer (`tgw-http`).** New endpoints, mirroring the existing ItemData fence's shape:
- `POST /api/radar/entries` — create (encrypt server-side before insert; runs the
  exclusion-filter check from `#1565`'s ported logic before ever encrypting/storing —
  a rejected entry is never persisted, encrypted or not).
- `GET /api/radar/entries?type=&pending=` — list (decrypt server-side before returning;
  same bearer-token auth every other `tgw-http` endpoint already uses).
- `GET /api/radar/entries/{id}` — get one (decrypted).
- `POST /api/radar/entries/{id}/consume` — mark consumed (Dave picked it; not a delete —
  matches the project's "never silently discard" principle, consumed entries stay
  queryable/auditable, just no longer flagged pending).

One Python module (`src/tgw/radar.py`) owns `create_entry()`/`list_entries()`/
`get_entry()`/`consume_entry()` against Postgres; `tgw-http`'s endpoints and the CLI both
call these functions — neither duplicates the logic, matching the fence principle.

**Encryption (`src/tgw/radar_crypto.py`, new).** Thin wrapper: `get_or_create_key()`
(fetches/creates a symmetric key in the OS Secret Service), `encrypt(plaintext) -> bytes`,
`decrypt(ciphertext) -> str`. Called only from `radar.py`'s create/list/get functions —
never by a client directly. Library choice (`secretstorage`/`python-keyring`/raw D-Bus)
deferred to execution time (see Open Questions) — pick whichever has the lighter dependency
footprint for this codebase.

**Delivery (agents).** `tgw_radar_deliver(content, label=None, entry_type='prompt')` MCP
tool in `mcp_server.py`, `TGW_MCP_READONLY`-gated exactly like `tgw_enqueue`/
`tgw_add_suggest` (so Tigwa's training-mode restriction covers it automatically, same as
`#1563` already proved live). Calls `radar.create_entry(..., origin='agent',
requested_by=<caller>)` — either directly (if colocated on tgw-prod, same as `tgw-http`)
or via the HTTP endpoint if that's the cleaner boundary at execution time; either way, it
funnels through the one `create_entry()` function, never a parallel write path.

**Client surface.**
- `tgw radar {create,list,get,pick}` CLI — calls `tgw-http`'s endpoints (bearer-token auth,
  same pattern every other `tgw` CLI-to-HTTP call already uses), not local SQLite.
- Picker: **wofi**, not rofi — `#1564`'s live finding was that rofi isn't even installed on
  tgw-prod's Sway session and no keybind currently reaches any picker at all; fixing that
  reachability gap is part of this build, not a separate prerequisite. Shows entries from
  `tgw radar list`, filterable by `entry_type` (a `Prompts` chip is the concrete v1 case).
- a1131 (Tigwa's machine, Dave's secondary desktop) reaches the same `tgw-http` service
  over the network — already-reachable per the existing `TGW-a1131-CLI-Wrapper.md` pattern,
  no new cross-machine mechanism needed.
- Phone/Flutter: explicitly deferred (see below) — `apps/tgw_app/` has zero
  server-initiated-communication capability today (confirmed in `PP-EVENTD-001-design.md`);
  when built, it's just another `tgw-http` bearer-token client, same shape as everything
  else the app already does.

**What's reused from `#1563`/`#1565` (both DONE this session):**
- `tgw_clip_deliver`'s exact MCP registration pattern → `tgw_radar_deliver`.
- `origin`/`label`/`requested_by` column concept → carried into `radar_entries` directly.
- `#1565`'s password-hint + secret-pattern exclusion logic (`looks_like_secret()` and the
  MIME-hint check) → ported into `radar.create_entry()`'s pre-insert check. Note: the
  MIME-hint mechanism (`x-kde-passwordManagerHint` via `wl-paste --list-types`) was built
  against `tgw-clipd`'s live-clipboard-watching context — Radar has no live clipboard
  watcher to query, so only the content-pattern half (`looks_like_secret()`) is directly
  applicable; the MIME-hint check doesn't have an equivalent trigger point here unless a
  future clipboard input adapter (Dave's "maybe later") is added.

**What's explicitly deferred, not in this build:**
- True end-to-end encryption (real per-device key exchange, `tgw-http` never sees
  plaintext) — named as a real, harder upgrade, not attempted now.
- Offline fallback for non-tgw-prod devices.
- The Flutter/Android client leg.
- Entry types beyond `prompt` (SKU/URL/combined-buffer) — schema has the column, only
  `prompt` is populated/exercised in this build; extending types is additive later work.
- PP-EVENTD-001's Go daemon / hardware fan-out (barcode readers etc.) — unrelated surface,
  not superseded or blocked by this plan.

## Files to change

| File | Change |
|------|--------|
| DB migration (wherever `state_machine`'s schema migrations live — verify convention at execution time) | `radar_entries` table + indexes, as above |
| `src/tgw/radar.py` (new) | `create_entry()`, `list_entries()`, `get_entry()`, `consume_entry()` — the single write/read surface, calling `radar_crypto` + the ported exclusion check |
| `src/tgw/radar_crypto.py` (new) | `get_or_create_key()`, `encrypt()`, `decrypt()` via OS Secret Service |
| `src/tgw/http_server.py` | New `/api/radar/entries` endpoints (POST create, GET list, GET one, POST consume) — same bearer-token auth pattern as every existing endpoint |
| `src/tgw/api.py` | New `radar` subcommand family (`tgw radar {create,list,get,pick}`), calling `tgw-http`'s endpoints, not local storage |
| `src/tgw/mcp_server.py` | `tgw_radar_deliver(content, label=None, entry_type='prompt')`, `TGW_MCP_READONLY`-gated |
| Sway config / keybind (wherever `#1564`'s picker-reachability fix lands — coordinate, don't duplicate) | Wire an actual keybind to the wofi-based Radar picker |
| `tests/test_radar.py` (new) | `create_entry()`/`list_entries()`/`consume_entry()`; exclusion filter (port `#1565`'s test table); encrypt/decrypt round-trip; MCP tool `TGW_MCP_READONLY` gating |
| `tests/test_http_server.py` | New `/api/radar/*` endpoint tests — auth required, create/list/get/consume round-trip, encrypted-at-rest verified (raw DB row shows ciphertext) |

## Acceptance criteria

- [ ] `radar_entries` row content is genuinely encrypted at rest — querying the table
      directly via `psql` shows ciphertext in `content_enc`, not plaintext
- [ ] `tgw radar create "test" --label "test"` (via CLI → `tgw-http`) round-trips correctly:
      `tgw radar get <id>` returns the original plaintext, decrypted
- [ ] `tgw_radar_deliver` MCP tool: registered when `TGW_MCP_READONLY` unset/`0`, absent
      when `1`
- [ ] A password-manager-hinted or API-key-shaped `create`/`deliver` call is rejected, not
      persisted (even encrypted) — live-verified
- [ ] Wofi picker on tgw-prod, reachable via a real keybind, shows entries filterable by
      type, and selecting one loads the correct decrypted content onto the live clipboard —
      live-verified on tgw-prod's actual Sway session (Prime Directive 4)
- [ ] Cross-machine proof: create/deliver an entry from tgw-prod, read it back via
      `tgw radar list`/`get` from a1131 (or equivalent network-based verification) —
      proves the "networked, not per-machine" property actually holds, not just claimed
- [ ] Worked-example proof of value: the eBay support-ticket case (or an equivalent real
      artifact available at execution time) delivered end-to-end and used by Dave
- [ ] Full test suite green; `tgw health` unchanged; `tgw-http.service` restart clean

## Naming — confirmed (Dave, 2026-07-19)

`PP-RADAR-001`, distinct from PP-EVENTD-001's existing "Radar" concept name (which stays
scoped to the Go cross-machine daemon / hardware fan-out territory). Dave: "I like the
name. It would stand up as a good name for an open source project if we ever decide to
spin it off." Not a scope change for this build — no extra genericization/de-TGW-ing work
is being done now on the strength of a hypothetical future spinoff — but worth the two
architectural choices already in this plan that happen to keep that door open rather than
closed: `tgw-http`-as-fence keeps Radar's actual read/write logic in `radar.py`/
`radar_crypto.py`, not smeared across TGW-specific call sites, and the schema itself
(`radar_entries`) has no ItemData/SKU-specific columns baked in at the type level. If a
spinoff is ever actually pursued, that's a deliberate later decision with its own scoping
pass — not something this plan is signing up to design toward now.

## Open questions

- **Encryption library choice**: `secretstorage` vs `python-keyring` vs raw D-Bus — decide
  at execution time based on what's lightest to add as a dependency.
- **DB migration convention**: this plan assumes `state_machine`'s schema evolves via
  whatever mechanism the project already uses (raw `schema.sql`, a migration tool, etc.) —
  not verified here, confirm at execution time (see `reference-schema-sql-apply-role`
  convention: applied as `postgres` superuser, not `tgw`).
- **Single build phase, not split into drip-fed phases** (per Dave: ship the real
  improvement now, iterate later) — this plan is written as one dispatchable packet rather
  than the earlier revision's Phase 1/2/3 split. If it proves too large for one `tgw-coder`
  packet at execution time, split by layer (data+fence, then CLI+MCP, then picker) rather
  than by feature-completeness — flag that call to whoever executes it, don't silently
  reintroduce the phasing this revision deliberately dropped.
