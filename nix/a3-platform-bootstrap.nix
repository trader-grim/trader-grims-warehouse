{ config, lib, ... }:
let
  cfg = config.services.tgw-a3-platform-bootstrap;
  wrapper = "${cfg.package}/bin/tgw-nix-observer-render-wrapper";
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
        assertion = lib.hasPrefix "ssh-ed25519 " cfg.sshAuthorizedPublicKey && !(lib.hasInfix "PRIVATE KEY" cfg.sshAuthorizedPublicKey);
        message = "A3 bootstrap accepts one public ed25519 authorized key, never private key material";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    environment.etc = {
      "tgw/nix-observer-render-wrapper.conf" = { source = cfg.wrapperConfig; mode = "0400"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-composition.json" = { source = cfg.composition; mode = "0400"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-prerequisite.json" = { source = cfg.prerequisiteReceipt; mode = "0444"; user = "root"; group = "root"; };
      "tgw/nix-observer-render-attestation.pub" = { source = cfg.attestationPublicKey; mode = "0444"; user = "root"; group = "root"; };
    };
    users.users.${cfg.remoteUser}.openssh.authorizedKeys.keys = [ cfg.sshAuthorizedPublicKey ];
    # sudoers' explicit empty argument string permits this executable with no argv.
    security.sudo.extraConfig = ''
      ${cfg.remoteUser} ALL=(root) NOPASSWD: ${wrapper} ""
    '';
  };
}
