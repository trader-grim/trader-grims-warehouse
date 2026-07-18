# Result: todo #1483 PP-UIUX-001 Phase 1 UI inventory + tgw-mapping (re-check)
Status: done
Todo: #1483   PP: PP-UIUX-001

## Pre-flight finding (important — read before treating this as fresh work)

This exact packet was **already executed once**, live, directly on
`catio-nix-0.0.1-alpha` — commit `4bf044c` ("PP-UIUX-001 Phase 1 (#1483):
live UI inventory + tgw-mapping, refresh TGW-HTTP-API.md", 2026-07-17),
with its own result manifest already present at this same path. The todo
itself was still open in the tracker (no `--done`), so this dispatch
re-ran the same packet rather than being a duplicate no-op. Per invariant
C11 (verify assumptions live before changing anything), I diffed the
existing 2026-07-17 doc against the live route table instead of assuming
it was current, and found real drift: **4 routes landed in the ~1 day
between the first pass and this one**, after `4bf044c` closed but before
today. This result documents the incremental re-check, not a from-scratch
rebuild — the bulk of the prior pass's content (79-route inventory,
Flutter screen mapping, `/form/review` redirect finding, etc.) is still
accurate and was left as-is.

Files touched:
- `docs/TGW-Plan-Vault/reference/TGW-HTTP-API.md` (updated, not rewritten)
- `docs/TGW-Plan-Vault/reference/UI-Inventory-PP-UIUX-001.md` (updated, not rewritten)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1483-ui-inventory.md` (breadcrumb)

No code files touched — documentation-only task per packet scope.

## Discrepancies found (live route-table diff, 83 routes vs. 79 documented)

Systematic check: extracted every `@app.get/post/patch/delete` decorator
from `src/tgw/http_server.py` (83 total, confirmed via
`grep -n '@app\.\(get\|post\|patch\|delete\)'`), then checked each path
string's presence in the existing `TGW-HTTP-API.md`. 4 were missing, all
traced to commits that landed *after* `4bf044c` (the first Phase 1 pass)
but before this re-check:

1. **`GET /api/queue/daily_stats`** — per-queue succeeded/failed counts
   for one day + hourly breakdown; backs `/form/system`'s "Done
   today"/"Failed today" columns (`http_server.py:8994` fetch call,
   confirmed).
2. **`GET /form/search`** and **`GET /api/search/full-text`** (todo
   #1147, PP-KNOWLEDGE-001 R2, commit `70b3b44`) — recoll full-text
   search, web bar + JSON endpoint + `tgw search --full-text` CLI, all
   three sharing `tgw.search_full.run_full_text_search()`. `/form/search`
   is notably no-auth (network trust) like `/form/todos`/`/form/intake`,
   NOT session-cookie-gated like most `/form/*` pages — documented this
   distinction explicitly since the old doc implied uniform cookie gating.
3. **`POST /api/items/{sku}/inventory-lock`** (Dave, 2026-07-18 padlock
   design) — toggles whether one `item_attributes` key auto-syncs from
   the eBay draft; called from `/form/items/{sku}`'s embedded JS
   (`toggleInventoryLock()`, confirmed at `http_server.py:6997`); notable
   because it deliberately bypasses `item_attributes_history`, unlike
   every other Set A write path — documented that distinction too.

No route found to have been *removed* — same pattern as the first pass,
100% coverage-gap staleness, zero incorrect-claim staleness. Also
corrected the stale "19 of **79**" Flutter-parity-gap total to "19 of
**83**" and added the newly-confirmed-uncalled-by-Flutter endpoints
(`inventory-lock`, `daily_stats`, `search/full-text`) to that gap list.

No Flutter caller found for any of the 4 new routes (`grep -rn` across
`apps/tgw_app/lib` came back empty for all 4 path fragments) — consistent
with the existing Phase 2/3 parity-gap note, not a new finding requiring
its own todo.

## Live evidence
- `grep -n '@app\.\(get\|post\|patch\|delete\)' src/tgw/http_server.py | wc -l` → 83
  (vs. 79 the existing doc's Overview claimed).
- Python cross-check script comparing all 83 extracted route paths
  against `TGW-HTTP-API.md`'s text: 1 miss, `/docs/{path:path}` — same
  known false-negative as the first pass (doc phrases it as a combined
  `/docs, /docs/{path}` row); manually confirmed present, not a real gap.
- `git log --oneline 4bf044c..HEAD -- src/tgw/http_server.py apps/tgw_app/`
  → 5 commits since the first pass; 3 touch `http_server.py` meaningfully
  (`70b3b44` search, `89634e3`/`9e7d5eb`/`4380ca2` C14 fixes to existing
  endpoints, no new routes from those three); `apps/tgw_app/` untouched
  since 2026-06-29 (`git log -1 --format=%cd`), confirming the Flutter
  side of the doc needed no changes this pass.
- `grep -n "padlock\|inventory-lock" src/tgw/http_server.py` — confirmed
  both the route (`:2731`) and its sole caller
  (`toggleInventoryLock()` JS at `:6997`) live in source.
- `grep -n "daily_stats" src/tgw/http_server.py` — confirmed route
  (`:2091`) and its `/form/system` JS caller (`:8994`).

## Deviations from spec
None — followed the same 4-step process the original packet specified
(web UI enumeration, Flutter enumeration, endpoint mapping, doc refresh),
applied as an incremental re-verification given the packet had already
been executed once. Did not rewrite either doc from scratch since the
bulk of the 2026-07-17 content was still accurate; only added/corrected
what live-diffing found stale, per invariant C11 ("verify assumptions
live" — not "regenerate everything regardless of what changed").

## Out-of-scope findings filed
None new. Confirmed the prior pass's one open question (whether
`/api/items/{sku}/append`/`/api/items/{sku}/ebay-write` are orphaned UI
endpoints) is still correctly resolved as "no, internal `apis/fence.py`
worker path" — no change needed there. The "Flutter has no caller yet
for 19+ endpoints including the newly-found ones" observation is
Phase 2/3 design input per the existing parity note, not a new
actionable finding on its own.
