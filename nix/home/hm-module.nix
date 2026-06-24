# =============================================================================
# TGW Home Manager NixOS integration module (PP-HM-001)
#
# Wires home-manager.users.db into the NixOS system by merging two layers:
#   nix/home/db.nix     — CatioNIX operator UX (shell basics, claude wrapper)
#   nix/tgw/home.nix    — TGW additions (tgw wrapper, aliases, venv path)
#
# Import this alongside home-manager.nixosModules.home-manager in flake.nix.
# =============================================================================
{ ... }:
{
  home-manager.useGlobalPkgs        = true;
  home-manager.useUserPackages      = true;
  home-manager.backupFileExtension  = "hm-backup";

  home-manager.users.db = { imports = [ ./db.nix ../tgw/home.nix ]; };
}
