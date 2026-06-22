# =============================================================================
# TGW-VAULT USB auto-stamp (production only)
#
# Watches for a btrfs USB partition labelled TGW-VAULT and automatically
# runs tgw-usb-stamp.sh when it is plugged in.  The stamp script copies:
#   secrets/  — /opt/TGW/secrets/
#   dumps/    — pg_dump of state_machine (dated, keeps 2 most recent)
#   flake/    — git bundle of the full repo
#
# Mechanism:
#   udev rule  → fires tgw-usb-stamp.service when LABEL=TGW-VAULT appears
#   systemd    → oneshot service; not started at boot, only by udev
#
# Import only in bases/master.nix (production host only).
# =============================================================================
{ pkgs, ... }:
{
  # Trigger the stamp service when the TGW-VAULT partition is inserted.
  # ExecStartPre sleep gives the kernel time to settle the block device
  # before tgw-usb-stamp.sh tries to mount it.
  systemd.services.tgw-usb-stamp = {
    description = "Stamp TGW secrets and state to TGW-VAULT USB";
    serviceConfig = {
      Type            = "oneshot";
      ExecStartPre    = "${pkgs.coreutils}/bin/sleep 3";
      ExecStart       = "${pkgs.bash}/bin/bash /opt/TGW/src/trader-grims-warehouse/scripts/tgw-usb-stamp.sh";
      User            = "root";
      Restart         = "no";
      StandardOutput  = "journal";
      StandardError   = "journal";
      # Prevent the service from running longer than 5 minutes
      TimeoutStartSec = "300";
    };
    # Not wanted at boot — only started by the udev rule below
    wantedBy = [];
  };

  services.udev.extraRules = ''
    ACTION=="add", SUBSYSTEM=="block", ENV{ID_FS_LABEL}=="TGW-VAULT", \
      TAG+="systemd", ENV{SYSTEMD_WANTS}="tgw-usb-stamp.service"
  '';
}
