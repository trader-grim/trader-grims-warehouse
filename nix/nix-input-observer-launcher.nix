{ config, lib, pkgs, ... }:
let
  cfg = config.services.tgw-nix-input-observer-launcher;
  command = "/run/current-system/sw/bin/tgw-nix-input-observer-launcher";
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
    request_sha256=${cfg.requestSha256}
    transport_config_sha256=${cfg.transportConfigSha256}
    observer_cgroup=0::/tgw-nix-input-observer.slice/tgw-nix-input-observer@
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
    requestSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; description = "The sole immutable prepared observer request accepted by this generation."; };
    transportConfigSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; description = "Digest of the reviewed socket/service/slice transport configuration."; };
  };
  config = lib.mkIf cfg.enable {
    environment.etc."tgw/nix-input-observer-launcher.conf" = { text = descriptor; mode = "0400"; user = "root"; group = "root"; };
    environment.systemPackages = [ cfg.package ];
    systemd.slices."tgw-nix-input-observer".sliceConfig = {
      Description = "Fixed cgroup for bounded TGW Nix input observation";
      CPUQuota = "100%";
      MemoryMax = "1G";
      TasksMax = 64;
    };
    systemd.sockets.tgw-nix-input-observer = {
      description = "Closed local TGW Nix observer transport";
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "/run/tgw/nix-input-observer.sock";
        SocketUser = "codex";
        SocketGroup = "codex";
        SocketMode = "0600";
        Accept = true;
        MaxConnections = 1;
        RemoveOnStop = true;
      };
    };
    systemd.services."tgw-nix-input-observer@" = {
      description = "One-shot fixed TGW Nix observer %i";
      serviceConfig = {
        Type = "simple";
        ExecStart = command;
        StandardInput = "socket";
        StandardOutput = "socket";
        StandardError = "journal";
        Slice = "tgw-nix-input-observer.slice";
        User = "root";
        Group = "root";
        RuntimeMaxSec = 180;
        OOMPolicy = "stop";
      };
    };
    # Module disable removes the descriptor, socket, template service, and slice.
    # No profile or scratch state is written by the observer transport.
  };
}
