# Note — Hermes Desktop recovery deferred pending Nix-maintainer verification

**To:** Claude
**From:** Tigwa, relaying Dave
**Date:** 2026-07-22
**Status:** incident evidence / no implementation authorization

## Observed incident

During the Hermes Desktop update, npm failed while rebuilding `node-pty`:

```text
gyp ERR! stack Error: not found: make
```

This left no packaged Desktop artifact, so the Desktop UI could not open.

## Recovery completed

Tigwa rebuilt the packaged Desktop using temporary Nix build inputs (`gnumake`, `gcc`, `pkg-config`). The build completed and restored:

```text
/home/tigwa/.hermes/hermes-agent/apps/desktop/release/linux-unpacked/Hermes
```

The rebuilt package staged its native `node-pty` dependency successfully.

## Remaining runtime finding

A direct launch of the packaged Electron binary on this NixOS host lacks required runtime shared libraries such as GLib/GTK/NSS under the current `nix-ld` library set. Supplying an Electron runtime library path gets beyond the loader failure; the subsequent missing-display error is expected from Tigwa's non-graphical session and is not a package-build failure.

## Dave direction

Defer the durable Nix/runtime decision until Dave verifies the new Nix maintainer. Do not edit the flake, alter `nix-ld`, add a wrapper, change Hermes runtime configuration, restart services, or treat the temporary rebuild environment as a permanent solution.

When authorized, the decision should compare a narrow host-level `nix-ld` library declaration against a Hermes-specific launch wrapper, with an actual graphical-session launch test and rollback evidence.
