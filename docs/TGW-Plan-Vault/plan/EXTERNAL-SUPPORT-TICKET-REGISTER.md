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
| `EBAY-DS-1077` | **WITHDRAWN — client request-syntax error; no eBay 25707 defect or orphaned offer.** Former purge request retained only as disproven provenance. | `260719-000018` | `closure requested 2026-07-31; provider confirmation pending` | **FINAL AUTHORITATIVE CORRECTION (Dave, 2026-07-31):** “Murder on the Middle Fork” was an internal edit-and-correction lasting roughly 40 seconds and was never submitted to eBay. The developer had not validated the request syntax; after Dave required that check, the developer acknowledged the syntax error. The entire asserted 25707 incident was a phantom client-side issue, not an eBay site defect. Withdraw the orphaned-offer, malformed-SKU, server-side-purge, and provider-remediation claims. No purge, repair, meeting, or eBay remediation is required. Dave accepts responsibility, apologizes, and asks support to close the ticket. Closure message: `reference/EBAY-DS-1077-CLOSURE-2026-07-31.md`. **Do not mark provider-closed until eBay confirms closure. Disproven historical provenance follows:** eBay's first reply was generic SKU-troubleshooting boilerplate ("verify/provide a valid SKU", "use getInventoryItems") that ignored the ticket's own content. Re-verified live 2026-07-19 (post power outage, before replying) rather than take the prior findings on faith: (1) `GET /sell/inventory/v1/offer?sku=<exact 57-char SKU>` — still HTTP 400 / error 25707, identical message; (2) full paginated `GET /sell/inventory/v1/inventory_item` sweep (98 pages, 19,509 items, zero errors) — SKU not present in any page. **2026-07-21 (Dave: "write the whole story... every damned thing they can see already but are asking us for anyway"):** follow-up rewritten in full, long/dry/exhaustive form — every attempted path in order (direct lookup, %20/+/double-encoding variants, no-sku-param check, bulk fetch, DELETE attempt, full inventory_item sweep, no-offerId-ever-returned, legacy-Item#-check, Seller Hub drafts UI cross-check with a known-good offerId), the title↔SKU transposition provenance (legacy pre-Inventory-API import), how the SKU was traced back to this specific item (historical-record search, field-scramble evidence, byte-level encoding check), and the same 3 remedies (server-side purge / offerId + supported op / named alternative path). Kept to per-item technical facts and plain business impact — no internal system/worker/automation architecture named, per standing eBay minimal-disclosure practice.

**2026-07-22 correction (Tigwa content-hash cross-check, Dave-flagged):** the file at `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup.txt` was edited in place on tgw-prod (no `.stversions` backup exists for it — Syncthing versioning is not enabled/did not fire for this file), and the a1131 replica of the same path had NOT propagated the edits — Tigwa read a1131's copy directly and got the pre-edit 2026-07-19 version (SHA-256 `994bac97ba6c81950529eb1c6d92c1b64698bf0815dcb4407a95ad8f71a4d12b`, 65 lines, mtime `2026-07-19 12:28:57 -0700`), not the rewritten one. Root cause not yet diagnosed — **todo #1632/PP-DATAINTEGRITY-001 opened** to investigate the tgw-prod↔a1131 Syncthing propagation gap for `/home/db/Sync` (stale by 2+ days for at least this file; scope/duration of the gap beyond this one file not yet checked). **Resolution taken:** the full rewrite is now also delivered under a distinct, non-overwriting filename — `/home/db/Sync/ebay-dev-support-orphaned-offer-25707-followup-full-review-draft.txt`, verified on tgw-prod: SHA-256 `b7f96b6274a526fefcd8ffea6fa2ffe2468977c6b83a0103cc6ff29614b92fde`, 239 lines, 13,780 bytes. **Not independently verified on a1131** — this session's SSH to `claude@192.168.60.101` failed (publickey rejected, likely a session credential gap, not investigated further); Dave/Tigwa should confirm the new filename has actually propagated before treating either copy as current. **Sent 2026-07-25 (Dave)** — now awaiting eBay Support's reply; no further action pending their response. Evidence: taskboard #1077/#1566; verification script + raw JSON output `/tmp/tgw-verify-1566/verify_25707_orphan.py` + `-result.json`; runbook `reference/runbooks/ebay-api-operations.md` Incident 2; fresh rlogid `t6pitnmsgwj70dlkr%3D9vjujkpfsl41%60jhs.03e%60f%3F0%3Ag%60*w%60ut02%2Bln1r%7F-19f7b167982-0x23de`. |
| `EBAY-DS-260605-000035` | **Application Growth Check.** App Check / `buy.marketplace_insights` scope request, bundled with the EPS / `UploadSiteHostedPictures` daily call-limit increase request (taskboard #1076). | `260605-000035` | `closed (partial — 1 of 2 requests answered)` | 2026-07-20: eBay formally **denied** `buy.marketplace_insights` ("highly limited, reserved for approved partners") and **closed the case**. The bundled EPS increase request was never addressed before closure — Dave: eBay support gets credited per closed ticket, so bundling two asks and closing after answering one converts a real resolution into credit for closing multiple tickets. **Live-reverified 2026-07-20** via a real `getRateLimits` call that the EPS closure wasn't a silent grant either: `UploadSiteHostedPictures` limit is still exactly 5,000/day, unchanged since the 2026-07-02 baseline. See `EBAY-DS-1590` below for the new ticket reopening the unresolved half. Source: taskboard #79 and #1076; `reference/DRAFT-1076-eps-support-ticket.md`. |
| `EBAY-DS-1590` | **SUPERSEDED** — was a combined new ticket (EPS + alternative-options together). Dave, 2026-07-20: "let's just play ball... ask both separately." Split into `EBAY-DS-1591` and `EBAY-DS-1592` below; do not send this one. | _(never submitted)_ | `superseded / split` | Draft kept for record at `reference/DRAFT-1590-ebay-alternative-options-followup.md` (marked superseded at top). No further action on this row. |
| `EBAY-DS-1591` | New, standalone Application Growth Check for the EPS (`UploadSiteHostedPictures`) daily call-limit increase — clean ask, no reference to `260605-000035`'s closure history. | _(none yet — not submitted)_ | `prepared / not yet submitted` | Draft: `reference/DRAFT-1591-eps-growth-check.md`, todo #1591. Live-verified 2026-07-20 the limit is genuinely still 5,000/day (unchanged since 2026-07-02) — ticket's premise is current. |
| `EBAY-DS-1592` | New, standalone ticket asking eBay to name a concrete "alternative option" for sold/comp price data, per their own Marketplace Insights denial-letter offer. | _(none yet — not submitted)_ | `prepared / not yet submitted` | Draft: `reference/DRAFT-1592-alternative-options-sold-price.md`, todo #1592. 2026-07-20: Dave separately probed eBay's AI-generated support chat (not the formal ticket system) — it confirmed no item-level sold-price API/report exists for independent sellers; worth getting the same question on record through the formal ticket channel too. |
| `EBAY-DS-1593` | Status/re-request of a new application keyset (new App ID/Cert ID/Dev ID, all desired scopes) — self-service action taken via developer.ebay.com on 2026-06-05, never confirmed fulfilled, dropped from plan tracking with no resolution note. | _(none yet — not submitted)_ | `prepared / not yet submitted — check portal first` | Draft: `reference/DRAFT-1593-oauth-keyset-status.md`, todo #1593. Draft itself recommends Dave check developer.ebay.com's Application Keys page directly before sending — if a second keyset already exists there approved-but-unnoticed, the real next step is just adopting it (update `secrets_root/ebay-credentials.json`, re-run `get_access_token.py`), not a support ticket. Live App ID as of the 2026-06-05 request was still the original (`DaveBuko-DaveBuko-P-66170566`), suggesting the new keyset was likely never issued — but not independently re-verified this session. |

## Standing eBay strategy: legitimate lapse → prompt Growth Check request (Dave, 2026-07-20)

"When we note an api lapse because of legitimate requests we soon after
initiate a request for a rate increase... we will Application Growth Check
them to death... We will follow to the letter." When a real, legitimate
workload (not manufactured volume, not a self-imposed throttle) hits a
documented eBay rate ceiling, file the Growth Check / rate-increase request
promptly, following eBay's published process exactly — app must be live
with real usage, `MARKETPLACE_ACCOUNT_DELETION` subscription required
first, forecasted daily call volume, app URL, EPN publisher ID if
applicable. Dave is having Tigwa obtain a copy of eBay's actual Growth
Check request form so future requests are pre-staged rather than drafted
cold each time. Full framing: `TGW-Master-Plan.md` under
PP-EBAY-SNAPSHOT-001. Blocking gap as of 2026-07-20: the
`MARKETPLACE_ACCOUNT_DELETION` webhook has a written spec
(`reference/ISS-005-REST-Signature-Verification.md`) but no implementation
— any Growth Check submission is premature until that's built.

## Adding another external relationship

1. Create a local key: `<PROVIDER>-<CHANNEL>-<local-id>`.
2. Record only a provider-issued ticket/reference ID as `External reference`; never invent one.
3. When a provider limits open cases, add a separately named contained request under the active case and record its follow-up message/reference separately.
4. Save the prepared request, submitted copy, and provider replies as separately named artifacts; link their paths here.
5. Update state and next action after each external event. A send is not closure; closure requires a verified result or a recorded human decision.
6. Keep credentials, secrets, and copied cookies out of this register and its artifacts.

## v1 gate — not yet authorized

Before building a web/desktop interface, run v0 through several eBay tickets and review whether the case/request hierarchy, lifecycle, reference linkage, attachment handling, and response follow-up fields are sufficient. A later implementation must use the same explicit lifecycle, preserve original submitted text and provider replies, distinguish copied/prepared/submitted/delivered states, and never become an automatic external-message sender.
