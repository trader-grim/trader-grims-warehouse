# Response — #1435/#1436: admin-file inbox topology update, implemented

**To:** Tigwa (coordinator, #1434), Dave
**From:** Claude
**Status:** implemented, tested, dry-run verified live. Requesting review.

## Changed paths

- `src/tgw/workers/pm_intake.py` — `scan_and_enqueue`, `cmd_admin_file`, `_archive`,
  plus new `_iter_source_files`, `_relative_name`, `_sha256_file`, `build_intake_manifest`.
  `handle()`'s archive/review-dir call sites updated for owner-qualified paths.
- `src/tgw/api.py` — `--dry-run` flag added to the `admin-file` subparser and dispatch.
- `tests/test_pm_intake.py` — 7 new tests (discovery, exclusion, no-collision,
  idempotent rerun, payload provenance, dry-run manifest, dry-run non-mutation).
- `docs/TGW-Plan-Vault/inbox/README.md` — rewritten for the new topology.

## What it does now

- Discovers only `inbox/` (root), `inbox/dave/`, `inbox/tigwa/`. Never recurses into
  `inbox/claude/`, `inbox/queued/`, `inbox/archive/`, `inbox/review/` — structural via
  non-recursive `glob('*.md')` per directory, not just a filename check.
- Root files keep bare filenames (`queued/foo.md`, unchanged, backward compatible).
  `dave/`/`tigwa/` files are owner-qualified (`queued/dave/foo.md`,
  `queued/tigwa/foo.md`) so same-named notes from different owners never collide.
- Job payload now carries `filename` (owner-qualified), `owner`, `source_path`,
  `sha256`, `intake_ts` — not filename-only identity.
- Dedupe key is `pm_intake:{owner-qualified-name}:{sha256[:16]}` — same owner+name
  with unchanged content is not re-enqueued (idempotent rerun); an edited note or a
  same-named note from a different owner gets its own key.
- `_archive` now flattens the owner-qualified name into the archived filename
  (`archive/<ts>-tigwa__foo.md`) so provenance survives archiving too.
- `tgw admin-file --dry-run` reports a manifest (source path, owner, file type, size,
  mtime, age, sha256, eligibility + reason, planned queue path) with zero filesystem
  or state_machine mutation — verified live: `mock_sm.init.assert_not_called()` in
  tests, and a real `sudo -u tgw tgw admin-file --dry-run` run against the live vault
  correctly found and reported all 14 real notes currently in `inbox/tigwa/`
  (all correctly skipped, under the 4h delay gate) with zero writes.

## Test evidence

`python3 -m pytest tests/test_pm_intake.py -q --confcutdir=tests -p no:cacheprovider`
→ 46 passed (39 pre-existing + 7 new), 0 failures. `--confcutdir=tests` works around
the known repo-root `nix/` permission-scan issue (#1365, unrelated, already tracked).

Live dry-run against production vault (non-mutating, real data):
```
$ sudo -u tgw tgw admin-file --dry-run
14 candidate(s) found (dry run — nothing moved or enqueued):
  [skip] tigwa tigwa/Hermes-based-AI-worker-staffing-setup.md — 1.3h old, delay gate is 4h
  ... (13 more, all correctly scoped to inbox/tigwa/, all correctly gated)
```

## Explicitly did not do (per both requests' scope)

- Did not activate/restart the `pm_intake` worker.
- Did not run `admin-file` for real (no `--now` without `--dry-run`) against live
  Plan Vault material — every live check was `--dry-run`.
- No automatic Master Plan edits, permanent library-shelf logic, or semantic
  classification beyond what already existed.
- Did not touch `flake.nix`, service units, or provider/model config.
- Did not create `inbox/review/` proactively — the code already `mkdir(parents=True)`s
  it on first real flag_for_review use; nothing to pre-create.

## Remaining limitations / open questions (from #1436's review questions)

1. **Queued-path layout:** went with `queued/<owner>/<filename>` (subdirectory) over a
   flattened `queued/<owner>__<filename>` — mirrors the intake topology directly and
   keeps `Path` joins simple. Flag if a flat naming is actually preferred for some
   downstream tooling I don't have visibility into.
2. **Dedupe key:** used `sha256[:16]` directly in the dedupe key (path-based delivery
   identity + content hash combined), not a separate future intake ledger — simplest
   thing that satisfies "idempotent unless content changes" today. A real intake
   ledger (audit trail of what's been seen/processed) is still open if #1434's
   librarian workflow wants one; this doesn't preclude adding it later.
3. **Non-Markdown types:** out of scope per both requests (semantic normalization is a
   later supervised stage) — `admin-file` still only globs `*.md`. Non-.md files sitting
   in `inbox/dave/`/`inbox/tigwa/` are silently invisible to `admin-file` right now
   (neither staged nor flagged). Worth a decision: should `build_intake_manifest`
   surface non-.md files as "seen but not actionable" rather than not seeing them at
   all? Left as-is pending direction.

#1435/#1436 kept open per instruction, pending your review.
