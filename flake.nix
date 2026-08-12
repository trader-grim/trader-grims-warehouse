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
        mkdir -p $out/bin $out/share/tgw $out/tools
        cc -Wall -Wextra -Werror -o $out/bin/tgw-nix-input-observer-launcher ${./src/native/tgw_nix_input_observer_launcher.c} -lcrypto
        cp ${./src/tgw/nix_input_observation.py} $out/share/tgw/nix-input-observation.py
        for spec in python:${pkgs.python313}/bin/python3.13 ip:${pkgs.iproute2}/bin/ip nix:${pkgs.nix}/bin/nix nix-store:${pkgs.nix}/bin/nix-store git:${pkgs.git}/bin/git; do
          name="''${spec%%:*}"; source="''${spec#*:}"; resolved="$(${pkgs.coreutils}/bin/readlink -f "$source")"
          test -f "$resolved" && test ! -L "$resolved"
          cp --reflink=auto "$resolved" "$out/tools/$name"
          chmod 0555 "$out/tools/$name"
        done
      '';
      observerSystem = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nix/nix-input-observer-launcher.nix
          ({ ... }: {
            services.tgw-nix-input-observer-launcher = {
              enable = true; package = observerPackage;
              # Rendering uses explicit non-deployable fixture identities.  It
              # performs no IFD.  The build phase below hashes actual artifacts;
              # deployment must create a distinct final descriptor from receipt.
              pythonExecutable = "/nix/store/00000000000000000000000000000000-python-regular";
              ipExecutable = "/nix/store/00000000000000000000000000000000-ip-regular";
              nixExecutable = "/nix/store/00000000000000000000000000000000-nix-regular";
              nixStoreExecutable = "/nix/store/00000000000000000000000000000000-nix-store-regular";
              gitExecutable = "/nix/store/00000000000000000000000000000000-git-regular";
              observerScript = "/nix/store/00000000000000000000000000000000-observer.py";
              launcherSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              pythonSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              ipSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              observerSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              nixSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              nixStoreSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              gitSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
              requestSha256 = "sha256:0000000000000000000000000000000000000000000000000000000000000000";
            };
            system.stateVersion = "25.05";
          })
        ];
      };
      observerUnitNames = [ "tgw-nix-input-observer.slice" "tgw-nix-input-observer.socket" "tgw-nix-input-observer@.service" ];
      observerUnitFiles = map (name: { inherit name; file = pkgs.writeText name observerSystem.config.systemd.units.${name}.text; }) observerUnitNames;
      observerRenderedArtifacts = pkgs.runCommand "nix-input-observer-rendered-artifacts" { nativeBuildInputs = [ pkgs.python313 ]; } ''
        mkdir -p $out/units $out/etc
        ${builtins.concatStringsSep "\n" (map (unit: "cp ${unit.file} $out/units/${unit.name}") observerUnitFiles)}
        cp ${observerSystem.config.environment.etc."tgw/nix-input-observer-launcher.conf".source} $out/etc/nix-input-observer-launcher.conf
        cp ${observerSystem.config.environment.etc."tgw/nix-input-observer-transport.json".source} $out/etc/nix-input-observer-transport.json
        cp ${observerPackage}/bin/tgw-nix-input-observer-launcher $out/launcher
        cp ${observerPackage}/share/tgw/nix-input-observation.py $out/observer.py
        cp -r ${observerPackage}/tools $out/tools
        python3 - "$out" ${builtins.concatStringsSep " " observerUnitNames} <<'PY'
        import hashlib,json,pathlib,sys
        root=pathlib.Path(sys.argv[1])
        files=sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        value={"schema":"tgw-nix-input-observer-render/v1","system":"x86_64-linux","units":sys.argv[2:],"activation":False,"descriptor_status":"NON_DEPLOYABLE_RENDER_FIXTURE","files":[{"path":name,"sha256":"sha256:"+hashlib.sha256((root/name).read_bytes()).hexdigest()} for name in files]}
        (root/"verifier-metadata.json").write_text(json.dumps(value,sort_keys=True,separators=(",",":")))
        PY
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
