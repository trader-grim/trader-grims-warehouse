{ config, lib, pkgs, ... }:
let
  cfg = config.services.tgw-nix-input-observer-launcher;
  command = "/run/current-system/sw/bin/tgw-nix-input-observer-launcher";
  sudoRule = "codex ALL=(root) NOPASSWD: ${command} \"\"";
  descriptor = ''
    schema=tgw-nix-input-observer-launcher/v2
    uid=${toString cfg.uid}
    gid=${toString cfg.gid}
    python=${cfg.observerRuntime}/bin/python3
    ip=${pkgs.iproute2}/bin/ip
    observer=${cfg.observerScript}
    launcher_sha256=${cfg.launcherSha256}
    python_sha256=${cfg.pythonSha256}
    ip_sha256=${cfg.ipSha256}
    observer_sha256=${cfg.observerSha256}
    sudo_rule_sha256=${builtins.hashString "sha256" sudoRule}
    observer_cgroup=0::/tgw-nix-input-observer.slice
  '';
in {
  options.services.tgw-nix-input-observer-launcher = {
    enable = lib.mkEnableOption "fixed native TGW zero-fetch observer launcher";
    package = lib.mkOption { type = lib.types.package; };
    observerRuntime = lib.mkOption { type = lib.types.package; description = "Pinned Python environment containing only the admitted observer package closure."; };
    observerScript = lib.mkOption { type = lib.types.path; description = "Self-contained immutable observer source artifact."; };
    uid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    gid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    launcherSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    pythonSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    ipSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    observerSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
  };
  config = lib.mkIf cfg.enable {
    environment.etc."tgw/nix-input-observer-launcher.conf" = { text = descriptor; mode = "0400"; user = "root"; group = "root"; };
    environment.systemPackages = [ cfg.package ];
    security.sudo.extraConfig = sudoRule;
    systemd.slices."tgw-nix-input-observer".sliceConfig = {
      Description = "Fixed cgroup for bounded TGW Nix input observation";
      CPUQuota = "100%";
      MemoryMax = "1G";
      TasksMax = 64;
    };
    systemd.services.tgw-nix-input-observer-boundary = {
      description = "Lifecycle anchor for the fixed TGW Nix observer cgroup";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.coreutils}/bin/true";
        RemainAfterExit = true;
        Slice = "tgw-nix-input-observer.slice";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
      };
    };
    # Module disable removes its descriptor and sole empty-argv sudo rule.
    # It creates no profile, scratch directory, socket, or long-running service.
  };
}
