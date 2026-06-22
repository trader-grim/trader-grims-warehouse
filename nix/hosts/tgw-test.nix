# =============================================================================
# tgw-test — spare iMac12,1 (2011); NixOS familiarisation + flake validation
#
# Role: portable/client-shaped (no workers, no tgw-http), full CatioNIX desktop
#       with TGW Qtile widgets.
# Purpose: prove NixOS config, restore mechanics, and the desktop experience
#          before production cutover.  Not for AI inference (CPU-only hardware).
#
# Hardware: EFI boot via systemd-boot (installed 2026-06-20 from nixos-26.05 ISO).
# =============================================================================
{ lib, ... }:
{
  imports = [
    ../bases/portable.nix          # CatioNIX OS + TGW platform (client-shaped)
    ../os/desktop.nix              # CatioNIX desktop: X11 + Qtile + apps
    ../tgw/desktop.nix             # TGW layer: Qtile config + widgets
    ../hardware/tgw-test-hardware.nix
  ];

  networking.hostName = "tgw-test";

  # iMac12,1: mbpfan reads applesmc sensors for fan speed control
  services.mbpfan.enable = true;

  # Syncthing disabled on tgw-test: not configured, would crash on every boot.
  # Enable and configure when testing Syncthing topology on this host.
  services.syncthing.enable = lib.mkForce false;

  system.stateVersion = "25.05";
}
