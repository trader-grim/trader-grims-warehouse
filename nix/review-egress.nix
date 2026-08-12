{ config, lib, pkgs, ... }:
let cfg = config.services.tgw-review-egress;
in {
  options.services.tgw-review-egress = {
    enable = lib.mkEnableOption "TGW isolated review egress transport";
    package = lib.mkOption { type = lib.types.package; };
    credentialPath = lib.mkOption { type = lib.types.str; description = "Runtime path to dedicated review-only Codex auth; must never enter the Nix store."; };
    runtimePath = lib.mkOption { type = lib.types.str; description = "Pinned Codex runtime whose digest is bound by each run policy."; };
  };
  config = lib.mkIf cfg.enable {
    assertions = [{ assertion = !(lib.hasPrefix "/nix/store/" cfg.credentialPath); message = "review credentialPath must not be copied into the Nix store"; }];
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    users.users.tgw-review-broker = { isSystemUser = true; group = "tgw-review-broker"; uid = 972; };
    users.groups.tgw-review-broker.gid = 972;
    users.users.tgw-review-worker = { isSystemUser = true; group = "tgw-review-worker"; uid = 973; };
    users.groups.tgw-review-worker.gid = 973;
    systemd.services."tgw-review-egress@" = {
      description = "TGW exact-bound review egress broker %i";
      requires = [ "tgw-review-egress-namespace@%i.service" ];
      after = [ "tgw-review-egress-namespace@%i.service" ];
      serviceConfig = {
        Type = "simple"; User = "tgw-review-broker"; Group = "tgw-review-broker";
        NetworkNamespacePath = "/run/netns/tgw-review-%i";
        LoadCredential = "attestation.pub:/run/credentials/tgw-review-attestation.pub";
        ExecStart = "${cfg.package}/bin/tgw-review-egress-broker --policy /run/tgw-review/%i/policy.json --verify-runtime ${cfg.runtimePath} --network-attestation /run/tgw-review/%i/network-attestation.json --attestation-public-key \${CREDENTIALS_DIRECTORY}/attestation.pub --ready /run/tgw-review/%i/ready.json --receipt /run/tgw-review/%i/egress-receipt.json";
        NoNewPrivileges = true; PrivateDevices = true; PrivateTmp = true; ProtectSystem = "strict"; ProtectHome = true;
        ProtectKernelTunables = true; ProtectKernelModules = true; ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ]; CapabilityBoundingSet = "";
        ReadWritePaths = [ "/run/tgw-review/%i" ]; RuntimeMaxSec = 360;
      };
    };
    systemd.services."tgw-review-egress-namespace@" = {
      description = "Prepare fixed TGW review namespace %i";
      serviceConfig = {
        Type = "oneshot"; RemainAfterExit = true; User = "root";
        ExecStart = "${cfg.package}/bin/tgw-review-egress-namespace prepare %i --broker-uid 972 --worker-uid 973 --receipt /run/tgw-review/%i/namespace-prepare.json";
        ExecStop = "${cfg.package}/bin/tgw-review-egress-namespace teardown %i --broker-uid 972 --worker-uid 973 --receipt /run/tgw-review/%i/namespace-teardown.json";
        RuntimeDirectory = "tgw-review/%i"; RuntimeDirectoryMode = "0750";
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" "CAP_SYS_ADMIN" ]; AmbientCapabilities = [ "CAP_NET_ADMIN" "CAP_SYS_ADMIN" ];
        NoNewPrivileges = true; ProtectSystem = "strict"; ProtectHome = true; PrivateTmp = true;
        ReadWritePaths = [ "/run/netns" "/run/tgw-review/%i" ];
      };
    };
    # Root verifier reads live namespace/ruleset/process state and negative
    # probe evidence, then MACs the short-lived attestation. Broker sees only
    # the resulting receipt and a verification key, never provider auth.
    systemd.services."tgw-review-egress-attest@" = {
      serviceConfig = {
        Type = "oneshot"; User = "root";
        LoadCredential = "attestation.key:/run/credentials/tgw-review-attestation.key";
        ExecStart = "${cfg.package}/bin/tgw-review-egress-namespace attest %i --broker-uid 972 --worker-uid 973 --evidence /run/tgw-review/%i/kernel-probe-evidence.json --trust-key \${CREDENTIALS_DIRECTORY}/attestation.key --receipt /run/tgw-review/%i/network-attestation.json";
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" "CAP_SYS_PTRACE" ];
        NoNewPrivileges = true; ProtectSystem = "strict"; ProtectHome = true;
        ReadWritePaths = [ "/run/tgw-review/%i" ];
      };
    };
  };
}
