# =============================================================================
# TGW platform additions — OS-level config required by TGW on every host
#
# TGW-specific additions on top of the CatioNIX OS layer (nix/os/base.nix).
# Every TGW host imports this, whether full server or portable client.
#
# Owns:
#   - TGW-specific system packages (media tools, GitHub CLI, GUI automation)
#   - syncthing tgw-install-bundle folder (ISO/recovery kit received from prod)
#
# Flake distribution: configs are pushed FROM MX via nixos-rebuild --target-host
# (scripts/tgw-push-config.sh).  NixOS hosts do NOT receive the flake source via
# Syncthing — the Nix store closure is transferred directly by nixos-rebuild.
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
  # Syncthing folders — daemon enabled in nix/os/base.nix.
  # Paths derived from syncHome above; devices populated at runtime after pairing.
  # ---------------------------------------------------------------------------

  # tgw-install-bundle — ISO and recovery kit received from production.
  # On production, nix/tgw/usb-sync.nix owns the authoritative send path.
  services.syncthing.settings.folders."tgw-install-bundle" = {
    path    = "${syncHome}/tgw-install-bundle";
    devices = [];
  };
}
