# Note: 1692-pythonpath-fix-done-push-queued-awaiting-dave

**From:** claude
**To:** tigwa
**Date:** 2026-07-25T23:49Z

Status update on todo #1692 (the src-on-PYTHONPATH merge gate you confirmed): Dave approved the bounded repair, dispatched nix-flake-maintainer per PP-FLAKEGATE-001/E17.

Fixed: ~/tgw-flake branch todo/consolidated-nix-fleet-20260725, commit a692acb (local only, not pushed) — added a shellHook putting src/ on PYTHONPATH, plus rounded out the dev-shell's python3.withPackages list to match tgwPackage's actual deps (httpx, uvloop, httptools, websockets, watchfiles, python-dotenv, pyperclip, xlib were missing).

Acceptance evidence, from the clean todo/consolidated-nix-source-20260725 worktree with an input override to the fixed tgw-flake branch: 'nix develop -c pytest -q' -> 2756 passed, 4 skipped, 2 failed (the pre-existing known-stale C12 allowlist test, unrelated), zero collection errors. 'nix flake check' also passes clean.

Push queued via 'tgw flake request-push', not executed by the agent -- job id 4cd3a02f-529d-465f-9947-62b119222b87, state queued. Dave still needs to run the actual git push and 'tgw flake mark-executed' himself before this is real on origin.

One unrelated thing the maintainer flagged while checking for drift, not touched: a1131's ~/tgw-flake checkout is 1 commit ahead of origin/master (ac10fea, hermaroid group/Xauthority work, todo #1665) -- flagging in case it's relevant to anything you're tracking.

Once Dave pushes, the remaining chain per your original process note is: source-adapter branch (todo/consolidated-nix-source-20260725) gets its lock updated to the new fleet-branch commit, then a fresh independent merge/test review before it can merge into catio-nix-0.0.1-alpha. Not there yet -- this is just the flake-side gate closing.
