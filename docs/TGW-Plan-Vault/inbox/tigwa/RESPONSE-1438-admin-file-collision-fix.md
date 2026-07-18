# Response — #1438 admin-file collision-safe rename + non-Markdown manifest

**Reviewer being answered:** Tigwa (`TIGWA-REVIEW-1438-admin-file-collision-and-manifest.md`)
**Delivered by:** Claude, 2026-07-16
**File changed:** `src/tgw/workers/pm_intake.py`, `tests/test_pm_intake.py`

## 1. Queued-path overwrite — fixed

`scan_and_enqueue()` now checks `dest.exists()` before the move:

- **Identical content already queued** (same owner+filename+sha256) —
  treated as an idempotent duplicate: no move, no re-enqueue, source file
  left in place (nothing is deleted). Logged via
  `pm_intake_duplicate_skipped`.
- **Different content at the same owner-qualified name** — the incoming
  file is never allowed to overwrite the existing queued artifact. It's
  routed to a content-addressed sibling filename
  (`<stem>__<sha8><suffix>`, inside the same owner subdirectory) instead.
  Both artifacts survive; the payload's `filename`/dedupe key reflect the
  actual destination used, `source_path` still carries the original
  owner-qualified name for provenance.

New regression tests:
- `test_scan_and_enqueue_never_overwrites_existing_queued_file` — pre-existing
  queued file with different content survives untouched; incoming file lands
  at a distinct content-addressed path.
- `test_scan_and_enqueue_identical_content_collision_is_idempotent_noop` —
  identical content already queued: no move, `enqueue_job` not called, one
  file in `queued/`.

## 2. Non-Markdown visibility in `--dry-run` — fixed

`build_intake_manifest()` now enumerates every direct-child regular file
(via new `_iter_all_candidate_files()`), not just `*.md`. Non-Markdown
entries report `eligible: false`, reason `"seen but not actionable:
unsupported source type pending supervised normalization"`, and still
carry `owner`/`file_type`/`size_bytes`/`mtime`/`sha256` — only
`planned_queue_path` is omitted since they're not stageable. Control files
(`README.md`, `Untitled.base`) stay excluded from both the manifest and
the mutating scan, matching the original spec.

New regression test: `test_build_intake_manifest_surfaces_non_markdown_as_deferred`
— covers a PDF and an HTML file across owners, plus confirms control-file
exclusion still holds and nothing is mutated.

## Evidence

```text
python3 -m pytest tests/test_pm_intake.py -q --confcutdir=tests -p no:cacheprovider
→ 49 passed (was 46; +3 new)

python3 -m pytest tests/ -q --confcutdir=tests -p no:cacheprovider
→ full suite green, no regressions

sudo -u tgw tgw admin-file --dry-run
→ 33 candidates; PDFs/HTML/TXT across root and tigwa/ now show
  "[skip] ... seen but not actionable: unsupported source type pending
  supervised normalization" instead of being invisible. No filesystem or
  queue mutation (worker remains stopped).
```

## Unrelated issue surfaced (not in scope for #1438)

3 root-level `.md` files (`TIGWA-REVIEW-android-alarm-dual-route.md`,
`TIGWA-REVIEW-context-burden-retrieval-first.md`,
`TIGWA-REVIEW-deepseek-v4-flash-work-routing.md`) fail the dry-run's
hashing step with `PermissionError: [Errno 13]`. Worth a filesystem
permissions check on `inbox/` root — flagging, not fixing here since it's
outside this todo's scope.

## Still out of scope (unchanged from #1435/#1436/#1438)

Worker activation/restart, real intake processing, LLM calls from
admin-file, Master Plan writes, permanent-library shelf selection,
semantic document normalization, flake/service/provider changes.

Leaving #1438 open for your independent re-review, same pattern as
#1435/#1436.
