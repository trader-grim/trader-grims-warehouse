# RESEARCH-1143: nix flake subsystem cohesion+correctness audit (FINAL SLICE)

Part of todo #1143 (full-codebase cohesion+correctness audit, staged per-subsystem).
This slice: the canonical NixOS flake at `/home/db/tgw-flake` (not inside the git
repo — the main repo's `nix/` is a stale placeholder per prior session notes),
29 files / 2,355 lines. Sixth and final subsystem after `workers/`, `apis/ebay/`,
`http_server.py`, `queue/state-machine`, `scripts/`.

## Method

Workflow tool, 5 file-groups (bases, hosts+hardware, os, tgw app layer, home+misc),
each candidate finding adversarially verified by 3 independent agents (2-of-3
survival bar). 50 agents, ~1.78M subagent tokens, ~11.7 min wall. One verify vote
hit `StructuredOutput retry cap exceeded` but the other 2 votes for that finding
still resolved it — no retry needed.

## Result: 13/14 confirmed as distinct findings, 1 dropped

(14 raw confirms — two file-groups independently reported the same keyd.nix
import bug from different angles, merged into one todo below.)

**3 security findings:**

| Todo | File:line | Summary |
|------|-----------|---------|
| #1216 | os/base.nix:20 | SSH password auth enabled + hardcoded `initialPassword="tgw"` for db/root + `wheelNeedsPassword=false` — any LAN/Tailscale peer can SSH in with the default password and sudo to root with no further prompt, during the unattended bootstrap window before the operator rotates it |
| #1217 | os/base.nix:78 | Syncthing GUI on `db`'s instance bound `0.0.0.0:8384`, firewall open, zero auth/HTTPS — full unauthenticated access to the sync config driving `nixos-rebuild` and ItemData sync |
| #1218 | tgw/platform.nix:78 | second Syncthing instance (owns the plan-vault docs sync) has the same unauthenticated-GUI gap on port 8385, exposing business planning content |
| #1219 | nfs-exports.nix:24 | the Queue intake NFS export is read-write to the **entire /24 subnet** with `all_squash`, unlike the read-only single-host-locked exports right below it — any LAN device can inject fabricated files straight into the intake pipeline |

**8 correctness/cohesion findings:**

| Todo | File:line | Summary |
|------|-----------|---------|
| #1220 | bases/portable.nix:23 | `services.tgw.enable=true` unconditionally forces PostgreSQL on every portable/client host, contradicting the file's own "no PostgreSQL" header — fails activation on a1131 |
| #1221 | hosts/a1131.nix:16 | imports `keyd.nix` despite it being explicitly documented production-host-only — risk of silently hijacking a1131's keyboard input if a matching USB ID is ever plugged in |
| #1222 | os/sway.nix:108 | `kdeconnectd` systemd unit defined twice (system-level + home-manager) for tgw-prod — home-manager's version silently wins and crash-loops, defeating the documented Wayland-env-var fix |
| #1223 | tgw/backup.nix:39 | snapshot timer named/documented "hourly" but its `OnCalendar` actually fires twice per hour — doubles real I/O load vs. what's planned around |
| #1224 | hosts/tgw-prod-disko.nix:138 | stale comment claims ~292G free LVM space for future microVMs; actual free space is ~0.1G — following the documented provisioning steps in the same file will fail |
| #1225 (batched) | portable.nix:32/:27, a1131.nix:8, tgw/desktop.nix:1 | asymmetric gid assertion between the two base tiers; duplicated bootloader config instead of a shared module; a1131 never imports its documented power-management counterpart module (dead code, no power mgmt applied); a dead stub file kept past its own "delete me" comment |

**Dropped (1, refuted on verify, not filed as a todo):** `tgw-catalog-verify-nightly`
oneshot running as root instead of `tgw:tgw` like its siblings — the adversarial
verify pass found this plausible-but-unconfirmed; noted here for awareness only,
not actioned.

## #1143 — full audit complete

This closes out todo #1143. All 6 subsystems audited:

| Subsystem | Files/lines | Confirmed | Security | Report |
|-----------|-------------|-----------|----------|--------|
| workers/ | 25/6,817 | 17 (1 dropped) | 0 | RESEARCH-1143-workers-audit.md |
| apis/ebay/ | 11/2,219 | 11 (0 dropped) | 1 (#1174) | RESEARCH-1143-apis-ebay-audit.md |
| http_server.py | 1/9,211 | 18 (3 dropped) | 5 (#1184-#1188) | RESEARCH-1143-http-server-audit.md |
| queue/state-machine | 5/1,255 | 4 (0 dropped) | 0 | RESEARCH-1143-queue-audit.md |
| scripts/ | 17/3,533 | 11 (0 dropped) | 0 | RESEARCH-1143-scripts-audit.md |
| nix flake | 29/2,355 | 13 (1 dropped) | 3 (#1216-#1219) | RESEARCH-1143-nix-flake-audit.md (this file) |
| **Total** | **88/25,390** | **74 confirmed, 5 dropped** | **9 security findings** | |

**Security remediation batch (9 findings, first priority per Dave):** #1174
(unsigned eBay webhook forgery), #1184-#1188 (XSS ×3, open-redirect, unsanitized
markdown render on http_server.py), #1216-#1219 (SSH default-password root
escalation, 2× unauthenticated Syncthing GUIs, subnet-wide-writable intake NFS
export).

Everything else is filed as individual todos (#1162-#1225 range, minus the one
dropped/unfiled finding) for opportunistic remediation.
