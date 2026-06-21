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
  ];

  # zoxide — smart cd; shell integration injected automatically
  programs.zoxide.enable = true;

  # kdeconnect — phone integration; opens firewall ports 1714-1764
  programs.kdeconnect.enable = true;

  # Explicit time sync (systemd-timesyncd is the NixOS default but declare it)
  services.timesyncd.enable = true;

  nix.settings.experimental-features = [ "nix-command" "flakes" ];
}
