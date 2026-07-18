# Superseding clarification: use a Git-installed Hermes for `tigwa`

**From:** Dave via Tigwa
**Owner:** Claude
**Related:** prior `TIGWA-REQUEST-tgw-prod-tigwa-account-setup-2026-07-17.md`
**Date:** 2026-07-17

Dave clarified the deployment foundation:

- The current flake rebuild is only for creating/provisioning the `tigwa` account and home directory.
- Do **not** use the Nix/flake-provided Hermes package as Tigwa-lite's installation.
- Install Hermes for the dedicated `tigwa` account from a Git checkout, user-owned under that account's home (the standard Git-install layout), with its associated virtual environment/launcher.

## Why
A Git installation makes Hermes updates practical and independently controllable for Tigwa-lite. The production `db` Nix-installed Hermes remains separate and untouched.

## Scope refinement
Within the already-authorized account foundation work, please:

1. Install Hermes as a `tigwa`-owned Git checkout using the upstream-supported Git-install path.
2. Keep all checkout, venv, launcher, config, and future update state owned by `tigwa` under `/home/tigwa`.
3. Record the repository URL, checked-out revision/branch, executable path, and the exact non-root update command/sequence.
4. Verify `sudo -u tigwa` can invoke that Git-installed Hermes and report its version.

Still do **not** create/start the `t-lite` profile or gateway, copy any credentials, migrate Telegram, or alter the existing `db` Hermes installation/services.
