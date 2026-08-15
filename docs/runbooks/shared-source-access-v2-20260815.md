# TGW shared application source access — v2 (2026-08-15)

**Supersedes:** the earlier shared-source-access v1 procedure.
**Reason:** v1 overstated GitHub registration and did not keep application and
production-flake credentials mechanically distinct.

Use this runbook with
[`repository-separation-v1-20260815.md`](repository-separation-v1-20260815.md).

## Correct credential findings

Each `db` key was retested with `-F /dev/null`, `IdentitiesOnly=yes`, and
`IdentityAgent=none`:

- `/home/db/.ssh/id_ed25519-github` authenticates as the deploy key for
  `trader-grim/tgw-flake` and is denied application-repository writes.
- `/home/db/.ssh/id_ed25519_codex_maintenance_20260814` is not registered with GitHub.
- `/home/db/.ssh/id_ed25519_new` is not registered with GitHub.

The application credential is a separate key under
`/etc/tgw/credentials/github/trader-grims-warehouse/`, owned by `tgw-git`, and
loaded into the private system agent. Harness accounts cannot read the private
key or connect to that agent directly. Members of `tgw-coders` receive only exact
sudo permission for the fixed `tgw-source-git` operations.

## GitHub registration and activation

Register `/etc/ssh/tgw_github_app.pub` as a write-enabled deploy key on
`trader-grim/trader-grims-warehouse` only. Its fingerprint is:

```text
SHA256:YXP8QdZ6BIkp11hN/f9wzzdft74oas20GJAPjUdy/m0
```

Then activate from reviewed application source:

```bash
sudo scripts/install-tgw-source-access activate
sudo -n -u tgw-git /usr/local/bin/tgw-source-git publish-candidate
```

Activation proves remote read and dry-run candidate publication before changing
canonical `origin`. Publication can update only
`refs/heads/repair/application-clean-v1` and performs exact readback. It cannot
update `main`, `production`, or `integrate/full-plan-fb9`; promotion requires a
separate reviewed integration action.

Do not attach this key to `tgw-flake`, and do not move the existing flake deploy
key back to the application repository. Independent keys prevent a rotation or
revocation from silently redirecting repository authority.
