{
  # ===========================================================================
  # Trader Grim's Warehouse — Nix flake (PP-NIXOS-001)
  #
  # AUTHORED FOR VM VALIDATION.  NixOS is the committed target OS; this flake +
  # ./nix/tgw.nix package the Python app and declare the full service stack
  # (PostgreSQL state_machine DB, tgw-http, the worker fleet, the backup unit)
  # so Dave can build/boot it in a NixOS VM before any cutover.  It is NOT built
  # on the current MX host.
  #
  # Design decisions (revisit during VM validation — see ./nix/tgw.nix options):
  #   * buildPythonApplication (not poetry2nix): there is no poetry.lock and the
  #     dependency set is small + all in nixpkgs, so an explicit propagated-deps
  #     build is more stable and reviewable.  Switch to poetry2nix only if a
  #     lockfile-pinned closure becomes necessary.
  #   * Paths stay at /opt/TGW (the app hardcodes this via config.py); the module
  #     manages that tree with tmpfiles rather than relocating it.
  # ===========================================================================

  description = "Trader Grim's Warehouse — inventory + eBay automation platform";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
  };

  outputs = { self, nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
      pkgsFor = system: import nixpkgs { inherit system; };

      # The Python application + its propagated runtime deps.  Consumed by the
      # NixOS module (default package) and exposed for `nix build`.
      tgwPackage = pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "trader-grims-warehouse";
          version = "0.1.0";
          pyproject = true;
          src = ./.;

          build-system = [ pkgs.python3Packages.setuptools ];

          # Mirrors [project.dependencies] + the thumbnails extra in pyproject.toml.
          # psycopg2-binary → psycopg2 (nixpkgs builds it against libpq); uvicorn
          # gets its [standard] extras explicitly.
          #
          # VM-validation watch-item: `python3Packages.mcp` (the Model Context
          # Protocol SDK, pyproject requires mcp>=1.0) is recent — if the pinned
          # nixos-24.11 channel lacks it or ships <1.0, switch the flake input to
          # `nixos-unstable` or add a small overlay that packages mcp.
          dependencies = with pkgs.python3Packages; [
            requests
            textual
            psycopg2
            fastapi
            uvicorn
            uvloop
            httptools
            websockets
            watchfiles
            python-dotenv
            mcp
            pillow
          ];

          # The test suite needs PostgreSQL + secrets + network; skip at build
          # time (validation happens in the VM via `tgw health`).
          doCheck = false;

          pythonImportsCheck = [ "tgw" ];

          meta = with pkgs.lib; {
            description = "TGW inventory management + eBay automation platform";
            mainProgram = "tgw";
            platforms = platforms.linux;
          };
        };
    in
    {
      packages = forAllSystems (system:
        let pkgs = pkgsFor system; in {
          tgw = tgwPackage pkgs;
          default = tgwPackage pkgs;
        });

      # NixOS module: import this and set `services.tgw.enable = true;`.
      nixosModules.tgw = import ./nix/tgw.nix self;
      nixosModules.default = self.nixosModules.tgw;

      # A minimal VM host config for `nixos-rebuild build-vm --flake .#vm` so
      # Dave can boot the whole stack and run `tgw health` (PP-DEPLOY-001 prep).
      nixosConfigurations.vm = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          ({ ... }: {
            services.tgw.enable = true;
            # VM convenience only — real deploys keep secrets out of the store.
            users.users.root.initialPassword = "tgw";
            system.stateVersion = "24.11";
            virtualisation.vmVariant.virtualisation = {
              memorySize = 4096;
              cores = 4;
            };
          })
        ];
      };

      devShells = forAllSystems (system:
        let pkgs = pkgsFor system; in {
          default = pkgs.mkShell {
            packages = [
              (pkgs.python3.withPackages (ps: with ps; [
                requests textual psycopg2 fastapi uvicorn mcp pillow
                pytest ruff
              ]))
              pkgs.postgresql
            ];
          };
        });
    };
}
