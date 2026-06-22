# =============================================================================
# tgw-test-rehearsal — dress rehearsal config for tgw-test (iMac12,1)
#
# Switches tgw-test from the client (portable.nix) profile to the full server
# (master.nix) profile so Phase 4 of the NixOS migration plan can be executed:
# real secrets, real pg_dump restore, full worker fleet, tgw-http — minus
# inference (hardware can't run Ollama models) and minus eBay-writing workers.
#
# Usage (from MX):
#   bash scripts/tgw-push-config.sh tgw-test-rehearsal 192.168.60.101
#
# After the rehearsal, push tgw-test back to restore the client profile:
#   bash scripts/tgw-push-config.sh tgw-test 192.168.60.101
#
# eBay-writing workers are listed in the mask list below.  Confirm they are
# masked BEFORE starting any workers (R7 safety rule — see PLAN-nixos-migration.md).
#
# Restore sequence (Phase 4.1):
#   1. Push this config:  bash scripts/tgw-push-config.sh tgw-test-rehearsal <ip>
#   2. Copy secrets:      sudo rsync -a /media/<vault>/secrets/ /opt/TGW/secrets/
#                         sudo chown -R tgw:tgw /opt/TGW/secrets
#                         sudo chmod -R go-rwx /opt/TGW/secrets
#   3. Copy site config:  sudo rsync -a /media/<vault>/site-config/ /opt/TGW/config/
#   4. Restore DB:        sudo -u tgw pg_restore -d state_machine <dump-file>
#   5. Install app:       sudo -u tgw pip install -e /opt/TGW/src/trader-grims-warehouse
#   6. Health check:      sudo -u tgw tgw health
#   7. Start workers:     sudo systemctl start tgw-workers.target
#   8. Record timings.
# =============================================================================
{ lib, ... }:
{
  imports = [
    ../bases/master.nix
    ../os/desktop.nix
    ../tgw/desktop.nix
    ../hardware/tgw-test-hardware.nix
  ];

  networking.hostName = "tgw-test";

  # iMac12,1: mbpfan + fan control
  services.mbpfan.enable = true;

  # Inference disabled — iMac12,1 is CPU-only, cannot run Ollama models.
  # ai_identify will claim jobs and requeue them cleanly (validates queue path).
  services.ollama.enable = lib.mkForce false;

  # Syncthing disabled — not configured on this host.
  services.syncthing.enable = lib.mkForce false;

  # Disko not needed here — disk is already partitioned from the original install.

  # ── R7 SAFETY: mask all eBay-writing workers ─────────────────────────────
  # These workers must NEVER run on a non-production host while production is live.
  # systemd.services.<name>.wantedBy = [] prevents them starting via the target,
  # but masking is the hard guarantee — systemctl mask survives config switches.
  # Run after first push:
  #   sudo systemctl mask \
  #     tgw-worker@token_refresh.service \
  #     tgw-worker@ebay_upload.service \
  #     tgw-worker@ebay_price.service \
  #     tgw-worker@ebay_stage.service \
  #     tgw-worker@ebay_publish.service \
  #     tgw-worker@ebay_sync.service \
  #     tgw-worker@ebay_legacy_sync.service \
  #     tgw-worker@ebay_sku_migrate.service \
  #     tgw-worker@ebay_dole.service
  # ─────────────────────────────────────────────────────────────────────────

  system.stateVersion = "25.05";
}
