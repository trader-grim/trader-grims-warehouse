# In progress: todo #1369 (PP-COHESION-001)

tgw-coder executing on branch `todo/1369-announce-script-run` in worktree
`/opt/TGW/var/worktrees/1369-announce-script-run`.

Task: audit `src/tgw/workers/*.py` and `tools/*.py`/`scripts/*.py` one-off
scripts for missing `announce_script_run()` calls (invariant E9). Adding
the call to genuine one-off scripts; distinguishing from persistent
QueueWorker-based workers which run continuously via systemd (not one-off
invocations, so likely out of scope for E9's intent) — confirming via
systemd unit inventory before deciding.

Status: DONE. See docs/TGW-Plan-Vault/plan/packets/results/1369-RESULT.md
for the full result manifest. 25 files touched (announce_script_run added
to 22 genuine one-off scripts across tools/, scripts/, and two non-
QueueWorker files in src/tgw/workers/), plus a fix for a deeper silent-
no-op logging gap (setup_logging() missing meant the announce call was
inert without it). Full offline suite: 2373 passed. Live evidence
captured in the result manifest (real log entries in /opt/TGW/var/log/).
