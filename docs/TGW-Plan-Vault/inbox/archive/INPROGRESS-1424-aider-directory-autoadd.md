# INPROGRESS: todo #1424 — aider directory-mention auto-add reflection loop

Working in worktree `/opt/TGW/var/worktrees/1424-aider-directory-autoadd` on
branch `todo/1424-aider-directory-autoadd`.

Root-caused against the installed aider-chat 0.83.1 source (downloaded via
`pip download aider-chat==0.83.1 --no-deps` since the binary isn't reachable
in this sandbox — no live `aider --help` possible here, noted as a pre-flight
deviation in the result manifest): the mention-auto-add scan
(`base_coder.get_file_mentions` / `check_for_file_mentions`) builds its
candidate list from `GitRepo.get_tracked_files()`, which walks the git tree
and adds any **blob**-type entry — git stores symlinks as blobs (mode
120000), so `nix`, `flake.nix`, `flake.lock` (all git-tracked symlinks
pointing at `/home/db/tgw-flake/*`, per todo #1365's finding) are exactly as
"addable" as a real file. When the model's reply mentions the word "nix",
aider auto-adds it under `--yes`; reading it fails (IsADirectoryError, or
PermissionError for the other two since tgw can't traverse `/home/db`), the
edit can't apply, and the retry loop burns the 3-reflection cap.

`GitRepo.get_tracked_files()` already filters via `ignored_file()`, which
consults `.aiderignore` (default, wired automatically at git root, no code
change needed) via `pathspec`. Fix: add `/nix`, `/flake.nix`, `/flake.lock`
to `.aiderignore` — that's option (a), a real existing aider mechanism, no
bridge code change required.
