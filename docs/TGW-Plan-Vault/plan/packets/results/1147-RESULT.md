# Result: 1147 search-full-text
Status: done
Todo: #1147   PP: PP-KNOWLEDGE-001

## Files touched
- `src/tgw/search_full.py` (new) — `recollq` wrapper: `run_full_text_search(query, limit)` →
  `{ok, query, count, elapsed_ms, results:[{url,title,mtype,fbytes,abstract}]}`; `format_results_text()`
  for CLI rendering.
- `src/tgw/api.py` — `search` subcommand gets `--full-text QUERY` (dispatches to `search_full` instead
  of the item-DB `list_items`); `--skus-only` extracts `tgw<17 digits>` SKU tokens from result URLs.
- `src/tgw/http_server.py` — new `/form/search` page (server-rendered search bar, session-gated like
  every other `/form/*` page) and `/api/search/full-text` JSON endpoint (Bearer-auth-gated like the
  rest of `/api/*`).
- `src/tgw/static/nav.js` — "Search" nav link added.
- `src/tgw/mcp_server.py` — new MCP tool `tgw_search_full(query, limit)`, same output contract.
- `tests/test_search_full.py` (new) — 20 tests for the wrapper (parsing, limit clamping, error paths,
  formatting), `subprocess.run` mocked.
- `tests/test_mcp_server.py` — `EXPECTED_TOOLS` + count updated 12→13, 3 new tests for `tgw_search_full`.
- `tests/test_invariant_c12_field_set_accessors.py` — one allowlist line-number entry updated
  (5555→5653) to track an existing unrelated allowlisted hit that shifted because of this packet's
  line insertions into `http_server.py`. Not a new C12 finding — the same `revision_draft.delta` access
  already allowlisted before this packet, just moved.

## Live evidence
Three surfaces, three real recovery/audit-style queries against the live `/opt/TGW/.recoll` index
(441K+ docs), all "seconds not hours" per the acceptance framing:

**CLI** (`tgw search --full-text`, run as `tgw` via the worktree's own copy — `tgw.api.__file__`
confirmed resolving under the worktree path before testing):
```
$ tgw search --full-text "Grant's Scotch Whisky Miniature" --limit 5
5 result(s) for "Grant's Scotch Whisky Miniature" (108 ms)
  [text/plain] tgw202606021133367.json (23049 bytes)
      file:///opt/TGW/data/ItemCatalog/by-location/TRNK0425/tgw202606021133367/tgw202606021133367.json
  ...

$ tgw search --full-text "PP KNOWLEDGE 001 recoll" --limit 5
2 result(s) for 'PP KNOWLEDGE 001 recoll' (35 ms)
  [text/markdown] PP-EVENTD-001-design.md ...
  [text/markdown] PP-CATALOG-INCR-001.md ...

$ tgw search --full-text "tgw202606021107459" --limit 5
5 result(s) for 'tgw202606021107459' (149 ms)
  [text/plain] tgw202606021107459.json (22757 bytes)
      file:///opt/TGW/data/ItemCatalog/by-location/FF800/tgw202606021107459/tgw202606021107459.json
  ...
```

**Web UI** (`/form/search`, via FastAPI TestClient against the real app + real recollq subprocess —
session cookie injected directly rather than through `/login` since this is a worktree instance, not
the live tgw-http process; real code path, real index, no mocking):
```
=== Grant's Scotch Whisky Miniature === status 200
9 result(s) for "Grant&#x27;s Scotch Whisky Miniature" — 286 ms
   file:///opt/TGW/data/ItemCatalog/by-location/TRNK0425/tgw202606021133367/tgw202606021133367.json
   ...
=== PP KNOWLEDGE 001 recoll === status 200
2 result(s) for "PP KNOWLEDGE 001 recoll" — 25 ms
   file:///opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/reference/PP-EVENTD-001-design.md
   ...
=== tgw202606021107459 === status 200
5 result(s) for "tgw202606021107459" — 71 ms
   file:///opt/TGW/data/ItemCatalog/by-location/FF800/tgw202606021107459/tgw202606021107459.json
   ...
```

**MCP tool** (`tgw_search_full`, called directly — FastMCP's `@mcp.tool()` returns the original
function, matching the existing `test_mcp_server.py` convention):
```
=== Grant's Scotch Whisky Miniature === ok True count 5 ms 61.9
   file:///opt/TGW/data/ItemCatalog/by-location/TRNK0425/tgw202606021133367/tgw202606021133367.json
=== PP KNOWLEDGE 001 recoll === ok True count 2 ms 24.4
   file:///opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/reference/PP-EVENTD-001-design.md
=== tgw202606021107459 === ok True count 5 ms 74.4
   file:///opt/TGW/data/ItemCatalog/by-location/FF800/tgw202606021107459/tgw202606021107459.json
```

Full pytest suite (worktree copy, `PYTHONPATH` override confirmed): **2485 passed, 1 skipped, 0
failed** — no regressions. (Initial run had 2 failures from a line-number shift in the C12 accessor
allowlist test, caused by this packet's own line insertions moving an unrelated pre-existing
allowlisted access; fixed by updating the allowlist entry, not a new violation — see Files touched.)

## Deviations from spec
1. **"the recoll Python API" (design doc wording) → `recollq` CLI.** Verified live: no Python
   binding/API is installed on this system (only the `recoll`/`recollq` CLI binaries exist on PATH,
   per pre-flight check). The todo brief itself anticipated this ("recollq-backed", "substitute the
   correct real invocation if `recollq` isn't the right binary name") — `recollq` *is* the right
   binary, confirmed. Flagging per Prime Directive 3 since the design doc's wording differs from what
   got built, even though the todo brief's own instructions call for exactly this substitution.
2. **"the six hours-to-seconds queries from FUTURE-IDEAS PP-SEARCH-001" — no literal enumerated list
   found.** Searched `FUTURE-IDEAS.md`, `TGW-Master-Plan.md`, `PP-DRIVE-INDEX-plan.md`,
   `recoll-annex-jetstream.md` — PP-SEARCH-001's content was folded into PP-KNOWLEDGE-001's
   master-plan section 2026-07-11 without preserving a discrete numbered list of six queries, if one
   ever existed as such outside Dave's original s44/s45 transcript (not in the repo). Per this
   profile's pre-flight step 3 instruction ("if the actual spec document differs from this summary...
   follow the document"), and since this is a missing *artifact* rather than an ambiguous *spec*
   (Track R2's technical shape in `recoll-annex-jetstream.md` is unambiguous and was followed exactly),
   I did not stop the whole packet on this — instead substituted three representative real
   recovery/audit-style queries (title-keyword item recovery, PP cross-reference, exact-SKU lookup)
   as the acceptance evidence above, explicitly flagged here rather than silently assumed equivalent.
   If Dave has the original six queries recorded anywhere not found by this search, they should be
   re-run against these same three surfaces to close the gap cleanly.
3. **`-a` (ALL TERMS / AND) mode chosen over recoll's default OR-with-priority query language** for
   both the CLI and MCP tool, to match the "recovery/audit lookup" framing (precise, not fuzzy-ranked)
   and the GUI's own simple-search default. Not explicitly specified in Track R2; flagged as a design
   choice rather than silently picked.
4. **`/form/search` is session-gated** (redirects to `/login` like every other `/form/*` page) rather
   than the "no Bearer auth (network trust)" framing that applies to *some* other form pages — this
   turned out to be existing site-wide `_session_guard` middleware behavior on the `/form/*` prefix,
   not a choice made in this packet; confirmed live (unauthenticated request 303-redirects to
   `/login`), consistent with `/form/todos`/`/form/intake`/etc., which also hit this same guard despite
   their docstrings saying "no Bearer auth" (that phrase refers only to the separate API-key Bearer
   mechanism, not the session cookie). No code change needed — noted so the "web UI search bar" is
   understood correctly as behind the same login as the rest of the operator UI, not open to the LAN.

## Out-of-scope findings filed
- #1518 (PP-KNOWLEDGE-001): Track R3 OCR sweep (tesseract via recoll filter, serials/labels/barcodes
  on ItemData photos) — named as the next Track R packet in `recoll-annex-jetstream.md` but not
  previously tracked as its own todo. Filed rather than built, per this packet's scope (R2 only).
- Track R1 (recoll field mapping for annex metadata/xattrs) already has its own todo, #1148 — no new
  filing needed, confirmed via `sudo -u tgw tgw todo`/master-plan cross-check.
