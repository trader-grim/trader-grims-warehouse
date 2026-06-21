# TGW macroboard — keyd key remapping (production host only, not tgw-test)
# Import in the production host config: ./nix/keyd.nix
{ ... }:
{
  services.keyd.enable = true;
  environment.etc."keyd/tgw-macroboard.conf".source = ./keyd-macroboard.conf;
}
