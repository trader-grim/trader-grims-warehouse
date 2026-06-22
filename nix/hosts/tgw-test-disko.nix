# =============================================================================
# tgw-test disk layout (iMac12,1 / A1131, 500 GB SATA SSD)
#
# Three partitions on /dev/sda:
#   sda1  512 MiB   vfat (ESP — systemd-boot)
#   sda2  200 GiB   btrfs — NixOS system  (/, /home, /nix subvols)
#   sda3  ~300 GiB  btrfs — TGW data      (/opt/TGW subvol)
#
# /opt/TGW is a btrfs subvolume so the snapshot service can take
# read-only snapshots with `btrfs subvolume snapshot -r /opt/TGW ...`.
#
# Disko manages all fileSystems entries — do NOT duplicate them in
# nix/hardware/tgw-test-hardware.nix.
# =============================================================================
{
  disko.devices.disk.main = {
    type   = "disk";
    device = "/dev/sda";
    content = {
      type = "gpt";
      partitions = {

        ESP = {
          size     = "512M";
          type     = "EF00";
          priority = 1;
          content  = {
            type         = "filesystem";
            format       = "vfat";
            mountpoint   = "/boot";
            mountOptions = [ "fmask=0077" "dmask=0077" ];
          };
        };

        # WARNING: extraArgs = ["-f"] on both Btrfs partitions means disko will
        # silently reformat them if nixos-anywhere is re-run. Only run
        # nixos-anywhere on this machine when a full reinstall is intended.
        nixos = {
          size     = "200G";
          priority = 2;
          content  = {
            type      = "btrfs";
            extraArgs = [ "-f" ];
            subvolumes = {
              "@" = {
                mountpoint   = "/";
                mountOptions = [ "compress=zstd" ];
              };
              "@home" = {
                mountpoint   = "/home";
                mountOptions = [ "compress=zstd" ];
              };
              "@nix" = {
                mountpoint   = "/nix";
                mountOptions = [ "compress=zstd" "noatime" ];
              };
            };
          };
        };

        tgw = {
          size     = "100%";
          priority = 3;
          content  = {
            type      = "btrfs";
            extraArgs = [ "-f" ];
            subvolumes = {
              "@tgw" = {
                mountpoint   = "/opt/TGW";
                mountOptions = [ "compress=zstd" ];
              };
            };
          };
        };

      };
    };
  };
}
