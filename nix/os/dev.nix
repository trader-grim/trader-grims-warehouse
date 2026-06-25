# =============================================================================
# Dev tool layer — import only on hosts where active development happens.
#
# Omit on client/portable machines (tgw-test, satellites) and headless servers
# that don't carry the codebase.
#
# System packages: Node.js (for Claude CLI via npm).
# User packages (Home Manager): Aider.
# PATH: ~/.npm/bin added to fish for Claude CLI.
#
# Post-install one-time step (first login as db):
#   npm install -g @anthropic-ai/claude-code
# =============================================================================
{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    nodejs_22    # npm; Claude CLI installs into ~/.npm/bin
    ruff         # Python linter; pre-commit hook uses system ruff
  ];

  # Per-user dev packages merged into the operator's Home Manager config.
  # These merge cleanly with nix/home/db.nix via the NixOS module system.
  home-manager.users.db = { pkgs, ... }: {
    home.packages = with pkgs; [
      aider-chat
    ];

    programs.fish.shellInit = ''
      fish_add_path $HOME/.npm/bin
    '';
  };
}
