{ config, lib, ... }:
let
  cfg = config.services.tgw-a3-platform-bootstrap;
  wrapper = "/run/current-system/sw/bin/tgw-nix-observer-render-wrapper";
  sudoCommand = "/run/wrappers/bin/sudo -n -- ${wrapper}";
  authorizedKeyPrefix = ''restrict,command="${sudoCommand}" ssh-ed25519 '';
  strictAuthorizedKey = builtins.match ''restrict,command="/run/wrappers/bin/sudo -n -- /run/current-system/sw/bin/tgw-nix-observer-render-wrapper" ssh-ed25519 [A-Za-z0-9+/]+={0,2}'' cfg.sshAuthorizedPublicKey != null;
  remoteAuthorizedKeys = config.users.users.${cfg.remoteUser}.openssh.authorizedKeys;
  soleAuthorizedKeysFile = "/etc/ssh/authorized_keys.d/%u";
in {
  options.services.tgw-a3-platform-bootstrap = {
    enable = lib.mkEnableOption "one exact TGW A3 W09 platform bootstrap generation";
    package = lib.mkOption { type = lib.types.package; };
    wrapperConfig = lib.mkOption { type = lib.types.path; description = "Reviewed public root-owned wrapper configuration."; };
    composition = lib.mkOption { type = lib.types.path; description = "Exact reviewed composition descriptor."; };
    prerequisiteReceipt = lib.mkOption { type = lib.types.path; description = "Exact public wrapper prerequisite receipt."; };
    attestationPublicKey = lib.mkOption { type = lib.types.path; description = "Public verifier key only; private material is external."; };
    sshAuthorizedPublicKey = lib.mkOption { type = lib.types.str; description = "One exact public SSH key for the remote account."; };
    remoteUser = lib.mkOption { type = lib.types.enum [ "codex" ]; default = "codex"; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = strictAuthorizedKey && lib.hasPrefix authorizedKeyPrefix cfg.sshAuthorizedPublicKey && !(lib.hasInfix "\n" cfg.sshAuthorizedPublicKey) && !(lib.hasInfix "\r" cfg.sshAuthorizedPublicKey);
        message = "A3 bootstrap requires one single-line Ed25519 key with restrict and the exact forced no-argv sudo command";
      }
      {
        assertion = remoteAuthorizedKeys.keys == [ cfg.sshAuthorizedPublicKey ] && remoteAuthorizedKeys.keyFiles == [ ];
        message = "A3 bootstrap remote account authorization must remain exactly one key with no merged keyFiles";
      }
      {
        assertion = config.services.openssh.authorizedKeysFiles == [ soleAuthorizedKeysFile ]
          && config.services.openssh.settings.AuthorizedKeysCommand == "none"
          && config.services.openssh.settings.TrustedUserCAKeys == "none"
          && config.services.openssh.settings.AuthorizedPrincipalsCommand == "none"
          && config.services.openssh.settings.AuthorizedPrincipalsFile == "none"
          && config.services.openssh.settings.AuthenticationMethods == "publickey"
          && config.services.openssh.settings.PasswordAuthentication == false
          && config.services.openssh.settings.KbdInteractiveAuthentication == false;
        message = "A3 bootstrap remote account must have no alternate authorized-key source";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    environment.etc = {
      "tgw/nix-observer-render-wrapper.conf" = { source = cfg.wrapperConfig; mode = "0400"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-composition.json" = { source = cfg.composition; mode = "0400"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-prerequisite.json" = { source = cfg.prerequisiteReceipt; mode = "0444"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-attestation.pub" = { source = cfg.attestationPublicKey; mode = "0444"; user = "root"; group = "root"; };
    };
    # The final assertions make attempted keys/keyFiles merges a configuration
    # failure instead of silently broadening this account.
    users.users.${cfg.remoteUser}.openssh.authorizedKeys = {
      keys = [ cfg.sshAuthorizedPublicKey ];
      keyFiles = [ ];
    };
    services.openssh.authorizedKeysFiles = [ soleAuthorizedKeysFile ];
    services.openssh.settings = {
      AuthorizedKeysCommand = "none";
      TrustedUserCAKeys = "none";
      AuthorizedPrincipalsCommand = "none";
      AuthorizedPrincipalsFile = "none";
      AuthenticationMethods = "publickey";
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
    };
    # Canonical command identity: forced SSH command invokes this exact sudo
    # command; sudoers permits the stable wrapper path with an empty argv only.
    security.sudo.extraConfig = ''
      ${cfg.remoteUser} ALL=(root) NOPASSWD: ${wrapper} ""
    '';
  };
}
