# =============================================================================
# tgw-prod disk layout
#
# Storage architecture decision (2026-06-22):
#   LVM for: OS base partitions + PostgreSQL data + (future) microVM volumes
#   Btrfs for: /opt/TGW general data (ItemData, catalogs, logs, secrets)
#
# Rationale: Btrfs CoW causes severe write amplification on WAL-heavy PostgreSQL
# workloads. /var/lib/postgresql lives on an XFS LV with noatime + allocsize tuning.
# LVM also provides the raw block devices that microvm.nix requires for microVM
# root disks (passed via microvm.volumes[].type = "block").
#
# Device: set /dev/sda to match actual hardware; confirm with `lsblk` on first boot.
# Sizes: tuned for a ~1 TB drive. Adjust lv_* and Btrfs partition sizes to match
# actual capacity. The LVM partition is intentionally under-allocated to leave free
# PEs for future microVM LVs created with lvcreate.
#
# Wire into flake.nix alongside disko.nixosModules.disko (already done).
# =============================================================================
{
  disko.devices = {

    disk.main = {
      type   = "disk";
      device = "/dev/nvme0n1";   # production NVMe — confirmed 2026-06-23
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

          # LVM PV — OS base + PostgreSQL + microVM headroom
          lvm = {
            size     = "200G";   # nvme0n1 is ~477G; 200G LVM + rest Btrfs fits cleanly
            priority = 2;
            content  = {
              type = "lvm_pv";
              vg   = "vg_tgw";
            };
          };

          # Btrfs — /opt/TGW (ItemData, catalogs, logs, secrets, plan vault)
          #
          # WARNING: extraArgs = ["-f"] runs mkfs.btrfs --force at disko-format
          # time (nixos-anywhere only — NOT on nixos-rebuild switch).
          # If nixos-anywhere is run again after data has been restored, this
          # partition is silently re-formatted and all /opt/TGW data is lost.
          # Only run nixos-anywhere on this host from a bare/freshly-wiped disk
          # or when you intend a full reinstall. After any nixos-anywhere run,
          # data must be restored from backup before starting workers.
          tgw = {
            size     = "100%";
            priority = 3;
            content  = {
              type      = "btrfs";
              extraArgs = [ "-f" ];
              subvolumes = {
                "@tgw" = {
                  mountpoint   = "/opt/TGW";
                  mountOptions = [ "compress=zstd" "noatime" ];
                };
              };
            };
          };

        };
      };
    };

    lvm_vg.vg_tgw = {
      type = "lvm_vg";
      lvs  = {

        # OS root filesystem
        root = {
          size    = "50G";
          content = {
            type       = "filesystem";
            format     = "ext4";
            mountpoint = "/";
          };
        };

        # Operator + tgw service account home directories
        home = {
          size    = "20G";
          content = {
            type       = "filesystem";
            format     = "ext4";
            mountpoint = "/home";
          };
        };

        # Nix store — read-mostly; noatime reduces derivation overhead
        nix = {
          size    = "80G";
          content = {
            type         = "filesystem";
            format       = "ext4";
            mountpoint   = "/nix";
            mountOptions = [ "noatime" ];
          };
        };

        # PostgreSQL state_machine ledger — XFS for DB workload
        # noatime + nodiratime + allocsize=64m tuned for WAL + heap writes
        postgres = {
          size    = "50G";   # ledger is small today; plenty of headroom
          content = {
            type         = "filesystem";
            format       = "xfs";
            mountpoint   = "/var/lib/postgresql";
            mountOptions = [ "noatime" "nodiratime" "allocsize=64m" ];
          };
        };

        # Swap
        swap = {
          size    = "8G";
          content = { type = "swap"; };
        };

        # -----------------------------------------------------------------------
        # microVM volumes (future PP-AIOPS-001 Phase 5)
        # Do NOT format — each VM gets a raw LV passed via microvm.nix:
        #   microvm.volumes = [{ image = "/dev/vg_tgw/lv_microvm_<name>"; type = "block"; }]
        # Create at microVM provisioning time:
        #   lvcreate -n lv_microvm_<name> -L <size>G vg_tgw
        # The 500G LVM partition leaves ~292G of free PEs on a 1 TB drive
        # for microVM volumes after the above LVs are allocated.
        # -----------------------------------------------------------------------

      };
    };

  };
}
