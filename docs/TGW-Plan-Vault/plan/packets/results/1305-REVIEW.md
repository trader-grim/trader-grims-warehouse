# Review: 1305 itemdata_scrub.py fence path/read fix
Status: cleared
Reviewer: Claude (runner-review, 2026-07-14 morning)
Branch: worktree-agent-adb8e7a854ca5a84f (commit 03e9868)

Checked:
- Manifest sanity (step 1): status/files-touched/live-evidence all present;
  PYTHONPATH override to worktree confirmed in live evidence — treated as
  verified, not unverified.
- Diff vs merge-base (6f2d7ef, the branch's actual fork point — NOT current
  catio-nix-0.0.1-alpha HEAD, which has since diverged with unrelated doc
  churn): 5 files, all in scope — itemdata_scrub.py, its own
  self-authored packet + result + breadcrumb, and one existing test file
  (carve-out: tests for a module already in the packet's declared scope).
  No out-of-scope files touched.
- Spec conformance: path construction now delegates to config.sku_json();
  unsafe-sku rejection now uses the canonical _safe_segment() ValueError
  path (same log line/return value as before); read replaced with
  resolver.load_item_doc() + find_current_sku() alias fallback — matches
  spec exactly, no silent extras.
- Out-of-scope items correctly left untouched: recursive key-deletion
  write (scrub_value/scrub_itemdata/atomic_write_json call) unchanged,
  matches the packet's explicit reasoning (no fence equivalent exists,
  real fence-API redesign not a mechanical fix) and the in-file
  PP-FENCE-001 gap comment, now updated to note the scope split.
- invariants.md: A1 (atomic write) unaffected — archive_root already
  passed, no change. A4 (canonical path construction) — this diff is a
  direct fix toward A4, no violation.
- No live/production writes attempted; worker has no installed systemd
  unit (re-confirmed in manifest).

No trigger fired. Cleared for stitch.
