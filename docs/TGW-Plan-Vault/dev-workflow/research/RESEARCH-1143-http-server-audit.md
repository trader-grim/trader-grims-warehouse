# RESEARCH-1143: http_server.py subsystem cohesion+correctness audit

Part of todo #1143 (full-codebase cohesion+correctness audit, staged per-subsystem).
This slice: `src/tgw/http_server.py`, a single 9,211-line FastAPI file (all routes,
HTML rendering, eBay webhook receiver). Third subsystem after `workers/` and
`apis/ebay/`.

## Method

Workflow tool, 6 line-range groups (aligned to function boundaries, ~1,500 lines
each) run in parallel (Find phase), each candidate finding then adversarially
verified by 3 independent agents against the real code (Verify phase, 2-of-3
survival bar to confirm). First attempt hit the session rate limit (all 6 Find
agents failed); retried after reset — 69 agents total, ~2.53M subagent tokens,
~11.5 min wall.

## Result: 18/21 candidate findings confirmed, 3 refuted

**5 security findings** — the most of any subsystem slice so far:

| Todo | File:line | Summary |
|------|-----------|---------|
| #1184 | http_server.py:288 | `/login` interpolates unescaped `next` param via `str.format` — reflected XSS |
| #1185 | http_server.py:305 | open-redirect guard accepts `//evil.com` protocol-relative URLs |
| #1186 | http_server.py:2954 | `/form/intake/{sku}` 404 page renders raw unescaped sku — reflected XSS |
| #1187 | http_server.py:2759 | `intake_form` renders weight_oz/barcode/ai_hint unescaped into HTML attrs — stored attribute-injection XSS |
| #1188 | http_server.py:9128 | `/docs` vault viewer renders markdown with `escape=False` — any synced/generated .md with a script tag executes |

13 correctness/invariant findings:

| Todo | File:line | Summary |
|------|-----------|---------|
| #1189 | :959 | `_apply_ebay_write` field-protection loop is a no-op — never actually blocks clobbering protected fields |
| #1190 | :1128 | `bulk_action` mark_sold bypasses documented quantity-decrement rule |
| #1191 | :1165 | `ebay_end_listing` calls eBay withdraw before local write — can desync local/eBay state with no persisted finding |
| #1192 | :724 | `patch_item` reports "location" updated even when the underlying write failed |
| #1193 | :2248 | `remove_comp` recomputes price_comps stats with a different formula than the original computation |
| #1194 | :2044 | `catalog_snapshot` leaks temp .db files on backup failure |
| #1195 | :6687 | `apply_revision` has no error handling around the post-apply enqueue — false 500 after a real eBay change |
| #1196 | :6710 | `discard_revision` swallows enqueue exceptions with bare `except: pass`, no logging (C11 violation) |
| #1197 | :7893 | `cancel_job` SELECT-then-UPDATE race with no WHERE-state guard |
| #1198 (batched) | :2167, :2282, :4922, :4263 | cohesion: duplicated enqueue call, missing traversal guard vs sibling routes, dead code + silent fallback, duplicate helper functions |

**Dropped (3, refuted on verify):** unescaped sku in an inline `<script>` block at
:4837 (refuted — sku is validated upstream by then), the 3-different-error-handling
cohesion observation at :6710 (subsumed into individually-confirmed findings), and
the webhook ack-before-persist-confirmed finding at :9206 (refuted — a downstream
poller reconciles).

## Priority note

Security findings this slice (#1184-#1188) join #1174 from the apis/ebay/ slice
(unsigned webhook forgery) as the highest-priority remediation targets — Dave has
already flagged #1174 as first-up; these 5 belong in the same remediation batch.

## Remaining subsystems queue

queue/state-machine, scripts/, nix flake.
