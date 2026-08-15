{ inputs, ... }:
{
  imports = [ inputs.tgw-lib.nixosModules.a3-platform-bootstrap ];

  services.tgw-a3-platform-bootstrap = {
    enable = true;
    package = inputs.tgw-lib.packages.x86_64-linux.a3-platform-bootstrap;
    wrapperConfig = ../../a3-public/nix-observer-render-wrapper.conf;
    composition = ../../a3-public/nix-observer-render-composition.json;
    prerequisiteReceipt = ../../a3-public/nix-observer-render-prerequisite.json;
    attestationPublicKey = ../../a3-public/nix-observer-render-attestation.pub;
    sshAuthorizedPublicKey = builtins.readFile ../../a3-public/codex-authorized-key.txt;
  };
}
