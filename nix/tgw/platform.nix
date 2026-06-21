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
{ config, pkgs, ... }:
let
  # Derive all Syncthing folder paths from the declared Syncthing user so that
  # changing the operator username in nix/os/users.nix propagates everywhere
  # automatically — no /home/<hardcoded-name> anywhere in this file.
  syncUser = config.services.syncthing.user;
  syncHome = config.users.users.${syncUser}.home;
in
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
  # Syncthing folders — the syncthing daemon is enabled in nix/os/base.nix.
  # Paths derived from syncHome above; devices populated at runtime after pairing.
  # ---------------------------------------------------------------------------

  # tgw-flake — the NixOS flake repo; used by tgw-rebuild on every host
  services.syncthing.settings.folders."tgw-flake" = {
    path    = "${syncHome}/tgw-flake";
    devices = [];
  };

  # tgw-install-bundle — install/recovery kit received from production.
  # On production, nix/tgw/usb-sync.nix owns the authoritative send path.
  services.syncthing.settings.folders."tgw-install-bundle" = {
    path    = "${syncHome}/tgw-install-bundle";
    devices = [];
  };

  # tgw-rebuild — apply the synced flake to this host.
  # path: forces Nix to evaluate raw filesystem state rather than git HEAD —
  # required when the flake arrives via Syncthing outside of any local git repo.
  environment.shellAliases.tgw-rebuild =
    "sudo nixos-rebuild switch --flake path:${syncHome}/tgw-flake#$(hostname)";

  # tgw-rebuild-check — validate without applying (safe to run anytime)
  environment.shellAliases.tgw-rebuild-check =
    "nix flake check path:${syncHome}/tgw-flake";
}
