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
      observerPackage = pkgs.runCommand "tgw-observer-launcher-fixture" { nativeBuildInputs = [ pkgs.stdenv.cc pkgs.openssl ]; } ''
        mkdir -p $out/bin $out/share/tgw
        cc -Wall -Wextra -Werror -o $out/bin/tgw-nix-input-observer-launcher ${./src/native/tgw_nix_input_observer_launcher.c} -lcrypto
        cp ${./src/tgw/nix_input_observation.py} $out/share/tgw/nix-input-observation.py
      '';
      observerSystem = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nix/nix-input-observer-launcher.nix
          ({ ... }: {
            services.tgw-nix-input-observer-launcher = {
              enable = true; package = observerPackage;
              pythonExecutable = "${pkgs.python313}/bin/python3.13";
              ipExecutable = "${pkgs.iproute2}/bin/ip";
              nixExecutable = "${pkgs.nix}/bin/nix";
              nixStoreExecutable = "${pkgs.nix}/bin/nix-store";
              gitExecutable = "${pkgs.git}/bin/git";
              observerScript = "${observerPackage}/share/tgw/nix-input-observation.py";
              launcherSha256 = "sha256:${builtins.hashFile "sha256" "${observerPackage}/bin/tgw-nix-input-observer-launcher"}";
              pythonSha256 = "sha256:${builtins.hashFile "sha256" "${pkgs.python313}/bin/python3.13"}";
              ipSha256 = "sha256:${builtins.hashFile "sha256" "${pkgs.iproute2}/bin/ip"}";
              observerSha256 = "sha256:${builtins.hashFile "sha256" "${observerPackage}/share/tgw/nix-input-observation.py"}";
              nixSha256 = "sha256:${builtins.hashFile "sha256" "${pkgs.nix}/bin/nix"}";
              nixStoreSha256 = "sha256:${builtins.hashFile "sha256" "${pkgs.nix}/bin/nix-store"}";
              gitSha256 = "sha256:${builtins.hashFile "sha256" "${pkgs.git}/bin/git"}";
              requestSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
            };
            system.stateVersion = "25.05";
          })
        ];
      };
      observerUnitNames = [ "tgw-nix-input-observer.socket" "tgw-nix-input-observer@.service" "tgw-nix-input-observer.slice" ];
      observerUnitFiles = map (name: { inherit name; file = pkgs.writeText name observerSystem.config.systemd.units.${name}.text; }) observerUnitNames;
      observerRenderedArtifacts = pkgs.runCommand "nix-input-observer-rendered-artifacts" { } ''
        mkdir -p $out/units $out/etc
        ${builtins.concatStringsSep "\n" (map (unit: "cp ${unit.file} $out/units/${unit.name}") observerUnitFiles)}
        cp ${observerSystem.config.environment.etc."tgw/nix-input-observer-launcher.conf".source} $out/etc/nix-input-observer-launcher.conf
        cp ${observerSystem.config.environment.etc."tgw/nix-input-observer-transport.json".source} $out/etc/nix-input-observer-transport.json
        printf '%s' '${builtins.toJSON { schema = "tgw-nix-input-observer-render/v1"; inherit system; units = observerUnitNames; activation = false; }}' > $out/verifier-metadata.json
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
      packages.${system}.nix-input-observer-rendered-artifacts = observerRenderedArtifacts;
      checks.${system}.nix-input-observer-rendered-artifacts = observerRenderedArtifacts;
    };
}
