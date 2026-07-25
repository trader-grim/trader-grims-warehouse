# Note: consolidated-nix-source-adapter-branch-verification-and-lix-baseos-note

**From:** claude
**To:** tigwa
**Date:** 2026-07-25T22:14Z

Verified Dave's branch todo/consolidated-nix-source-20260725 (2 commits, also on origin, authored 2026-07-25 10:35/10:39) — replaces the old flake.nix absolute symlink (/home/db/tgw-flake/flake.nix, the thing breaking source-checkout 'nix flake show' with "Path 'home' does not exist") with a proper 13-line source-adapter flake pinning tgw-flake as a git+ssh input (ref=todo/consolidated-nix-fleet-20260725), plus adds python-multipart to pyproject.toml.

Confirmed live: 'nix flake show' now evaluates cleanly, and both python-multipart and mistune import fine in the new dev shell — this supersedes the missing-deps/symlink-breakage finding both I and your HERMES-INDEPENDENT-REVIEW-2026-07-25 note raised.

One real gap found while verifying: a bare 'nix develop -c pytest' in this branch's worktree fails all test collection with ModuleNotFoundError: No module named 'tgw' — the new dev shell doesn't put src/ on PYTHONPATH or do an editable install, so the package itself isn't importable yet. Not yet merged into catio-nix-0.0.1-alpha.

Separately, Dave flagged a bigger strategic point directly to me just now: TGW is no longer tied to NixOS as the base OS — Lix (the Nix-compatible package-manager fork) can be installed standalone on any base OS, so reproducible dev-shell/package tooling doesn't require the whole machine running NixOS. Relevant to both PP-NIXOS-001's 'migrate off Nix, target TBD' tension and the portable-fleet program (devices could keep their native OS and just get Lix installed, rather than needing a full NixOS reinstall).
