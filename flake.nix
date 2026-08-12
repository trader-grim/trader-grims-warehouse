{
  description = "TGW Python source development adapter";

  # Evaluation is deliberately independent of the production tgw-flake.  The
  # one exact nixpkgs input is lock-bound and must already exist in the remote
  # store; the provider runs offline with no substituters.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/ac62194c3917d5f474c1a844b6fd6da2db95077d";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      fixturePackage = pkgs.runCommand "tgw-review-egress-evaluation-fixture" { } ''
        mkdir -p $out/bin
        for name in tgw-review-egress-broker tgw-review-egress-namespace; do
          printf '#!${pkgs.runtimeShell}\nexit 0\n' > $out/bin/$name
          chmod 0555 $out/bin/$name
        done
      '';
      evaluated = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nix/review-egress.nix
          ({ ... }: {
            services.tgw-review-egress = {
              enable = true;
              package = fixturePackage;
              credentialPath = "/run/credentials/tgw-review-auth.json";
              runtimePath = "${fixturePackage}/bin/tgw-review-egress-broker";
            };
            system.stateVersion = "25.05";
          })
        ];
      };
      unitNames = [
        "tgw-review-egress@.service"
        "tgw-review-egress-attest@.service"
        "tgw-review-egress-namespace@.service"
      ];
      unitFiles = map (name: {
        inherit name;
        file = pkgs.writeText name evaluated.config.systemd.units.${name}.text;
      }) unitNames;
      verifierMetadata = pkgs.writeText "verifier-metadata.json" (builtins.toJSON {
        schema = "tgw-review-egress-systemd-units/v1";
        inherit system;
        units = unitNames;
        activation = false;
      });
      reviewEgressSystemdUnits = pkgs.runCommand "review-egress-systemd-units" { } ''
        mkdir -p $out/units
        ${builtins.concatStringsSep "\n" (map (unit: "cp ${unit.file} $out/units/${unit.name}") unitFiles)}
        cp ${verifierMetadata} $out/verifier-metadata.json
      '';
    in {
      devShells.${system}.default = pkgs.mkShell { };
      inputIdentities.nixpkgs = {
        outPath = nixpkgs.outPath;
        rev = "ac62194c3917d5f474c1a844b6fd6da2db95077d";
        narHash = "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=";
      };
      packages.${system}.review-egress-systemd-units = reviewEgressSystemdUnits;
      checks.${system}.review-egress-systemd-units = reviewEgressSystemdUnits;
    };
}
