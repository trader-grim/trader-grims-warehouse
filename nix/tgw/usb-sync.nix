# =============================================================================
# TGW install bundle — USB distribution via Syncthing (production only)
#
# Replaces the rsync-based tgw-secrets-usb-sync / tgw-secrets-usb@.service
# approach (PP-BACKUP-001 A3) with Syncthing's markerName mechanism.
#
# How it works:
#   1. USB drives prepared by bin/tgw-secrets-usb-prep carry LABEL=TGW-SECRETS
#      and a sentinel file /.tgw-bundle at their filesystem root.
#   2. NixOS mounts LABEL=TGW-SECRETS at /media/db/TGW-SECRETS via an
#      x-systemd.automount fileSystems entry (mounts on first access, not boot).
#   3. Syncthing has a "tgw-usb-bundle" folder pointing at that mount path.
#      markerName=".tgw-bundle" tells Syncthing to pause sync when the sentinel
#      is absent (USB unplugged / unmounted) and resume when it appears (USB in).
#   4. The folder is send-only: production is the authoritative source; the USB
#      is a recipient.  Other Syncthing peers (tgw-test etc.) receive via the
#      tgw-install-bundle folder declared in nix/tgw/platform.nix.
#
# Distribution chain:
#   production (USB plugged in)
#     → USB drives (via Syncthing usb-bundle, markerName-gated)
#     → other TGW machines (via Syncthing device pairing, tgw-install-bundle)
#     → GitHub (git push of flake repo)
#     → GDrive (rclone, existing backup job)
#
# Import only in bases/master.nix.
# =============================================================================
{ ... }:
{
  # ---------------------------------------------------------------------------
  # Auto-mount: LABEL=TGW-SECRETS USBs at a consistent path
  #
  # x-systemd.automount: mount is triggered on first access, not at boot.
  # nofail: missing USB does not block boot or cause errors.
  # noatime: reduce unnecessary writes to the USB.
  # Syncthing running as db needs write access — see bin/tgw-secrets-usb-prep
  # which chowns the USB filesystem root to db:users.
  # ---------------------------------------------------------------------------
  fileSystems."/media/db/TGW-SECRETS" = {
    device  = "LABEL=TGW-SECRETS";
    fsType  = "ext4";
    options = [
      "noauto"
      "nofail"
      "noatime"
      "x-systemd.automount"
      "x-systemd.idle-timeout=0"   # don't auto-unmount while Syncthing is active
    ];
  };

  # ---------------------------------------------------------------------------
  # Syncthing USB folder
  #
  # Points at the USB mount path.  markerName gates sync on USB presence.
  # type=sendonly: production pushes to USBs; USB changes are never pulled back.
  # devices: populated at runtime after pairing (same peers as tgw-install-bundle).
  # ---------------------------------------------------------------------------
  services.syncthing.settings.folders."tgw-usb-bundle" = {
    path       = "/media/db/TGW-SECRETS";
    markerName = ".tgw-bundle";
    type       = "sendonly";
    devices    = [];
  };
}
