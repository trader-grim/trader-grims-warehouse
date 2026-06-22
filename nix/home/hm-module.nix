# =============================================================================
# TGW Home Manager NixOS integration module (PP-HM-001)
#
# Wires home-manager.users.db and home-manager.users.tgw into the NixOS system.
# Import this alongside home-manager.nixosModules.home-manager in flake.nix.
#
# User configs live in nix/home/db.nix and nix/home/tgw.nix.
# =============================================================================
{ ... }:
{
  home-manager.useGlobalPkgs   = true;
  home-manager.useUserPackages = true;
  home-manager.backupFileExtension = "hm-backup";

  home-manager.users.db = import ./db.nix;
}
