# In progress: todo #1283 clip.py DB permissions + TTL

Working in isolated worktree
`/opt/TGW/var/worktrees/1283-clip-db-permissions-ttl` on branch
`todo/1283-clip-db-permissions-ttl`. Implementing PP-COHESION-001 packet
1283: hardening `src/tgw/clip.py`'s `_connect()` to chmod the db parent
dir `0700` and db file `0600` on every connect (self-healing), plus adding
a 14-day TTL prune (`_RETENTION_DAYS = 14`) alongside the existing
2000-row cap prune in `record_clip()`. Added tests to
`tests/test_clip.py` covering fresh-create perms, self-heal of a
pre-existing 644 file, TTL prune of backdated rows, and row-count
retention still enforced. Full offline suite green (2124 passed, 1
skipped). Next: write result manifest and commit.
