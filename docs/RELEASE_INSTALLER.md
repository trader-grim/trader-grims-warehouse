# Immutable release installer

`tgw-release-install` materializes a verified source archive beneath a TGW
root and atomically selects it with an expected-current compare-and-swap.
It does not build an archive, change configuration, restart services, migrate
the database, or delete old releases.

## Install and select

Build the archive and obtain the Git identities outside this command. Normal
production use is only through the registered W16 procedure runner; the
installer rejects selection without both signed admission and environment
preflight evidence:

```text
tgw-release-install --root /opt/TGW install \
  --archive /path/to/release.tar.gz \
  --generation ppworkflow-<short-commit>-<date> \
  --commit <40-character-commit> \
  --tree <40-character-tree> \
  --archive-sha256 <64-character-sha256> \
  --expected-current <current-generation> \
  --operation-id <unique-operation-id> \
  --admission-receipt <admission.json> \
  --environment-preflight-receipt <preflight.json> \
  --admission-public-key <root-configured-public-key> \
  --environment-public-key <root-configured-public-key> \
  --current-plan-commit <40-character-plan-commit> \
  --current-solution-hash <sha256:...>
```

The installer rejects unsafe archive members, verifies the supplied archive
digest, writes and fsyncs every file and directory, records an exact file
manifest, removes write permission from the release, and verifies it before
selection. Selection succeeds only if `/opt/TGW/current` still names the
exact `--expected-current` generation. A durable prepared operation is
written before the symlink swap and a completion receipt afterward.

The command is idempotent for the same materialized generation and exact
completed operation. Reusing a generation or operation ID with different
identity is rejected.

This syntax documents the installer contract; it is not permission for an
agent or chat session to invoke it directly. `app-release-install/v1` supplies
these exact arguments after a signed, expiring human deployment approval and
writes its own prepared/completed/refusal receipt.

## Verify

```text
tgw-release-install --root /opt/TGW verify <generation>
```

Verification rejects symlinks, writable content, changed files, unexpected
files, and manifest/source identity mismatches.

## Recover an interrupted selection

```text
tgw-release-install --root /opt/TGW recover
```

Recovery only completes a prepared receipt when `current` names its exact
selected generation and that release still verifies against the manifest
bound into the prepared operation. An unexpected selector state fails as
ambiguous and requires operator investigation.

## Roll back selection

```text
tgw-release-install --root /opt/TGW rollback \
  --receipt /opt/TGW/receipts/<deploy-operation-id>.json \
  --expected-current <deployed-generation> \
  --operation-id <unique-rollback-operation-id>
```

Rollback is another verified compare-and-swap selection. It retains both
release directories and all operation/selection receipts. Stop or restart
services, revert configuration, and handle additive database migrations as
separate explicit operational steps appropriate to the release.

## Durable paths

Within the selected `--root`:

- `releases/<generation>/` — immutable extracted source and manifest
- `operations/<operation-id>.json` — prepared/completed selector intent
- `receipts/<operation-id>.json` — completed selector receipt
- `current` — relative symlink to `releases/<generation>`

The implementation takes an exclusive selector lock. Tests and staging can
use an injected temporary root; only an explicit `--root /opt/TGW` targets
production.
