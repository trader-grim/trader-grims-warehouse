# =============================================================================
# TGW portable base — client / satellite tier (PP-PORTABLE-CATALOG-001)
#
# For hosts that carry the TGW catalog (read-only SQLite satellite + thumbnails)
# and Syncthing for delivery, but do NOT run the full server platform.
# No worker fleet, no tgw-http, no PostgreSQL, no inference, no eBay secrets.
#
# Layer structure:
#   CatioNIX OS:  os/base.nix + os/users.nix
#   TGW platform: tgw/users.nix + tgw/platform.nix
#
# Desktop is opt-in — import os/desktop.nix + tgw/desktop.nix in the host file.
# =============================================================================
{ config, lib, ... }:
{
  imports = [
    ../os/base.nix
    ../os/users.nix
    ../tgw/users.nix
    ../tgw/platform.nix
  ];

  services.tgw.enable     = true;
  services.tgw.workers    = [];
  services.tgw.enableHttp = false;

  boot.loader.systemd-boot.enable      = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking.networkmanager.enable = true;

  assertions = [
    {
      assertion = config.users.users ? tgw && config.users.users.tgw.uid == 900;
      message   = "tgw user must exist at uid 900 (nix/tgw/users.nix) — portable base requires it";
    }
  ];
}
