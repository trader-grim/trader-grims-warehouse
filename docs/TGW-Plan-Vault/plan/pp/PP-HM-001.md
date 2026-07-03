## PP-HM-001 — Home Manager: Declarative User Environments

### Status: Phase 1 DONE (session 38, 2026-06-22) — Phase 2 open

### Why
Fresh NixOS installs create `/home/db/.config` owned by root during system activation.
`systemd.tmpfiles` rules that create user dotfile symlinks (e.g. `~/.config/qtile/config.py`)
fail with "unsafe path transition" errors until the directory is manually chowned.
This is a structural problem: system-level NixOS activation runs as root and can't safely
own user-space configuration.

Home Manager solves this cleanly:
- User config files are managed under the user's own identity from the start
- XDG dirs, dotfiles, shell config, desktop session files — all declarative
- Different user types (operator `db` vs service `tgw`) get different config profiles
- No tmpfiles hacks, no post-install chown, no ordering races
- Upgrades and rollbacks cover user config alongside system config atomically

### Scope
- `db` (operator, uid 1000): Qtile config, zsh/shell config, XDG defaults, desktop tools
- `tgw` (service, uid 900): shell env, maybe tool config — limited scope; workers are
  managed by systemd so most tgw config stays in the NixOS module
- Both managed as `home-manager.users.<name>` blocks in the flake

### Integration approach
NixOS module style (not standalone): add `home-manager` as a flake input and use
`home-manager.nixosModules.home-manager` in each host's module list. This keeps
everything in one `nixos-rebuild switch` / `nixos-anywhere` invocation — no separate
`home-manager switch` step.

```nix
inputs.home-manager.url = "github:nix-community/home-manager/release-25.05";
inputs.home-manager.inputs.nixpkgs.follows = "nixpkgs";
```

### Phase 1 — `db` operator config (unblocks PP-WM-001 Phase 2 + PP-CLIP-001)
- Add `home-manager` input to `flake.nix`, follows nixpkgs 25.05
- New file `nix/home/db.nix`: operator HM config module
  - `home.stateVersion = "25.05"`
  - `home.file.".config/qtile/config.py".source = ../../qtile/config.py` (replaces tmpfiles hack)
  - `home.file.".config/qtile/tgw_widgets.py".source = ../../qtile/tgw_widgets.py`
  - zsh config: aliases, PATH for `/opt/TGW/.venvironments/tgw/bin`, prompt
  - XDG user dirs
- Remove `systemd.tmpfiles.rules` Qtile block from `nix/tgw/desktop.nix`
- Wire `home-manager.nixosModules.home-manager` + `home-manager.users.db` into all hosts
  that import `bases/master.nix` or `bases/portable.nix`
- Validate on `tgw-test`: `nixos-rebuild switch`, log in, confirm `~/.config/qtile/` correct

### Phase 2 — Shell + tool config (operator quality-of-life)
- zsh: completion, history, starship or custom prompt showing `[tgw]` env indicator
- Git config: user.name / user.email for `db`
- SSH config: `~/.ssh/config` entries for `tgw-test`, `tgw-prod`, Tailscale hostnames
- Konsole profile: dark theme, correct font, tgw-http URL in env
- Autostart: `~/.config/qtile/autostart.sh` managed by HM (compositor, notification daemon)
- `xdg.mimeApps` defaults: Dolphin for folders, Gwenview for images, browser for URLs

### Phase 3 — `tgw` service account (if needed)
- Evaluate whether workers benefit from HM-managed dotfiles
- Likely limited to: shell aliases for `tgw` sessions, maybe `.psqlrc` for convenience
- Workers themselves stay as systemd units in `nix/tgw.nix` — HM doesn't touch those

### Promotion criteria (Phase 1 → production)
- tgw-test runs one full session with HM-managed Qtile config, no manual fixups
- `nixos-rebuild switch` from MX pushes HM user config cleanly
- No orphaned tmpfiles rules remaining in `nix/tgw/desktop.nix`

---

