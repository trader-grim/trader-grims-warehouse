# =============================================================================
# TGW desktop config — Qtile widgets + config files (opt-in)
#
# Import alongside nix/os/desktop.nix on any host with a display.
# This module adds the TGW-specific layer on top of the CatioNIX desktop:
#
#   - Qtile extraPackages: httpx + psycopg2 for tgw_widgets.py (queue status bar)
#   - /etc/qtile/config.py and /etc/qtile/tgw_widgets.py from the flake repo
#   - systemd tmpfiles symlinks into db's ~/.config/qtile
#
# os/desktop.nix enables Qtile without config; this module supplies it.
# =============================================================================
{ config, ... }:
let
  # Derive operator identity from the declared Syncthing user — the same user
  # who runs the desktop session.  Changing services.syncthing.user in
  # nix/os/base.nix propagates here automatically.
  opUser = config.services.syncthing.user;
  opHome = config.users.users.${opUser}.home;
in
{
  # tgw_widgets.py imports httpx (HTTP API queries) and psycopg2 (queue stats)
  services.xserver.windowManager.qtile.extraPackages =
    python3Packages: with python3Packages; [ httpx psycopg2 ];

  # Qtile config — lives in nix/qtile/ so it travels with the flake.
  # Paths are relative to this file (nix/tgw/desktop.nix → nix/qtile/).
  environment.etc."qtile/config.py".source       = ../qtile/config.py;
  environment.etc."qtile/tgw_widgets.py".source  = ../qtile/tgw_widgets.py;
  environment.etc."qtile/cheatsheet.txt".source  = ../qtile/cheatsheet.txt;

  # ~/.config/qtile/ is now managed by Home Manager (nix/home/db.nix).
  # The files above are kept in /etc/qtile/ for reference / emergency use.
}
