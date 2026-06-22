# =============================================================================
# Home Manager config for operator account: db (uid 1000)
#
# Shell strategy:
#   fish  — primary login shell; autosuggestions + zoxide built-in, no plugins needed
#   bash  — fallback; history settings for the occasional bash session
#   zsh   — not configured; fish covers all the zsh use-cases more cleanly
#
# Zoxide: enabled system-wide in nix/os/base.nix (programs.zoxide.enable).
# HM's fish integration picks up the init automatically — no manual eval needed.
# =============================================================================
{ ... }:
{
  home.stateVersion = "25.05";

  # Qtile config — managed directly from the flake source tree.
  home.file.".config/qtile/config.py".source      = ../qtile/config.py;
  home.file.".config/qtile/tgw_widgets.py".source = ../qtile/tgw_widgets.py;
  home.file.".config/qtile/cheatsheet.txt".source = ../qtile/cheatsheet.txt;

  # XDG user directories
  xdg.userDirs = {
    enable     = true;
    createDirectories = true;
    desktop    = "$HOME/Desktop";
    documents  = "$HOME/Documents";
    download   = "$HOME/Downloads";
    pictures   = "$HOME/Pictures";
    music      = null;
    videos     = null;
    templates  = null;
    publicShare = null;
  };

  # ---------------------------------------------------------------------------
  # fish — primary shell
  # ---------------------------------------------------------------------------
  programs.fish = {
    enable = true;

    shellAliases = {
      ll     = "ls -lh";
      la     = "ls -A";
      l      = "ls -CF";
      tgwlog = "journalctl -u 'tgw-worker@*' -f";
      tgwps  = "psql -U tgw state_machine";
    };

    # fish_add_path prepends to $fish_user_paths (persists across sessions).
    # TGW venv exposes the `tgw` CLI without sudo.
    shellInit = ''
      fish_add_path /opt/TGW/.venvironments/tgw/bin
    '';

    # fish history: dedup + timestamps are on by default.
    # Ctrl+R and up-arrow search history; no extra config needed.
  };

  # ---------------------------------------------------------------------------
  # bash — fallback / script compatibility
  # ---------------------------------------------------------------------------
  programs.bash = {
    enable         = true;
    historyControl = [ "ignoreboth" ];   # ignoredups + ignorespace
    historySize    = 10000;
    shellOptions   = [ "histappend" "checkwinsize" ];

    shellAliases = {
      ll = "ls -lh";
      la = "ls -A";
      l  = "ls -CF";
    };

    # Minimal prompt matching your MX color scheme
    initExtra = ''
      PURPLE='\[\e[1;35m\]'; CYAN='\[\e[1;36m\]'; GREEN='\[\e[1;32m\]'; nc='\[\e[0m\]'
      PS1="$PURPLE\u$nc@$CYAN\H$nc:$GREEN\w$nc\n$GREEN\$$nc "
      export PATH="/opt/TGW/.venvironments/tgw/bin:$PATH"
    '';
  };
}
