# =============================================================================
# Home Manager config for operator account: db (uid 1000)
#
# CatioNIX layer — platform-generic operator UX only.
# NO TGW-specific config here.  TGW shell additions (tgw wrapper, tgwlog,
# tgwps, venv path) live in nix/tgw/home.nix and are merged at import time
# by nix/home/hm-module.nix.
#
# Shell strategy:
#   fish  — primary login shell; autosuggestions + zoxide built-in
#   bash  — fallback; history settings for occasional bash sessions
#
# Zoxide: enabled system-wide in nix/os/base.nix (programs.zoxide.enable).
# npm/aider: managed by nix/os/dev.nix (prod hosts only).
# =============================================================================
{ ... }:
{
  home.stateVersion = "25.05";

  # Qtile config — managed directly from the flake source tree.
  home.file.".config/qtile/config.py".source      = ../qtile/config.py;
  home.file.".config/qtile/tgw_widgets.py".source = ../qtile/tgw_widgets.py;
  home.file.".config/qtile/cheatsheet.txt".source = ../qtile/cheatsheet.txt;
  home.file.".config/qtile/autostart.sh".source   = ../qtile/autostart.sh;

  # XDG user directories
  xdg.userDirs = {
    enable            = true;
    createDirectories = true;
    desktop           = "$HOME/Desktop";
    documents         = "$HOME/Documents";
    download          = "$HOME/Downloads";
    pictures          = "$HOME/Pictures";
    music             = null;
    videos            = null;
    templates         = null;
    publicShare       = null;
  };

  # ---------------------------------------------------------------------------
  # tgw-clipd — TGW clipboard history daemon (PP-CLIP-001)
  # Runs as a user service in the graphical session so it inherits DISPLAY and
  # XAUTHORITY. Uses the TGW venv binary; python-xlib installed in that venv.
  # ---------------------------------------------------------------------------
  systemd.user.services.tgw-clipd = {
    Unit = {
      Description = "TGW clipboard history daemon";
      After       = [ "graphical-session.target" ];
      PartOf      = [ "graphical-session.target" ];
    };
    Service = {
      ExecStart = "/opt/TGW/.venvironments/tgw/bin/tgw-clipd";
      Restart   = "on-failure";
      RestartSec = 3;
      # X11 + Wayland session vars — imported via autostart.sh on each login.
      # PATH must include system bin so wl-paste and xclip are reachable.
      PassEnvironment = [ "DISPLAY" "XAUTHORITY" "WAYLAND_DISPLAY" "XDG_RUNTIME_DIR" "XDG_SESSION_TYPE" ];
      Environment = [
        "PATH=/run/current-system/sw/bin:/opt/TGW/.venvironments/tgw/bin"
      ];
    };
    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  # ---------------------------------------------------------------------------
  # fish — primary shell (CatioNIX operator UX)
  # ---------------------------------------------------------------------------
  programs.fish = {
    enable = true;

    shellAliases = {
      ll = "ls -lh";
      la = "ls -A";
      l  = "ls -CF";
    };

    shellInit = ''
      fish_add_path $HOME/.local/bin
    '';

    functions = {
      claude = {
        description = "Claude Code with auto-retry on rate-limit and connectivity gaps";
        body        = ''
          if command -q claude-auto-retry
              claude-auto-retry $argv
          else
              command claude $argv
          end
        '';
      };
    };
  };

  # ---------------------------------------------------------------------------
  # bash — fallback / script compatibility
  # ---------------------------------------------------------------------------
  programs.bash = {
    enable         = true;
    historyControl = [ "ignoreboth" ];
    historySize    = 10000;
    shellOptions   = [ "histappend" "checkwinsize" ];

    shellAliases = {
      ll = "ls -lh";
      la = "ls -A";
      l  = "ls -CF";
    };

    initExtra = ''
      PURPLE='\[\e[1;35m\]'; CYAN='\[\e[1;36m\]'; GREEN='\[\e[1;32m\]'; nc='\[\e[0m\]'
      PS1="$PURPLE\u$nc@$CYAN\H$nc:$GREEN\w$nc\n$GREEN\$$nc "
      export PATH="$HOME/.local/bin:$HOME/.npm/bin:$PATH"
    '';
  };
}
