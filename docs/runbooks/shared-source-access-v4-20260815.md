# TGW shared application source access — v4 (2026-08-15)

**Supersedes operational use of:** `shared-source-access-v3-20260815.md`.
The earlier files remain unchanged as incident and recovery evidence.

Application Git publication is mediated by the `tgw-release` service account and
the `tgw-release` Unix operator group. The account owns the repository-scoped
private key at
`/var/lib/tgw-release/.ssh/trader-grims-warehouse_deploy_ed25519`; operators and
harness accounts do not read its private bytes. The key is registered only to
`trader-grim/trader-grims-warehouse`, with write permission, and has fingerprint:

```text
SHA256:sQVAyzhrHt57a0dVSCKtraX5Nt81mnM4QgoDB1DzGMU
```

The `tgw-release` Unix group is the local authorization boundary. Membership does
not expose the key: it grants only exact sudo execution of the fixed broker as the
`tgw-release` service account:

```bash
sudo -n -u tgw-release /usr/local/bin/tgw-source-git status
sudo -n -u tgw-release /usr/local/bin/tgw-source-git fetch
sudo -n -u tgw-release /usr/local/bin/tgw-source-git dry-run
sudo -n -u tgw-release /usr/local/bin/tgw-source-git publish
```

Membership in `tgw-coders` alone does not grant publication. Add or remove release
operators through the `tgw-release` group; do not grant direct key access. The
service account is separately a member of `tgw-coders` so the fixed broker can read
the canonical Git object store. Its login shell remains `nologin`.

The broker is pinned to the canonical application Git directory, the application
GitHub repository, and the three admitted application refs (`main`, `production`,
and `integrate/full-plan-fb9`). It verifies lineage, refuses non-fast-forward main
publication, and reads every published ref back. It accepts no repository, remote,
ref, tag, force, or shell argument.

## Credential incident corrected

The temporary `tgw-git` broker installed on 2026-08-15 used a newly generated key
with fingerprint `SHA256:YXP8QdZ6BIkp11hN/f9wzzdft74oas20GJAPjUdy/m0`. That key
was never registered at GitHub. The already-provisioned `tgw-release` key remained
registered and write-enabled throughout. The correction reuses the working key and
does not attach the production-flake deploy key to the application repository.

The obsolete `tgw-git` account and unregistered credential are retained inertly for
forensic recovery. They are not loaded into the running agent and confer no GitHub
authority.

## Per-harness workflow

1. Work in `/opt/TGW/tgw-lib/actors/<actor>/worktrees/<task>` from an exact commit.
2. Commit a coherent change and obtain the review required by the target policy.
3. Merge the reviewed change into canonical application `main` without mixing Plan
   or production-flake history.
4. Run the fixed `status` and `dry-run` operations as `tgw-release`.
5. Run `publish`, verify exact remote readback, then install an immutable release
   generation from that exact commit.

If the agent key fingerprint, source lineage, remote repository, or readback differs,
stop. Never borrow the `tgw-flake` credential or add a direct per-agent GitHub key as
a workaround.
