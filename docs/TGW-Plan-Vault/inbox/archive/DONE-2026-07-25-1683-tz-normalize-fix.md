# In progress: todo #1683 (PP-DATAINTEGRITY-001)

Fixing mixed offset-aware/naive datetime comparison TypeError in
`_job_finished_at()` / `_superseded_by_success()` in `src/tgw/http_server.py`,
and checking `_after_baseline()` for the same latent bug. Working in
worktree `/opt/TGW/var/worktrees/1683-tz-normalize-fix` on branch
`todo/1683-tz-normalize-fix`. Adding mixed-direction regression tests to
`tests/test_http_server.py`. Acceptance: pytest on
`tests/test_http_server.py` + `tests/test_ebay_upload_dimension_limit.py`.
