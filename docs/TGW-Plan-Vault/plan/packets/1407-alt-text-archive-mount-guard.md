# Packet: alt_text history-archive — verify target before writing, don't crash

Todo: #1407   PP: PP-DATALEARN-001   Track: bugfix (single packet)

## Context budget (ALL the model may load)
This packet + `src/tgw/alt_text.py` (whole file) +
`src/tgw/workers/ebay_stage.py` lines ~200-225 (category_not_leaf finding,
reference pattern) + `src/tgw/apis/fence.py`'s `patch_item()` signature +
`reference/invariants.md` C11 section.

## Verified live before this packet was written
- 2026-07-14: `tgw-worker@alt_text.service` was newly enabled (#1108). On
  first run, every job crashed: `_history_sku_dir()`
  (`alt_text.py:111-114`) resolves to `/opt/TGW/data/history/ItemData/<sku>`,
  which is `itemdata_root.parent / "history" / "ItemData" / sku`. On disk,
  `/opt/TGW/data/history` is a symlink to `/media/tgw/MasterArchive/history`
  — MasterArchive (drive `sdf`, cold-archive tier per DRIVE-REGISTRY.md) is
  not currently mounted/spun up (Dave: hard to leave it running
  continuously). `history_sku_dir.mkdir(parents=True, exist_ok=True)` at
  `alt_text.py:299` throws `FileExistsError` on the broken symlink itself
  (Linux `mkdir()` returns EEXIST for a dangling symlink regardless of
  target) — `exist_ok=True` does NOT suppress this because pathlib's
  `mkdir()` only swallows `FileExistsError` when `self.is_dir()` is True,
  and a broken symlink's `is_dir()` is False.
- Each retry burned a real LLM call before crashing on the archive step
  (confirmed in `journalctl -u tgw-worker@alt_text.service`: `google_direct
  unavailable ... falling back to openrouter` immediately preceded each
  `FileExistsError` failure) — wasted quota, not just a log spam problem.
- Worker was stopped manually after all 5 queued jobs went to
  `dead_letter` (1 had already succeeded before the pattern was
  understood). Currently stopped, pending this fix.
- Dave, 2026-07-14: "Hard to leave another device running. There should be
  a mechanism to stop it, maybe just a simple verify the target before
  running?" — confirms the fix direction: pre-flight check, not "just
  mount the drive permanently."
- The C11 durable-finding pattern (used by `ebay_stage.py`'s
  `category_not_leaf` and `ebay_draft.py`'s `photo_files_readable`, both
  from this afternoon's PP-DEADLETTER-001 batch) is:
  ```python
  fence_patch_item(self.config, sku, {'pipeline_error': {
      'code':   '<finding_code>',
      'detail': '<human-readable explanation>',
      'ts':     datetime.now(timezone.utc).isoformat(),
      'source': '<worker_name>',
  }})
  ```
  followed by a `tgw_logging.log_event(...)` call. Import:
  `from tgw.apis.fence import patch_item as fence_patch_item`.

## Spec
1. In `alt_text.py`, before the archive-write block at line ~295-303, add
   a pre-flight check that the history target is actually reachable —
   e.g. `history_sku_dir.parent.parent.exists()` won't work for a broken
   symlink either; use something that correctly detects "symlink resolves
   to nothing" vs "real directory missing but creatable", such as
   `os.path.exists(os.path.realpath(...))`-style resolution, or simpler:
   check whether the configured history root (`itemdata_root.parent /
   "history"`) is itself a broken symlink / unmounted path before
   attempting `mkdir`. Use your judgment on the cleanest correct check —
   the key requirement is it must not itself throw on a broken symlink.
2. If the target is unreachable: **do not crash the job.** Skip the
   archive step, set `archived = False`, and persist a durable finding via
   `fence_patch_item` using the pattern above:
   `'code': 'archive_target_unmounted'`, `'source': 'alt_text'`, `'detail'`
   should name the resolved (broken) path so an operator can see exactly
   what's missing.
3. The rest of the job (alt_text/seo_caption generation, writing to
   `draft_listing`, the vision_results raw-preservation append) must still
   complete normally — the archive step is supplementary, not gating. Per
   Prime Directive 1, the ORIGINAL photo itself is not being discarded
   here (it stays in `ItemData/<sku>/` untouched) — only the cold-archive
   *copy* is deferred, which is what makes skip-with-finding acceptable
   rather than a Data Charter violation. Say so explicitly in your result
   manifest reasoning if you agree, or flag if you think this reasoning is
   wrong.
4. Do NOT build a "retry the archive later" mechanism, a catalog-verify
   rule, or a bulk sweep script that clears these findings once the drive
   comes back — out of scope for this packet. A future packet can sweep
   `pipeline_error.code == archive_target_unmounted` items once
   MasterArchive is mounted and re-run the archive step; this packet just
   needs the finding to exist and be inspectable.
5. Run the full offline suite — zero regressions.

## Test plan (live, in two stages — read carefully)
**Stage A — drive still unmounted (do this first, MasterArchive is
offline right now):**
1. Manually requeue one of the 4 now-dead-lettered `alt_text` jobs (or
   craft a synthetic one for a test SKU) with the drive still unmounted.
2. Start `tgw-worker@alt_text.service` (or run `cmd_alt_text()` directly
   for one SKU without the systemd unit, whichever is faster/safer to
   verify).
3. Confirm: job succeeds (no crash), `alt_text`/`seo_caption` get written
   to the item, and the item JSON now has `pipeline_error.code ==
   'archive_target_unmounted'`.
4. Stop the worker again after this one test — do not leave it running
   unattended against the remaining dead-lettered jobs; that's a decision
   for after Dave reviews, not automatic in this packet.

**Stage B — do NOT attempt yourself.** Dave is separately spinning up the
MasterArchive drive to test the ORIGINAL (pre-existing, unmodified)
archive-write path once it's mounted. Leave a clear note in the result
manifest of exactly what command/steps to re-run once the drive is live
(e.g. requeue another test job, confirm `archived: True` and the file
actually lands in `/opt/TGW/data/history/ItemData/<sku>/`) — Dave will run
that verification himself, this is not part of this packet's completion
criteria.

## Out of scope
- Mounting/unmounting MasterArchive — that's Dave's hardware action.
- Any change to `_history_sku_dir()`'s path derivation itself.
- A sweep/backfill mechanism for skipped archives.
- Any other worker.

## Dataset
No data loss — see Spec point 3's Data Charter reasoning. This only
changes failure-mode behavior (crash → deferred finding), touches no
existing stored data.

## Acceptance (live)
1. Code diff shown (the pre-flight check + finding-write).
2. Stage A test executed live exactly as described, output/result shown
   (item JSON snippet with the new `pipeline_error` finding).
3. Worker left STOPPED at the end of this packet, regardless of Stage A
   outcome — Dave restarts it himself once ready.
4. Full offline suite result — zero regressions.
5. Result manifest includes the exact Stage B verification steps for
   Dave to run once he mounts the drive.

## Quota/risk
Low — one real LLM call for the Stage A test SKU. No bulk operations.
