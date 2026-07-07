# DONE — 2026-07-06 session: flake reconfigure, audit#1143 mitigation, router research

Full session, several threads. Tracker (`tgw todo`) is canonical for what's
still open — this note is the recovery breadcrumb, not the source of truth.

## 1. Flake decouple (todo #1227, EXECUTED, DONE)

Hermes' `settings.model` and Aider's nixpkgs pin pulled out of the flake per
a new standing rule (iterated-on tools stay out of Nix — see master plan
PP-NIXOS-001 and memory `feedback-flake-minimal-surface`). `android-tools` +
`pipx` added (settled tools). `nixos-rebuild switch` succeeded; Aider now
pipx-managed (0.86.2). Plan doc: `docs/ai-plans/decouple-hermes-aider-flake.md`.

Hermes' model live-edited to `deepseek-v4-flash` in `config.yaml` (Dave
purchased DeepSeek + Google credits). **`hermes-agent` was deliberately NOT
restarted** — `DEEPSEEK_API_KEY` doesn't exist yet (Dave: "paid but haven't
created the key"). Next session: once the key exists, `echo
"DEEPSEEK_API_KEY=<key>" >> /opt/TGW/secrets/hermes.env` then `systemctl
restart hermes-agent`.

## 2. audit#1143 nix-flake mitigation batch (todos #1216/#1220-#1225, DONE)

All 10 findings reconciled against live state first (all confirmed real),
then fixed: SSH password auth disabled (new ed25519 key generated + verified
*before* the flip); `services.tgw.enablePostgres` option added (portable
tier now genuinely skips Postgres — this fix itself regressed
`tgw/users.nix`'s unconditional postgres-user line, caught by `nix flake
check` before reaching a1131, then fixed); a1131's stray `keyd.nix` import
removed; duplicate `kdeconnectd` unit removed (kept `os/sway.nix`'s, verified
running post-rebuild); backup timer relabeled to match its
confirmed-intentional 30-min cadence (Dave: on purpose, not a bug); stale
disko free-space comment corrected; dead `tgw/desktop.nix` stub deleted +
gid-assertion symmetry added.

**Deliberately NOT applied:** #1219 NFS export (no static IP for the actual
intake device — todo #1228, still open); #1217/#1218 Syncthing GUI auth
(Dave still actively configuring Syncthing peers, explicitly deferred).

**#1231 follow-up, also DONE this session:** a1131's power-management gap —
the "fix" would have imported `IdleAction=suspend`, directly contradicting
a1131's own "never suspend, iMac12,1 bug" note. Rewrote `power-client.nix`
suspend-free instead of blindly importing it; also deduped byte-identical
boot-loader/networkmanager lines into `os/base.nix`. **New todo #1233: a1131
itself still needs a config push to pick up these fixes** (only tgw-prod has
been rebuilt so far; a1131's own rebuild is pending, possibly asleep — wake
via `wakeonlan c8:2a:14:2a:a1:85`).

**New finding, todo #1229, also FIXED this session (no rebuild needed, plain
repo scripts):** keyd-macroboard's `tm`/`tgw-macro` hardcoded
`WAYLAND_DISPLAY=wayland-0` but tgw-prod's live Sway session runs
`wayland-1` — fixed to discover the real socket dynamically. Verified live.

## 3. Todo consolidation (this session)

19 audit#1143 findings merged into 6 todos by shared root cause (not just
same file) — see #1234-#1239. Originals marked `SUPERSEDED` with pointers,
not deleted. One correction caught mid-merge: #1209 looked like it belonged
in the atomic-write cluster but is actually a sequencing bug — left
standalone.

## 4. Router ecosystem research (todo #1232, PROPOSAL only, not started)

D-Link DIR-868L → DD-WRT recommended over OpenWrt (OpenWrt doesn't support
this Broadcom chipset; DD-WRT does, cleanly). Full research + candidate
services (VLAN segmentation, router health in `tgw health`, DNS, WireGuard,
config backup) in `docs/ai-plans/router-dlink-dir868l-ecosystem.md`. DHCP
reservation table captured; cameras/intake device not yet reserved, which is
why #1219/#1228 stay open.

## Still open, tracker-canonical

#1219/#1228 (NFS host-lock, needs intake device IP), #1230 (governance/policy
review — not started, discussion not code), #1232 (router, proposal stage),
#1233 (push fixes to a1131), #1234-#1239 (the 6 consolidated audit fixes),
plus whatever was already open before this session (untouched).

## Next session should

1. Restart `hermes-agent` once `DEEPSEEK_API_KEY` exists.
2. Push the pending flake fixes to a1131 (#1233) when it's awake.
3. Get the intake device's reserved IP to unblock #1219/#1228.
4. Pick up #1234-#1239 as normal execution-track work packets, or #1230's
   governance review if Dave wants a planning session instead.
