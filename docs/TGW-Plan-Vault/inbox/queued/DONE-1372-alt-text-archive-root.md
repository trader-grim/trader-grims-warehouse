Working todo #1372 (PP-COHESION-001, invariant E5, follow-up from #1306) in
isolated worktree `/opt/TGW/var/worktrees/1372-alt-text-archive-root` on
branch `todo/1372-alt-text-archive-root`. Task: alt_text.py's
atomic_write_json() calls never pass archive_root=cfg['archive_root'], so
archive-before-overwrite isn't enforced on this file's item-JSON writes —
same bug class as the #1298 cluster, different file. Part of the follow-up
cleanup batch (#1369-1374).
