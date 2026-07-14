# Review: 1315 scrub.py archive_root fix
Status: cleared
Reviewer: Claude (runner-review, 2026-07-14 morning)
Branch: todo/1315-scrub-archive-root (based on current catio-nix-0.0.1-alpha HEAD)

Checked:
- Manifest sanity: status/files-touched/live-evidence present; worktree
  PYTHONPATH confirmation present (`tgw.scrub.__file__` resolved under
  the worktree before testing).
- Diff vs current HEAD: 3 files, all in scope — scrub.py's 3 named call
  sites, its own result manifest, and one existing test file (carve-out).
  No out-of-scope files touched.
- Spec conformance: exact — 3-line change, `archive_root=cfg.get('archive_root')`
  added to the exact 3 call sites named in the finding, matching the
  codebase convention used elsewhere (api.py/alt_text.py/revision.py/
  items.py). No behavior change beyond invariant E5 (archive-before-
  overwrite) now firing for these passes.
- invariants.md E5 — this diff is a direct fix, no violation introduced.
- No live/production writes attempted.

No trigger fired. Cleared for stitch.
