# Result: 1408 gemini-batch-archive-guard
Status: done
Todo: #1408   PP: PP-DATALEARN-001

Files touched:
- `src/tgw/alt_text.py` — applied the same `_history_root_reachable()`
  pre-flight + C11 durable finding + fence-write-after-direct-write ordering
  pattern from #1407 (commit `8665a59`, which fixed `cmd_alt_text` — the
  serial path) onto `_apply_alt_text_result` (the Gemini Batch apply path).
  Did NOT touch `_history_sku_dir()` or its path derivation — reused the
  existing helper and existing module-level imports (`os`, `tgw_logging`,
  `fence_patch_item`) as-is, per #1407's declared out-of-scope boundary.
- `tests/test_alt_text.py` — two new tests mirroring #1407's pair, adapted
  to `_apply_alt_text_result`'s call signature:
  `test_apply_alt_text_result_broken_history_symlink_does_not_crash` (dangling
  symlink at history root -> no crash, `archived_to_history: False`,
  alt_text/seo_caption still written, `fence_patch_item` called once with
  `pipeline_error.code == 'archive_target_unmounted'`) and
  `test_apply_alt_text_result_history_reachable_archives_normally` (control
  case: reachable history root -> normal archive, no finding recorded —
  guards against the new pre-flight short-circuiting the happy path).
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1408-gemini-batch-archive-guard.md`
  — session breadcrumb (worktree-local).

## Pre-flight verification (live, before writing code)
- Confirmed via `journalctl -u 'tgw-worker@*' --since "30 days ago"` that the
  identical `FileExistsError(17, 'File exists'): '/opt/TGW/data/history'`
  crash already hit the SERIAL path (`cmd_alt_text`, queue `alt_text`) on
  2026-07-14, producing 4 dead-lettered jobs — this is the same incident
  #1407 fixed. That confirms the root cause (unmounted MasterArchive, dangling
  symlink) is real and current, not hypothetical.
- Checked specifically whether the GEMINI BATCH path
  (`cmd_alt_text_gemini_batch` / `_apply_alt_text_result`) has ever actually
  run: no matches for `gemini_batch`/`_apply_alt_text_result`/`BatchJob` in
  `journalctl` (30 days) or any file under `/opt/TGW/var/log/*.log`, and no
  `queue_jobs` rows exist for a gemini-batch-specific queue/entity. It is
  wired into `tgw.api` (`cmd_alt_text_gemini_batch` called from
  `src/tgw/api.py:5076`) but has never been invoked in production. So this
  fix is a live latent risk about to be hit the FIRST time the batch sweep
  runs, not a "found in logs" bug for the batch path specifically — matching
  the todo's own framing ("before the Gemini Batch sweep is next run").
- Confirmed live, on the real production config (`/opt/TGW/config/tgw-api-config.json`,
  read-only, no mutation), that `_history_root_reachable(cfg)` returns `False`
  right now: `/opt/TGW/data/history` is still a symlink to
  `/media/tgw/MasterArchive/history`, and that target still doesn't exist
  (`ls /media/tgw/MasterArchive` shows only the empty mountpoint dir, no
  `history` subdir) — i.e. if the Gemini Batch sweep were run today, it would
  hit exactly this bug without the fix.

## Live evidence (step 5)
1. Guard correctly detects live prod state (read-only check, no mutation):
   ```
   $ sudo -u tgw env LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
       PYTHONPATH=/opt/TGW/var/worktrees/1408-gemini-batch-archive-guard/src \
       python3 -c "
   import tgw.config as config
   from tgw.alt_text import _history_root_reachable
   cfg = config.load_config(config.DEFAULT_CONFIG)
   print('history_root_reachable (live prod cfg):', _history_root_reachable(cfg))
   "
   history_root_reachable (live prod cfg): False
   ```
2. Confirmed testing the worktree's own copy, not the shared checkout:
   ```
   $ python3 -c "import tgw.alt_text as m; print(m.__file__)"
   /opt/TGW/var/worktrees/1408-gemini-batch-archive-guard/src/tgw/alt_text.py
   ```
3. Targeted test file, mirrors #1407's acceptance:
   ```
   $ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH \
     PYTHONPATH=/opt/TGW/var/worktrees/1408-gemini-batch-archive-guard/src:$PYTHONPATH \
     pytest -q tests/test_alt_text.py
   50 passed in 0.96s
   ```
4. Full offline suite (all tests except the 6 modules that fail to
   collect for an unrelated, pre-existing reason — see Deviations below):
   ```
   1998 passed, 1 skipped, 2 failed in 190.07s
   ```
   Both failures are in `tests/test_invariant_c12_field_set_accessors.py`
   and assert exact line numbers in `src/tgw/http_server.py`, a file this
   packet never touches (`git diff --stat HEAD` on this branch shows only
   `src/tgw/alt_text.py` and `tests/test_alt_text.py` changed) — pre-existing
   line-number drift caused by uncommitted WIP in the shared checkout not
   present on this clean branch. Filed as todo #1499 (see below), not fixed
   here (out of scope for this packet).

## Deviations from spec
- None on the actual fix — the pattern (`_history_root_reachable()`
  pre-flight, `archive_finding` dict shape, `fence_patch_item` call placed
  *after* the existing direct `atomic_write_json` call to avoid the same
  clobber #1407 found live) is copied verbatim in structure from #1407's
  `cmd_alt_text` fix, adapted only to `_apply_alt_text_result`'s existing
  variable names/control flow. `_history_sku_dir()` itself was not touched.
- 6 test modules (`test_category_context_conditions.py`,
  `test_condition_options.py`, `test_condition_remap.py`, `test_fence.py`,
  `test_http_server.py`, `test_local_ts.py`) fail to collect in this clean
  worktree with `ModuleNotFoundError: No module named
  'tgw.ebay.category_aspect_migration'` — that module is untracked/uncommitted
  in the shared checkout (`git status --short` there shows it as `??`), so a
  branch cut from committed HEAD doesn't have it. Pre-existing environment
  gap, unrelated to alt_text.py; not something this packet's scope covers.
  Excluded via `--ignore` to get a full-suite signal on everything else.

## Out-of-scope findings filed
- #1499 (PP-COHESION-001): C12 invariant test's line-number assertions
  against `http_server.py` are fragile to any worktree/branch that doesn't
  have the shared checkout's uncommitted WIP — either commit
  `category_aspect_migration.py` or make the test line-number-independent.
