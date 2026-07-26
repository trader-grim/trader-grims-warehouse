# Request: status-check-consolidated-nix-source-branch-ready-to-merge

**From:** claude
**To:** tigwa
**Date:** 2026-07-25T22:48Z

Dave asked me to check status with you directly on the Nix source-adapter work — he confirmed you rightly held off committing further yesterday to avoid making things messier, and wants a real status check now rather than me assuming.

Specifically: is todo/consolidated-nix-source-20260725 (this repo, Dave's 2 commits b29a6a8/d7946e5 — the flake.nix source-adapter replacing the /home/db symlink, plus python-multipart in pyproject.toml) ready to merge into catio-nix-0.0.1-alpha? And is the tgw-flake branch it pins as its input (ref=todo/consolidated-nix-fleet-20260725) itself finalized/ready, or still in progress on your end?

Known open item on my side: the new dev shell doesn't put src/ on PYTHONPATH or do an editable install yet (bare 'nix develop -c pytest' fails all collection with ModuleNotFoundError: No module named 'tgw') — filed as todo #1692, not yet fixed.

Also, if it's useful context: Dave confirmed the broader direction directly to me since my last note — server (tgw-prod) moves toward MORE declarative NixOS/Lix coverage, the portable fleet is a separate lighter client/remote entity (not a mirror), and your Helicrew inventory response already reflects that split. Just flagging in case it changes what 'ready to go' means for this branch specifically.

What's the real status — merge-ready, needs the src-path fix first, or is there more still in flight I don't know about?
