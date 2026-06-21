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
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
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
          ./nix/base.nix
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

      # Spare iMac12,1 (2011) — familiarity + flake validation host (Phase 3).
      # Client-mode only: no workers, no eBay secrets, no inference.
      # Boot: EFI via systemd-boot (installed 2026-06-20 from nixos-26.05 ISO).
      nixosConfigurations.tgw-test = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          ./nix/base.nix
          ./nix/tgw-test-hardware.nix
          ({ pkgs, ... }: {
            services.tgw.enable = true;
            services.tgw.workers = [];
            services.tgw.enableHttp = false;

            boot.loader.systemd-boot.enable = true;
            boot.loader.efi.canTouchEfiVariables = true;

            # iMac12,1: mbpfan reads applesmc sensors for fan speed control
            services.mbpfan.enable = true;

            networking.hostName = "tgw-test";
            networking.networkmanager.enable = true;

            # Desktop — X11 + Qtile (TGW config via /etc/qtile/ symlinks)
            services.xserver = {
              enable = true;
              displayManager.sddm.enable = true;
              windowManager.qtile = {
                enable = true;
                # tgw_widgets.py imports httpx + psycopg2 for queue/health bar
                extraPackages = python3Packages: with python3Packages; [
                  httpx psycopg2
                ];
              };
            };

            # Qtile config — lives in nix/qtile/ so it travels with the flake
            environment.etc."qtile/config.py".source = ./nix/qtile/config.py;
            environment.etc."qtile/tgw_widgets.py".source = ./nix/qtile/tgw_widgets.py;

            systemd.tmpfiles.rules = [
              "d /home/dave/.config/qtile 0755 dave users -"
              "L+ /home/dave/.config/qtile/config.py      - - - - /etc/qtile/config.py"
              "L+ /home/dave/.config/qtile/tgw_widgets.py - - - - /etc/qtile/tgw_widgets.py"
            ];

            nixpkgs.config.allowUnfree = true;

            # Solaar — Logitech device manager (udev rules via hardware option)
            hardware.logitech.wireless.enable = true;
            hardware.logitech.wireless.enableGraphical = true;

            environment.systemPackages = with pkgs; [
              kdePackages.konsole   # Super+Enter terminal (nixos-25.05: top-level konsole removed)
              dmenu                 # Super+D launcher
              xterm                 # fallback terminal
              firefox
              google-chrome
              obsidian
              vscodium
              kdePackages.kcalc
              kdePackages.dolphin
              kdePackages.gwenview
              kdePackages.krfb
              libreoffice
              tigervnc
              cloudflared
            ];

            users.users.root.initialPassword = "tgw";

            users.users.dave = {
              isNormalUser = true;
              extraGroups = [ "wheel" "networkmanager" ];
              initialPassword = "tgw";
            };

            security.sudo.wheelNeedsPassword = false;

            system.stateVersion = "24.11";
          })
        ];
      };

      # Production host — full TGW stack + desktop + inference + keyd + NFS
      # Hardware file generated by nixos-generate-config on first boot.
      # Hostname must be "tgw-prod" for `tgw-rebuild` alias to resolve correctly.
      nixosConfigurations.tgw-prod = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          self.nixosModules.tgw
          ./nix/base.nix
          ./nix/inference.nix
          ./nix/keyd.nix
          ./nix/nfs-exports.nix
          ./nix/tgw-prod-hardware.nix   # generated by nixos-generate-config
          ({ pkgs, ... }: {
            services.tgw.enable = true;

            networking.hostName = "tgw-prod";
            networking.networkmanager.enable = true;

            # Bootloader — set after inspecting hardware (EFI or BIOS)
            boot.loader.systemd-boot.enable = true;
            boot.loader.efi.canTouchEfiVariables = true;

            # Desktop — X11 + Qtile (same config as tgw-test)
            services.xserver = {
              enable = true;
              displayManager.sddm.enable = true;
              windowManager.qtile = {
                enable = true;
                extraPackages = python3Packages: with python3Packages; [
                  httpx psycopg2
                ];
              };
            };

            environment.etc."qtile/config.py".source = ./nix/qtile/config.py;
            environment.etc."qtile/tgw_widgets.py".source = ./nix/qtile/tgw_widgets.py;

            systemd.tmpfiles.rules = [
              "d /home/dave/.config/qtile 0755 dave users -"
              "L+ /home/dave/.config/qtile/config.py      - - - - /etc/qtile/config.py"
              "L+ /home/dave/.config/qtile/tgw_widgets.py - - - - /etc/qtile/tgw_widgets.py"
            ];

            nixpkgs.config.allowUnfree = true;

            hardware.logitech.wireless.enable = true;
            hardware.logitech.wireless.enableGraphical = true;

            environment.systemPackages = with pkgs; [
              kdePackages.konsole
              dmenu
              xterm
              firefox
              google-chrome
              obsidian
              vscodium
              kdePackages.kcalc
              kdePackages.dolphin
              kdePackages.gwenview
              kdePackages.krfb
              libreoffice
              tigervnc
              cloudflared
            ];

            users.users.root.initialPassword = "tgw";

            users.users.dave = {
              isNormalUser = true;
              extraGroups = [ "wheel" "networkmanager" ];
              initialPassword = "tgw";
            };

            users.users.tgw = {
              isNormalUser = true;
              uid = 1001;
              group = "tgw";
              extraGroups = [ "keyd" ];
            };
            users.groups.tgw.gid = 1001;

            security.sudo.wheelNeedsPassword = false;

            system.stateVersion = "25.05";
          })
        ];
      };

      # Note: in the deployed system the runtime nvm/npm/venv live UNDER /opt/TGW
      # (NVM_DIR=/opt/TGW/.nvm, NPM_CONFIG_PREFIX=/opt/TGW/.npm, venv at
      # /opt/TGW/.venvironments) so the tree is a fully self-contained imageable
      # entity with no ~tgw dependency — see ./nix/tgw.nix commonService.environment
      # and ./nix/README.md. This devShell is for interactive dev only.
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
