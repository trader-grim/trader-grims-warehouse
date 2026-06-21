# =============================================================================
# TGW platform additions — OS-level config required by TGW on every host
#
# TGW-specific additions on top of the CatioNIX OS layer (nix/os/base.nix).
# Every TGW host imports this, whether full server or portable client.
#
# Owns:
#   - TGW-specific system packages (media tools, GitHub CLI, GUI automation)
#   - syncthing tgw-flake folder (flake repo synced from MX host)
#   - syncthing tgw-install-bundle folder (install/recovery kit; received here)
#   - tgw-rebuild shell alias
#
# USB distribution (production only) lives in nix/tgw/usb-sync.nix.
# NFS server + ports live in nix/nfs-exports.nix (production only).
# =============================================================================
{ pkgs, ... }:
{
  # ---------------------------------------------------------------------------
  # TGW system packages — tools used by TGW workers or the operator for TGW ops
  # NOT in CatioNIX base because they are TGW-specific, not platform-generic
  # ---------------------------------------------------------------------------
  environment.systemPackages = with pkgs; [
    ffmpeg          # thumbnail_gen, media processing workers
    imagemagick     # photo normalization, resize
    exiftool        # EXIF extraction for item photos
    chafa           # terminal image preview (tgw inspect)
    gh              # GitHub CLI for flake/config repo management
  ];

  # ydotool — GUI automation (keyd macroboard actions, intake workflows)
  programs.ydotool.enable = true;
  # ---------------------------------------------------------------------------
  # Syncthing folders — the syncthing daemon is enabled in nix/os/base.nix
  # (runs as db).  Devices are populated at runtime via the Syncthing UI after
  # device pairing; the folder paths are pre-declared here.
  # ---------------------------------------------------------------------------

  # tgw-flake — the NixOS flake repo; used by tgw-rebuild on every host
  services.syncthing.settings.folders."tgw-flake" = {
    path    = "/home/db/tgw-flake";
    devices = [];
  };

  # tgw-install-bundle — the install/recovery kit (encrypted secrets bundle,
  # installer script, DR instructions).  This is the RECEIVE path on all
  # non-production hosts.  On production, nix/tgw/usb-sync.nix configures
  # the authoritative USB-backed path and sends to this folder on other hosts.
  services.syncthing.settings.folders."tgw-install-bundle" = {
    path    = "/home/db/tgw-install-bundle";
    devices = [];
  };

  # tgw-rebuild — apply the synced flake to this host
  environment.shellAliases.tgw-rebuild =
    "sudo nixos-rebuild switch --flake /home/db/tgw-flake#$(hostname)";
}
