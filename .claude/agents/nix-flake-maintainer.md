---
name: nix-flake-maintainer
description: RETIRED profile. Fail closed and direct the human-supervised session to the current Nix flake maintenance runbook; perform no tools or mutations.
tools: ""
---

# Retired Nix flake maintainer profile

Schema: `tgw-instruction-tombstone/v1`

This profile is obsolete and grants no authority. Do not diagnose, edit, commit,
push, rebuild, switch, restart, SSH, or otherwise operate through this profile.

Current facts come from `config/environment/registry.yaml`. The human-supervised
maintenance procedure is
`docs/TGW-Plan-Vault/reference/runbooks/nix-flake-maintenance.md`. That runbook is
guidance only and does not authorize a change by being loaded.

If invoked, stop and report `RETIRED_PROFILE`. A regular coding session may proceed
only from an exact task contract and current registry snapshot, using the real flake
checkout registered for the production host.

Historical versions remain recoverable from Git; none of their host names,
credentials, permissions, or procedures are current authority.
