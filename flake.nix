{
  # ===========================================================================
  # Trader Grim's Warehouse — Nix flake (PP-NIXOS-001)
  #
  # AUTHORED FOR VM VALIDATION.  NixOS is the committed target OS; this flake +
  # nix/tgw.nix package the Python app and declare the full service stack
  # (PostgreSQL state_machine DB, tgw-http, the worker fleet, the backup unit)
  # so Dave can build/boot it in a NixOS VM before any cutover.
  #
  # Module structure (nix/):
  #   os/base.nix          — CatioNIX: common OS config (SSH, admin tools, tailscale, syncthing…)
  #   os/users.nix         — CatioNIX: human accounts (db uid 1000, root)
  #   os/desktop.nix       — CatioNIX: opt-in GUI layer (X11+Qtile+apps, no TGW config)
  #   tgw/users.nix        — TGW: service account (tgw uid/gid 900) — SINGLE source of truth
  #   tgw/platform.nix     — TGW: system packages (ffmpeg, imagemagick, exiftool, chafa, gh), tgw-install-bundle Syncthing folder
  #   tgw/desktop.nix      — TGW: Qtile extraPackages + config files + db's symlinks
  #   bases/master.nix     — full server platform (os + tgw + inference + keyd + nfs-exports)
  #   bases/portable.nix   — client tier (os + tgw, no workers/http/inference)
  #   hosts/{vm,tgw-test,tgw-prod}.nix — per-host composition; host-specific bits only
  #   hardware/*.nix       — nixos-generate-config output per machine
  #   tgw.nix              — NixOS module: worker fleet, tgw-http, DB bootstrap, tmpfiles
  #   inference.nix  keyd.nix  nfs-exports.nix  — single-concern modules
  #
  # Design decisions:
  #   * buildPythonApplication (not poetry2nix): no poetry.lock; dependency set is
  #     small + all in nixpkgs.  Switch only if a lockfile-pinned closure is needed.
  #   * Paths stay at /opt/TGW (the app hardcodes this via config.py).
  #   * No flake-parts: not warranted at 2-system / 3-host / 1-module scale.
  #   * CatioNIX (nix/os/) is the OS layer; TGW (nix/tgw/) is the application
  #     layer built on top.  Neither layer knows about the other's internals.
  # ===========================================================================

  description = "Trader Grim's Warehouse — inventory + eBay automation platform";

  inputs = {
    nixpkgs.url        = "github:NixOS/nixpkgs/nixos-25.05";
    disko.url          = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";
    home-manager.url   = "github:nix-community/home-manager/release-25.05";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, disko, home-manager, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
      pkgsFor = system: import nixpkgs { inherit system; };

      # The Python application + its propagated runtime deps.  Consumed by the
      # NixOS module (default package) and exposed for `nix build`.
      tgwPackage = pkgs:
        pkgs.python312Packages.buildPythonApplication {
          pname = "trader-grims-warehouse";
          version = "0.1.0";
          pyproject = true;
          src = ./.;

          build-system = [ pkgs.python312Packages.setuptools ];

          # Mirrors [project.dependencies] + the thumbnails extra in pyproject.toml.
          # psycopg2-binary → psycopg2 (nixpkgs builds it against libpq); uvicorn
          # gets its [standard] extras explicitly.
          #
          # VM-validation watch-item: `python3Packages.mcp` (the Model Context
          # Protocol SDK, pyproject requires mcp>=1.0) is recent — if the pinned
          # nixos-25.05 channel lacks it or ships <1.0, switch the flake input to
          # `nixos-unstable` or add a small overlay that packages mcp.
          dependencies = with pkgs.python312Packages; [
            httpx
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
            pyperclip
            mistune
            pillow
          ];

          # psycopg2-binary → psycopg2 (nixpkgs builds against system libpq).
          # pyncthing may be absent from nixpkgs 25.05; runtime check skipped —
          # the actual syncthing integration is optional and not used on this host.
          pythonRemoveDeps = [ "psycopg2-binary" "pyncthing" ];

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
          tgw     = tgwPackage pkgs;
          default = tgwPackage pkgs;
        });

      # NixOS module: import this and set `services.tgw.enable = true;`
      # Option B (current): module uses services.tgw.venvPath; the package output
      # below is NOT wired into the NixOS configs.  Option A (future, tgw-test
      # hardening): wire tgwPackage into the module via services.tgw.package.
      nixosModules.tgw     = import ./nix/tgw.nix self;
      nixosModules.default = self.nixosModules.tgw;

      # ---------------------------------------------------------------------------
      # Host configurations
      # Each entry is intentionally minimal — all composition lives in nix/hosts/.
      # ---------------------------------------------------------------------------

      # Throwaway VM for full-stack validation: `nixos-rebuild build-vm --flake .#vm`
      nixosConfigurations.vm = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ self.nixosModules.tgw ./nix/hosts/vm.nix ];
      };

      # Spare iMac12,1 — NixOS familiarisation + flake/restore validation
      nixosConfigurations.tgw-test = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          disko.nixosModules.disko
          home-manager.nixosModules.home-manager
          ./nix/hosts/tgw-test.nix
          ./nix/hosts/tgw-test-disko.nix
          ./nix/home/hm-module.nix
        ];
      };

      # tgw-test in server (master) profile for Phase 4 dress rehearsal.
      # Push with: bash scripts/tgw-push-config.sh tgw-test-rehearsal <ip>
      # After rehearsal, push tgw-test to restore the client profile.
      nixosConfigurations.tgw-test-rehearsal = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          disko.nixosModules.disko
          home-manager.nixosModules.home-manager
          ./nix/hosts/tgw-test-rehearsal.nix
          ./nix/hosts/tgw-test-disko.nix
          ./nix/home/hm-module.nix
        ];
      };

      # Production host — full TGW stack + desktop
      nixosConfigurations.tgw-prod = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          home-manager.nixosModules.home-manager
          disko.nixosModules.disko
          ./nix/hosts/tgw-prod.nix
          ./nix/hosts/tgw-prod-disko.nix
          ./nix/home/hm-module.nix
        ];
      };

      # Dev shell — interactive development on any machine with Nix
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
