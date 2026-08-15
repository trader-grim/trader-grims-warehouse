# TGW shared application source access — v3 (2026-08-15)

**Supersedes operational use of:** `shared-source-access-v2-20260815.md`.
The older candidate-publication procedure remains historical evidence.

Application Git access is mediated by the `tgw-git` service account and a dedicated
deploy key registered only to `trader-grim/trader-grims-warehouse`. The production
flake uses a different key and repository. Private key bytes are not readable by
harness accounts.

Members of `tgw-coders` may use only the fixed audited operations:

```bash
sudo -n -u tgw-git /usr/local/bin/tgw-source-git status
sudo -n -u tgw-git /usr/local/bin/tgw-source-git fetch
sudo -n -u tgw-git /usr/local/bin/tgw-source-git dry-run
sudo -n -u tgw-git /usr/local/bin/tgw-source-git publish
```

`publish` can update only `main`, `production`, and
`integrate/full-plan-fb9`, performs fast-forward/lineage checks, and reads every
remote ref back exactly. It cannot push the flake repository, arbitrary refs, tags,
or force updates.

## Per-harness workflow

1. Create a task branch and worktree under
   `/opt/TGW/tgw-lib/actors/<actor>/worktrees/<task>` from an exact source commit.
2. Preserve unrelated dirt in the primary checkout; never reset or repurpose it.
3. Commit the coherent implementation on the task branch.
4. Obtain independent review appropriate to the environment and risk policy. Human
   release approval is the current production policy, but the architecture permits
   separately admitted non-human reviewers for non-production systems.
5. Merge reviewed application work into canonical `main` with its complete parent
   history. Never merge Plan or flake histories into it.
6. Run `tgw-source-git dry-run`, then `tgw-source-git publish`, and verify exact
   remote readback.
7. Build and install an immutable release generation from the reviewed commit.

If the agent socket, key, GitHub readback, lineage, or branch identity differs, stop
without modifying refs. Do not borrow the flake credential or create an alternate
clone as a workaround.
