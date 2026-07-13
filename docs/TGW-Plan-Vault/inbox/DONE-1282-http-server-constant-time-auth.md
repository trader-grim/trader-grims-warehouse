Working on todo #1282 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1282-http-server-constant-time-auth` on branch
`todo/1282-http-server-constant-time-auth`. Task: swap the bearer-token
equality check in `_require_auth()` (`src/tgw/http_server.py` ~line 273)
from plain `==` to `secrets.compare_digest()`, matching the password check
40 lines below. In progress: reading current file, then applying fix,
adding/spy test, running offline pytest with PYTHONPATH pinned to this
worktree, then writing result manifest.
