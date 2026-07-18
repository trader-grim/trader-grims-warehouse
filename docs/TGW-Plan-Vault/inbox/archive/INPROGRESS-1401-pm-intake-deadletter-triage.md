# INPROGRESS: todo #1401 (PP-DEADLETTER-001)

Read-only investigation of 3 pm_intake dead-letter jobs with PermissionError.
pm_intake is deprecated/permanently-stopped (Dave's standing direction,
re-stopped 2026-07-12) so this is low urgency triage only: identify the
exact file/path that triggered the PermissionError and determine whether
it's shared/fence-adjacent code (real bug, could affect active workers) or
purely inside pm_intake's own now-abandoned code path (cruft, no fix
needed). No code changes expected per the packet unless a genuine tiny
fence-adjacent bug is found. Working in isolated worktree at
/opt/TGW/var/worktrees/1401-pm-intake-deadletter-triage on branch
todo/1401-pm-intake-deadletter-triage.
