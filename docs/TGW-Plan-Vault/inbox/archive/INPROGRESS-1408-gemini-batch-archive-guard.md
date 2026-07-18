# INPROGRESS: todo #1408 Gemini Batch archive-write guard

Working in worktree `/opt/TGW/var/worktrees/1408-gemini-batch-archive-guard` on
branch `todo/1408-gemini-batch-archive-guard`. Applying the SAME
`_history_root_reachable()` pre-flight + C11 finding + fence-write-after-direct-write
ordering pattern that #1407 (commit 8665a59) applied to `cmd_alt_text`, onto
`_apply_alt_text_result` (the Gemini Batch path) in `src/tgw/alt_text.py`.
Not touching `_history_sku_dir()`'s path derivation itself (#1407's declared
out-of-scope, still respected here). Live pre-flight confirmed: MasterArchive
is still unmounted (`/opt/TGW/data/history` -> dangling symlink), and the
Gemini Batch sweep (`cmd_alt_text_gemini_batch`) has never actually been run
yet (no log/queue-job evidence) — so this is a live latent risk about to be
hit the first time that sweep runs, not purely theoretical.
