# Review: 1297 ebay-sku-migrate-default
Status: cleared — stitched in `e94155d` ("first concurrent batch, 1/3").
RECONSTRUCTED RETROACTIVELY 2026-07-13 from `1297-RESULT.md` evidence +
the merge commit; no contemporaneous REVIEW.md was written (compliance
gap — same concurrent-batch pattern as #1280/#1282/#1284/#1288/#1291).
Reviewer: Claude (retroactive reconstruction)

Checked (from RESULT.md's own reported evidence): live pre-flight
re-read of `/opt/TGW/config/tgw-api-config.json` confirmed
`ebay_sku_migrate.enabled` was explicitly `true` (not absent), so the
packet's live-behavior assumption held before proceeding. Single-line
change at `ebay_sku_migrate.py:783` — default flipped from
`.get('enabled', True)` to `.get('enabled', False)`, matching the
documented safe-off default; no other line touched. Confirmed against the
worktree's own module before trusting results. Acceptance verified:
absent-key case now defaults to disabled; explicit-true case still
proceeds unaffected; live config re-read post-change confirms no
behavior regression to current intentional operation. Targeted tests: 6
passed. Full offline suite: 2046 passed, 1 skipped, 0 failed. No
deviations from spec, no out-of-scope findings.

Stitched.
