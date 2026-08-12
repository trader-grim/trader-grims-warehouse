{ config, lib, pkgs, ... }:
let
  cfg = config.services.tgw-nix-input-observer-launcher;
  command = "/run/current-system/sw/bin/tgw-nix-input-observer-launcher";
  sliceConfig = {
    Description = "Fixed cgroup for bounded TGW Nix input observation";
    CPUQuota = "100%";
    MemoryMax = "1G";
    TasksMax = 64;
  };
  socketWantedBy = [ "sockets.target" ];
  socketConfig = {
    ListenStream = "/run/tgw/nix-input-observer.sock";
    SocketUser = "codex";
    SocketGroup = "codex";
    SocketMode = "0600";
    Accept = true;
    MaxConnections = 1;
    RemoveOnStop = true;
  };
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
  transportObject = {
    schema = "tgw-nix-input-observer-transport/v1";
    socket = { wantedBy = socketWantedBy; inherit socketConfig; };
    service = { inherit serviceConfig; };
    slice = { inherit sliceConfig; };
  };
  transportContract = builtins.toJSON transportObject;
  transportHash = "sha256:${builtins.hashString "sha256" transportContract}";
  descriptor = ''
    schema=tgw-nix-input-observer-launcher/v2
    uid=${toString cfg.uid}
    gid=${toString cfg.gid}
    python=${cfg.pythonExecutable}
    ip=${cfg.ipExecutable}
    observer=${cfg.observerScript}
    nix=${cfg.nixExecutable}
    nix_store=${cfg.nixStoreExecutable}
    git=${cfg.gitExecutable}
    launcher_sha256=${cfg.launcherSha256}
    python_sha256=${cfg.pythonSha256}
    ip_sha256=${cfg.ipSha256}
    observer_sha256=${cfg.observerSha256}
    nix_sha256=${cfg.nixSha256}
    nix_store_sha256=${cfg.nixStoreSha256}
    git_sha256=${cfg.gitSha256}
    request_sha256=${cfg.requestSha256}
    transport_config_sha256=${transportHash}
    observer_cgroup=0::/tgw-nix-input-observer.slice/tgw-nix-input-observer@
  '';
in {
  options.services.tgw-nix-input-observer-launcher = {
    enable = lib.mkEnableOption "fixed native TGW zero-fetch observer launcher";
    package = lib.mkOption { type = lib.types.package; };
    pythonExecutable = lib.mkOption { type = lib.types.path; description = "Resolved regular Python executable, never a symlink."; };
    ipExecutable = lib.mkOption { type = lib.types.path; };
    nixExecutable = lib.mkOption { type = lib.types.path; };
    nixStoreExecutable = lib.mkOption { type = lib.types.path; };
    gitExecutable = lib.mkOption { type = lib.types.path; };
    observerScript = lib.mkOption { type = lib.types.path; description = "Self-contained immutable observer source artifact."; };
    uid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    gid = lib.mkOption { type = lib.types.ints.positive; default = 1004; };
    launcherSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    pythonSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    ipSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    observerSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    nixSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    nixStoreSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    gitSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; };
    requestSha256 = lib.mkOption { type = lib.types.strMatching "sha256:[0-9a-f]{64}"; description = "The sole immutable prepared observer request accepted by this generation."; };
  };
  config = lib.mkIf cfg.enable {
    environment.etc."tgw/nix-input-observer-launcher.conf" = { text = descriptor; mode = "0400"; user = "root"; group = "root"; };
    environment.etc."tgw/nix-input-observer-transport.json" = { text = transportContract; mode = "0444"; user = "root"; group = "root"; };
    environment.systemPackages = [ cfg.package ];
    systemd.slices."tgw-nix-input-observer".sliceConfig = sliceConfig;
    systemd.sockets.tgw-nix-input-observer = {
      description = "Closed local TGW Nix observer transport";
      wantedBy = socketWantedBy;
      inherit socketConfig;
    };
    systemd.services."tgw-nix-input-observer@" = {
      description = "One-shot fixed TGW Nix observer %i";
      inherit serviceConfig;
    };
    # Module disable removes the descriptor, socket, template service, and slice.
    # No profile or scratch state is written by the observer transport.
  };
}
