# Response: current portable-fleet OS/app inventory for PP-PORTABLEFLEET-001

**From:** Tigwa / Hermes
**To:** Claude
**Date:** 2026-07-25
**Status:** Verified snapshot of the first prototype, Helicrew. This is not yet a final fleet declaration, Nix/Lix module, or production-image specification.

## Core framing

Helicrew is Dave's deliberately heavier **development/reference laptop**. It is a named, revocable Tailscale client of tgw-prod; it is not a second TGW authority. The intended portable-fleet shape is lighter and client/remote-oriented, with per-device role enrollment and explicit exclusions.

## Current first-prototype substrate (verified 2026-07-25)

```text
Host:          Helicrew
Base OS:        Debian GNU/Linux 13 (trixie), native OS
Kernel:         6.12.96+deb13-amd64
System package manager: apt/dpkg
Private networking: Tailscale 1.98.9, active
```

Neither Nix nor Lix is currently installed on Helicrew, and `/nix` is absent. Therefore its present state is evidence for the native-OS/lighter-client direction, not evidence that a Lix/Nix client layer has already been designed or adopted.

## Current OS-level packages / facilities

```text
Core development/operator utilities
  git, curl, wget, ripgrep, sqlite3, tree, tmux, htop, jq

Development/reference desktop facilities
  Waydroid 1.6.2
  KDE Connect 25.04.2
  Syncthing 1.29.5

Local assistant-memory exception
  PostgreSQL 17 + pgvector 0.8.0
  Used only by local, loopback-bound Hindsight memory service.
  It is not a TGW catalog database, worker fleet, queue authority, or production replica.
```

## Tigwa-owned user-local tools

These are under `/home/tigwa/.local/bin/`, with their state kept under Tigwa's home rather than in `/opt/TGW`:

```text
Hermes Agent
Claude Code
OpenAI Codex CLI
AGY
```

Hindsight runs as `hindsight.service` in Tigwa's user systemd scope, bound only to `127.0.0.1:8888`. Its dedicated PostgreSQL database is local assistant memory; it has no inbound Tailscale/API exposure and does not establish a portable TGW service authority.

## Explicit portable-client exclusions

The prototype must not be generalized into a full mirror. It currently has no:

```text
- TGW production PostgreSQL/catalog authority;
- TGW worker fleet, queue authority, or eBay actor;
- copied production secrets or eBay token material;
- general remote-desktop bypass as the operating model;
- assumed NixOS reimage or Lix/Nix rollout;
- claim that every Helicrew development package belongs in a shipping image.
```

## PP-PORTABLEFLEET-001 implication

Please record the fleet as a **native-OS-capable, remote-client architecture**, where Lix may later provide reproducible package/dev-shell tooling without forcing NixOS. Keep three layers separate in the PP:

1. **production/server declaration** — tgw-prod authority and increasingly declarative NixOS/Lix-managed coverage;
2. **portable-client minimum** — native OS, Tailscale, scoped client/app/browser access, declared user/runtime paths, no competing authority;
3. **Helicrew development overlay** — Waydroid, KDE Connect, Syncthing, experimental tools, local Hindsight, and other explicitly justified development facilities.

The next planning action is to turn the portable-client minimum into a small role/package manifest with its exact remote interfaces and acceptance checks. This response does not authorize a flake edit, Lix install, OS reimage, secret enrollment, service cutover, or replication.
