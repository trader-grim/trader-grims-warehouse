# DONE — todo #1249: diagnose 2771 dead-lettered ebay_draft jobs

## Diagnosis (breakdown by error, live psql query against state_machine)

| Count | % | Cause | Date range |
|---:|---:|---|---|
| 2658 | 95.9% | OpenRouter 402 Payment Required | 2026-07-02 → 07-04 |
| 95 | 3.4% | HardFailure "model returned non-JSON" | mixed |
| 12 | 0.4% | eBay Taxonomy API 429 | 2026-07-01 → 07-02 |
| 6 (4 unique SKUs) | 0.2% | Corrupt/truncated photo files | — |

**2658 + 12 (96.3%) — OpenRouter 402 + Taxonomy 429, all pre-date the
2026-07-08 LLM direct-API provider fix.** Very likely fixable by requeue
now, but this is a bulk billing-cost operation in the exact area that
caused the 6,607-job resubmission storm (the `requeue_ebay_draft_402_dead_letters.py`
incident, todo #1250). Filed as **todo #1265** — needs Dave's explicit
go, a hard attempt cap, and a fresh non-timestamp-suffixed dedupe key.
Did NOT re-run the old script (left untouched per standing instruction).

**95 "model returned non-JSON" — root cause undeterminable from stored
data.** The error message truncated the raw LLM response to 200 chars
before storage (`raw[:200]`), so every failure sample looked identically
"cut off mid-JSON" regardless of what actually happened — I cannot tell
from the historical data whether these were genuinely malformed model
output or something else. **Fixed forward**: raised the truncation to 2000
chars (`src/tgw/workers/ebay_draft.py`) so the next occurrence of this
error class is actually diagnosable. The 95 historical jobs themselves
can't be retroactively diagnosed — the untruncated text was never stored.

**6 corrupt-photo OSErrors (4 unique SKUs) — already known, no new
finding.** Checked all 4 SKUs (`tgw201601011311007`, `tgw201601011312446`,
`tgw201707050929532`, `tgw201707151156404`) against
`/opt/TGW/var/reports/photo-integrity-2026-07-05.tsv` — all 4 are already
in that roster from the existing photo-integrity sweep (same Feb-2022
corruption event, mtimes match). Covered by todo #1154, which is next in
this session's queue.

## What shipped (in-scope, low-risk)

- `src/tgw/workers/ebay_draft.py` — non-JSON error message truncation
  raised from 200 to 2000 chars.
- 2 new tests confirming the new limit (and that it's still capped, not
  unbounded).

## What did NOT ship (flagged, needs Dave)

- **Todo #1265**: whether/how to bulk-requeue the 2670 402/429 dead-letters
  now that the provider fix should make them succeed.

## Live evidence

- `pytest -q` — 2051 passed, 1 skipped (was 2049 — 2 new tests).
- `ruff check` — clean.
- Diagnosis queries run directly against `state_machine.queue_jobs` (not
  inferred from code).
