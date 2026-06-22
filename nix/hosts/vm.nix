# =============================================================================
# TGW vm — throwaway NixOS VM for full-stack validation (PP-NIXOS-001)
#
# Boot with: nixos-rebuild build-vm --flake .#vm && ./result/bin/run-*-vm
# Inside: systemctl status tgw-http tgw-worker-ai_identify postgresql
#         sudo -u tgw psql state_machine -c '\dt'
#
# Headless (no desktop) — validates the server platform only.
# Users (tgw + db + root) come from tgw/users.nix + os/users.nix via master.
# Secrets are NOT provisioned — restore from backup before tgw health passes.
# =============================================================================
{ lib, ... }:
{
  imports = [
    ../bases/master.nix
    # no os/desktop.nix — headless validation only
  ];

  # Stub filesystem for nix flake check — build-vm uses virtual disks at runtime.
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  networking.hostName = "tgw-vm";

  system.stateVersion = "25.05";

  virtualisation.vmVariant.virtualisation = {
    memorySize = 4096;
    cores      = 4;
  };
}
