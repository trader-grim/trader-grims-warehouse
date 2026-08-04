# In progress — #1438 admin-file collision-safe rename + non-Markdown manifest

Working `src/tgw/workers/pm_intake.py` per Tigwa's review
(`TIGWA-REVIEW-1438-admin-file-collision-and-manifest.md`, now archived):

1. `scan_and_enqueue()`'s `md_file.rename(dest)` can silently overwrite an
   already-queued file with the same owner-qualified name. Fix: detect
   `dest.exists()` before the move; if content (sha256) is identical, skip
   as an idempotent duplicate (no move, no re-enqueue); if content differs,
   route the incoming file to a content-addressed destination
   (`<stem>__<sha8><suffix>`) instead of overwriting.
2. `build_intake_manifest()` only globs `*.md`, so non-Markdown files in
   root/dave/tigwa are invisible in `--dry-run` instead of visible-but-
   deferred. Fix: enumerate all direct-child regular files (excluding
   control files: README.md, Untitled.base), report non-Markdown entries
   as `eligible: false` with reason `"seen but not actionable: unsupported
   source type pending supervised normalization"`, preserving path/owner/
   type/size/mtime/sha256 same as Markdown entries.

Adding regression tests for both, running `pytest tests/test_pm_intake.py -q`,
then a fresh `sudo -u tgw tgw admin-file --dry-run` live check, then an
updated report to `inbox/tigwa/`. Not touching worker activation, LLM calls,
or Master Plan writes — same boundaries as #1435/#1436/#1438.

Todo: #1438 (primary), #1456 (opened same content by mistake — will close
as dup once #1438 is done).
