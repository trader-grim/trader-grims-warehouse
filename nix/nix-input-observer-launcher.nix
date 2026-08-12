{ config, lib, pkgs, ... }:
let
  cfg = config.services.tgw-nix-input-observer-launcher;
  command = "${cfg.package}/bin/tgw-nix-input-observer-launcher";
  sudoRule = "codex ALL=(root) NOPASSWD: ${command} \"\"";
  descriptor = builtins.toJSON {
    schema = "tgw-nix-input-observer-launcher/v1";
    uid = cfg.uid;
    gid = cfg.gid;
    launcher = command;
    python = "${pkgs.python313}/bin/python3";
    ip = "${pkgs.iproute2}/bin/ip";
    launcher_sha256 = cfg.launcherSha256;
    python_sha256 = cfg.pythonSha256;
    ip_sha256 = cfg.ipSha256;
    sudo_rule_sha256 = builtins.hashString "sha256" sudoRule;
    observer_cgroup = cfg.observerCgroup;
  };
in {
  options.services.tgw-nix-input-observer-launcher = {
    enable = lib.mkEnableOption "fixed TGW zero-fetch observer launcher";
    package = lib.mkOption { type = lib.types.package; };
    uid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    gid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    launcherSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    pythonSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    ipSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    observerCgroup = lib.mkOption { type = lib.types.strMatching "0::/.+"; description = "Exact pre-observed invocation cgroup; mismatches fail before namespace creation."; };
  };
  config = lib.mkIf cfg.enable {
    environment.etc."tgw/nix-input-observer-launcher.json" = { text = descriptor; mode = "0400"; user = "root"; group = "root"; };
    security.sudo.extraConfig = sudoRule;
    # Disabling the module removes both the descriptor and its one exact sudo rule;
    # it never creates a service, profile generation, or persistent scratch state.
  };
}
