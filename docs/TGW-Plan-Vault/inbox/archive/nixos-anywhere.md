Installing NixOS remotely or over a network typically involves a PXE (Preboot Execution Environment) or netboot setup. With a declarative OS like NixOS, you can use your existing PXE infrastructure to boot the installer over the network, then automatically apply a pre-defined system configuration.1. Generating the Netboot ImageTo install NixOS via PXE, you first need to build a netboot image from your existing Nix environment. This generates the kernel and the initial RAM disk (initrd). Run the following command on a host that already has Nix installed:bashnix-build -A netboot nixos/release.nix
Use code with caution.This will output a result/ folder containing three key files:bzImage: The Linux kernel.initrd: The RAM disk.netboot.ipxe: An example iPXE script mapping the required kernel arguments.2. Configuring Your PXE/BOOTP ServerOnce the image is generated, transfer the bzImage and initrd files to your network's TFTP and HTTP servers.Standard PXE: Configure your bootloader menu (such as pxelinux) to point to the bzImage and initrd files, and pass the required kernel command-line arguments provided in netboot.ipxe.iPXE / TFTP: Many users prefer utilizing iPXE via a DHCP server (like dnsmasq or Mikrotik) to directly serve the NixOS netboot image over HTTP for faster, more reliable transfers.3. Executing the Remote InstallationWith the PXE infrastructure running:Turn on the target machine and configure its NIC/BIOS to initiate a PXE/Network Boot.The machine will load the NixOS live environment over the network.Once booted, log into the minimal live environment (it defaults to the root user with an empty password, or SSH if enabled).Partition your target disks, mount your drives to /mnt (and /mnt/boot), and generate your initial hardware-configuration.nix.Clone or write your NixOS configuration file (usually configuration.nix), and apply it using:bashsudo nixos-install --root /mnt
Use code with caution.If you are using custom deployment scripts or handling secrets, you might prefer using tools like nixos-anywhere or clan-core to automate the partitioning and config deployment entirely over SSH without interacting with the machine locally.If you'd like to narrow down your deployment process, let me know:Do you have existing PXE/iPXE infrastructure set up, or are you starting from scratch?Are you installing this on bare metal servers or virtual machines?Are you planning to use NixOS Flakes to manage your target host's configuration?11 sitesNetboot - NixOS Wikixyz. Just select NixOS from Linux installs and you should be ready to go. Note: Your iPXE must be recent enough to support https:/Nix WikiGitHub - alexandergall/nixos-pxe-installer: A set of modules to ...Installing the Client. Configure the client's EFI boot loader to perform a PXE boot on the desired interface and initiate a system...GitHubBooting NixOS over network with iPXE and MikroTik - RedditComments Section. dimitrijer89. 2y ago. This is the second post in my homelab series1, describing how servers in my homelab boot o...RedditShow allYou said: bare metal install. migrating from mx flafored debian to nix flake modular model. PXE installed on MX for LTSP.To migrate your bare-metal machines from MX Linux (Debian-based) to a modular NixOS Flake model using your existing LTSP/PXE setup, you can serve a custom NixOS netboot image directly through your current TFTP/HTTP directories.Because NixOS handles its root filesystem fundamentally differently than standard Debian-based LTSP, you will use PXE to boot a NixOS installer environment, then write the Flake configuration to the local disk.1. Structure Your Modular NixOS FlakeCreate a modular directory structure on your development machine. This separates your core system hardware configurations from your reusable software modules.text/my-nixos-flake
├── flake.nix
├── hosts/
│   └── bare-metal-node/
│       ├── configuration.nix
│       └── hardware-configuration.nix
└── modules/
    ├── core.nix
    ├── desktop.nix
    └── network.nix
Use code with caution.Example flake.nixnix{
  description = "Modular NixOS Flake for Bare Metal Migration";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }@inputs: {
    nixosConfigurations = {
      bare-metal-node = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./hosts/bare-metal-node/configuration.nix
          ./modules/core.nix
          ./modules/desktop.nix
        ];
      };
    };
  };
}
Use code with caution.2. Generate the Netboot ArtifactsInstead of a stock NixOS ISO, build a netboot image using Nix flakes. Run this command inside your flake directory to generate the required kernel and initrd:bashnix build github:nixos/nixpkgs/nixos-unstable#nixosTests.netboot.driver
# Alternatively, use a specialized netboot derivation:
nix build .#nixosConfigurations.bare-metal-node.config.system.build.netbootRamdisk
Use code with caution.This produces a result directory containing bzImage (kernel) and initrd (initramfs).3. Integrate with Existing MX Linux LTSPLTSP on MX Linux typically uses dnsmasq or isc-dhcp-server along with ipxe or syslinux served from /srv/tftp/ or /var/lib/tftpboot/.Copy Artifacts: Move the generated bzImage and initrd files into your LTSP TFTP/HTTP directory (e.g., /srv/tftp/nixos/).Update iPXE / Boot Menu: Add an entry for the NixOS installer to your LTSP menu configuration file (usually found in /etc/ltsp/ or /srv/tftp/ltsp/ltsp.ipxe):text:nixos-install
kernel http://${srv}/nixos/bzImage init=${nixos}/init systemd.setenv=wpa_supplicant=0 boot.shell_on_fail
initrd http://${srv}/nixos/initrd
boot
Use code with caution.(Replace ${srv} with your MX Linux LTSP server IP address).4. Execute the Bare Metal InstallationBoot the target bare-metal machine via network PXE and select the NixOS install entry.Once booted into the volatile RAM environment, partition and format your local target drives (e.g., using fdisk, cfdisk, or parted).Mount your newly formatted root partition to /mnt and your boot partition to /mnt/boot.Generate the base hardware configuration for the target machine:bashnixos-generate-config --root /mnt
Use code with caution.Copy the generated /mnt/etc/nixos/hardware-configuration.nix back into your modular Flake directory under ./hosts/bare-metal-node/hardware-configuration.nix.Run the final deployment directly from your Flake repository (hosted on GitHub/GitLab or a local USB drive):bashnixos-install --root /mnt --flake .#bare-metal-node
Use code with caution.5. Automated Alternative: nixos-anywhereSince your MX Linux machines are currently running an active Debian environment with SSH access, you can bypass the PXE boot menu entirely for migration using nixos-anywhere.From your development machine, you can trigger a completely automated overwrite of the active MX Linux system over SSH:bashnix run github:nix-community/nixos-anywhere -- --flake .#bare-metal-node root@target-mx-linux-ip
Use code with caution.This tool handles masking the active MX Linux RAM, partitioning the drive via a specified layout, and kexec-booting directly into your NixOS Flake configuration.To refine this migration, please let me know:Do your target bare-metal machines require specific legacy BIOS or UEFI boot configurations?Would you like an example of a disko script to fully automate the disk partitioning phase during the flake deployment?3 sitesGitHub - alexandergall/nixos-pxe-installer: A set of modules to perform a fully automated installation of a customised NixOS systemnixos-pxe-installer A set of modules to perform a fully automated installation of a customised NixOS system over the network via P...GitHubIt’s Alive! Bootstrapping a Declarative NixOS Homelab [PART-1] | by Akash RajvanshiJul 20, 2025 — (Check your disk device with lsblk; we'll assume /dev/sda). 4. Generate and Edit Configuration: * Generate the base configuration ...AWS in Plain EnglishGitHub - alexandergall/nixos-pxe-installer: A set of modules to perform a fully automated installation of a customised NixOS systemThe hardware-specific NixOS configuration is created by executing nixos-generate-config --root=/mnt . This will generate the file ...GitHubYou said: one of the targets requires special handling. iMac a1131-12.1The iMac12,1 (Mid 2011) poses distinct challenges compared to standard x86 servers. It has a 64-bit Sandy Bridge CPU but handles network booting and graphics in a highly non-standard manner.You must address two critical hurdles to provision it cleanly into your NixOS flake topology: Apple EFI PXE behavior and unfree legacy Radeon graphics drivers.1. The Network Booting HurdleApple hardware does not use standard Intel PXE firmware. It requires a specific network boot protocol called NetBoot (BSDP over DHCP) and relies strictly on an EFI-aware bootloader. Your existing MX Linux LTSP server (which likely serves standard PXE/iPXE) will fail to boot it out of the box unless configured correctly.Option A: Intercept with iPXE via USB (Recommended)Rather than redesigning your entire LTSP network infrastructure for one machine, flash a small USB drive with an iPXE EFI compilation (ipxe.efi).Insert the iPXE USB into the iMac.Hold down the Option (Alt) key at startup and select the EFI USB drive.Once the iPXE command-line interface loads, chain-load directly to your MX Linux LTSP server bypasses Apple's proprietary netboot handshake:textdhcp
chain http://<YOUR_LTSP_SERVER_IP>/nixos/netboot.ipxe
Use code with caution.Option B: Bypass PXE with nixos-anywhereSince the iMac is currently running MX Linux, bypass the network boot process entirely. Execute the migration over local network SSH via nixos-anywhere from a machine running Nix. It will inject the image directly into memory and rewrite the disk.2. Configure the Bootloader in Your FlakeIntel Macs from this era are notoriously finicky with systemd-boot. You must explicitly configure GRUB with EFI support in your modular flake layout to ensure the machine reboots successfully after installation.Add a dedicated host file for the machine: hosts/imac-a1131/configuration.nixnix{ config, pkgs, ... }:

{
  # Bootloader setup specifically engineered for vintage Apple EFI
  boot.loader.systemd-boot.enable = false;
  boot.loader.efi.canTouchEfiVariables = false; # Apple EFI does not tolerate Linux NVRAM variables well
  
  boot.loader.grub = {
    enable = true;
    device = "nodev";
    efiSupport = true;
    # Force GRUB to install to the removable media path if the NVRAM entries fail to register
    efiInstallAsRemovable = true; 
  };

  # Essential kernel parameters for 2011 Radeon graphics stability
  boot.kernelParams = [ "radeon.modeset=1" "radeon.si_support=0" "radeon.cik_support=0" ];
}
Use code with caution.3. Handle the Legacy GPU & FirmwareThe iMac 12,1 features an AMD Radeon HD 6750M or 6770M discrete graphics chip. To prevent the machine from dropping to a black screen upon booting the graphical stage, you must explicitly enable non-free firmwares within your flake modules.Create a specific hardware profile: modules/hardware/imac-gpu.nixnix{ config, pkgs, ... }:

{
  # Necessary for loading proprietary AMD graphics microcode
  hardware.enableAllFirmware = true;
  hardware.cpu.intel.updateMicrocode = true;
  
  # NixOS Flakes enforce pure evaluation; you must allow unfree packages 
  # to build the kernel with the required legacy firmware blocks.
  nixpkgs.config.allowUnfree = true;

  # Accelerated Graphics Setup
  hardware.graphics = {
    enable = true;
    enable32Bit = true; # Crucial if running legacy apps or wine wrappers
  };

  # Force use of the open-source Radeon kernel driver (not the defunct amdgpu or fglrx)
  services.xserver.videoDrivers = [ "radeon" ];
}
Use code with caution.4. Wire the iMac Into Your Main flake.nixIntegrate these modules into your master configuration file under nixosConfigurations. This ensures the target configuration evaluates cleanly under x86_64-linux.nix{
  description = "Modular Fleet Infrastructure Flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }@inputs: {
    nixosConfigurations = {
      # Standard Bare Metal Targets
      standard-node = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ ./hosts/bare-metal/configuration.nix ];
      };

      # Special handling target for the a1131 iMac
      imac-target = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./hosts/imac-a1131/configuration.nix
          ./modules/hardware/imac-gpu.nix
          ./modules/core.nix # Reusable global networking/user modules
        ];
      };
    };
  };
}
Use code with caution.Would you like me to generate a disko partition table script formatted for standard Apple EFI single-drive GPT schemas, or do you need assistance configuring Wi-Fi firmware modules for the internal Broadcom card on this iMac?6 sitesAll iMac with iMac12,1 Model Identifier - EveryMac.comTable_title: For a Serial Number lookup, be sure to enter the entire serial number. Table_content: | Intro. | May 3, 2011 | Octobe...EveryMac.comiMac "Core i5" 2.5 21.5" (Mid-2011) Specs ... - EveryMac.comThis model is powered by a 32 nm, 64-bit Intel Core i5 "Sandy Bridge" processor with quad cores (four independent processor cores ...EveryMac.comiMac "Core i5" 2.7 21.5" (Mid-2011) Specs (Mid ... - EveryMacThe iMac "Core i5" 2.7 21.5-Inch Aluminum (Mid-2011) is powered by a Quad Core 2.7 GHz Intel "Core i5" I5-2500S (Sandy Bridge) pro...EveryMacShow allYou said: the old imac does install fine from a usb. once installed it seems the nixos-anywhere would work fine and be much better than pxe, usb sticks, or syncthing.Since you confirmed the iMac successfully boots and installs from a local USB drive, transitioning to nixos-anywhere after that initial setup is an excellent choice. It completely eliminates the need to manage finicky Apple PXE network handshakes, physical USB updates, or file-syncing utilities like Syncthing for ongoing modifications.Once the baseline NixOS installation is running on the iMac with an active SSH server, nixos-anywhere can completely take over. It acts as a remote provisioning tool that can wipe the system, repartition the drives, and deploy your updated modular Flake configurations entirely over the local network.1. Preparing the Running iMac for nixos-anywhereFor nixos-anywhere to connect and safely overwrite the system from your development machine, the running target OS needs a few baseline settings. Ensure your initial USB installation includes these parameters in its /etc/nixos/configuration.nix:nix# Enable the OpenSSH daemon
services.openssh.enable = true;

# Allow root login via SSH keys (temporary or permanent for deployment)
services.openssh.settings.PermitRootLogin = "prohibit-password";

# Inject your development machine's public SSH key into the root account
users.users.root.openssh.authorizedKeys.keys = [
  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@dev-machine"
];
Use code with caution.Run sudo nixos-rebuild switch on the iMac after adding these keys.2. Add disko to Automate Drive Partitioningnixos-anywhere relies heavily on a tool called Disko to format and partition disks declaratively. This ensures that every time you run a remote deployment, the partition schema is correctly applied without human intervention.Create a partition layout file in your flake path at hosts/imac-a1131/disko.nix:nix{
  disko.devices = {
    disk = {
      main = {
        type = "disk";
        device = "/dev/sda"; # Verify if the iMac drive is /dev/sda or /dev/nvme0n1
        content = {
          type = "gpt";
          partitions = {
            # Vintage Apple EFI partitions must be explicitly sized and typed
            ESP = {
              size = "512M";
              type = "EF00";
              content = {
                type = "filesystem";
                format = "vfat";
                mountpoint = "/boot";
                mountOptions = [ "defaults" ];
              };
            };
            root = {
              size = "100%";
              content = {
                type = "filesystem";
                format = "ext4";
                mountpoint = "/";
              };
            };
          };
        };
      };
    };
  };
}
Use code with caution.3. Update the Flake DefinitionsImport both the disko module and the custom configuration you created earlier into your primary flake.nix layout.nix{
  description = "Modular Fleet Infrastructure Flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    # Add disko input
    disko.url = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, disko, ... }@inputs: {
    nixosConfigurations = {
      imac-target = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          disko.nixosModules.disko
          ./hosts/imac-a1131/disko.nix
          ./hosts/imac-a1131/configuration.nix
          ./modules/hardware/imac-gpu.nix
          ./modules/core.nix
        ];
      };
    };
  };
}
Use code with caution.4. Running the Remote DeploymentWhenever you make a change to the core modules or the host configuration files on your development laptop, you can force the iMac to fetch, rebuild, and re-apply the environment.Execute the following command from the root folder of your development machine's flake workspace:bashnix run github:nix-community/nixos-anywhere -- \
  --flake .#imac-target \
  root@<IMAC_IP_ADDRESS>
Use code with caution.What happens under the hood?nixos-anywhere authenticates to the running iMac via SSH.It uploads a minimal kexec installer image directly into the iMac's RAM.The iMac kexecs (reboots without hitting the BIOS/EFI) straight into the temporary RAM-based installer.Disko executes, wiping the local hard drive and structuring the partitions perfectly for the Apple EFI path.Your custom modular flake environment compiles and installs directly onto the freshly formatted drives.The iMac reboots directly into your production-ready, modular NixOS target system.Would you like me to show you how to structure shared network settings or user profiles within your modules/core.nix file so they apply identically to both your iMac and standard bare-metal targets?6 sitesnixos-anywhere/README.md at main · nix-community/nixos-anywhereYou can then initiate an unattended installation with a single CLI command. Since nixos-anywhere can access the new machine using ...GitHubWhy NIXOS is a Great Linux DistributionJun 9, 2025 — After successfully installing NixOS, the next step is configuring the system to suit your preferences. Whether it's selecting the ...TikTokWe don't need NixOS cloud images anymoreJul 6, 2023 — So how does it work? First, nixos-anywhere connects to the remote machine via SSH. Next, it uses kexec to boot into a NixOS instal...NumtideShow allYou said: sureStructuring your modules/core.nix file with unified settings ensures that shared configurations—like user accounts, system locales, ssh access, and package suites—are maintained in one place while scaling seamlessly across both your vintage Apple hardware and standard bare-metal servers.Here is how you can write a highly portable, production-ready modules/core.nix file.1. The Portable Core Configuration (modules/core.nix)This module aggregates global parameters that every node in your cluster needs, regardless of unique hardware quirks like the iMac's GPU.nix{ config, pkgs, ... }:

{
  # ----------------------------------------------------
  # 1. System Base & Locales
  # ----------------------------------------------------
  time.timeZone = "America/New_York"; # Adjust to your local timezone
  i18n.defaultLocale = "en_US.UTF-8";
  console.keyMap = "us";

  # ----------------------------------------------------
  # 2. Shared Network Settings
  # ----------------------------------------------------
  networking = {
    # Enables modern systemd-networkd backend for stable DHCP across all NIC types
    useDHCP = false;
    useNetworkd = true;
    
    # Enable predictable firewall structures
    firewall = {
      enable = true;
      allowedTCPPorts = [ 22 ]; # Keep SSH open across your fleet
      # allowedUDPPorts = [ ... ];
    };
  };

  # Enable systemd-networkd for automatic interface discovery and DHCP
  systemd.network.networks."10-dhcp-all" = {
    matchConfig.Name = "en* eth* wlan*"; # Matches standard and Apple Broadcom interfaces
    networkConfig.DHCP = "yes";
  };

  # ----------------------------------------------------
  # 3. Global SSH Settings (Crucial for nixos-anywhere)
  # ----------------------------------------------------
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "prohibit-password"; # Enforce secure key-based root login
      PasswordAuthentication = false;      # Hardens against local password brute-forcing
    };
  };

  # ----------------------------------------------------
  # 4. Fleet User Management
  # ----------------------------------------------------
  users.mutableUsers = false; # Enforces fully declarative users from this file

  users.users = {
    # Administrative account deployed across all nodes
    admin = {
      isNormalUser = true;
      description = "Fleet Administrator";
      extraGroups = [ "wheel" "networkmanager" "video" ];
      # Pre-bake public SSH keys across your fleet for immediate admin control
      openssh.authorizedKeys.keys = [
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@dev-machine"
      ];
    };
    
    # Pre-populate root with the same keys so nixos-anywhere works continuously
    root.openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@dev-machine"
    ];
  };

  # ----------------------------------------------------
  # 5. Core Package Set & Nix Optimizations
  # ----------------------------------------------------
  environment.systemPackages = with pkgs; [
    curl
    git
    htop
    tmux
    vim
    wget
  ];

  # Enable flakes and optimize storage automatically
  nix = {
    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      trusted-users = [ "root" "@wheel" ];
    };
    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 14d";
    };
    settings.auto-optimise-store = true; # Deduplicates identical store paths
  };

  # Set your baseline state version for reproducibility
  system.stateVersion = "24.11"; 
}
Use code with caution.2. How the Modular Flow Benefits YouBecause modules/core.nix is now a generic component, look at how clean your actual hardware-specific node declarations become inside flake.nix:Standard Bare-Metal Nodes: Simply pull in ./modules/core.nix + their specific local drive profiles.The iMac Target: Pulls in ./modules/core.nix, but also stacks ./modules/hardware/imac-gpu.nix to handle the legacy Radeon firmware smoothly without cluttering up your main cluster config.3. Executing Seamless Cluster UpdatesOnce nixos-anywhere establishes the base OS via this configuration, you do not even need to use nixos-anywhere for normal system upgrades or software adjustments.From your development machine, you can push config updates remotely via nixos-rebuild over SSH:bashnixos-rebuild switch \
  --flake .#imac-target \
  --target-host admin@<IMAC_IP_ADDRESS> \
  --use-remote-sudo
Use code with caution.This evaluates the flake locally on your machine and sends only the differences over the network, making updates to the old iMac fast and lightweight.Now that your core infrastructure settings are modular, would you like to explore adding Home Manager to your flake to manage personalized user dotfiles and application environments across your nodes, or should we add tailscale/VPN parameters to modules/core.nix for secure off-site communication?You said: tailscal is installed, use it. Home manager is in the planSince you already have Tailscale in place and plan to integrate Home Manager, we can weave both directly into your modular Flake layout.Using Tailscale natively inside your core module ensures that your old iMac and standard bare-metal nodes automatically spin up their mesh VPN interfaces on boot. This also lets you run remote updates via nixos-rebuild or nixos-anywhere using secure Tailscale IPs instead of volatile local DHCP addresses.1. Upgrade modules/core.nix with TailscaleModify your modules/core.nix file to enable the Tailscale service daemon and configure the network to trust the Tailscale interface.nix{ config, pkgs, ... }:

{
  # ... Keep your existing system base, locale, and user setups intact ...

  # ----------------------------------------------------
  # 2. Shared Network & Tailscale Settings
  # ----------------------------------------------------
  networking = {
    useDHCP = false;
    useNetworkd = true;
    
    # Strictly define firewalls for local ports while trusting Tailscale
    firewall = {
      enable = true;
      allowedTCPPorts = [ 22 ]; # Keep standard SSH open for backup local access
      
      # Always trust the Tailscale network interface completely
      trustedInterfaces = [ "tailscale0" ];
      
      # Required ports for Tailscale negotiation
      allowedUDPPorts = [ config.services.tailscale.port ];
    };
  };

  # Enable the Tailscale Daemon
  services.tailscale.enable = true;

  # Core Package Set (Explicitly adding tailscale here)
  environment.systemPackages = with pkgs; [
    tailscale
    curl
    git
    htop
    vim
  ];

  # ... Keep your existing nix store optimizations and stateVersion ...
}
Use code with caution.2. Inject Home Manager into Your flake.nixInstead of running Home Manager as a standalone tool, the best practice in a modular Flake schema is to run it as a NixOS module. This couples your system configuration and user dotfiles into a single unified build step.Update your top-level flake.nix file to include the Home Manager input and pipe it into your node configurations:nix{
  description = "Modular Fleet Infrastructure Flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    
    disko.url = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";

    # Add Home Manager Input
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, disko, home-manager, ... }@inputs: {
    nixosConfigurations = {
      # The iMac Target Profile
      imac-target = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          disko.nixosModules.disko
          ./hosts/imac-a1131/disko.nix
          ./hosts/imac-a1131/configuration.nix
          ./modules/hardware/imac-gpu.nix
          ./modules/core.nix

          # Inject Home Manager as a system module
          home-manager.nixosModules.home-manager
          {
            home-manager.useGlobalPkgs = true;
            home-manager.useUserPackages = true;
            
            # Map your user profile configurations directly
            home-manager.users.admin = import ./modules/home/admin.nix;
          }
        ];
      };
    };
  };
}
Use code with caution.3. Create Your Home Manager User ProfileNow create the user profile configuration at modules/home/admin.nix. This handles shell aliases, git configs, and user-specific packages for your admin account across the entire fleet.nix{ config, pkgs, ... }:

{
  # Home Manager needs to know its state version for backwards compatibility
  home.stateVersion = "24.11"; 

  # User-specific packages that shouldn't clutter the root system profile
  home.packages = with pkgs; [
    neofetch
    ripgrep
    fd
  ];

  # Declarative Shell Configuration (e.g., Bash or Zsh)
  programs.bash = {
    enable = true;
    shellAliases = {
      ll = "ls -l";
      fleet-update = "nixos-rebuild switch --flake .#imac-target";
      ts = "tailscale status";
    };
  };

  # Declarative Git setup
  programs.git = {
    enable = true;
    userName = "Fleet Administrator";
    userEmail = "admin@yourdomain.local";
    extraConfig = {
      init.defaultBranch = "main";
    };
  };
}
Use code with caution.4. Deploying via TailscaleOnce the iMac or bare-metal machines are authenticated to your Tailscale tailnet once via sudo tailscale up, they will persist their Tailscale IP address.You no longer need to worry about what local IP address your router hands out. You can perform target rebuilds directly over the secure Tailscale mesh tunnel from your development machine:bashnixos-rebuild switch \
  --flake .#imac-target \
  --target-host admin@100.X.Y.Z \ # Your iMac's Tailscale IP
  --use-remote-sudo
Use code with caution.Would you like to see how to automate the Tailscale authentication step during the nixos-anywhere deployment using a Tailscale Auth Key passed via sops-nix or a secret file, or should we look at configuring a graphical desktop environment (like XFCE or a window manager) within Home Manager for the old iMac?You said: automating tailscaleTo automate Tailscale authentication during a cold, bare-metal installation via nixos-anywhere, you must bypass the standard interactive browser login. The most robust way to achieve this is by configuring NixOS to consume a Tailscale Auth Key natively on boot.Because you are using nixos-anywhere, you can pass this cryptographic key directly from your deployment host into the target machine's temporary RAM installer disk layout, preventing secrets from being checked into your public or private Git repositories.1. Generate a Tailscale Auth KeyGo to your Tailscale Admin Console -> Settings -> Keys.Click Generate auth key.Ensure you choose the following configurations:Reusable: Keep this checked if you want to deploy multiple machines using the same configuration.Expiration: Tailscale enforces a maximum 90-day validity window for standard auth keys.Tags: (Optional but highly recommended) Assign a tag like tag:server. This prevents the machine's keys from expiring on your tailnet automatically over time.Copy the generated key (it looks like tskey-auth-abcd1234...).Save this key inside a local text file on your development computer, outside your flake repository path (e.g., ~/secrets/tailscale-key).2. Configure Your Core Module to Consume the Key FileNixOS provides a native configuration parameter called services.tailscale.authKeyFile. This points the tailscaled-autoconnect systemd service directly to a path on the local drive.Update your global modules/core.nix file to configure this path:nix{ config, pkgs, ... }:

{
  # Ensure the Tailscale daemon is enabled
  services.tailscale = {
    enable = true;
    
    # Points to a runtime directory protected by root privileges
    authKeyFile = "/run/secrets/tailscale-key";
    
    # Pass extra instructions (like tags if configured on your console)
    extraUpFlags = [ "--advertise-tags=tag:server" ];
  };

  # Make sure the target system layout permits traffic out-of-the-box
  networking.firewall = {
    trustedInterfaces = [ "tailscale0" ];
    allowedUDPPorts = [ config.services.tailscale.port ];
  };
}
Use code with caution.3. Deploying Remotely via nixos-anywhere --extra-filesWhen you execute nixos-anywhere, the target drive is partitioned and completely blanked out. To make sure the target machine has the auth key file waiting inside /run/secrets/tailscale-key before the first boot cycle terminates, use the --extra-files flag to pass directory structures on-the-fly.Step A: Structure a Local Secrets DirectoryCreate a temporary tree structure on your development laptop that matches the path expected by the target machine:bashmkdir -p /tmp/target-secrets/run/secrets
cp ~/secrets/tailscale-key /tmp/target-secrets/run/secrets/tailscale-key
chmod 600 /tmp/target-secrets/run/secrets/tailscale-key
Use code with caution.Step B: Run the Deploy ScriptRun the installation command from your development computer over standard local network SSH. Point it to the target's temporary root configuration and specify your extra file tree:bashnix run github:nix-community/nixos-anywhere -- \
  --flake .#imac-target \
  --extra-files /tmp/target-secrets \
  root@<LOCAL_IMAC_IP>
Use code with caution.What happens next?nixos-anywhere securely copies the /tmp/target-secrets/ structure directly into the transient installation tree of the target device.The machine completes the declarative installation cycle.Upon its very first real boot sequence, the native tailscaled-autoconnect systemd unit triggers.It reads the token file at /run/secrets/tailscale-key, pairs silently with your Tailscale control plane, and establishes its secure IP node mapping.4. Transitioning Post-Install to sops-nix or agenixWhile using the --extra-files runtime injection flag is perfect for the initial automated installation phase, you will want persistent runtime secret management. For ongoing configurations, use a framework like sops-nix.Once the machine is running, you can alter modules/core.nix so that authKeyFile dynamically hooks directly into a sops secret block:nix# Example of shifting to sops-nix after the initial installation is complete
services.tailscale.authKeyFile = config.sops.secrets.tailscale_key.path;
sops.secrets.tailscale_key.file = ../../secrets/secrets.yaml;
Use code with caution.Now that your network authentication steps are fully automated, would you like to map out the Home Manager configuration files next to prepare user specific dotfiles, or do you want to configure Tailscale exit-node / subnet routing properties for your bare metal targets?NixOS as a server, part 2: Flake, tailscale - Guekka's blogMay 17, 2023 — Secrets with Sops. In order to set up Tailscale, we will use a pre-auth key. This allows us to connect to our server without inter...GitHubAuth keys · Tailscale DocsDec 4, 2025 — To generate an auth key: * Open the Keys page of the admin console. * Select Generate auth key. * Fill out the form fields to spec...Tailscale[solved] Possible to automatically authenticate Tailscale after ...Jul 29, 2021 — # always allow traffic from your Tailscale network networking. trustedInterfaces = [ "tailscale0" ]; # allow the Tailscale UDP por...NixOS DiscourseSetting Up Headscale & Tailscale on NixOS: A Zero-Trust ...May 7, 2025 — hey YouTube today we are diving into setting up head scale and tail scale on Nixos. this is a powerful combination that allows eas...37:16YouTube·Sascha KoenigNixOS Automated Remote Bootstrapping with SecretsJun 3, 2024 — hi everyone i'm really excited to be finally kicking this video series off i'm going to start with a demonstration of the entire p...8mYouTube·EmergentMindTailscale - NixOS WikiBasic setup. Enabling tailscale is as simple as adding services. tailscale. enable = true; to your Nix config and running "sudo ta...Nix WikiNeed some help getting services.tailscale to run : r/NixOSNov 14, 2025 — { config, lib, pkgs, specialArgs, ... }: let homelabSettings = specialArgs.homelabSettings; in { options = { tailscale = { enable ...Reddit·r/NixOSservices.tailscale.authKeyFile - MyNixOSA file containing the auth key. Tailscale will be automatically started if provided. Services that bind to Tailscale IPs should or...MyNixOStailscaled-autoconnect.service prevents multi-user.target from ...Aug 3, 2025 — Describe the bug. tailscaled-autoconnect. service currently runs forever if tailscaled fails to connect. This prevents multi-user.GitHubTailscale - Official NixOS WikiJun 7, 2026 — Basic setup. To enable Tailscale, add the following to your configuration: ❄︎ /etc/nixos/configuration.nix. { services. tailscale ...NixOS WikiSecurely connect to your Azure Linux Virtual Machine with Tailscale SSHAug 12, 2022 — In the Tailscale Admin console, click on “ Settings”, then click on “ Keys”, then “ Generate auth key..”.paulyu.devGitHub - imeach-sd/tailscale_silent_install: Automation & Reliable Deployment of TailscaleOct 10, 2025 — How to generate your Auth Key in Tailscale Description: Give it an easy-to-remember name, like automatic-login-key . Reusable: Mak...GitHub
