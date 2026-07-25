# Result: 1683 tz-normalize-fix
Status: done
Todo: #1683   PP: PP-DATAINTEGRITY-001
Files touched:
- src/tgw/http_server.py (added `_parse_ts()` helper; `_job_finished_at()` and
  `_after_baseline()` now normalize offset-naive timestamps to aware-UTC
  before any comparison, instead of raw `datetime.fromisoformat(ts)`)
- tests/test_http_server.py (added 3 regression tests: aware-dead_letter +
  naive-succeeded; naive-dead_letter + aware-succeeded; naive `baseline_at` +
  aware job timestamp in `_after_baseline`)

Live evidence:
- Ran the exact repro from the packet after applying the fix (the packet
  itself already confirmed this raised TypeError pre-fix; verified here that
  it no longer does):
  ```
  live store-categories fetch failed, falling back to category-groups.json: 'ebay_token_path'
  live fulfillment-policy fetch failed, falling back to static cache: 'ebay_token_path'
  REPRO OK - no TypeError
  ```
  (the two log lines are pre-existing unrelated fallback warnings from
  offline/config-less test invocation, not errors)
- Full acceptance command, run with PYTHONPATH pointed at this worktree's
  `src/` (confirmed via `tgw.http_server.__file__` resolving under
  `/opt/TGW/var/worktrees/1683-tz-normalize-fix/src/tgw/http_server.py`) and
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH`:
  ```
  $ python3 -m pytest tests/test_http_server.py tests/test_ebay_upload_dimension_limit.py -q
  ........................................................................ [ 20%]
  ........................................................................ [ 41%]
  ........................................................................ [ 62%]
  ........................................................................ [ 83%]
  .........................................................                [100%]
  345 passed, 1 warning in 8.91s
  ```

Deviations from spec: none — followed the packet's prescribed fix shape
exactly (attach `timezone.utc` to naive parses via a single normalization
point; `timezone` was already imported at file top, no import change
needed; `_after_baseline()` had the identical latent bug against
`_baseline_at` and got the same fix, per the packet's explicit ask; added
both mixed-direction regression tests plus a third for the `_after_baseline`
case).

Out-of-scope findings filed: none — no adjacent issues found while working
this narrow fix. Sum-of-dimensions eBay upload fix (separate commit) was
left untouched as instructed.
