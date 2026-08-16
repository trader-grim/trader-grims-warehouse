{ config, lib, pkgs, ... }:
let
  cfg = config.services.tgw-application-release-bootstrap;
  helper = "/run/current-system/sw/bin/tgw-application-release-helper";
  sudoCommand = "/run/wrappers/bin/sudo -n -- ${helper}";
  authorizedKeyPrefix = ''restrict,command="${sudoCommand}" ssh-ed25519 '';
  strictAuthorizedKey = builtins.match ''restrict,command="/run/wrappers/bin/sudo -n -- /run/current-system/sw/bin/tgw-application-release-helper" ssh-ed25519 [A-Za-z0-9+/]+={0,2}'' cfg.sshAuthorizedPublicKey != null;
in {
  options.services.tgw-application-release-bootstrap = {
    enable = lib.mkEnableOption "one exact W09 dynamic application release transaction";
    package = lib.mkOption { type = lib.types.package; };
    helperConfig = lib.mkOption { type = lib.types.path; description = "Reviewed root-owned W09 helper configuration."; };
    sshAuthorizedPublicKey = lib.mkOption { type = lib.types.str; description = "Dedicated one-command W09 public key."; };
    remoteUser = lib.mkOption { type = lib.types.enum [ "tgw-release-bootstrap" ]; default = "tgw-release-bootstrap"; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = strictAuthorizedKey && lib.hasPrefix authorizedKeyPrefix cfg.sshAuthorizedPublicKey
          && !(lib.hasInfix "\n" cfg.sshAuthorizedPublicKey) && !(lib.hasInfix "\r" cfg.sshAuthorizedPublicKey);
        message = "W09 app release requires one restricted Ed25519 key with the exact no-argv sudo helper command";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    environment.etc."tgw/application-release-helper.json" = {
      source = cfg.helperConfig;
      mode = "0400";
      user = "root";
      group = "root";
    };
    systemd.tmpfiles.rules = [
      "d /opt/TGW/var/lib/application-release 0700 root root -"
      "d /opt/TGW/var/backups/application-release 0700 root root -"
    ];
    users.groups.${cfg.remoteUser} = {};
    users.users.${cfg.remoteUser} = {
      isSystemUser = true;
      group = cfg.remoteUser;
      shell = pkgs.bashInteractive;
      openssh.authorizedKeys.keys = [ cfg.sshAuthorizedPublicKey ];
      openssh.authorizedKeys.keyFiles = [ ];
    };
    security.sudo.extraConfig = ''
      ${cfg.remoteUser} ALL=(root) NOPASSWD: ${helper} ""
    '';
  };
}
