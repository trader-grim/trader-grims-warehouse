# Packet: ebay_sync dead-letters on unhandled 400 from GET /sell/inventory/v1/offer

Todo: #1397   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — dispatched alongside 7 other packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/ebay/sync.py` (whole file, focus on
`fetch_all_offers()` lines 714-772) + `src/tgw/workers/ebay_sync.py` (whole
file, focus on the try/except around `fetch_all_offers()` at lines
110-165). Nothing else until you've confirmed the real error IDs.

## Verified live before this packet was written
- 9 `ebay_sync` dead-letters, `error_code=WORKER_EXCEPTION`,
  `HTTPError('400 Client Error: Bad Request for url:
  https://api.ebay.com/sell/inventory/v1/offer?marketplace_id=EBAY_US&limit=100&offset=0')`.
- `fetch_all_offers()` (`src/tgw/ebay/sync.py:714-772`) already has
  specific 400-handling: it parses `eBay error 25702/25710/25009` as a
  graceful "no offers" empty result, and separately its caller in
  `ebay_sync.py:117-160` already has a well-developed fallback for the
  known **eBay error 25707** (orphaned non-alphanumeric-SKU offer, see
  todo #1077, session-41 circuit breaker to cap the ~2000-call/run
  fallback to once per 24h). **Both paths already re-raise for any 400
  whose error IDs aren't in their known set** — meaning these 9
  dead-letters are a 400 with error ID(s) that are NEITHER 25702/25710/25009
  NOR 25707. This is a genuinely new/unhandled eBay error, not a gap in
  the already-built 25707 handling — don't assume it's the same bug,
  verify what error ID it actually is.
- The stored `error_detail` in `queue_jobs` is just `str(HTTPError)`,
  which does **not** include the response body / eBay error IDs (only the
  URL and status code). You cannot determine the actual error ID from the
  dead-letter row alone — you'll need to either (a) reproduce the call
  live against the real eBay API and capture `exc.response.json()`, or
  (b) check `/opt/TGW/var/log/` for the worker's log output around the
  `finished_at` timestamps of these 9 jobs (the `else: raise` branches at
  lines 158/764 don't currently log the error IDs before re-raising for
  unrecognized errors — that's itself part of the fix, see Spec below).

## Spec
1. Determine the actual eBay error ID(s) behind these 9 dead-letters
   (live reproduction preferred over log archaeology if quota allows —
   check thermal/quota state first).
2. Fix `fetch_all_offers()`'s and/or `ebay_sync.py`'s 400-handling so an
   **unrecognized** eBay error ID is logged with its actual error ID and
   message *before* re-raising (currently the `else: raise` branches at
   `sync.py:764` and `ebay_sync.py:158` are silent about what specifically
   wasn't recognized — this makes future triage of "yet another eBay error
   ID we don't handle" much faster, independent of what today's specific
   ID turns out to be).
3. Once you know the real error ID: decide whether it belongs in the
   graceful-empty set (`_NO_OFFERS_IDS`), the 25707-style fallback path, or
   is a genuine hard failure that should stay dead-lettered (client error,
   not transient) — don't assume "add it to the ignore list" is correct
   without understanding what the error actually means.

## Out of scope
- Don't touch the existing 25707 fallback/circuit-breaker logic — it's
  deliberately tuned (todo #1077, session-41) and working as designed.
- Requeuing the 9 dead-lettered jobs — separate step after merge.
- Any other ebay_sync behavior not related to this specific 400 class.

## Dataset
None — error-handling/logging fix only.

## Acceptance (live)
1. Unit test: mock a 400 response with an error ID not in any known set —
   confirm it's now logged with the error ID/message before re-raising
   (assert on log output or an equivalent testable signal).
2. Unit test: confirm the existing 25702/25710/25009 graceful-empty and
   25707 fallback paths are unaffected (no regression — these are working
   production paths).
3. If you determined the real error ID and it warrants a behavior change
   (not just better logging), add a targeted unit test for that specific
   handling.
4. Run the full offline suite — zero regressions.
5. Report in the result manifest the actual error ID/message found and
   your reasoning for how (or whether) it should be handled going forward.

## Quota/risk
Live reproduction (if used) costs eBay API calls — check
`thermal.status` and current quota state first per CLAUDE.md discipline;
prefer log archaeology if the timestamps are still in
`/opt/TGW/var/log/` before spending a live call.
