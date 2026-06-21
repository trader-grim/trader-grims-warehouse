# =============================================================================
# Trader Grim's Warehouse — NixOS module (PP-NIXOS-001)
#
# Declares the full runtime: the tgw system user, the PostgreSQL `state_machine`
# database, the tgw-http API service, the worker fleet, and the backup unit.
# Imported by flake.nix as `nixosModules.tgw`.  AUTHORED FOR VM VALIDATION.
#
# Usage in a host config:
#   imports = [ tgw-flake.nixosModules.tgw ];
#   services.tgw.enable = true;
#
# Notes for VM validation (Dave):
#   * Paths are fixed at /opt/TGW because the app hardcodes them (config.py).
#     The module manages that tree with tmpfiles; it does NOT relocate it.
#   * Secrets (/opt/TGW/secrets, mode 0700) are NOT provisioned here — restore
#     them from backup before `tgw health` will pass eBay/Discogs checks.
#   * The live MX host runs workers as `tgw-worker@<queue>.service` template
#     instances; the NixOS-idiomatic equivalent below is one concrete
#     `tgw-worker-<queue>.service` per queue, each running that worker's
#     console-script entry point from pyproject.toml.
# =============================================================================

self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.tgw;

  # queue name  →  console-script entry point (pyproject [project.scripts]).
  # NOT a clean transform (token_refresh → tgw-token-worker), hence explicit.
  workerScripts = {
    token_refresh     = "tgw-token-worker";
    pm_intake         = "tgw-pm-intake-worker";
    bundle_intake     = "tgw-bundle-intake-worker";
    multi_intake      = "tgw-multi-intake-worker";
    ai_identify       = "tgw-ai-identify-worker";
    catalog_rebuild   = "tgw-catalog-rebuild-worker";
    thumbnail_gen     = "tgw-thumbnail-gen-worker";
    ebay_draft        = "tgw-ebay-draft-worker";
    ebay_upload       = "tgw-ebay-upload-worker";
    ebay_price        = "tgw-ebay-price-worker";
    ebay_price_reducer = "tgw-ebay-price-reducer-worker";
    ebay_stage        = "tgw-ebay-stage-worker";
    ebay_publish      = "tgw-ebay-publish-worker";
    ebay_sync         = "tgw-ebay-sync-worker";
    ebay_legacy_sync  = "tgw-ebay-legacy-sync-worker";
    ebay_sku_migrate  = "tgw-ebay-sku-migrate-worker";
    velocity_stats    = "tgw-velocity-stats-worker";
    echo              = "tgw-echo-worker";
  };

  # Shared hardening + environment for every long-running tgw unit.
  commonService = {
    after = [ "postgresql.service" "network-online.target" ];
    requires = [ "postgresql.service" ];
    wants = [ "network-online.target" ];
    # /opt/TGW is the whole entity — keep the runtime self-contained and
    # home-dir-independent so a snapshot/restore of the tree carries everything.
    # HOME, nvm (NVM_DIR), and npm prefix all live under cfg.dataDir, not ~tgw.
    environment = {
      PYTHONUNBUFFERED = "1";
      HOME = cfg.dataDir;
      NVM_DIR = "${cfg.dataDir}/.nvm";
      NPM_CONFIG_PREFIX = "${cfg.dataDir}/.npm";
    };
    serviceConfig = {
      User = cfg.user;
      Group = cfg.group;
      WorkingDirectory = cfg.dataDir;
      Restart = "on-failure";
      RestartSec = 5;
      TimeoutStopSec = 30;
      KillSignal = "SIGTERM";
    };
  };

  mkWorker = queue: script:
    lib.nameValuePair "tgw-worker-${queue}" (lib.recursiveUpdate commonService {
      description = "TGW worker — ${queue}";
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${cfg.package}/bin/${script}";
    });

  enabledWorkers = lib.filterAttrs (queue: _: lib.elem queue cfg.workers) workerScripts;
in
{
  options.services.tgw = {
    enable = lib.mkEnableOption "Trader Grim's Warehouse platform";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.system}.tgw;
      defaultText = lib.literalExpression "tgw flake package";
      description = "The TGW application package (tgw CLI + worker entry points).";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "tgw";
      description = "System user the platform runs as.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "tgw";
      description = "Primary group for the tgw user.";
    };

    uid = lib.mkOption {
      type = lib.types.int;
      default = 900;
      description = ''
        Numeric uid for the tgw user.  Must match the uid declared in
        modules/users.nix (900) and the live system uid after the
        PLAN-nixos-migration.md step-0.6 usermod/chown migration.
        Do not change without updating both files and re-running the
        full-disk ownership audit (tgw-permissions-reset.sh --check).
      '';
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/opt/TGW";
      description = "Root of the TGW tree (app hardcodes /opt/TGW; do not change without source edits).";
    };

    httpHost = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Bind host for tgw-http.";
    };

    httpPort = lib.mkOption {
      type = lib.types.port;
      default = 7373;
      description = "Bind port for tgw-http.";
    };

    workers = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = lib.attrNames workerScripts;
      description = "Which worker queues to run as systemd services.";
    };

    enableHttp = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run the tgw-http API service.";
    };

    enableBackup = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Run the trader-grims-backup watcher.  Off by default — it needs
        config/trader-grims-backup.yaml + rclone remotes restored first.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = lib.all (q: workerScripts ? ${q}) cfg.workers;
      message = "services.tgw.workers contains an unknown queue (not in workerScripts).";
    }];

    # users.users.tgw and users.groups.tgw are declared in modules/users.nix —
    # that file is the single source of truth.  This module only references the
    # user/group by name (cfg.user / cfg.group) for service and tmpfiles ownership.

    # PostgreSQL work ledger.  Local peer auth: services run as `tgw`, so the
    # `tgw` role authenticates without a password.  After=postgresql ordering on
    # every unit avoids the "connect before DB ready" race the analysis flagged.
    #
    # Note: ensureDBOwnership requires the database name to match the username,
    # which conflicts with our `state_machine` name.  Ownership is granted via
    # a postStart hook instead.
    services.postgresql = {
      enable = true;
      ensureUsers = [{ name = cfg.user; }];
    };

    # Own the /opt/TGW tree.  Secrets is 0700 (contents restored out-of-band).
    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir}                 0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/config          0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/secrets         0700 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data            0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data/ItemData   0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data/ItemCatalog 0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/var             0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/var/log         0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/incoming        0750 ${cfg.user} ${cfg.group} -"
      # Home-dir-independent runtime: nvm (NVM_DIR), npm prefix, and the venv all
      # live under /opt/TGW so a snapshot of the tree carries the whole runtime.
      "d ${cfg.dataDir}/.nvm            0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/.npm            0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/.venvironments  0750 ${cfg.user} ${cfg.group} -"
    ];

    environment.systemPackages = [ cfg.package ];

    systemd.services = lib.mkMerge [
      # DB init — create state_machine and assign ownership (ensureDBOwnership
      # requires db name == user name in NixOS 24.11+, so we do it ourselves).
      {
        tgw-db-init = {
          description = "Initialize TGW state_machine database";
          after = [ "postgresql.service" ];
          requires = [ "postgresql.service" ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            User = "postgres";
          };
          script = ''
            psql=${config.services.postgresql.package}/bin/psql
            $psql -tAc "SELECT 1 FROM pg_database WHERE datname='state_machine'" \
              | grep -q 1 \
              || $psql -c "CREATE DATABASE state_machine OWNER \"${cfg.user}\";"
            $psql -c "ALTER DATABASE state_machine OWNER TO \"${cfg.user}\";"
          '';
        };
      }

      # Worker fleet — one service per enabled queue.
      (lib.mapAttrs' mkWorker enabledWorkers)

      # tgw-http API
      (lib.optionalAttrs cfg.enableHttp {
        "tgw-http" = lib.recursiveUpdate commonService {
          description = "TGW HTTP API service (port ${toString cfg.httpPort})";
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart =
            "${cfg.package}/bin/tgw serve --host ${cfg.httpHost} --port ${toString cfg.httpPort}";
        };
      })

      # Backup watcher (opt-in)
      (lib.optionalAttrs cfg.enableBackup {
        "trader-grims-backup" = lib.recursiveUpdate commonService {
          description = "Trader Grims Backup Watcher";
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart =
            "${cfg.package}/bin/trader-grims-backup ${cfg.dataDir}/config/trader-grims-backup.yaml"
            + " --log-dir ${cfg.dataDir}/var/log/trader_grims_backup";
        };
      })
    ];
  };
}
