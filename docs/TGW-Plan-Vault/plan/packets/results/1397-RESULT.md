# Result: 1397 ebay-sync-offer-400
Status: done
Todo: #1397   PP: PP-DEADLETTER-001

Files touched:
- src/tgw/ebay/sync.py (fetch_all_offers 400-handling: log the "empty errors
  list" edge case before re-raising, not just the populated-list case)
- src/tgw/workers/ebay_sync.py (handle()'s 400 except block: log the
  errorId/message -- or the raw response text if unparseable -- before
  re-raising for any 400 that isn't the known 25707 orphaned-SKU class)
- tests/test_ebay_sync_unrecognized_400.py (new -- 5 tests: unrecognized
  errorId logged before raise, unparseable-body case also logs, empty
  errors-list case in fetch_all_offers also logs, 25707 fallback path
  regression guard, 25702/25710/25009 graceful-empty regression guard)
- docs/TGW-Plan-Vault/plan/packets/results/1397-RESULT.md (this file)

## What the actual error was (spec item 1)

Could not recover the literal eBay errorId for the 9 dead-lettered jobs --
both /opt/TGW/var/log/worker_ebay_sync.log* (oldest rotated copy starts
2026-07-02 12:27) and journald (--disk-usage confirms retention starting
2026-07-02) do not go back far enough; 2026-06-30 is outside both retention
windows. Live reproduction was not attempted (out of scope per Acceptance
step 5's "prefer log archaeology" and the finding below made it
unnecessary/unjustified to spend quota).

However, cross-referencing queue_jobs.finished_at against git history
gives strong circumstantial evidence these are NOT a new/unhandled error
class, contradicting the packet's framing:

- All 9 dead-letters (error_code=WORKER_EXCEPTION, the 400 on
  GET /sell/inventory/v1/offer?marketplace_id=EBAY_US&limit=100&offset=0)
  have finished_at between 2026-06-30 01:32 UTC and 2026-06-30 23:07 UTC.
- Commit 382f3f0 ("ebay_sync: catch HTTP 400/25707 ... root cause is an
  orphaned draft offer on eBay with a book-title SKU ... todo #1077")
  landed 2026-06-30 22:24:04 -0700 = 2026-07-01 05:24:04 UTC -- i.e.
  *after every single one* of the 9 dead-letter timestamps. Before this
  commit, EbaySyncWorker.handle() had NO except HTTPError around
  fetch_all_offers() at all -- any 400 (including 25707, the exact bug
  that commit's own message describes fixing that same day) propagated
  straight to dead-letter, unhandled and unlogged at the worker-caller
  level.
- The URL itself corroborates the timeline independently: it includes
  marketplace_id=EBAY_US, a param fetch_all_offers() no longer sends
  as of commit 85e0764 (2026-07-09, PP-EBAY-MOTORS-001 follow-up,
  "old hardcoded EBAY_US would silently exclude Motors offers"). The
  current code cannot produce this exact URL at all.

Conclusion: these 9 dead-letters are stale, from code that predates
both the day-of 25707 fallback (382f3f0) and the later marketplace_id
removal (85e0764). They were never requeued after the fix landed hours
later the same evening. The specific errorId is very likely 25707 (matches
the commit message's same-day root-cause and the general symptom -- the
known orphaned-draft-offer / todo #1077 issue) but this can't be proven
retroactively since the response body was never persisted (only
str(HTTPError), confirmed per the packet's own note).

## Spec item 2 -- logging fix

Implemented in both files per the packet, even though sync.py's
fetch_all_offers() already logged populated errors lists before
re-raising (contrary to the packet's claim that line 764 was silent --
verified live in the code, lines 761-763 do log). The real gaps fixed:
1. sync.py:758-765 -- a 400 that parses OK but has an EMPTY errors
   list previously fell through the "for e in errors:" loop as a silent
   no-op before re-raising. Now logs a warning with the raw response text.
2. ebay_sync.py's own except HTTPError block (the "else: raise" at the
   old line 158, not-25707 branch) had NO logging of its own -- it
   relied entirely on fetch_all_offers() having already logged upstream.
   Now logs the errorId/message (or raw body if unparseable) independently,
   so triage doesn't depend on the inner log line surviving unbroken up
   the call chain.

## Spec item 3 -- behavior decision

No behavior change beyond logging. Not added to _NO_OFFERS_IDS or the
25707-style fallback: the evidence above indicates this is very likely
already the 25707 class, which already has a working, deliberately-tuned
fallback (todo #1077, session-41 circuit breaker) -- out of scope to touch
per the packet. If it turns out to be a genuinely different errorId, the
new logging (this fix) will surface it plainly the next time it recurs,
which it hasn't since 2026-06-30 (no similar 400 dead-letters in
queue_jobs since).

Live evidence: pytest -q -- full offline suite, 2215 passed, 1 skipped, 0
failed, 404.19s (run from /opt/TGW/var/worktrees/1397-ebay-sync-offer-400
with LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=.../src:$PYTHONPATH,
confirmed tgw.workers.ebay_sync.__file__ resolves under the worktree
before running). New test file tests/test_ebay_sync_unrecognized_400.py
(5 tests) passes; targeted run of it plus test_ebay_sync_fetch_all_offers.py,
test_ebay_sync_fallback_state.py, test_health_ebay_sync_fallback.py
also passes (23 passed) confirming no regression in the existing 25707/
graceful-empty paths.

Deviations from spec: none in the code changes. One factual correction to
the packet's own framing: the packet states "both paths already re-raise
for any 400 whose error IDs aren't in their known set" implying sync.py
line 764 is silent about it -- it is not (already logs populated error
lists); only the empty-list edge case and ebay_sync.py's own block were
actually silent. Flagged here rather than silently reinterpreting the
packet.

Out-of-scope findings filed: none -- no new adjacent bug found; the 9
dead-lettered jobs themselves are explicitly out of scope per the packet
("Requeuing the 9 dead-lettered jobs -- separate step after merge").
