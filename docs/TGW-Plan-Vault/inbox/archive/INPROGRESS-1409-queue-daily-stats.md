# INPROGRESS: todo #1409 / PP-QUEUESTATS-001

Working in worktree `/opt/TGW/var/worktrees/1409-queue-daily-stats` on branch
`todo/1409-queue-daily-stats` (base: `catio-nix-0.0.1-alpha`, live-verified).

Task: the `/form/pipeline` webui page's "Done today"/"Failed" columns come from
`queue_status()` which is a lifetime cumulative `queue_jobs` GROUP BY with no date
filter. Building a real date-scoped per-queue daily stats source (view or
parameterized query, America/Los_Angeles day boundary per existing quota.py
convention) and wiring the pipeline page to it. Keeping data granular enough
(per-hour or per-day rows, not a single collapsed number) to support future
anomaly/surge detection later, but NOT building that detection logic now
(explicitly out of scope per the PP).

Next: locate queue_status() and the pipeline page renderer in http_server.py,
verify live against psql that current counts are indeed lifetime not
today-scoped, then design+build the fix.
