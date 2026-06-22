# =============================================================================
# CatioNIX base — common OS config for every host
#
# CatioNIX is a TGW-agnostic AI operational safety platform layer.
# This file knows nothing about TGW.  The test: would this config make sense
# on a host running a completely different application instead of TGW?
# If yes → it belongs here.  If no → it belongs in nix/tgw/.
#
# TGW-specific additions (syncthing tgw-flake folder, tgw-rebuild alias,
# media tools, GitHub CLI, ydotool) live in nix/tgw/platform.nix.
# NFS server lives in nix/nfs-exports.nix (server hosts only).
# =============================================================================
{ pkgs, ... }:
{
  time.timeZone = "America/Los_Angeles";

  i18n.defaultLocale = "en_US.UTF-8";

  # SSH — primary remote management path on every host
  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = true;
  };
  networking.firewall.allowedTCPPorts = [ 22 ];

  # Platform tools: present on every CatioNIX host regardless of application
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
    inotify-tools
    nettools
    python3      # venv creation/rebuild after restore: python3 -m venv + pip install -e
  ];

  programs.zoxide.enable = true;
  programs.fish.enable   = true;
  programs.zsh.enable    = true;

  services.tailscale.enable = true;

  # avahi — mDNS/DNS-SD (network discovery; also needed by KDE Connect on desktop hosts)
  services.avahi = {
    enable   = true;
    nssmdns4 = true;
  };

  services.smartd.enable = true;

  # syncthing — platform sync daemon, running as the operator user (db).
  # Folders are declared per layer: tgw-flake/tgw-install-bundle in nix/tgw/platform.nix.
  services.syncthing = {
    enable           = true;
    user             = "db";
    group            = "users";
    openDefaultPorts = true;
  };

  services.timesyncd.enable = true;

  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Allow wheel-group users (operator `db`) to push unsigned closures via
  # `nixos-rebuild --target-host` from the MX build machine.  MX does not
  # sign its Nix builds, so without this tgw-test refuses incoming paths.
  nix.settings.trusted-users = [ "root" "@wheel" ];
}
