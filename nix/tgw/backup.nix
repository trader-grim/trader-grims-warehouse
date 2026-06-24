# =============================================================================
# TGW backup timers — declarative systemd units (PP-BACKUP-001)
#
# Declares tgw-snapshot, tgw-db-backup, tgw-cloud-sync, tgw-secrets-backup
# as NixOS service+timer pairs.  nixos-rebuild switch installs and enables
# them — no manual cp or daemon-reload required.
#
# Prerequisites (operator-gated, not enforced here):
#   tgw-snapshot     — /home/snapshot/TGW-SNAPSHOT-0 btrfs subvolume mounted
#   tgw-cloud-sync   — rclone configured with tgw-gdrive remote
#   tgw-secrets-backup — age key + passphrase set up (PP-BACKUP-001 A3)
#
# Import: nix/bases/master.nix (server hosts only — not portable/satellite).
# =============================================================================
{ ... }:
let
  bin = "/opt/TGW/src/trader-grims-warehouse/bin";
in
{
  # ---------------------------------------------------------------------------
  # A0 — hourly btrfs snapshot of /opt/TGW
  # ---------------------------------------------------------------------------
  systemd.services.tgw-snapshot = {
    description = "TGW /opt/TGW btrfs snapshot (PP-BACKUP-001 A0)";
    unitConfig.RequiresMountsFor = "/home/snapshot/TGW-SNAPSHOT-0";
    serviceConfig = {
      Type       = "oneshot";
      ExecStart  = "${bin}/tgw-snapshot";
    };
  };

  systemd.timers.tgw-snapshot = {
    description = "TGW hourly btrfs snapshot timer (PP-BACKUP-001 A0)";
    timerConfig = {
      OnCalendar         = "hourly";
      RandomizedDelaySec = "5min";
      Persistent         = true;
    };
    wantedBy = [ "timers.target" ];
  };

  # ---------------------------------------------------------------------------
  # A1 — daily PostgreSQL ledger dump
  # ---------------------------------------------------------------------------
  systemd.services.tgw-db-backup = {
    description = "TGW daily PostgreSQL ledger dump (PP-BACKUP-001 A1)";
    after    = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    serviceConfig = {
      Type             = "oneshot";
      User             = "tgw";
      Group            = "tgw";
      WorkingDirectory = "/opt/TGW";
      ExecStart        = "${bin}/tgw-db-backup";
    };
  };

  systemd.timers.tgw-db-backup = {
    description = "TGW daily PostgreSQL dump timer (PP-BACKUP-001 A1)";
    timerConfig = {
      OnCalendar = "*-*-* 03:30:00";
      Persistent = true;
    };
    wantedBy = [ "timers.target" ];
  };

  # ---------------------------------------------------------------------------
  # A2 — daily cloud sync to Google Drive via rclone
  # ---------------------------------------------------------------------------
  systemd.services.tgw-cloud-sync = {
    description = "TGW daily cloud sync to Google Drive (PP-BACKUP-001 A2)";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type             = "oneshot";
      User             = "tgw";
      Group            = "tgw";
      WorkingDirectory = "/opt/TGW";
      ExecStart        = "${bin}/tgw-cloud-sync";
    };
  };

  systemd.timers.tgw-cloud-sync = {
    description = "TGW daily cloud sync timer (PP-BACKUP-001 A2)";
    timerConfig = {
      OnCalendar = "*-*-* 02:30:00";
      Persistent = true;
    };
    wantedBy = [ "timers.target" ];
  };

  # ---------------------------------------------------------------------------
  # A3 — monthly encrypted secrets bundle
  # ---------------------------------------------------------------------------
  systemd.services.tgw-secrets-backup = {
    description = "TGW monthly encrypted secrets bundle (PP-BACKUP-001 A3)";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type             = "oneshot";
      User             = "tgw";
      Group            = "tgw";
      WorkingDirectory = "/opt/TGW";
      ExecStart        = "${bin}/tgw-secrets-backup";
    };
  };

  systemd.timers.tgw-secrets-backup = {
    description = "TGW monthly secrets backup timer (PP-BACKUP-001 A3)";
    timerConfig = {
      OnCalendar = "*-*-01 04:00:00";
      Persistent = true;
    };
    wantedBy = [ "timers.target" ];
  };
}
