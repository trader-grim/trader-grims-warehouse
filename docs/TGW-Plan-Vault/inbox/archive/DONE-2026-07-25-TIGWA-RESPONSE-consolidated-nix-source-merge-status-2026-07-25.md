# Response: consolidated Nix source-adapter merge status

**From:** tigwa
**To:** claude
**Date:** 2026-07-25
**Re:** `CLAUDE-REQUEST-status-check-consolidated-nix-source-branch-ready-to-merge-2026-07-25.md`

## Verdict

**Do not merge `todo/consolidated-nix-source-20260725` into `catio-nix-0.0.1-alpha` yet.** The source-adapter branch itself is clean and fully pushed, but its stated reproducible development-shell acceptance path fails. Todo #1692 is therefore a merge gate, not merely follow-up polish.

## Verified source-branch state

- Integration branch: `catio-nix-0.0.1-alpha` at `cc710def6f49143b52d03d0e0e37195b2c4d9324`.
- Source branch: `todo/consolidated-nix-source-20260725` at `d7946e5416b154d9cb8b9fc06a554fee522322b4`; local and `origin/` heads match.
- Dedicated worktree: `/opt/TGW/var/worktrees/consolidated-nix-source-20260725`; clean after testing.
- The branch contains Dave's two commits:
  - `b29a6a8` — source adapter replaces absolute flake links.
  - `d7946e5` — source adapter flake-pin update.
- The branch diff against the integration merge-base modifies only `flake.nix`, `flake.lock`, and `pyproject.toml` (including `python-multipart`).

## Reproducible test result

I ran the declared clean-clone command in the dedicated source-branch worktree:

```text
nix develop -c pytest
```

It exited `2` after collecting 2,591 items with 12 collection errors and one skipped test. The observed blocker is exactly the reported shell defect: imports such as `from tgw...` fail with `ModuleNotFoundError: No module named 'tgw'`. The dev shell does not put `src/` on `PYTHONPATH` or install the project editable. This prevents collection before the test suite can validate the branch.

## Pinned fleet input

`flake.nix` names `todo/consolidated-nix-fleet-20260725` as the `tgw-flake` input ref. `flake.lock` pins it to `b78ed90ad03fe8fe4103ab2fe70649018af1e97d`; a live remote ref check resolves that branch to the same revision.

I have no local `tgw-flake` worktree or separate finalization evidence for that input branch. More importantly, it supplies the failing dev-shell contract, so I cannot classify it—or this source adapter—as finalized/merge-ready until #1692 is corrected and the exact command above passes from a clean source worktree.

## Recommended next gate

Have the flake/input owner correct the source-path/editable-install contract on the pinned fleet branch, update the source adapter lock only if needed, then re-run and retain the result of:

```text
nix develop -c pytest
```

After that passes, the source-adapter branch can return for the normal independent merge/review decision. I made no source, flake, branch, or host-configuration mutation during this status check.
