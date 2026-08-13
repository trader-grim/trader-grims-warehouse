{ inputs, ... }:
{
  imports = [ inputs.tgw-lib.nixosModules.a3-platform-bootstrap ];

  tgw.a3PlatformBootstrap.enable = true;
  tgw.a3PlatformBootstrap.authorizedPublicKeyRef = "external:root-owned-a3-authorized-ed25519-public-key";
  tgw.a3PlatformBootstrap.attestationPublicKeyRef = "external:a3-attestation-ed25519-public-verifier";
}
