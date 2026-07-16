# INPROGRESS: todo #1407 alt_text archive mount guard

Working in worktree `/opt/TGW/var/worktrees/1407-alt-text-archive-mount-guard` on
branch `todo/1407-alt-text-archive-mount-guard`. Adding a pre-flight check in
`src/tgw/alt_text.py::cmd_alt_text` before the history-archive write block so a
broken/unmounted MasterArchive symlink doesn't crash the job with
`FileExistsError` — skip archive, set `archived=False`, persist a C11 durable
finding (`pipeline_error.code = archive_target_unmounted`) via `fence_patch_item`,
and let the rest of the job (alt_text/seo_caption/vision_results) complete
normally. Stage A live test only (drive still unmounted); worker left stopped
at the end per Dave's instruction.
