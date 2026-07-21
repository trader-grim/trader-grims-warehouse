# In progress: todo #1615 (PP-DATALEARN-001)

Working in worktree `/opt/TGW/var/worktrees/1615-history-staging` on branch
`todo/1615-history-staging`. Task: stop `alt_text.py` archive-copy writes
from going through the `history` symlink (removable MasterArchive drive);
write to new local `/opt/TGW/data/history-staging/<sku>/` instead. Removing
`_history_root_reachable()` guard and `archive_target_unmounted` finding
branch since staging is always-local. Scope is exactly `src/tgw/alt_text.py`
plus tests referencing the old behavior.
