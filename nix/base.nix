# =============================================================================
# TGW base NixOS config — imported by every TGW host (vm, tgw-test, production)
# =============================================================================
{ pkgs, ... }:
{
  time.timeZone = "America/Los_Angeles";

  i18n.defaultLocale = "en_US.UTF-8";

  # SSH on every machine — primary remote management path
  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = true;
  };
  networking.firewall.allowedTCPPorts = [ 22 ];

  # Admin tools available on every host
  environment.systemPackages = with pkgs; [
    git
    curl
    wget
    rsync
    rclone
    htop
    mc
    tmux
    age
    fzf
    ripgrep
    gh
    ffmpeg
    imagemagick
    exiftool
    inotify-tools
    chafa
    nettools
    thefuck
    j4-dmenu-desktop
  ];

  # zoxide — smart cd; shell integration injected automatically
  programs.zoxide.enable = true;

  # zsh
  programs.zsh.enable = true;

  # kdeconnect — phone integration; opens firewall ports 1714-1764
  programs.kdeconnect.enable = true;

  # tailscale
  services.tailscale.enable = true;

  # bluetooth
  hardware.bluetooth.enable = true;
  services.blueman.enable = true;

  # avahi — mDNS/DNS-SD (required by kdeconnect, network discovery)
  services.avahi = {
    enable = true;
    nssmdns4 = true;
  };

  # printing
  services.printing.enable = true;

  # disk health monitoring
  services.smartd.enable = true;

  # syncthing — runs as dave; data dir default (~/.local/share/syncthing)
  services.syncthing = {
    enable = true;
    user = "dave";
    group = "users";
    openDefaultPorts = true;
  };

  # ydotool — automation daemon
  programs.ydotool.enable = true;

  # NFS server
  services.nfs.server.enable = true;
  networking.firewall.allowedTCPPorts = [ 22 2049 ];
  networking.firewall.allowedUDPPorts = [ 2049 ];

  # Explicit time sync (systemd-timesyncd is the NixOS default but declare it)
  services.timesyncd.enable = true;

  nix.settings.experimental-features = [ "nix-command" "flakes" ];
}
