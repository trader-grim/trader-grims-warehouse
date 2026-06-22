# =============================================================================
# Trader Grim's Warehouse — NixOS module (PP-NIXOS-001)
#
# Declares the full runtime: PostgreSQL `state_machine` DB, tgw-http API,
# worker fleet, and the /opt/TGW directory tree.  Imported by flake.nix as
# `nixosModules.tgw`.
#
# Python application deployment — Option B (current, server migration):
#   The NixOS module manages OS services and paths.  The Python app is
#   installed into a venv at cfg.venvPath (/opt/TGW/.venvironments/tgw) the
#   same way it is on MX Linux — `pip install -e .` after cloning the repo.
#   ExecStart binaries come from the venv, not from a Nix-built package.
#
# Option A (future, tgw-test hardening after production cutover):
#   services.tgw.venvPath will be replaced with services.tgw.package pointing
#   at a Nix-built package fetched from GitHub.  The flake.nix packages output
#   (tgwPackage) is the skeleton for that path — it is kept but not wired into
#   this module until Option A is implemented.
#
# Unit naming:
#   Workers are named tgw-worker@<queue>.service (at-sign, not dash).
#   This matches the live MX template-unit form so all tooling — tgw restart-
#   workers, tgwlogs, runbooks, CLAUDE.md — works identically on NixOS.
#   Concrete @-named units (not true systemd templates) are used because the
#   queue→script mapping is not a clean transform and needs the workerScripts
#   table below.  systemctl 'tgw-worker@*' glob still matches them correctly.
#
# Secrets: /opt/TGW/secrets (0700) is created by tmpfiles but NOT populated
#   here — restore from backup before `tgw health` will pass eBay/Discogs checks.
# =============================================================================

self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.tgw;

  # queue name → console-script entry point (pyproject.toml [project.scripts]).
  # Not a clean transform, hence explicit.
  workerScripts = {
    token_refresh      = "tgw-token-worker";
    pm_intake          = "tgw-pm-intake-worker";
    bundle_intake      = "tgw-bundle-intake-worker";
    multi_intake       = "tgw-multi-intake-worker";
    ai_identify        = "tgw-ai-identify-worker";
    catalog_rebuild    = "tgw-catalog-rebuild-worker";
    thumbnail_gen      = "tgw-thumbnail-gen-worker";
    ebay_draft         = "tgw-ebay-draft-worker";
    ebay_upload        = "tgw-ebay-upload-worker";
    ebay_price         = "tgw-ebay-price-worker";
    ebay_price_reducer = "tgw-ebay-price-reducer-worker";
    ebay_stage         = "tgw-ebay-stage-worker";
    ebay_publish       = "tgw-ebay-publish-worker";
    ebay_sync          = "tgw-ebay-sync-worker";
    ebay_legacy_sync   = "tgw-ebay-legacy-sync-worker";
    ebay_sku_migrate   = "tgw-ebay-sku-migrate-worker";
    velocity_stats     = "tgw-velocity-stats-worker";
    plan_render        = "tgw-plan-render-worker";
    echo               = "tgw-echo-worker";
  };

  # Shared hardening + environment for every long-running tgw unit.
  # HOME and runtime dirs live under cfg.dataDir so a tree snapshot is complete.
  commonService = {
    after   = [ "postgresql.service" "network-online.target" ];
    requires = [ "postgresql.service" ];
    wants   = [ "network-online.target" ];
    environment = {
      PYTHONUNBUFFERED  = "1";
      HOME              = cfg.dataDir;
      NVM_DIR           = "${cfg.dataDir}/.nvm";
      NPM_CONFIG_PREFIX = "${cfg.dataDir}/.npm";
    };
    serviceConfig = {
      User              = cfg.user;
      Group             = cfg.group;
      WorkingDirectory  = cfg.dataDir;
      Restart           = "on-failure";
      RestartSec        = 5;
      TimeoutStopSec    = 30;
      KillSignal        = "SIGTERM";
    };
  };

  # Unit name uses @ form: tgw-worker@<queue>.service
  # Matches the live MX template-unit naming so all tooling works unchanged.
  mkWorker = queue: script:
    lib.nameValuePair "tgw-worker@${queue}" (lib.recursiveUpdate commonService {
      description = "TGW worker — ${queue}";
      wantedBy    = [ "tgw-workers.target" ];
      serviceConfig.ExecStart = "${cfg.venvPath}/bin/${script}";
    });

  enabledWorkers = lib.filterAttrs (q: _: lib.elem q cfg.workers) workerScripts;
in
{
  options.services.tgw = {
    enable = lib.mkEnableOption "Trader Grim's Warehouse platform";

    venvPath = lib.mkOption {
      type        = lib.types.str;
      default     = "${cfg.dataDir}/.venvironments/tgw";
      description = ''
        Path to the Python venv that contains the TGW application binaries
        (tgw CLI + worker console scripts).  The venv is installed out-of-band
        via pip — `pip install -e /path/to/repo` after cloning.
        Option A (future): this option will be superseded by services.tgw.package
        pointing at a Nix-built package fetched from GitHub.
      '';
    };

    user = lib.mkOption {
      type    = lib.types.str;
      default = "tgw";
      description = "System user the platform runs as.";
    };

    group = lib.mkOption {
      type    = lib.types.str;
      default = "tgw";
      description = "Primary group for the tgw user.";
    };

    uid = lib.mkOption {
      type    = lib.types.int;
      default = 900;
      description = ''
        Numeric uid for the tgw user.  Must match the uid declared in
        nix/tgw/users.nix (900) and the live system uid after the
        PLAN-nixos-migration.md step-0.6 usermod/chown migration.
      '';
    };

    dataDir = lib.mkOption {
      type    = lib.types.path;
      default = "/opt/TGW";
      description = "Root of the TGW tree.  The app hardcodes /opt/TGW; do not change without source edits.";
    };

    httpHost = lib.mkOption {
      type    = lib.types.str;
      default = "127.0.0.1";
      description = "Bind host for tgw-http.";
    };

    httpPort = lib.mkOption {
      type    = lib.types.port;
      default = 7373;
      description = "Bind port for tgw-http.";
    };

    workers = lib.mkOption {
      type    = lib.types.listOf lib.types.str;
      default = lib.attrNames workerScripts;
      description = "Which worker queues to run as systemd services.";
    };

    enableHttp = lib.mkOption {
      type    = lib.types.bool;
      default = true;
      description = "Run the tgw-http API service.";
    };

    enableBackup = lib.mkOption {
      type    = lib.types.bool;
      default = false;
      description = ''
        Run the trader-grims-backup watcher.  Off by default — requires
        config/trader-grims-backup.yaml + rclone remotes restored first.
        PP-BACKUP-001 owns this unit; it is not built into the TGW venv.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = lib.all (q: workerScripts ? ${q}) cfg.workers;
      message   = "services.tgw.workers contains an unknown queue name (not in workerScripts).";
    }];

    # PostgreSQL work ledger.
    # ensureDBOwnership requires db name == username in NixOS 25.05+, so we
    # grant ownership via a oneshot instead (see tgw-db-init below).
    services.postgresql = {
      enable      = true;
      package     = pkgs.postgresql_17;
      ensureUsers = [{ name = cfg.user; }];
    };

    # /opt/TGW directory tree.  Secrets subdir is 0700; contents restored out-of-band.
    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir}                  0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/config           0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/secrets          0700 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data             0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data/ItemData    0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/data/ItemCatalog 0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/var              0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/var/log          0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/incoming         0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/.nvm             0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/.npm             0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/.venvironments   0750 ${cfg.user} ${cfg.group} -"
      "d ${cfg.dataDir}/src              0750 ${cfg.user} ${cfg.group} -"
    ];

    systemd.services = lib.mkMerge [

      # DB init — create state_machine DB, assign ownership, apply schema.
      #
      # Three cases handled:
      #   1. Fresh install:  DB doesn't exist → create it + apply schema SQL.
      #   2. Restore (pg_restore already ran): DB + tables exist → skip schema.
      #   3. PostgreSQL in WAL recovery (replica / restoring): exit 0, do nothing.
      #
      # Schema SQL files are idempotent (CREATE IF NOT EXISTS / DO $$...IF NOT EXISTS$$)
      # so running them on an already-populated DB is always safe.
      {
        tgw-db-init = {
          description = "Initialize TGW state_machine database and schema";
          after       = [ "postgresql.service" ];
          requires    = [ "postgresql.service" ];
          wantedBy    = [ "multi-user.target" ];
          serviceConfig = {
            Type            = "oneshot";
            RemainAfterExit = true;
            User            = "postgres";
          };
          script = ''
            psql=${config.services.postgresql.package}/bin/psql

            # WAL-recovery guard: don't touch a standby/recovering instance.
            if $psql -tAc "SELECT pg_is_in_recovery()" | grep -q t; then
              echo "tgw-db-init: PostgreSQL is in recovery — skipping init"
              exit 0
            fi

            # Create the database if it doesn't exist.
            $psql -tAc "SELECT 1 FROM pg_database WHERE datname='state_machine'" \
              | grep -q 1 \
              || $psql -c "CREATE DATABASE state_machine OWNER \"${cfg.user}\";"
            $psql -c "ALTER DATABASE state_machine OWNER TO \"${cfg.user}\";"

            # Apply schema SQL files.  Each is idempotent — safe to run on a
            # DB already populated by pg_restore (tables already exist → no-op).
            tgw_psql() { $psql -v ON_ERROR_STOP=1 -d state_machine "$@"; }
            tgw_psql -f ${self}/src/tgw/queue/schema.sql
            tgw_psql -f ${self}/src/tgw/queue/sku_history.sql
            tgw_psql -f ${self}/src/tgw/queue/image_hashes.sql
            echo "tgw-db-init: schema ready"
          '';
        };
      }

      # Worker fleet — one tgw-worker@<queue>.service per enabled queue.
      (lib.mapAttrs' mkWorker enabledWorkers)

      # tgw-http API
      (lib.optionalAttrs cfg.enableHttp {
        "tgw-http" = lib.recursiveUpdate commonService {
          description = "TGW HTTP API (port ${toString cfg.httpPort})";
          wantedBy    = [ "multi-user.target" ];
          serviceConfig.ExecStart =
            "${cfg.venvPath}/bin/tgw serve --host ${cfg.httpHost} --port ${toString cfg.httpPort}";
        };
      })

      # Backup watcher (opt-in; PP-BACKUP-001 owns the binary)
      (lib.optionalAttrs cfg.enableBackup {
        "trader-grims-backup" = lib.recursiveUpdate commonService {
          description = "Trader Grims Backup Watcher";
          wantedBy    = [ "multi-user.target" ];
          serviceConfig.ExecStart =
            "${cfg.venvPath}/bin/trader-grims-backup ${cfg.dataDir}/config/trader-grims-backup.yaml"
            + " --log-dir ${cfg.dataDir}/var/log/trader_grims_backup";
        };
      })

    ];

    # Target that groups the worker fleet — mirrors queue-workers.target on MX.
    systemd.targets."tgw-workers" = {
      description = "TGW worker fleet";
      wantedBy    = [ "multi-user.target" ];
    };
  };
}
