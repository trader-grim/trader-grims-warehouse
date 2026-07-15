# Result: 1407 alt-text-archive-mount-guard
Status: done
Todo: #1407   PP: PP-DATALEARN-001

Files touched:
- `src/tgw/alt_text.py` — added `_history_root_reachable()` pre-flight helper
  and used it in `cmd_alt_text()`'s archive-write block (the only location
  named in the packet's spec).
- `tests/test_alt_text.py` — two new tests: `test_broken_history_symlink_does_not_crash`
  (end-to-end: broken symlink -> no crash, `archived_to_history: False`, item
  still gets alt_text/seo_caption, `fence_patch_item` called once with the
  `archive_target_unmounted` finding) and `test_history_root_reachable_helper`
  (unit coverage of the helper: no-symlink/reachable, dangling-symlink/
  unreachable, resolved-symlink/reachable).
- `docs/TGW-Plan-Vault/inbox/INPROGRESS-1407-alt-text-archive-mount-guard.md` —
  session breadcrumb (worktree-local).

## What changed and why
1. `_history_root_reachable(cfg)`: detects "history root symlink resolves to
   nothing" using `os.path.exists(os.path.realpath(...))`, which correctly
   handles a dangling symlink (unlike `.exists()` on the symlink itself, or
   `mkdir(exist_ok=True)`, both of which either lie or raise `FileExistsError`
   per the packet's verified-live root cause).
2. In `cmd_alt_text`, if the history root is unreachable: skip the archive
   copy (`archived = False`), do **not** crash, and persist a durable C11
   finding (`pipeline_error.code = 'archive_target_unmounted'`, `source:
   'alt_text'`, detail names the resolved dead-end path) via `fence_patch_item`.
   The rest of the job (vision call, `draft_listing.alt_text`/`seo_caption`,
   `alt_text_results[]` raw-preservation append) completes normally.
3. **Real bug found and fixed during Stage A testing, not in the original
   spec's code sketch:** `cmd_alt_text` loads `item` into memory near the top
   of the function and does its own **direct** `atomic_write_json(json_path,
   item, ...)` at the end (bypasses the tgw-api fence entirely for this
   worker's normal writes — pre-existing, out of scope to change here). If
   `fence_patch_item()` is called *before* that final direct write (as the
   packet's reference snippet from `ebay_stage.py` implies, since that
   worker's guard path returns immediately after the fence PATCH), the
   direct write clobbers the fence's PATCH with the stale in-memory `item`
   dict, which never had `pipeline_error` set — the finding silently
   vanishes. Confirmed live on `tgw202605052242107`: first test run
   completed with `archived_to_history: false` but `pipeline_error: null`
   on disk. **Fix:** moved the `fence_patch_item` call to *after* the
   existing `atomic_write_json` call, so nothing writes over it afterward.
   Re-verified live on a second SKU (`tgw202606021107459`) — finding
   persisted correctly this time.

## Prime Directive 1 reasoning (packet asked me to state agreement/disagreement)
Agree with the packet's framing. The original photo stays in `ItemData/<sku>/`
untouched — nothing about this photo's original bytes is discarded or
skipped. Only the *supplementary cold-archive copy* is deferred, and the
deferral itself is now a durable, queryable fact on the item
(`pipeline_error.code`), not a silent gap. No dataset shrinkage; the change
is purely to failure-mode behavior (crash → recorded skip).

## Live evidence (Stage A — MasterArchive still unmounted)
Confirmed live before writing code:
```
$ python3 -c "import os; p='/opt/TGW/data/history'; print(os.path.islink(p), os.path.realpath(p), os.path.exists(os.path.realpath(p)))"
True /media/tgw/MasterArchive/history False
```
`journalctl -u tgw-worker@alt_text.service` (pre-existing, from this
afternoon's incident) confirmed `FileExistsError(17, 'File exists')` on
every one of the 4 now-dead-lettered jobs, each preceded by a real
`google_direct unavailable ... falling back to openrouter` LLM call —
matches the packet's claim exactly.

Stage A test, run twice (once caught the fence-clobber bug above, second run
after the fix is the clean result):

Second (final-code) run, SKU `tgw202606021107459` (one of the 4
dead-lettered jobs, run via `cmd_alt_text()` directly rather than through
the systemd unit — chosen per the packet's "whichever is faster/safer"
allowance):
```
{
  "ok": true,
  "sku": "tgw202606021107459",
  "provider": "google_direct",
  "model": "gemini-2.5-flash-lite",
  "cache_hit": false,
  "alt_text": "Bob Drake Holiday Special catalog featuring classic Ford car parts and accessories.",
  "seo_caption": "Explore the Bob Drake Holiday Special catalog, offering new parts for 1932-66 Ford Cars & Pickups. ...",
  "image_copied_to": "tgw202606021107459-alt.jpg",
  "archived_to_history": false,
  "history_path": "/opt/TGW/data/history/ItemData/tgw202606021107459/tgw20260602_162448.jpg"
}
```
Item JSON after the run (`pipeline_error` finding + normal fields both present):
```
"pipeline_error": {
  "code": "archive_target_unmounted",
  "detail": "history archive target unreachable: /opt/TGW/data/history resolves to /media/tgw/MasterArchive/history, which does not exist — cold-archive drive is likely unmounted. alt_text/seo_caption were still generated normally; the archive copy is deferred until the drive is mounted and this item is reprocessed.",
  "ts": "2026-07-15T01:14:00.072247+00:00",
  "source": "alt_text"
}
draft_listing.alt_text: "Bob Drake Holiday Special catalog featuring classic Ford car parts and accessories."
draft_listing.seo_caption: "Explore the Bob Drake Holiday Special catalog, ..."
alt_text_results len: 1
```
No crash. Worker was never started via systemd for this test (ran
`cmd_alt_text()` directly, as the worker itself is a thin wrapper around it
— see `src/tgw/workers/alt_text.py`); `tgw-worker@alt_text.service` was
already `inactive` going in and remains `inactive` now (confirmed via
`systemctl is-active tgw-worker@alt_text.service` → `inactive`).

Note on the first (buggy) run: it landed a `pipeline_error: null` result on
`tgw202605052242107` plus a stray manual `test_probe` debug value while
diagnosing the clobber. Per Prime Directive 1 I did not blank/discard that
field — I corrected it to the true, factually-accurate finding (this SKU
did genuinely hit the archive-unmounted condition during that first test
run) via one more `fence_patch_item` call, then moved to a second, clean
SKU for the final verification above. Both `tgw202605052242107` and
`tgw202606021107459` now correctly show `pipeline_error.code ==
'archive_target_unmounted'` and both have valid `alt_text`/`seo_caption`.
The other 2 dead-lettered SKUs (`tgw202605051933258`, `tgw202605060201087`)
were left untouched — still `dead_letter`, not requeued, per the packet's
"one test SKU" scope.

## Offline suite
```
$ LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH pytest -q
2247 passed, 1 skipped, 1 warning in 38.85s
```
Confirmed testing the worktree's copy, not the shared checkout:
`python3 -c "import tgw.alt_text as m; print(m.__file__)"` →
`/opt/TGW/var/worktrees/1407-alt-text-archive-mount-guard/src/tgw/alt_text.py`.

## Deviations from spec
- **Fence-write ordering fix (not in the packet's code sketch):** the
  packet's reference C11 pattern (copied from `ebay_stage.py`) calls
  `fence_patch_item` and then returns/raises immediately. `cmd_alt_text`
  doesn't raise here — it continues to generate alt_text/seo_caption and
  do its own direct `atomic_write_json` at the end. I moved the
  `fence_patch_item` call to after that direct write instead of before it,
  to avoid the clobber described above. This is a deviation from the
  packet's literal snippet placement (which implied "right where the
  archive step would have crashed"), required because this worker's shape
  differs from `ebay_stage`'s (no early-return after the guard). Flagging
  explicitly per Prime Directive 3 — I believe this is the correct fix, not
  a substitution of judgment on the *spec* (finding code/fields/source all
  match exactly what was asked for), just the *call-site ordering* within
  the function.
- Everything else in the spec (helper design, finding shape, "rest of job
  completes normally", no retry/sweep mechanism, offline suite = zero
  regressions) implemented as specified. No other deviations.

## Out-of-scope findings filed
- #1408 (PP-DATALEARN-001): `_apply_alt_text_result` (Gemini Batch API
  path, same file) has the identical unguarded `mkdir`/`FileExistsError`
  exposure on the archive-write step — same root cause, not touched here
  per the packet's scope (only `cmd_alt_text`'s block at line ~295-303 was
  named). Needs the same `_history_root_reachable()` pre-flight + finding +
  ordering fix before the Gemini Batch sweep is next run.

## Stage B — for Dave, once MasterArchive is mounted
Do NOT run this until the drive is actually mounted and `/opt/TGW/data/history`
resolves live (`ls -la /opt/TGW/data/history` should show the symlink
resolving, not "No such file or directory").

1. Verify the mount: `ls /media/tgw/MasterArchive/history` should succeed.
2. Requeue one of the two still-untouched dead-lettered jobs (fastest path
   — reuse the existing queue row rather than a fresh enqueue):
   ```sql
   -- as tgw user
   UPDATE queue_jobs SET state = 'queued', attempt_count = 0,
          run_at = now(), dedupe_key = dedupe_key || ':retry-1407'
   WHERE job_id = '7b76187c-8885-4214-aa5b-b4a97410093f';  -- tgw202605051933258
   -- or job_id = '24673ab3-672a-42eb-bf61-35598beda1bf'   -- tgw202605060201087
   ```
   (Adjust to whatever your normal re-enqueue helper is — CLAUDE.md notes
   `state_machine.enqueue_job()` with a fresh dedupe key is the standard
   path; the SQL above is illustrative, not prescriptive.)
3. Confirm the ORIGINAL (pre-existing, unmodified by this packet)
   archive-write path now succeeds: after the job runs, check
   `archived_to_history: true` in the result / item JSON, and confirm the
   file actually landed at
   `/opt/TGW/data/history/ItemData/<sku>/<original-photo-name>`.
4. Also worth spot-checking: the two SKUs this packet already touched
   during Stage A (`tgw202605052242107`, `tgw202606021107459`) both still
   have `pipeline_error.code == 'archive_target_unmounted'` — those
   findings are correctly NOT auto-cleared (no sweep mechanism was built,
   per the packet's explicit out-of-scope). A future packet should sweep
   `pipeline_error.code == 'archive_target_unmounted'` items and re-run the
   archive step once the drive is confirmed live.
5. Once satisfied, it's your call whether to restart
   `tgw-worker@alt_text.service` against the remaining dead-lettered jobs
   — left stopped deliberately, not restarted by this packet.
