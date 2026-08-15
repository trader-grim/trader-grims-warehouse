# TGW shared source access — v1 (2026-08-15)

**Owner:** shared; operator authority: Dave

**Scope:** Git access to `trader-grim/trader-grims-warehouse` for local harness
accounts in `tgw-coders`

## Outcome

All admitted local coding harnesses use:

- the canonical repository at `/opt/TGW/tgw-lib/src/trader-grims-warehouse`;
- Unix group `tgw-coders` for local repository/worktree access;
- SSH host alias `github-tgw-app` with one repository-scoped deploy credential;
- a system SSH agent exposing only its socket to `tgw-coders`;
- a root-owned GitHub host key and SSH policy;
- individual Unix accounts and commit author identities for attribution.

This credential grants Git access only to the application repository. It grants no
Nix, release installation, production shell, eBay, or business-data authority.
Production installation is a separate capability associated with the reserved
`tgw-release` group and governed installer procedures. `tgw-release` remains empty
until an explicit installation-role decision adds members.

## Why this replaces per-user SSH setup

The earlier state mixed:

- a Codex key with no GitHub host entry;
- three `db` keys registered as deploy keys for the different `tgw-flake` repository;
- no GitHub credential for most harness accounts;
- a canonical application repository whose local group permissions were already
  correct but whose remote credential was not shared.

That made source publication depend on which account happened to run Git. The
system alias makes host verification, repository identity, and key selection
consistent for every `tgw-coders` member.

## Prepare once on `tgw-lib`

From an exact reviewed source checkout:

```bash
sudo scripts/install-tgw-source-access prepare
```

The installer:

1. installs GitHub's pinned Ed25519 host key in
   `/etc/ssh/tgw_github_known_hosts`;
2. installs `/etc/ssh/ssh_config.d/10-tgw-github-app.conf`;
3. creates the repository-specific key only if both key files are absent;
4. refuses partial or mismatched credential state;
5. stores the private key as `tgw-git:tgw-git` mode `0600` inside a protected
   directory and installs a separate public copy at `/etc/ssh/tgw_github_app.pub`;
6. installs and starts `tgw-github-agent.service`;
7. exposes only `/run/tgw-github-agent/agent.sock` to `tgw-coders`;
8. prints only the public-key fingerprint and public-key path.

It never overwrites an existing private key. Harness accounts cannot read the
private key; OpenSSH uses the loaded identity through the group-accessible agent
socket.

## One GitHub owner action

In GitHub repository settings for
`trader-grim/trader-grims-warehouse`, add the contents of:

```text
/etc/ssh/tgw_github_app.pub
```

as a deploy key named `tgw-lib tgw-coders`, with **Allow write access** enabled.
Do not add it to `tgw-flake`; that repository has separate credentials and authority.

This is the only GitHub-side registration. Individual harness accounts do not need
their own copied host entries or private keys.

## Activate after GitHub registration

```bash
sudo scripts/install-tgw-source-access activate
```

Activation first proves:

- remote `main` can be read;
- `main`, `production`, and `integrate/full-plan-fb9` pass a dry-run push;
- the canonical local refs exist.

Only then does it change canonical `origin` to:

```text
git@github-tgw-app:trader-grim/trader-grims-warehouse.git
```

The prior remote is not changed if any proof fails.

## Publish and verify the reconciled refs

After activation, publish exact refs without a force option:

```bash
REPOSITORY=/opt/TGW/tgw-lib/src/trader-grims-warehouse
git -c safe.directory="$REPOSITORY" -C "$REPOSITORY" push origin \
  refs/heads/main:refs/heads/main \
  refs/heads/production:refs/heads/production \
  refs/heads/integrate/full-plan-fb9:refs/heads/integrate/full-plan-fb9

git -c safe.directory="$REPOSITORY" -C "$REPOSITORY" ls-remote --heads origin \
  main production integrate/full-plan-fb9
```

Compare every returned commit to the local ref. Do not retire recovery or historical
worktrees until the remediation strategy's preservation and stale-reference checks
also pass.

## Harness preflight

Each coding account should be able to run:

```bash
ssh -G github-tgw-app | grep -E '^(hostname|user|identityfile|hostkeyalias) '
SSH_AUTH_SOCK=/run/tgw-github-agent/agent.sock ssh-add -l
git -c safe.directory=/opt/TGW/tgw-lib/src/trader-grims-warehouse \
  -C /opt/TGW/tgw-lib/src/trader-grims-warehouse ls-remote --heads origin
```

Harnesses retain separate home directories, worktrees, evidence, and commit author
configuration. Sharing the repository-scoped transport credential does not permit
sharing mutable worktrees.

## Rotation and revocation

To rotate:

1. create a new path/version rather than overwriting the admitted private key;
2. register the new public deploy key;
3. update and review the system SSH alias;
4. prove fetch and dry-run push for every canonical ref;
5. activate the new config;
6. remove the old deploy key from GitHub;
7. preserve a receipt containing only public fingerprints and transition evidence.

Never commit private keys, print them in logs, copy them into agent homes, or reuse
the application deploy key for another repository.
