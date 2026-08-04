status: cleared
reviewer: Claude (main session, catio-nix-0.0.1-alpha)
todo: #1407   pp_ref: PP-DATALEARN-001
branch: todo/1407-alt-text-archive-mount-guard @ 8665a59

## Checked
- Diff scope (merge-base vs branch, not the stale `main`): `src/tgw/alt_text.py`,
  `tests/test_alt_text.py`, inbox breadcrumb, result manifest. Nothing outside
  packet scope.
- Spec items 1-4 (pre-flight `_history_root_reachable()`, skip-with-finding
  on unreachable target, rest of job completes normally, no retry/sweep
  mechanism built) all implemented exactly as specified.
- Deviation disclosed in manifest (moved `fence_patch_item` call to *after*
  the existing direct `atomic_write_json`, to avoid the write clobbering the
  finding — confirmed live during the executor's own Stage A testing) is a
  call-site-ordering fix required by this worker's shape (no early-return
  after the guard, unlike `ebay_stage`'s reference pattern), not a scope or
  spec-content change. Finding code/fields/source match the spec exactly.
  Accepted.
- invariants.md C11 ("a skip/guard is a finding, not a log line") satisfied:
  `pipeline_error.code = archive_target_unmounted` persisted via the fence,
  durable and catalog-verify-queryable, not just logged.
- Live evidence in the manifest is a real observed result (item JSON with
  the finding + alt_text/seo_caption both present, SKU tgw202606021107459),
  not a "should work" claim.
- Re-ran independently in the worktree (`PYTHONPATH` overridden to the
  worktree's own `src/`, confirmed via `tgw.alt_text.__file__`):
  `tests/test_alt_text.py` 48 passed; full offline suite 2247 passed, 1
  skipped — matches the manifest's numbers exactly.
- Worker left `inactive` per Acceptance item 3 (not independently
  re-verified live by this review — taken from manifest, low risk).
- No trigger from the out-of-control list fired.

## Not re-verified by this review
- Did not re-run the live Stage A LLM call myself (would burn a duplicate
  real LLM call for no new information) — manifest's live evidence is
  concrete and specific enough (SKU, exact JSON, journalctl cross-check)
  to accept as-is.

## Summary
Clean. Cleared for stitch.
