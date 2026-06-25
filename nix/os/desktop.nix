# =============================================================================
# CatioNIX desktop — X11 + Qtile GUI layer (opt-in)
#
# OS-level desktop: enables X11, SDDM, Qtile, KDE Connect, bluetooth, Logitech,
# printing, and the standard desktop application suite.
#
# This module knows nothing about TGW.  It enables Qtile without any specific
# config or extraPackages — those are provided by nix/tgw/desktop.nix when TGW
# is running on this host.
#
# Import this on any host that has a display.  Omit for headless hosts.
# =============================================================================
{ pkgs, ... }:
{
  nixpkgs.config.allowUnfree = true;

  services.xserver = {
    enable = true;
    windowManager.qtile.enable = true;
    # extraPackages: not set here — added by nix/tgw/desktop.nix for TGW widgets
  };

  services.displayManager.sddm.enable = true;

  # KDE Connect — phone integration; opens firewall ports 1714-1764
  programs.kdeconnect.enable = true;

  hardware.bluetooth.enable          = true;
  services.blueman.enable            = true;

  hardware.logitech.wireless.enable          = true;
  hardware.logitech.wireless.enableGraphical = true;

  services.printing.enable = true;

  environment.systemPackages = with pkgs; [
    kdePackages.konsole    # fallback / scratchpad terminal
    kitty                  # GPU-accelerated terminal — default Super+Enter
    dmenu                  # Super+D launcher
    j4-dmenu-desktop       # .desktop file wrapper for dmenu
    xterm                  # fallback terminal
    firefox
    google-chrome
    obsidian
    vscodium               # fallback editor; Cursor added per-user in home/db.nix
    kdePackages.kcalc
    kdePackages.dolphin
    kdePackages.gwenview
    kdePackages.krfb
    libreoffice
    tigervnc
    cloudflared

    # Clipboard + X automation
    xclip
    xsel
    wl-clipboard           # wl-copy / wl-paste for Wayland sessions
    xdotool
    xdg-utils              # xdg-open: open URLs/files with default app

  ];
}
