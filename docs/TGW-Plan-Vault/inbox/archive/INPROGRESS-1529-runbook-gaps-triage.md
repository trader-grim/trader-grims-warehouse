# INPROGRESS: todo #1529 — PP-RUNBOOK-001 gaps #8-13 triage

Working in isolated worktree `/opt/TGW/var/worktrees/1529-runbook-gaps-triage`
on branch `todo/1529-runbook-gaps-triage`.

Task: triage the 6 remaining non-thermal/non-eBay gaps (#8-13) from
`docs/TGW-Plan-Vault/reports/TIGWA-REPORT-runbook-gaps-20260713.md`,
confirmed untouched by #1380 (see its result manifest's "Out-of-scope
findings filed" section). Each gap gets one of: fixed directly (small),
new todo filed (real build task), or deferred to FUTURE-IDEAS.md.

Gaps in scope:
- #8 restore command syntax inconsistency (Quickstart vs TGW-VAULT-RESTORE)
- #9 snapshot/vault naming ambiguity (TGW-VAULT vs TGW-SNAPSHOT-0 vs archive disks)
- #10 stale pre-NixOS MX restore material unlabeled
- #11 remote-backup instructions conflict with dbukove rclone boundary
- #12 USB restore path incompletely drilled
- #13 recovery-doc weak-evidence-search danger (PP-RECOVERY-001 false conclusion)

Status: starting pre-flight reads of the actual referenced docs before
deciding disposition per gap.
