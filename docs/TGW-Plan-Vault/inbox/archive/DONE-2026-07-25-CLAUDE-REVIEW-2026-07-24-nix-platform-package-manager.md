# REVIEW — PP-NIXOS-001: Nix as TGW platform package manager

**From:** Dave decision recorded by Tigwa, 2026-07-24  
**Scope:** independent boundary review only; no flake, host, service, credential, or data mutation is requested.

## Decision now recorded in the canonical Master Plan

TGW will retain Nix as the package/build manager for a stable TGW platform facility set, rather than retain NixOS as the host operating system. A conventional Linux host will own OS/desktop/network/users/disks and mutable runtime state. The settled `tgw-flake`/lock will produce pinned TGW packages/toolchain, including the state-machine and stable library facilities. Existing host service management launches built artifacts. Mutable data, databases, logs, and secrets remain outside `/nix/store`; deployment must retain a GC root/release reference. Iterated-on tools remain outside the flake. This does not choose a final distro, authorize a flake change, or authorize a cutover.

Canonical anchor: `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md`, PP-NIXOS-001, decision inserted 2026-07-24; post-write SHA-256 `e7aedb8e17ab7e27bb41a8939c77e3e65d6e7a2071d76ad57a7073dd66cab2e6`.

## Request

Review the decision as a deployment/recovery boundary. Return only concrete gaps or corrections, especially:

1. Is the flake/package-output versus host-service boundary sufficiently clear for the library deployment?
2. What is the smallest reliable GC-root/release handoff mechanism that does not reintroduce NixOS coupling?
3. Which facilities must remain explicitly host-owned and mutable?
4. What minimum build/install/rollback/restore evidence should a later bounded implementation packet require?

Do not draft or alter the flake, select a distribution, migrate a host, create accounts, or change any service. A review response is not implementation authorization.
