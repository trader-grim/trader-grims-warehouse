# External Support Ticket Register — v0

**Owner:** Dave Bukove  
**Steward:** Tigwa  
**Started:** 2026-07-19  
**Purpose:** one durable, operator-readable log and lightweight interface for support relationships outside TGW. It records the actual external communication lifecycle; it is not a task tracker and does not send tickets automatically.

## v0 interaction contract

Each provider case receives a stable local key, provider/account, external reference, lifecycle state, concise framing, contained requests, authoritative artifacts, latest response, next operator action, and closure evidence. A provider may limit the account to one open case; in that situation several related requests belong under the same external case rather than being falsely represented as separate tickets.

Lifecycle states:

`draft` → `ready-to-submit` → `submitted` → `awaiting-provider` → `response-received` → `verified-closed`

`withdrawn` and `archived` are manual operator actions only. Sending, replying, closing, or deleting remains an explicit Dave action. A later UI may expose the same fields, copy prepared text to the clipboard, and link response artifacts; v0 remains this reviewable register plus the named attachment files.

## eBay Developer Support

| Local key | Provider case / contained requests | External reference | State | Latest evidence / next action |
|---|---|---|---|---|
| `EBAY-DS-1077` | Purge orphaned unpublished Inventory API offer blocked by error 25707. This is a separate ticket. | `260719-000018` | `response-received / follow-up drafted` | eBay's first reply was generic SKU-troubleshooting boilerplate ("verify/provide a valid SKU", "use getInventoryItems") that ignored the ticket's own content. Re-verified live 2026-07-19 (post power outage, before replying) rather than take the prior findings on faith: (1) `GET /sell/inventory/v1/offer?sku=<exact 57-char SKU>` — still HTTP 400 / error 25707, identical message; (2) full paginated `GET /sell/inventory/v1/inventory_item` sweep (98 pages, 19,509 items, zero errors) — SKU not present in any page. This is new evidence beyond the original ticket (previous enumeration covered offers only, not inventory_item). Follow-up reply drafted with both fresh results, **not yet sent** — Dave to review/send. Evidence: taskboard #1077/#1566; verification script + raw JSON output `/tmp/tgw-verify-1566/verify_25707_orphan.py` + `-result.json`; drafted reply `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt`; fresh rlogid `t6pitnmsgwj70dlkr%3D9vjujkpfsl41%60jhs.03e%60f%3F0%3Ag%60*w%60ut02%2Bln1r%7F-19f7b167982-0x23de`. |
| `EBAY-DS-260605-000035` | **Application Growth Check.** App Check / `buy.marketplace_insights` scope request, bundled with the EPS / `UploadSiteHostedPictures` daily call-limit increase request (taskboard #1076). | `260605-000035` | `closed (partial — 1 of 2 requests answered)` | 2026-07-20: eBay formally **denied** `buy.marketplace_insights` ("highly limited, reserved for approved partners") and **closed the case**. The bundled EPS increase request was never addressed before closure — Dave: eBay support gets credited per closed ticket, so bundling two asks and closing after answering one converts a real resolution into credit for closing multiple tickets. **Live-reverified 2026-07-20** via a real `getRateLimits` call that the EPS closure wasn't a silent grant either: `UploadSiteHostedPictures` limit is still exactly 5,000/day, unchanged since the 2026-07-02 baseline. See `EBAY-DS-1590` below for the new ticket reopening the unresolved half. Source: taskboard #79 and #1076; `reference/DRAFT-1076-eps-support-ticket.md`. |
| `EBAY-DS-1590` | **SUPERSEDED** — was a combined new ticket (EPS + alternative-options together). Dave, 2026-07-20: "let's just play ball... ask both separately." Split into `EBAY-DS-1591` and `EBAY-DS-1592` below; do not send this one. | _(never submitted)_ | `superseded / split` | Draft kept for record at `reference/DRAFT-1590-ebay-alternative-options-followup.md` (marked superseded at top). No further action on this row. |
| `EBAY-DS-1591` | New, standalone Application Growth Check for the EPS (`UploadSiteHostedPictures`) daily call-limit increase — clean ask, no reference to `260605-000035`'s closure history. | _(none yet — not submitted)_ | `prepared / not yet submitted` | Draft: `reference/DRAFT-1591-eps-growth-check.md`, todo #1591. Live-verified 2026-07-20 the limit is genuinely still 5,000/day (unchanged since 2026-07-02) — ticket's premise is current. |
| `EBAY-DS-1592` | New, standalone ticket asking eBay to name a concrete "alternative option" for sold/comp price data, per their own Marketplace Insights denial-letter offer. | _(none yet — not submitted)_ | `prepared / not yet submitted` | Draft: `reference/DRAFT-1592-alternative-options-sold-price.md`, todo #1592. 2026-07-20: Dave separately probed eBay's AI-generated support chat (not the formal ticket system) — it confirmed no item-level sold-price API/report exists for independent sellers; worth getting the same question on record through the formal ticket channel too. |
| `EBAY-DS-1593` | Status/re-request of a new application keyset (new App ID/Cert ID/Dev ID, all desired scopes) — self-service action taken via developer.ebay.com on 2026-06-05, never confirmed fulfilled, dropped from plan tracking with no resolution note. | _(none yet — not submitted)_ | `prepared / not yet submitted — check portal first` | Draft: `reference/DRAFT-1593-oauth-keyset-status.md`, todo #1593. Draft itself recommends Dave check developer.ebay.com's Application Keys page directly before sending — if a second keyset already exists there approved-but-unnoticed, the real next step is just adopting it (update `secrets_root/ebay-credentials.json`, re-run `get_access_token.py`), not a support ticket. Live App ID as of the 2026-06-05 request was still the original (`DaveBuko-DaveBuko-P-66170566`), suggesting the new keyset was likely never issued — but not independently re-verified this session. |

## Adding another external relationship

1. Create a local key: `<PROVIDER>-<CHANNEL>-<local-id>`.
2. Record only a provider-issued ticket/reference ID as `External reference`; never invent one.
3. When a provider limits open cases, add a separately named contained request under the active case and record its follow-up message/reference separately.
4. Save the prepared request, submitted copy, and provider replies as separately named artifacts; link their paths here.
5. Update state and next action after each external event. A send is not closure; closure requires a verified result or a recorded human decision.
6. Keep credentials, secrets, and copied cookies out of this register and its artifacts.

## v1 gate — not yet authorized

Before building a web/desktop interface, run v0 through several eBay tickets and review whether the case/request hierarchy, lifecycle, reference linkage, attachment handling, and response follow-up fields are sufficient. A later implementation must use the same explicit lifecycle, preserve original submitted text and provider replies, distinguish copied/prepared/submitted/delivered states, and never become an automatic external-message sender.
