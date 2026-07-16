Working todo #1370 (PP-COHESION-001, spun off from #1306/#1298-1300/#1309/#1302/#1303/#1304/#1314 all hitting the same flake) in isolated worktree
`/opt/TGW/var/worktrees/1370-llm-google-direct-test-isolation` on branch
`todo/1370-llm-google-direct-test-isolation`. Task:
test_llm_google_direct.py::test_success_does_not_touch_openrouter reads/
mutates a real shared quota-budget state file instead of an isolated
fixture, so it fails only in full-suite runs (passes alone). Needs
per-test quota-state isolation. Part of the follow-up cleanup batch
(#1369-1374) from the prior PP-COHESION-001 concurrent rounds.
