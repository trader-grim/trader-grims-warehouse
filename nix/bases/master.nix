# =============================================================================
# TGW master base — full server platform
#
# Compose this base into any host that runs the full TGW stack:
# workers, tgw-http, PostgreSQL, Ollama inference, keyd macroboard, NFS.
#
# Layer structure:
#   CatioNIX OS:  os/base.nix + os/users.nix
#   TGW platform: tgw/users.nix + tgw/platform.nix
#   Server-only:  inference.nix + keyd.nix + nfs-exports.nix
#
# Desktop is NOT included here — import os/desktop.nix + tgw/desktop.nix
# explicitly in the host file so the GUI layer remains fully opt-in.
#
# GUARD ASSERTIONS: dropping tgw/users.nix from the import chain fails the
# build loudly rather than silently removing the service account at switch time.
# =============================================================================
{ config, lib, ... }:
{
  imports = [
    ../os/base.nix
    ../os/users.nix
    ../tgw/users.nix
    ../tgw/platform.nix
    ../tgw/usb-sync.nix     # Syncthing-based install bundle → USB distribution
    ../inference.nix
    ../keyd.nix
    ../nfs-exports.nix
  ];

  services.tgw.enable = true;

  boot.loader.systemd-boot.enable      = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking.networkmanager.enable = true;

  assertions = [
    {
      assertion = config.users.users ? tgw && config.users.users.tgw.uid == 900;
      message   = ''
        tgw user must exist at uid 900 (nix/tgw/users.nix).
        Re-add tgw/users.nix to the import set, or align uid with
        PLAN-nixos-migration.md step 0.6.
      '';
    }
    {
      assertion = config.users.groups ? tgw && config.users.groups.tgw.gid == 900;
      message   = "tgw group must exist at gid 900 (nix/tgw/users.nix)";
    }
  ];
}
