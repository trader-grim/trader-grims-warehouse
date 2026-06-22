# Production host hardware configuration
# Replace with output of: nixos-generate-config --show-hardware-config
# on the target machine, then commit.
{ lib, ... }:
{
  # Placeholder — prevents flake eval failure before hardware is known.
  # Fill in: fileSystems, swapDevices, boot.initrd.availableKernelModules,
  # hardware.cpu.*, nixpkgs.hostPlatform at cutover.
  # Disko will manage fileSystems when the production disko config is added.
  fileSystems."/" = lib.mkDefault {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };
}
