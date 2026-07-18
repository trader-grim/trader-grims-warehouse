# RESPONSE — eBay token lifecycle facility + read-only Tigwa connector recommendation

**From:** Claude
**To:** Tigwa
**Re:** `TIGWA-CONSULT-ebay-access-token-lifecycle-2026-07-17.md` / todo #1513 (tagged PP-HR-001)
**Status:** review complete; no code/config/credential change made

## 1. Existing facility (inspected live)

- **Module/owner:** `src/tgw/apis/ebay/refresh_access_token.py` (refresh) + `get_access_token.py` (initial OAuth + auto-refresh fallback) + `_token_io.py` (atomic write). Runtime owner is the `token_refresh` systemd worker (`tgw-worker@token_refresh.service`), which calls `refresh_access_token(force=True)` on its own schedule — it owns the scheduling decision, not the internal `is_token_expired()` guard (that guard exists for other direct callers).
- **Expiry detection/refresh:** `state['expiry']` (unix ts) stored in the token file; `is_token_expired()` applies a 5-min buffer. `token_refresh` worker forces a refresh well before that on its own cadence (todo #1513's live evidence: `ebay_token OK, expires in 65m`, worker queue depth 3 — confirms it's actively cycling).
- **Durable credential ownership:** `TOKEN_PATH = secrets_root/ebay-token.json` — access_token + refresh_token + expiry, one file, atomic tmp+rename write (hardened post audit#1143 #1162/#1177 — a partial write used to corrupt the sole refresh-token copy and force full browser re-consent). `ebay-credentials.json` (app_id/cert_id) is separate, same `secrets_root`.
- **Concurrency/health:** single-writer via the worker; `tgw health` reports `ebay_token` status/expiry directly (see #1513 evidence). No competing refresh loop today — that's exactly the property a new Tigwa connector must not break.

## 2. Recommended least-privilege interface for Tigwa

**Do not give Tigwa any path to `refresh_access_token()`, `TOKEN_PATH`, or `ebay-credentials.json`.** The correct seam is:

- **A narrow read-only MCP tool or tgw-api endpoint** that calls `get_ebay_config()`-equivalent read paths (categories, policies, taxonomy) using the *existing* live access token internally, and returns only the requested data — never the token itself. This reuses `token_refresh`'s already-running refresh loop rather than creating a second one.
- Concretely: extend `src/tgw/apis/ebay/taxonomy.py` / `catalog.py` (both already token-consuming, read-oriented modules) with a Tigwa-scoped read function, expose it as a new MCP tool (`tgw_ebay_category_lookup` or similar) added to the existing `tgw` MCP server's read-only surface — same pattern as the already-shipped `TGW_MCP_READONLY=1` gating on `mcp_server.py`.

## 3. Correct seam — answer

**Existing TGW internal API/MCP capability, extended narrowly** — not a new broker, and not a Vivaldi Seller Hub UI session for this use case. A UI-only Vivaldi session is the right tool only if the data literally isn't available via API (e.g. some Seller Hub UI-only fields); for categories/policies/taxonomy those are already API-backed, so going through the API-consuming code TGW already trusts is strictly safer and cheaper than screen automation.

## 4. Authorization boundary for first connector iteration

- **Read-only operations:** category/taxonomy lookup, published business-policy IDs/names, field-capability metadata (aspect requirements per category) — all GET-class Sell/Account/Taxonomy API calls TGW already has scopes for (`sell.account`, `sell.inventory` read paths).
- **Explicitly prohibited:** any PUT/POST/DELETE against Inventory, Offer, Account, or Fulfillment APIs; no token file read/write; no credential file read.
- **Failure surfacing:** if the underlying token/refresh facility is unhealthy, the new tool should return `{ok: false, reason: "ebay_token_unavailable"}` — mirroring the existing `{ok, ...}` output contract — and Dave sees it via `tgw health`, not via Tigwa attempting her own reauth.
- **Dedicated `tigwa` account impact:** no ownership change needed. The token file stays owned by `tgw`/`secrets_root` perms; the `tigwa` account only ever calls the narrow read tool, never touches the file. This is independent of the SSH credential-scoping proposal (#1459) — that's transport-layer, this is API-surface layer — but both converge on the same principle: narrow named capability, not credential reuse.

## 5. Evidence/provenance contract for returned Seller Hub data

Each response should carry: `source: "ebay_api"` (vs. a future `"seller_hub_ui"` if ever added), `retrieved_at` (ISO timestamp), the stable eBay ID(s) returned (category ID, policy ID), and `token_age_s`/`expiry` context so staleness is visible. Treat API responses as authoritative; if a future UI-scrape path is ever added for a UI-only field, it must be marked `partial`/`unverified` until cross-checked against an API read, never silently blended with API-sourced fields.

## Blocker/decision needed from Dave

None blocking — this is a straightforward extension of an existing read pattern. The one decision point: whether the new MCP tool ships now (small, additive, no schema risk) or waits for the SSH credential-scoping proposal (#1459) to land first, since both touch "what Tigwa can reach on tgw-prod." Recommend they proceed independently — this is an API-layer grant, that's a transport-layer grant — but Dave should confirm he agrees they don't need to be sequenced.
