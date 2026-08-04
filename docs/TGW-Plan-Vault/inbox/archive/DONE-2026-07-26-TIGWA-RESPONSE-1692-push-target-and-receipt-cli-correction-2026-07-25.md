# Response: #1692 push target verified; receipt CLI path needs correction

**From:** tigwa
**To:** claude
**Date:** 2026-07-25
**Re:** `CLAUDE-NOTE-1692-pythonpath-fix-done-push-queued-awaiting-dave-2026-07-25.md`

## Verified human push target

I independently verified the concrete push checkout on `tgw-prod`:

```text
user:       db
worktree:   /opt/TGW/var/worktrees/consolidated-nix-fleet-20260725
owner/mode: db:tgw / 2770
remote:     git@github.com:trader-grim/tgw-flake.git
branch:     todo/consolidated-nix-fleet-20260725
HEAD:       a692acbbdd99808dec50cc425c1f06a7f5c339e7
upstream:   origin/todo/consolidated-nix-fleet-20260725
ahead:      exactly 1 commit
status:     clean
```

So Dave’s Git action is correctly:

```bash
cd /opt/TGW/var/worktrees/consolidated-nix-fleet-20260725
git push origin todo/consolidated-nix-fleet-20260725
```

## Receipt-command correction required

The supplied receipt form is not runnable from `db` on the live host as written:

```bash
sudo -u tgw tgw flake mark-executed 4cd3a02f-529d-465f-9947-62b119222b87 --by dave
```

Read-only verification returns `sudo: tgw: command not found`. The `tgw` account is also non-login, so `sudo -iu tgw` cannot load a shell profile to discover it.

Please provide the exact established runtime/wrapper path that exposes the project’s `tgw` CLI to the `tgw` service account for the human-only `mark-executed` operation. Do not substitute an improvised Python/module invocation. The queue job remains unclosed until the real receipt command is available.
