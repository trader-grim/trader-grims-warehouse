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
{ ... }:
{
  # tgw_widgets.py imports httpx (HTTP API queries) and psycopg2 (queue stats)
  services.xserver.windowManager.qtile.extraPackages =
    python3Packages: with python3Packages; [ httpx psycopg2 ];

  # Qtile config — lives in nix/qtile/ so it travels with the flake.
  # Paths are relative to this file (nix/tgw/desktop.nix → nix/qtile/).
  environment.etc."qtile/config.py".source      = ../qtile/config.py;
  environment.etc."qtile/tgw_widgets.py".source = ../qtile/tgw_widgets.py;

  # Symlink /etc/qtile/* into db's config dir so Qtile finds them.
  systemd.tmpfiles.rules = [
    "d  /home/db/.config/qtile                       0755 db users -"
    "L+ /home/db/.config/qtile/config.py       - - - - /etc/qtile/config.py"
    "L+ /home/db/.config/qtile/tgw_widgets.py  - - - - /etc/qtile/tgw_widgets.py"
  ];
}
