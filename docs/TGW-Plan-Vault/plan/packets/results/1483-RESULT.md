# Result: 1483 PP-UIUX-001 Phase 1 UI inventory + tgw-mapping
Status: done
Todo: #1483   PP: PP-UIUX-001

Files touched:
- `docs/TGW-Plan-Vault/reference/TGW-HTTP-API.md` (rewritten — see below)
- `docs/TGW-Plan-Vault/reference/UI-Inventory-PP-UIUX-001.md` (new — the
  Phase 1 UI-surface inventory artifact)
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1483-uiux-inventory.md`
  (breadcrumb, to be processed/deleted by the next session-start pass)

No code files touched — documentation-only task per packet scope.

## What changed in TGW-HTTP-API.md
Live-verified against the actual `@app.get/post/patch/delete` route
table in `src/tgw/http_server.py` (79 routes total as of 2026-07-17;
prior doc, dated 2026-06-04, documented 14). Concretely:
- Added full endpoint tables for all previously-undocumented routes:
  bulk (`/api/bulk/*`), queue/jobs/pipeline, system (`/api/system/*`,
  `/api/health`), full eBay category API surface, catalog/reference data
  (`/api/catalog/snapshot`, `/api/dashboard`, `/api/activity`), offers
  (`/api/offers*`), PM chat (`/api/pm/*`), suggest/inbox, auth/media, and
  the remaining 15 `/form/*` pages beyond the two previously documented.
- Corrected the `POST /api/items/{sku}/action` valid-action list — the
  old doc listed 8 actions; the live `PIPELINE_ACTIONS` set (line 143)
  has 15. 7 were undocumented: `ebay_end_listing`, `ebay_update`,
  `accept_proposals`, `dismiss_proposals`, `approve`, `archive`,
  `migrate_unblock`.
- Documented that `/form/review` is a pure 303 redirect to `/form/drafts`
  (back-compat alias), not a real page — the old doc didn't mention this
  route existing at all under either name.
- Clarified the "no Bearer auth" language used throughout the
  `/form/*` docstrings actually means session-cookie auth via `/login`,
  not no auth.
- Nothing previously documented was found to have been *removed* —
  staleness was 100% coverage gap, not incorrect-claim drift.

## New inventory artifact
`UI-Inventory-PP-UIUX-001.md` — full page-by-page (17 web `/form/*`
pages) and screen-by-screen (7 Flutter screens in `apps/tgw_app/`)
inventory, each mapped to the exact `/api/*` endpoints it calls, derived
by grepping the actual embedded-JS `fetch()` calls per page template and
the Flutter `api_client.dart`/`repository.dart`/`providers.dart` call
chains — not from docstrings or intent, per invariant C11.

**Correction to standing session memory:** prior notes described
"android/ scaffold exists, never built" as if that were the whole
Flutter story. Live-verified this pass: `apps/android/` +
`apps/lib/main.dart` IS an unbuilt default `flutter create` stub (zero
real screens) — but `apps/tgw_app/` is a **separate, real** Flutter
project with 7 built feature screens, offline SQLite cache, an
outbox/mutation queue, and a Riverpod provider layer, last touched
2026-06-29. This distinction was not previously called out and is now
recorded in the new inventory doc for future PP-UIUX-001 phases.

## Live evidence
- Route-table completeness check (script output): "79 routes in code" /
  "1 not found verbatim in doc" — the one miss was a formatting-only
  false negative (`/docs/{path:path}` vs the doc's `/docs, /docs/{path}`
  combined-row phrasing); manually confirmed present.
- `PIPELINE_ACTIONS` set read directly from `src/tgw/http_server.py:143-158`
  (15 entries) vs. the pre-existing doc's 8-entry list — diff is the 7
  actions now documented.
- `apps/tgw_app/pubspec.yaml` (`name: tgw_app`) vs `apps/pubspec.yaml`
  (`name: apps`, `description: "A new Flutter project."`) — confirmed
  two distinct Flutter projects live in the repo, only one real.
- `find apps/android -iname "*.dart" | wc -l` → 0; `find apps/tgw_app
  -iname "*.dart"` → 17 files including 7 real feature screens —
  confirmed live during this session.
- `grep -rn "sku}/append\|ebay-write" src/tgw` → both trace to
  `src/tgw/apis/fence.py` (`append_item()`/`ebay_write()`), the
  worker-facing fence client, not orphaned UI-less routes.

## Deviations from spec
None. Followed the todo body's 4 steps as given (web UI enumeration →
Flutter screen enumeration, with an honest correction of what's real vs.
scaffold → API mapping via live fetch()/api_client grep → doc refresh).
Scoped strictly to Phase 1 (inventory/documentation); did not attempt to
redesign, unify, or fix any UI/API mismatch found (e.g. the Flutter app
covering only 19 of 79 routes) — noted in the new doc as a Phase 2/3
input, not actioned here.

## Out-of-scope findings filed
None. The one candidate finding (possible orphan endpoints
`/api/items/{sku}/append` and `/api/items/{sku}/ebay-write`) was
resolved live within this same pass — both trace to
`apis/fence.py`'s worker-write client, not orphans, no todo needed.
