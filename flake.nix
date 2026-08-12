{
  description = "TGW Python source development adapter";

  # The canonical NixOS configuration remains in trader-grim/tgw-flake.
  # This adapter exists only so a clean Python-source clone has an explicit,
  # reproducible development-shell entry point instead of an absolute symlink.
  inputs.tgw-flake.url = "git+ssh://git@github.com/trader-grim/tgw-flake.git?ref=todo/consolidated-nix-fleet-20260725";
  inputs.nixpkgs.follows = "tgw-flake/nixpkgs";

  outputs = { self, tgw-flake, nixpkgs }:
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
      devShells = tgw-flake.devShells;
      packages.${system}.review-egress-systemd-units = reviewEgressSystemdUnits;
      checks.${system}.review-egress-systemd-units = reviewEgressSystemdUnits;
    };
}
