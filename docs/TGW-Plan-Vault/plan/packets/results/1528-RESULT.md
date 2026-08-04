# Result: todo #1528 mcp-parameter-alias-pattern

Status: done
Todo: #1528   PP: PP-KNOWLEDGE-001

## Files touched

- `src/tgw/mcp_server.py`
- `tests/test_mcp_server.py`

## Spec followed

`docs/TGW-Plan-Vault/inbox/claude/TIGWA-REQUEST-mcp-parameter-alias-pattern-2026-07-18.md`
(read in full first, verbatim, per invariant C11). Tigwa's two already-shipped
live fixes (`tgw_get_todo`'s `agent`/`Agent`, `tgw_add_suggest`'s `text`/`Text`,
both via Pydantic `AliasChoices`) were read on the current
`catio-nix-0.0.1-alpha` HEAD before doing anything else, per the packet's
explicit instruction.

## Reconciliation with Tigwa's shipped fixes — explicit answer

**This builds on her work, does not undo or duplicate it.** Both of her
`AliasChoices` usages (`tgw_get_todo`, `tgw_add_suggest`) were refactored
onto the new shared `alias_field()` helper with byte-for-byte identical
`AliasChoices(name, name.title-form)` semantics — same canonical key
(`agent`/`text`), same accepted alias (`Agent`/`Text`), same default values,
same behavior. Nothing about how those two tools validate arguments changed;
only the *authoring* mechanism (helper call vs. inline `Field(...)`) did,
and that is covered by the pre-existing `test_get_todo_accepts_capitalized_agent_argument`
/ `test_add_suggest_accepts_capitalized_text_argument` tests, both still
green unmodified.

I also found that this session had already independently extended
`tgw_mailbox_send` (landed on `catio-nix-0.0.1-alpha` HEAD, commit
`4ecbdf3`/`521b0c5`, before this task started — not something I added) with
a *richer* alias set than the two-case baseline: `to_actor`/`To_actor`/`To`,
`from_actor`/`From_actor`/`From`, `msg_type`/`Msg_type`/`Type`,
`subject`/`Subject`, `todo_id`/`Todo_id`/`Todo`. That existing extra
shorthand (`To`, `From`, `Type`, `Todo` — friendlier than the mechanical
title-case form) was preserved exactly via `alias_field(name, extra_alias)`
and is now covered by a new regression test
(`test_mailbox_send_accepts_all_extra_aliases_after_alias_field_refactor`)
proving the refactor didn't silently drop it.

## Shared helper (requirement 3)

Added `alias_field(name: str, *extra_aliases: str)` in `src/tgw/mcp_server.py`,
directly above the tool definitions:

```python
def alias_field(name: str, *extra_aliases: str) -> Any:
    return Field(validation_alias=AliasChoices(name, name.capitalize(), *extra_aliases))
```

This DRYs up the pattern meaningfully — before this change it was already
duplicated 3x with growing per-field boilerplate (`get_todo`, `add_suggest`,
`mailbox_send`'s 6 fields); after, every covered parameter is one line:
`sku: Annotated[str, alias_field('sku')]`. A small factory was the right
call per requirement 3 — not a global argument-rewriting layer (no
middleware, no request interceptor, no schema-wide monkeypatch); each tool
still declares its own parameters and opts in per-field, exactly matching
Tigwa's existing per-tool style, just without repeating `Field(validation_alias=AliasChoices(...))`
by hand each time.

Title-casing convention matches Tigwa's own established precedent exactly:
`name.capitalize()` (first character upper, rest unchanged — since the rest
is already lowercase this is equivalent to "capitalize only the first
letter"), e.g. `to_actor` -> `To_actor`, **not** `To_Actor` (Python's
`.title()` would incorrectly title-case every underscore-separated word).

## Coverage — which parameters got the alias pattern, and why (requirement 6)

All 13 `@mcp.tool()`-decorated functions in `src/tgw/mcp_server.py` were
read and every parameter assessed individually.

**Covered** (canonical lowercase kept, `Name`-form + any extra alias added):

| Tool | Parameter(s) covered | Alias(es) |
|---|---|---|
| `tgw_get_item` | `sku` | `Sku` |
| `tgw_search_items` | `search`, `location`, `status`, `limit` | `Search`, `Location`, `Status`, `Limit` |
| `tgw_search_full` | `query`, `limit` | `Query`, `Limit` |
| `tgw_enqueue` | `sku`, `action` | `Sku`, `Action` |
| `tgw_get_todo` | `agent` | `Agent` (Tigwa's existing fix, unchanged) |
| `tgw_add_suggest` | `text` | `Text` (Tigwa's existing fix, unchanged) |
| `tgw_dead_letter` | `queue`, `limit` | `Queue`, `Limit` |
| `tgw_hint_trail` | `sku` | `Sku` |
| `tgw_catalog_verify` | `location`, `limit`, `severity`, `mark_verified`, `force`, `skip_verified` | `Location`, `Limit`, `Severity`, `Mark_verified`, `Force`, `Skip_verified` |
| `tgw_mailbox_send` | `to_actor`, `text`, `from_actor`, `msg_type`, `subject`, `todo_id` | `To_actor`+`To`, `Text`, `From_actor`+`From`, `Msg_type`+`Type`, `Subject`, `Todo_id`+`Todo` (all pre-existing, refactored not changed) |
| `tgw_get_plan_brief` | `pp` | `Pp` (mechanical form) **and** `PP` (deliberate judgment call — see below) |

**Deliberately left out** (no parameters requiring changes):

- `tgw_queue_status` — no parameters.
- `tgw_health` — no parameters.

**Judgment call flagged explicitly (not silent, per Prime Directive 3):**
`tgw_get_plan_brief`'s `pp` parameter got a second, non-mechanical alias
`PP` in addition to the mechanical `name.capitalize()` form `Pp`. `pp` is a
two-letter abbreviation used everywhere in this codebase as the all-caps
prefix of a plan-item ref (`PP-KNOWLEDGE-001`); a client presenting it
title-cased as `Pp` is plausible but a client presenting it as the
familiar all-caps abbreviation `PP` is at least as plausible, and the
mechanical rule alone wouldn't catch that. This is the one place I went
beyond a strict `name.capitalize()` reading of requirement 2 — flagged here
per Prime Directive 3 rather than done silently.

**Boolean parameters** (`mark_verified`, `force`, `skip_verified` on
`tgw_catalog_verify`) were included using the same snake-case-with-first-
letter-capitalized convention as `mailbox_send`'s existing multi-word
fields (`Mark_verified`, not camelCase `MarkVerified`) — consistent with
the one convention already established live, not a new invented style.
CamelCase variants were deliberately not added: there is no existing
precedent for them anywhere in this codebase's MCP layer, and inventing a
second casing convention alongside the title-case one would work against
requirement 1's "stays lowercase and stable" simplicity goal rather than
serve it. If a live friction case with a camelCase client label ever
surfaces, it's a one-line addition to the relevant `alias_field(...)` call.

**Enum-like string parameters** (`tgw_enqueue`'s `action`,
`tgw_catalog_verify`'s `severity`) were covered for the *parameter name*
only — the accepted *values* of those enums (`ai_identify`, `ebay_draft`,
`critical`/`warning`/`info`, etc.) were left untouched; requirement 5
explicitly rules out altering business/queue/permission behavior, and
value-casing was never Tigwa's two observed friction cases (`Agent`/`Text`
were both parameter-name mismatches, not value mismatches).

## Test evidence

`tests/test_mcp_server.py`: 33 -> 42 tests (9 new), all FastMCP-boundary
`tool.run({...})` calls through `mcp_server.mcp._tool_manager._tools[...]`
matching the existing file's established convention (not direct Python
function calls, not schema snapshots) — one new test per newly-covered
tool, invoking with the title-cased key(s), plus a dedicated mailbox_send
regression proving the pre-existing extra aliases survived the
`alias_field()` refactor, plus a dedicated `PP`-alias test for
`tgw_get_plan_brief`.

```
$ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH \
  pytest tests/test_mcp_server.py -q
............................................ [100%]
42 passed in 1.54s
```

Confirmed testing the worktree's own copy, not the shared checkout, via
`python3 -c "import tgw.mcp_server as m; print(m.__file__)"` resolving to
`/opt/TGW/var/worktrees/1528-mcp-parameter-alias-pattern/src/tgw/mcp_server.py`
before running pytest.

Also ran the full related-file set (`tests/test_mcp_server.py`,
`tests/test_audit1143_workers_cohesion.py`, `tests/test_invariant_a4.py`,
`tests/test_plan_render.py` — every test file in the repo that imports
`tgw.mcp_server` or `mcp`): **109 passed, 0 failed.** A full repo-wide
`pytest -q` run was attempted but timed out at the 2-minute cap on this
environment (196 test files, several requiring live PostgreSQL/eBay/Ollama
state well outside this change's scope) — not run as a blanket pass; the
scoped run above covers every file with any import-time or behavioral
dependency on this module.

## Live evidence (Acceptance)

A live stdio MCP client round-trip (Python `mcp.client.stdio` — this
environment doesn't expose an SSH-stdio path to the running `tgw` MCP
server the way Tigwa's own verification did, so this is the practical
equivalent noted as acceptable by the packet's Acceptance section) was run
as the `tgw` user against the worktree's own module
(`PYTHONPATH=/opt/TGW/var/worktrees/1528-mcp-parameter-alias-pattern/src`),
calling the real running server process (not a mock) against real
`ItemData`:

```
call_tool("tgw_get_item", {"Sku": "tgw201411151759014"})
-> {"ok": true, "sku": "tgw201411151759014"}
```

Title-cased `Sku` was accepted and correctly bound to the `sku` parameter
(this SKU was silently ignored/dropped before this change, matching the
exact class of friction Tigwa observed with `Agent`/`Text`). This is a
read-only lookup against real data; no writes, no eBay calls, no schema
change.

## Deviations from spec

None beyond the one explicitly-flagged judgment call above (`PP` alongside
`Pp` on `tgw_get_plan_brief`'s `pp` parameter) — flagged in-line per Prime
Directive 3, not silent.

## Out-of-scope findings filed

None. No adjacent broken behavior was found during this task; the packet's
"do not alter business authority, queue behavior, SSH credentials, or tool
permissions" boundary was respected — no `_READONLY` gating, no `_VALID_ACTIONS`
set, no queue/eBay/secrets code was touched, only parameter-name validation
aliasing.
