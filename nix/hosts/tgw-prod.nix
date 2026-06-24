# =============================================================================
# tgw-prod — production host; full TGW platform + CatioNIX desktop
#
# Full server stack via bases/master.nix:
#   tgw service account (uid/gid 900), PostgreSQL state_machine, complete
#   worker fleet, tgw-http, Ollama inference, keyd macroboard, NFS exports.
#
# Desktop via os/desktop.nix + tgw/desktop.nix:
#   X11, SDDM, Qtile + TGW status widgets, full app suite.
#   Comment out those two imports to go headless without touching anything
#   server-related.
#
# Hostname must be "tgw-prod" for the tgw-rebuild alias to resolve correctly.
# Hardware file: replace nix/hardware/tgw-prod-hardware.nix with output of
#   nixos-generate-config --show-hardware-config on first boot.
# =============================================================================
{ ... }:
{
  imports = [
    ../bases/master.nix            # full server platform
    ../os/desktop.nix              # CatioNIX desktop: X11 + Qtile + apps
    ../os/dev.nix                  # dev tool layer: Node.js, Aider, Claude CLI path
    ../tgw/desktop.nix             # TGW layer: Qtile config + widgets
    ../hardware/tgw-prod-hardware.nix
  ];

  networking.hostName = "tgw-prod";

  # keyd macroboard: tgw service account needs keyd group membership.
  # os/users.nix + tgw/users.nix own the base declarations; this extends tgw's.
  users.users.tgw.extraGroups = [ "keyd" ];

  # ---------------------------------------------------------------------------
  # Production storage — drives physically attached to this host only
  # ---------------------------------------------------------------------------

  # sda7 — btrfs snapshot target for tgw-snapshot.service (PP-BACKUP-001 A0).
  # nofail: if the drive is absent at boot, system comes up normally.
  fileSystems."/home/snapshot/TGW-SNAPSHOT-0" = {
    device  = "/dev/disk/by-label/TGW-SNAPSHOT-0";
    fsType  = "btrfs";
    options = [ "defaults" "nofail" ];
  };

  system.stateVersion = "25.05";
}
