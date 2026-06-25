# =============================================================================
# TGW Home Manager additions for the operator account (db)
#
# TGW-specific shell config: service account sudo wrapper, task aliases,
# venv path.  Imported alongside nix/home/db.nix via nix/home/hm-module.nix.
#
# Layer rule: everything here is TGW-specific and knows about /opt/TGW, the
# tgw service account, and the worker fleet.  Generic operator UX lives in
# nix/home/db.nix (CatioNIX layer).
# =============================================================================
{ ... }:
{
  programs.fish = {
    shellAliases = {
      tgwlog = "journalctl -u 'tgw-worker@*' -f";
      tgwps  = "psql -U tgw state_machine";
    };

    shellInit = ''
      fish_add_path /opt/TGW/.venvironments/tgw/bin
    '';

    functions = {
      tgw = {
        description = "Run tgw CLI as the tgw service account";
        body        = ''
          # clip reads the operator's own clipboard DB — run as current user, not tgw
          if test "$argv[1]" = clip
              command tgw $argv
          else
              sudo -u tgw tgw $argv
          end
        ''
      };
    };
  };

  programs.bash.initExtra = ''
    export PATH="/opt/TGW/.venvironments/tgw/bin:$PATH"
  '';
}
