# TGW application/production-flake repository separation — v1 (2026-08-15)

**Operator authority:** Dave
**Status:** active correction; supersedes procedures that infer repository
identity from `main`, `master`, a checkout directory name, or an SSH greeting.

## The two repositories

TGW has two distinct GitHub repositories and two distinct authority domains:

| Repository | GitHub repository | Canonical branch | Semantic identity |
|---|---|---|---|
| Application source | `trader-grim/trader-grims-warehouse` | `main` | `pyproject.toml` and `src/tgw/__init__.py`; must not contain `nix/hosts/tgw-prod.nix` |
| Production NixOS flake | `trader-grim/tgw-flake` | `master` | `flake.nix` and `nix/hosts/tgw-prod.nix`; must not contain `src/tgw/__init__.py` |

Both repositories may contain a `flake.nix`. In the application repository it is
the application build/development adapter. In `tgw-flake` it is the production
NixOS system authority. The filename does not make them one repository.

## Incident correction

On 2026-06-24 a new `tgw-flake` history was initialized from NixOS material then
present in application `main`. On 2026-06-25 application commit
`b858f39c2eb641c29dc53f75900852c015d85e6e` removed that production material and
declared the new repository canonical. The surviving `db` key now authenticates
to GitHub as the deploy key for `trader-grim/tgw-flake`.

The local record does not prove the GitHub settings operation that attached the
key, but repository extraction is the point at which the application credential
path ceased to be valid. Do not describe this as an unexplained key rotation, and
do not treat the resulting flake history or credential as application authority.

## Credential separation

- The existing `db` key with fingerprint
  `SHA256:+q+FqQeKC4N1DnjKGi9LsEHrc6tLomVFNembbaBI5HE` is scoped to
  `trader-grim/tgw-flake`. It is not an application credential.
- The application key with fingerprint
  `SHA256:YXP8QdZ6BIkp11hN/f9wzzdft74oas20GJAPjUdy/m0` is scoped only to
  `trader-grim/trader-grims-warehouse` and is brokered through `tgw-git`.
- Never move or reuse either deploy key between repositories. Rotation creates a
  new key and proves the new repository binding before the old key is removed.

## Mechanical guard

`/usr/local/bin/tgw-source-git` uses a fixed application Git directory and fixed
application GitHub URL. Before publication it validates the application markers
and rejects a tree containing the production host configuration. It accepts no
arbitrary repository, remote, ref, or force option.

Production-flake changes use their own repository, review, key, release procedure,
and receipt. An application release may update a pinned source input in a reviewed
flake candidate; that dependency edge is not permission to mix Git histories.

## Recovery safety

No remote history is deleted or force-pushed during reconstruction. Current refs,
the dirty application checkout, and both repository histories must be preserved
before a later reviewed cutover.
